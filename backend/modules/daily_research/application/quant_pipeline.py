"""Nightly all-published-strategy scan and event-weighted candidate ranking."""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from datetime import time as datetime_time
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from AI.eventStudy.review import news_dao
from backend.modules.analysis.application.contracts import AnalysisArtifact, ClaimedTask
from backend.modules.analysis.application.errors import (
    LeaseConflictError,
    RetryableAnalysisError,
)
from backend.modules.analysis.domain.enums import TaskStatus
from backend.modules.analysis.infrastructure.models import AnalysisReport, AnalysisTask
from backend.modules.analysis.infrastructure.quant_execution_market_data import (
    AllMarketUniverseBuilder,
)
from backend.modules.quant_strategy.application.data_readiness import (
    DataReadinessError,
    DataReadinessGate,
)
from backend.modules.quant_strategy.application.execution import QuantExecutionService
from backend.modules.quant_strategy.application.execution_constraints import (
    ExecutionPolicy,
)
from backend.modules.quant_strategy.domain.templates import (
    freeze_template_contract,
    get_template,
)
from backend.modules.quant_strategy.infrastructure.models import (
    QuantStrategy,
    QuantStrategyVersion,
)
from backend.modules.quant_strategy.infrastructure.signals import (
    QuantExecutionSignal,
)

logger = logging.getLogger(__name__)
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_DIRECTION_SIGN = {"bullish": 1.0, "bearish": -1.0, "neutral": 0.0, "mixed": 0.0}


def _strategy_snapshot(strategy: QuantStrategy, version: QuantStrategyVersion) -> dict[str, Any]:
    template = get_template(version.template_id) if version.template_id else None
    return {
        "schema_version": "daily_quant_scan_v1",
        "strategy": {
            "strategy_id": str(strategy.id),
            "version_id": str(version.id),
            "version_no": version.version_no,
            "source_code": version.source_code,
            "source_hash": version.source_hash,
            "name": strategy.name,
            "template_id": version.template_id,
            "template_params": version.template_params,
            "template_renderer_version": version.template_renderer_version,
            "required_bars": template.required_bars if template else 250,
            "template_contract": freeze_template_contract(template) if template else None,
            "published_at": version.published_at.isoformat() if version.published_at else None,
            "lifecycle_policy": None,
        },
        "portfolio": None,
        "positions": [],
        "pending_orders": [],
        "execution_policy": ExecutionPolicy().to_snapshot(),
    }


def _published_strategies(session) -> list[tuple[QuantStrategy, QuantStrategyVersion]]:
    latest = (
        select(
            QuantStrategyVersion.strategy_id,
            func.max(QuantStrategyVersion.version_no).label("version_no"),
        )
        .where(QuantStrategyVersion.status == "PUBLISHED")
        .group_by(QuantStrategyVersion.strategy_id)
        .subquery()
    )
    rows = session.execute(
        select(QuantStrategy, QuantStrategyVersion)
        .join(QuantStrategyVersion, QuantStrategyVersion.strategy_id == QuantStrategy.id)
        .join(
            latest,
            (latest.c.strategy_id == QuantStrategyVersion.strategy_id)
            & (latest.c.version_no == QuantStrategyVersion.version_no),
        )
        .order_by(QuantStrategy.id)
    )
    return [(strategy, version) for strategy, version in rows]


def _scheduled_news_dependency(session, *, workflow: dict, wait_until: datetime) -> dict[str, Any]:
    """Read same-day news state without delaying the independent strategy scan."""
    slot = workflow.get("slot")
    if workflow.get("trigger") != "scheduled" or slot not in {"quant_2100", "quant_news_refresh"}:
        return {"status": "not_required", "complete": True, "task_id": None}
    scheduled_at = datetime.fromisoformat(str(workflow["scheduled_at"]).replace("Z", "+00:00"))
    if slot == "quant_news_refresh":
        try:
            news_task_id = uuid.UUID(str(workflow["refresh_news_task_id"]))
        except (KeyError, ValueError, TypeError):
            news_task_id = None
        news_task = session.get(AnalysisTask, news_task_id) if news_task_id else None
    else:
        local_day = scheduled_at.astimezone(_SHANGHAI).date()
        day_start = datetime.combine(local_day, datetime.min.time(), tzinfo=_SHANGHAI)
        day_end = day_start + timedelta(days=1)
        news_task = session.scalar(
            select(AnalysisTask)
            .where(
                AnalysisTask.task_type == "DAILY_RESEARCH",
                AnalysisTask.request_params["daily_research"]["kind"].as_string() == "news",
                AnalysisTask.request_params["daily_research"]["slot"].as_string() == "news_2100",
                AnalysisTask.created_at >= day_start,
                AnalysisTask.created_at < day_end,
            )
            .order_by(AnalysisTask.created_at.desc())
            .limit(1)
        )
    if news_task is None:
        session.rollback()
        return {
            "status": "missing", "complete": False,
            "active": slot == "quant_2100" and datetime.now(timezone.utc) < wait_until,
            "task_id": None,
        }

    report = session.scalar(
        select(AnalysisReport)
        .where(AnalysisReport.task_id == news_task.id)
        .order_by(AnalysisReport.report_version.desc())
        .limit(1)
    )
    block = ((report.report_json or {}).get("daily_research") if report else None) or {}
    status = str(block.get("status") or news_task.status.lower())
    active = news_task.status in {
        TaskStatus.PENDING.value, TaskStatus.QUEUED.value, TaskStatus.RUNNING.value,
        TaskStatus.RETRYING.value, TaskStatus.CANCEL_REQUESTED.value,
    }
    complete = news_task.status == TaskStatus.SUCCEEDED.value and status == "completed"
    pending_news = None
    try:
        from backend.modules.daily_research.application.news_pipeline import (
            dbapi_connection,
        )

        cutoff = datetime.fromisoformat(
            str(workflow.get("news_cutoff_at") or workflow["scheduled_at"]).replace("Z", "+00:00")
        )
        pending_news = news_dao.count_unprocessed_news(
            dbapi_connection(session), as_of=cutoff.astimezone(timezone.utc),
        )
    except Exception:
        logger.warning("读取 21:00 截止前新闻待处理数失败", exc_info=True)
    dependency = {
        "status": status,
        "complete": complete and pending_news == 0,
        "active": active or (pending_news is not None and pending_news > 0),
        "pending_news_count": pending_news,
        "task_id": str(news_task.id),
        "report_as_of": block.get("report_as_of"),
        "market_outlook": block.get("market_outlook"),
        "source_coverage": block.get("source_coverage"),
    }
    if pending_news == 0:
        # Incremental same-day news runs may complete after the fixed 21:00 report;
        # as long as the source cutoff is unchanged, freeze their assessments at the
        # time the backlog actually reached zero.
        dependency["report_as_of"] = datetime.now(timezone.utc).isoformat()
    session.rollback()
    if dependency["active"] and datetime.now(timezone.utc) >= wait_until:
        dependency["active"] = False
    return dependency


def _event_snapshot_times(
    *, workflow: dict[str, Any], news_dependency: dict[str, Any],
) -> tuple[datetime, datetime]:
    """Keep the source-news cutoff distinct from assessment materialization time."""
    cutoff_text = workflow.get("news_cutoff_at") or workflow.get("scheduled_at")
    if not cutoff_text:
        raise ValueError("每日量化任务缺少事件快照截止时点")
    cutoff = datetime.fromisoformat(str(cutoff_text).replace("Z", "+00:00"))
    if cutoff.tzinfo is None:
        raise ValueError("事件新闻截止时点必须包含时区")
    assessment_as_of = cutoff
    if workflow.get("trigger") == "scheduled" and workflow.get("slot") in {
        "quant_2100", "quant_news_refresh",
    }:
        report_as_of_text = news_dependency.get("report_as_of")
        if report_as_of_text:
            report_as_of = datetime.fromisoformat(str(report_as_of_text).replace("Z", "+00:00"))
            if report_as_of.tzinfo is not None and report_as_of > assessment_as_of:
                assessment_as_of = report_as_of
    return assessment_as_of.astimezone(timezone.utc), cutoff.astimezone(timezone.utc)


def _manual_uses_latest(local_run_at: datetime, is_trading_day_fn: Callable[[str], bool]) -> bool:
    """Before the market data-ready close cut or on a holiday, use last complete data."""
    return (
        local_run_at.time() < datetime_time(15, 30)
        or not is_trading_day_fn(local_run_at.date().isoformat())
    )


def _uses_latest_complete_market_data(
    *, trigger: str, requested_trade_date: date, manual_uses_latest: bool,
    is_trading_day_fn: Callable[[str], bool], effective_trade_date: date | None = None,
) -> bool:
    return (effective_trade_date is not None and effective_trade_date < requested_trade_date) or (
        manual_uses_latest
    ) or (
        trigger == "scheduled" and not is_trading_day_fn(requested_trade_date.isoformat())
    )


def _workflow_instant(workflow: dict[str, Any], key: str) -> datetime:
    value = workflow.get(key)
    if not value:
        raise ValueError(f"每日量化任务缺少 {key}")
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"每日量化任务 {key} 必须包含时区")
    return parsed.astimezone(timezone.utc)


def _stock_universe_digest(codes) -> str:
    return hashlib.sha256("\n".join(sorted(codes)).encode("utf-8")).hexdigest()


def _is_sha256_digest(value) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _finish_quant_without_scan(
    bundle, claimed, *, workflow, requested_trade_date, scheduled_trade_date,
    event_as_of, event_cutoff_at, news_dependency, preflight, reason, persist_artifact,
) -> dict[str, Any]:
    target_date = None if preflight.get("suppress_target_trade_date") else (
        preflight.get("effective_trade_date") or requested_trade_date.isoformat()
    )
    market_outlook = news_dependency.get("market_outlook") or {"risk_gate": "block"}
    report = {
        "schema_version": "daily_research_quant_v1", "kind": "quant", "status": "partial",
        "slot": workflow.get("slot"), "strategy_scan_reused": False,
        "reason": reason, "target_trade_date": target_date,
        "requested_trade_date": scheduled_trade_date.isoformat(),
        "market_session_status": "UNKNOWN", "used_latest_complete_market_data": False,
        "market_as_of_trade_date": None,
        "event_as_of": event_as_of.isoformat() if event_as_of else None,
        "event_assessment_ids": [],
        "event_news_cutoff_at": event_cutoff_at.isoformat() if event_cutoff_at else None,
        "news_research_dependency": news_dependency,
        "event_evidence_complete": bool(news_dependency.get("complete", True)),
        "risk_gate": "block", "market_outlook": market_outlook,
        "quant_input_preflight": preflight,
        "strategies": [], "strategy_count": 0,
        "candidates": [], "candidate_count": 0, "candidate_total": 0,
        "warnings": ["量化行情预检或股票池一致性未在截止时间前通过，未运行策略扫描"],
    }
    return _complete_quant(bundle, claimed, report, persist_artifact)


def _refresh_market_snapshot_date(
    *,
    requested_trade_date: date,
    readiness_trade_date: date | None,
    parent_market_date: date | None,
) -> tuple[date, bool]:
    """Keep a news refresh on the original scan date when readiness is unavailable."""
    frozen_date = parent_market_date or requested_trade_date
    return frozen_date, readiness_trade_date != frozen_date


def _rank_percentiles(rows: list[QuantExecutionSignal]) -> dict[int, float]:
    by_strategy: dict[uuid.UUID | None, list[QuantExecutionSignal]] = defaultdict(list)
    for row in rows:
        by_strategy[row.strategy_version_id].append(row)
    result: dict[int, float] = {}
    for group in by_strategy.values():
        ordered = sorted(group, key=lambda row: (Decimal(str(row.score or 0)), row.ts_code, row.id))
        total = len(ordered)
        if total == 1:
            result[int(ordered[0].id)] = 50.0
            continue
        index = 0
        while index < total:
            end = index + 1
            score = Decimal(str(ordered[index].score or 0))
            while end < total and Decimal(str(ordered[end].score or 0)) == score:
                end += 1
            # Zero-based tie range -> 1-based average rank, as defined by the plan.
            midrank = (index + 1 + end) / 2
            rank_pct = 100.0 * (midrank - 0.5) / total
            for i in range(index, end):
                result[int(ordered[i].id)] = rank_pct
            index = end
    return result


def _membership_maps(market_conn) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    industries: dict[str, set[str]] = defaultdict(set)
    sectors: dict[str, set[str]] = defaultdict(set)
    for ref, ticker in market_conn.execute(
        "SELECT industry_code, ts_code FROM market.industry_member WHERE source='SW2021'"
    ).fetchall():
        industries[f"SW:{ref}"].add(ticker)
    for ref, ticker in market_conn.execute(
        "SELECT sector_code, ts_code FROM market.sector_member WHERE source='dc'"
    ).fetchall():
        sectors[f"CONCEPT:{ref}"].add(ticker)
    return industries, sectors


def _target_tickers(
    target: dict[str, Any],
    *,
    universe: set[str],
    industries: dict[str, set[str]],
    sectors: dict[str, set[str]],
) -> set[str]:
    scope = target.get("scope")
    refs = target.get("scope_refs") or []
    if scope == "market" and target.get("target") == "market:CN":
        return set(universe)
    if scope == "stock":
        return {ref.removeprefix("stock:") for ref in refs if ref.startswith("stock:")}
    if scope == "sector":
        result: set[str] = set()
        for ref in refs:
            result.update(industries.get(ref, set()))
            result.update(sectors.get(ref, set()))
        return result
    return set()


def _event_adjustments(
    candidates: list[dict[str, Any]],
    *,
    as_of: datetime,
    universe: set[str],
    industries: dict[str, set[str]],
    sectors: dict[str, set[str]],
) -> dict[str, list[dict[str, Any]]]:
    # Stable scoring atom: one event fact per target stock. If the same atom reaches
    # a stock by multiple memberships, the most specific target wins exactly once.
    atoms: dict[tuple[int, str, str], tuple[int, dict[str, Any]]] = {}
    for event in candidates:
        labels = event.get("labels") or {}
        fact = labels.get("fact") or {}
        available_at = event["available_at"]
        published_at = fact.get("first_published_at") or event.get("announced_at") or available_at
        if isinstance(published_at, str):
            published_at = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        assessment_at = available_at
        if isinstance(assessment_at, str):
            assessment_at = datetime.fromisoformat(assessment_at.replace("Z", "+00:00"))
        if assessment_at.tzinfo is None:
            assessment_at = assessment_at.replace(tzinfo=timezone.utc)
        freshness_at = assessment_at if event.get("novelty") == "update" else published_at
        age_days = max(0.0, (as_of - freshness_at.astimezone(timezone.utc)).total_seconds() / 86400)
        event_type = " ".join(str(event.get(name) or "") for name in ("event_type", "event_subtype")).casefold()
        if any(token in event_type for token in ("业绩", "财报", "利润", "营收", "earnings")):
            half_life_days = 30.0
        elif any(token in event_type for token in ("政策", "法规", "监管", "规划", "policy", "regulation")):
            half_life_days = 60.0
        else:
            half_life_days = 7.0
        expires_at = fact.get("valid_until")
        if expires_at:
            try:
                expires_day = date.fromisoformat(str(expires_at)[:10])
            except ValueError:
                expires_day = None
            if expires_day is not None and as_of.astimezone(_SHANGHAI).date() > expires_day:
                continue
        decay = 0.5 ** (age_days / half_life_days)
        fact_key = str(event.get("fact_key") or "")
        if not fact_key:
            continue
        for target in labels.get("targets") or []:
            scope = target.get("scope")
            if scope not in {"stock", "sector"}:
                # Market-wide information contributes to the market outlook/risk gate;
                # it is not cloned into every single-stock score.
                continue
            horizon = next((
                item for item in target.get("horizons") or []
                if int(item.get("trading_days") or 0) == 1
            ), None)
            if not horizon or horizon.get("direction") not in _DIRECTION_SIGN:
                continue
            relevance = 1.0 if scope == "stock" else 0.5
            contribution = (
                _DIRECTION_SIGN[horizon["direction"]]
                * float(horizon.get("strength") or 0)
                * float(horizon.get("confidence") or 0)
                * relevance
                * decay
            )
            affected = _target_tickers(
                target, universe=universe, industries=industries, sectors=sectors
            )
            specificity = 2 if scope == "stock" else 1
            for ticker in affected:
                key = (int(event["event_id"]), fact_key, ticker)
                driver = {
                    "assessment_id": str(event["assessment_id"]),
                    "event_id": int(event["event_id"]),
                    "fact_key": fact_key,
                    "title": event.get("event_title") or event["title"],
                    "stage": fact.get("stage", "unknown"),
                    "available_at": available_at,
                    "assessment_available_at": assessment_at,
                    "published_at": published_at,
                    "direction": horizon["direction"],
                    "strength": float(horizon.get("strength") or 0),
                    "confidence": float(horizon.get("confidence") or 0),
                    "relevance": relevance,
                    "half_life_days": int(half_life_days),
                    "age_days": round(age_days, 2),
                    "score": contribution,
                    "trading_days": 1,
                }
                current = atoms.get(key)
                if current is None or (specificity, abs(contribution)) > (current[0], abs(current[1]["score"])):
                    atoms[key] = (specificity, {"ticker": ticker, **driver})
    adjustments: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (_, _, ticker), (_, row) in atoms.items():
        adjustments[ticker].append(row)
    return adjustments


def _candidate_stock_research_context(
    candidates: list[dict[str, Any]],
    *,
    ticker: str,
    cutoff_at: datetime,
    market_as_of_trade_date: date,
    risk_gate: str,
    market_outlook: dict[str, Any],
    universe: set[str],
    industries: dict[str, set[str]],
    sectors: dict[str, set[str]],
) -> dict[str, Any]:
    """Freeze the candidate's own, sector, and market event evidence for stock agents."""
    matched: list[dict[str, Any]] = []
    for event in candidates:
        labels = event.get("labels") or {}
        relevant_targets = []
        for target in labels.get("targets") or []:
            scope = target.get("scope")
            if scope == "market":
                relevant_targets.append(target)
                continue
            if scope not in {"sector", "stock"}:
                continue
            if ticker in _target_tickers(
                target, universe=universe, industries=industries, sectors=sectors,
            ):
                relevant_targets.append(target)
        if not relevant_targets:
            continue
        evidence = [
            {
                key: row.get(key)
                for key in ("evidence_id", "quote", "source", "published_at")
                if row.get(key) is not None
            }
            for row in (event.get("evidence") or [])[:3]
            if isinstance(row, dict)
        ]
        matched.append({
            "event_id": int(event["event_id"]),
            "assessment_id": str(event["assessment_id"]),
            "fact_key": str(event["fact_key"]),
            "revision": int(event["revision"]),
            "title": event.get("event_title") or event.get("title"),
            "fact_summary": event.get("event_content") or event.get("content"),
            "event_type": event.get("event_type"),
            "stage": (labels.get("fact") or {}).get("stage", "unknown"),
            "first_published_at": (labels.get("fact") or {}).get("first_published_at"),
            "available_at": event.get("available_at"),
            "targets": relevant_targets,
            "evidence": evidence,
        })
    matched.sort(key=lambda item: str(item.get("available_at") or ""), reverse=True)
    outlook = market_outlook if isinstance(market_outlook, dict) else {}
    return {
        "schema_version": "daily_stock_research_context_v1",
        "cutoff_at": cutoff_at.isoformat(),
        "market_as_of_trade_date": market_as_of_trade_date.isoformat(),
        "risk_gate": risk_gate,
        "market_horizons": outlook.get("horizons") or {},
        "event_count": len(matched),
        "omitted_event_count": max(0, len(matched) - 30),
        "events": matched[:30],
    }


def _build_candidate_rows(
    signals: list[QuantExecutionSignal],
    *,
    strategies: dict[uuid.UUID, dict[str, Any]],
    event_adjustments: dict[str, list[dict[str, Any]]],
    limit: int = 500,
) -> tuple[list[dict[str, Any]], int]:
    percentiles = _rank_percentiles(signals)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for signal in signals:
        version_id = signal.strategy_version_id
        strategy = strategies.get(version_id, {"name": "未知策略", "version_no": None})
        grouped[signal.ts_code].append({
            "strategy_version_id": str(version_id) if version_id else None,
            "strategy_name": strategy["name"],
            "strategy_version_no": strategy["version_no"],
            "raw_score": float(signal.score or 0),
            "strategy_percentile": round(percentiles.get(int(signal.id), 50.0), 2),
            "reason": signal.reason,
            "signal_id": int(signal.id),
        })
    candidates: list[dict[str, Any]] = []
    for ticker, hits in grouped.items():
        # Different strategy hits are evidence, not extra votes. Use the highest
        # within-strategy percentile so duplicated strategies cannot inflate rank.
        quant_score = max(hit["strategy_percentile"] for hit in hits)
        events = sorted(event_adjustments.get(ticker, []), key=lambda row: abs(row["score"]), reverse=True)
        raw_event_score = sum(row["score"] for row in events)
        event_score = max(-1.0, min(1.0, raw_event_score)) * 20.0
        positive_score = max(0.0, sum(row["score"] for row in events if row["score"] > 0))
        negative_score = min(0.0, sum(row["score"] for row in events if row["score"] < 0))
        candidates.append({
            "ticker": ticker,
            "strategies": sorted(hits, key=lambda row: (-row["strategy_percentile"], row["strategy_name"])),
            "strategy_count": len(hits),
            "quant_score": round(quant_score, 2),
            "event_score": round(event_score, 2),
            "event_positive_score": round(positive_score * 20.0, 2),
            "event_negative_score": round(negative_score * 20.0, 2),
            "total_score": round(max(0.0, min(100.0, quant_score + event_score)), 2),
            "event_drivers": events[:5],
            "event_evidence_status": "available" if events else "insufficient",
        })
    candidates.sort(key=lambda row: (-row["total_score"], row["ticker"]))
    total = len(candidates)
    return candidates[:limit], total


def _reweight_candidate_rows(
    base_candidates: list[dict[str, Any]],
    *,
    event_adjustments: dict[str, list[dict[str, Any]]],
    limit: int = 1000,
) -> tuple[list[dict[str, Any]], int]:
    """Reapply event weights to a frozen strategy scan without rerunning strategies."""
    candidates: list[dict[str, Any]] = []
    for base in base_candidates:
        ticker = str(base.get("ticker") or "")
        if not ticker:
            continue
        events = sorted(
            event_adjustments.get(ticker, []),
            key=lambda row: abs(float(row.get("score") or 0)),
            reverse=True,
        )
        raw_event_score = sum(float(row.get("score") or 0) for row in events)
        event_score = max(-1.0, min(1.0, raw_event_score)) * 20.0
        positive_score = max(0.0, sum(
            float(row.get("score") or 0) for row in events if float(row.get("score") or 0) > 0
        ))
        negative_score = min(0.0, sum(
            float(row.get("score") or 0) for row in events if float(row.get("score") or 0) < 0
        ))
        quant_score = float(base.get("quant_score") or 0)
        candidate = {key: value for key, value in base.items() if key != "deep_research"}
        candidates.append({
            **candidate,
            "event_score": round(event_score, 2),
            "event_positive_score": round(positive_score * 20.0, 2),
            "event_negative_score": round(negative_score * 20.0, 2),
            "total_score": round(max(0.0, min(100.0, quant_score + event_score)), 2),
            "event_drivers": events[:5],
            "event_evidence_status": "available" if events else "insufficient",
        })
    candidates.sort(key=lambda row: (-row["total_score"], row["ticker"]))
    return candidates[:limit], len(candidates)


def _load_refresh_parent_report(session, workflow: dict[str, Any]) -> tuple[uuid.UUID, dict[str, Any]]:
    try:
        parent_id = uuid.UUID(str(workflow["refresh_parent_quant_task_id"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("量化新闻刷新任务缺少有效的原量化批次 ID") from exc
    report = session.scalar(
        select(AnalysisReport)
        .where(AnalysisReport.task_id == parent_id)
        .order_by(AnalysisReport.report_version.desc())
        .limit(1)
    )
    block = ((report.report_json or {}).get("daily_research") if report else None) or {}
    if not isinstance(block, dict) or block.get("kind") != "quant":
        session.rollback()
        raise ValueError("量化新闻刷新任务找不到原量化报告")
    session.rollback()
    return parent_id, block


def run_daily_quant(
    bundle,
    *,
    claimed: ClaimedTask,
    workflow: dict[str, Any],
    execution_control,
    market_dsn: str | None,
    on_progress: Callable[[str, int, int], None],
    quant_preflight: Callable[..., dict[str, Any]] | None = None,
    stock_research: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    persist_artifact: Callable[[dict, AnalysisArtifact], AnalysisArtifact] | None = None,
) -> dict[str, Any]:
    """Scan every current PUBLISHED strategy, add a frozen event score, then report only.

    This nightly path is deliberately scan-only: it creates no orders and touches no
    position lifecycle. Position planning remains a separate, portfolio-bound decision.
    """
    from AI.dataflows.utils.trading_calendar import is_trading_day
    from backend.modules.daily_research.application.news_pipeline import (
        _json_safe,
        dbapi_connection,
        ensure_event_schema,
    )
    from db.instrument.db import get_connection

    session = bundle.uow.session
    ensure_event_schema(session)
    is_news_refresh = workflow.get("slot") == "quant_news_refresh"
    refresh_parent_id = None
    refresh_parent_report: dict[str, Any] = {}
    if is_news_refresh:
        refresh_parent_id, refresh_parent_report = _load_refresh_parent_report(session, workflow)
    requested = claimed.effective_trade_date or datetime.now(_SHANGHAI).date()
    scheduled_trade_date = (
        date.fromisoformat(str(refresh_parent_report["requested_trade_date"])[:10])
        if is_news_refresh and refresh_parent_report.get("requested_trade_date")
        else requested
    )
    event_as_of_text = workflow.get("news_cutoff_at") or workflow.get("scheduled_at")
    scheduled_at_error = None
    if not is_news_refresh:
        wait_until = _workflow_instant(workflow, "wait_until")
        try:
            scheduled_at = _workflow_instant(workflow, "scheduled_at")
        except ValueError as exc:
            scheduled_at = None
            scheduled_at_error = str(exc)
    else:
        scheduled_at = (
            _workflow_instant(workflow, "scheduled_at") if workflow.get("scheduled_at") else None
        )
        wait_until_value = workflow.get("wait_until")
        wait_until = (
            datetime.fromisoformat(str(wait_until_value).replace("Z", "+00:00"))
            if wait_until_value else datetime.now(timezone.utc) + timedelta(hours=1)
        )
        if wait_until.tzinfo is None or wait_until.utcoffset() is None:
            raise ValueError("wait_until must include a timezone")
        wait_until = wait_until.astimezone(timezone.utc)
    if scheduled_at_error:
        event_cutoff_at = (
            _workflow_instant(workflow, "news_cutoff_at")
            if workflow.get("news_cutoff_at") else None
        )
    else:
        event_cutoff_at = (
            datetime.fromisoformat(str(event_as_of_text).replace("Z", "+00:00"))
            if event_as_of_text else datetime.now(timezone.utc)
        )
        if event_cutoff_at.tzinfo is None:
            raise ValueError("event snapshot cutoff must include a timezone")
        event_cutoff_at = event_cutoff_at.astimezone(timezone.utc)
    event_as_of = event_cutoff_at
    if scheduled_at_error:
        preflight = {
            "state": "unavailable", "error_code": "INVALID_SCHEDULED_AT",
            "suppress_target_trade_date": True,
        }
        news_dependency = {
            "status": "unavailable", "complete": False, "active": False,
            "reason": "INVALID_SCHEDULED_AT",
        }
        if datetime.now(timezone.utc) < wait_until:
            raise RetryableAnalysisError(
                "量化任务 scheduled_at 无效，等待有效任务元数据或截止时间",
                code="QUANT_INPUTS_NOT_READY",
            )
        return _finish_quant_without_scan(
            bundle, claimed, workflow=workflow,
            requested_trade_date=requested, scheduled_trade_date=scheduled_trade_date,
            event_as_of=event_as_of, event_cutoff_at=event_cutoff_at,
            news_dependency=news_dependency, preflight=preflight,
            reason="QUANT_METADATA_INVALID", persist_artifact=persist_artifact,
        )
    manual_local = None
    manual_uses_latest = False
    if workflow.get("trigger") == "manual":
        manual_local_value = scheduled_at or event_cutoff_at
        manual_local = manual_local_value.astimezone(_SHANGHAI)
        manual_uses_latest = _manual_uses_latest(manual_local, is_trading_day)
    local_date = requested.isoformat()
    scheduled_market_closed = (
        workflow.get("trigger") == "scheduled"
        and not is_trading_day(scheduled_trade_date.isoformat())
    )
    uses_latest_complete_market_data = _uses_latest_complete_market_data(
        trigger=str(workflow.get("trigger") or "scheduled"),
        requested_trade_date=scheduled_trade_date,
        manual_uses_latest=manual_uses_latest,
        is_trading_day_fn=is_trading_day,
    )

    # Read news readiness before the market preflight so deadline partials keep
    # the same event cutoff and dependency context as a completed quant report.
    # This is a read only: the independent news wait still happens after scanning.
    news_dependency = _scheduled_news_dependency(
        session, workflow=workflow, wait_until=wait_until,
    )
    event_as_of, event_cutoff_at = _event_snapshot_times(
        workflow=workflow, news_dependency=news_dependency,
    )
    news_market_outlook = news_dependency.get("market_outlook") or {}
    effective_risk_gate = (
        str(news_market_outlook.get("risk_gate") or "caution")
        if news_dependency.get("complete", True) else "block"
    )
    if effective_risk_gate not in {"normal", "caution", "block"}:
        effective_risk_gate = "caution"

    quant_input_preflight = None
    preflight_date = None
    if not is_news_refresh and quant_preflight is not None:
        mode = "retry" if workflow.get("trigger") == "manual" else "auto"
        try:
            quant_input_preflight = quant_preflight(
                at=scheduled_at, mode=mode,
                trigger="MANUAL_QUANT" if mode == "retry" else "SCHEDULED_QUANT",
            )
            if not isinstance(quant_input_preflight, dict):
                raise TypeError("quant preflight returned an invalid result")
        except Exception as exc:  # noqa: BLE001 - fail closed; never start a partial strategy scan
            logger.warning("量化行情补齐检查不可用：%s", type(exc).__name__)
            quant_input_preflight = {"state": "unavailable", "error_code": type(exc).__name__}
        preflight_ready = quant_input_preflight.get("state") == "ready"
        preflight_trade_date = quant_input_preflight.get("effective_trade_date")
        preflight_digest = quant_input_preflight.get("universe_digest")
        preflight_date = None
        if preflight_ready:
            try:
                preflight_date = date.fromisoformat(str(preflight_trade_date)[:10])
            except (TypeError, ValueError):
                preflight_date = None
        if preflight_ready and (preflight_date is None or not _is_sha256_digest(preflight_digest)):
            quant_input_preflight = {
                **quant_input_preflight, "state": "unavailable",
                "error_code": "INVALID_PREFLIGHT_CONTRACT",
            }
            preflight_ready = False
        if not preflight_ready:
            if datetime.now(timezone.utc) < wait_until:
                raise RetryableAnalysisError(
                    "量化行情输入仍在补齐或刷新服务暂不可用",
                    code="QUANT_INPUTS_NOT_READY",
                )
            return _finish_quant_without_scan(
                bundle, claimed, workflow=workflow,
                requested_trade_date=requested, scheduled_trade_date=scheduled_trade_date,
                event_as_of=event_as_of, event_cutoff_at=event_cutoff_at,
                news_dependency=news_dependency, preflight=quant_input_preflight,
                reason="QUANT_INPUTS_NOT_READY", persist_artifact=persist_artifact,
            )
        uses_latest_complete_market_data = _uses_latest_complete_market_data(
            trigger=str(workflow.get("trigger") or "scheduled"),
            requested_trade_date=scheduled_trade_date,
            manual_uses_latest=manual_uses_latest,
            is_trading_day_fn=is_trading_day,
            effective_trade_date=preflight_date,
        )
        requested = preflight_date
        local_date = requested.isoformat()
    daily_news: list[dict[str, Any]] = []
    event_ids: list[str] = []
    event_coverage: dict[str, Any] = {"complete": False, "candidate_total": 0}
    legacy_unassessed_count = 0

    with get_connection(market_dsn) as market_conn:
        # All strategies see one read-only market snapshot for the full batch.
        market_conn.rollback()
        market_conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        try:
            readiness = DataReadinessGate.resolve(market_conn, requested)
        except DataReadinessError as exc:
            readiness = None
            readiness_error = str(exc)
        else:
            readiness_error = None
        if is_news_refresh:
            parent_market_date = refresh_parent_report.get("market_as_of_trade_date")
            parent_market_as_of = (
                date.fromisoformat(str(parent_market_date)[:10]) if parent_market_date else None
            )
            market_as_of_trade_date, refresh_market_data_fallback = _refresh_market_snapshot_date(
                requested_trade_date=requested,
                readiness_trade_date=(readiness.market_as_of_trade_date if readiness else None),
                parent_market_date=parent_market_as_of,
            )
        else:
            market_as_of_trade_date = (
                readiness.market_as_of_trade_date if readiness is not None else None
            )
            refresh_market_data_fallback = False
        if uses_latest_complete_market_data and readiness is not None and not is_news_refresh:
            # Pre-close manual requests and scheduled holidays deliberately use the
            # latest complete common watermark, while preserving the requested day.
            requested = readiness.market_as_of_trade_date
            local_date = requested.isoformat()
        if (quant_input_preflight and quant_input_preflight.get("state") == "ready"
                and requested != preflight_date):
            if datetime.now(timezone.utc) < wait_until:
                market_conn.rollback()
                raise RetryableAnalysisError(
                    f"量化补齐目标日 {preflight_date} 与共同行情日 {requested} 不一致",
                    code="QUANT_INPUT_TARGET_MISMATCH",
                )
            market_conn.rollback()
            mismatch = {
                **quant_input_preflight, "state": "mismatch",
                "readiness_trade_date": readiness.market_as_of_trade_date.isoformat()
                if readiness else None,
            }
            return _finish_quant_without_scan(
                bundle, claimed, workflow=workflow,
                requested_trade_date=requested, scheduled_trade_date=scheduled_trade_date,
                event_as_of=event_as_of, event_cutoff_at=event_cutoff_at,
                news_dependency=news_dependency, preflight=mismatch,
                reason="QUANT_INPUT_TARGET_MISMATCH", persist_artifact=persist_artifact,
            )
        if is_news_refresh and refresh_market_data_fallback:
            # A refresh must preserve the existing strategy candidates even if the
            # readiness probe is temporarily unavailable; it only adds newer event
            # weights and never turns a market-data read failure into an empty scan.
            requested = market_as_of_trade_date
            local_date = requested.isoformat()
        elif readiness is None or readiness.market_as_of_trade_date != requested:
            if datetime.now(timezone.utc) < wait_until:
                market_conn.rollback()
                raise RetryableAnalysisError(
                    readiness_error or (
                        f"收盘行情未就绪：目标 {requested}，当前完整水位 "
                        f"{readiness.market_as_of_trade_date if readiness else 'unknown'}"
                    ),
                    code="QUANT_DATA_NOT_READY",
                )
            market_conn.rollback()
            report = {
                "schema_version": "daily_research_quant_v1", "kind": "quant", "status": "partial",
                "slot": workflow.get("slot"),
                "refresh_parent_quant_task_id": str(refresh_parent_id) if refresh_parent_id else None,
                "strategy_scan_reused": is_news_refresh,
                "reason": "QUANT_DATA_NOT_READY", "target_trade_date": local_date,
                "requested_trade_date": scheduled_trade_date.isoformat(),
                "market_session_status": "CLOSED" if scheduled_market_closed else "OPEN",
                "used_latest_complete_market_data": uses_latest_complete_market_data,
                "market_as_of_trade_date": market_as_of_trade_date.isoformat()
                if market_as_of_trade_date else None,
                "event_as_of": event_as_of.isoformat(), "event_assessment_ids": event_ids,
                "event_news_cutoff_at": event_cutoff_at.isoformat(),
                "news_research_dependency": news_dependency,
                "event_evidence_complete": bool(news_dependency.get("complete", True)),
                "risk_gate": effective_risk_gate,
                "market_outlook": news_market_outlook,
                "strategies": [], "candidates": [], "candidate_total": 0,
                "warnings": [readiness_error or "目标交易日的行情/因子/交易状态水位未完整"],
            }
            return _complete_quant(bundle, claimed, report, persist_artifact)

        universe_codes = AllMarketUniverseBuilder.list_active_cn_stocks(market_conn)
        universe = set(universe_codes)
        if quant_input_preflight and quant_input_preflight.get("state") == "ready":
            current_digest = _stock_universe_digest(universe_codes)
            if current_digest != quant_input_preflight.get("universe_digest"):
                if datetime.now(timezone.utc) < wait_until:
                    market_conn.rollback()
                    raise RetryableAnalysisError(
                        "量化扫描时的股票池与补齐任务冻结的股票池不一致",
                        code="QUANT_UNIVERSE_CHANGED",
                    )
                market_conn.rollback()
                mismatch = {
                    **quant_input_preflight, "state": "mismatch",
                    "current_universe_digest": current_digest,
                }
                return _finish_quant_without_scan(
                    bundle, claimed, workflow=workflow,
                    requested_trade_date=requested, scheduled_trade_date=scheduled_trade_date,
                    event_as_of=event_as_of, event_cutoff_at=event_cutoff_at,
                    news_dependency=news_dependency, preflight=mismatch,
                    reason="QUANT_UNIVERSE_CHANGED", persist_artifact=persist_artifact,
                )

        if is_news_refresh:
            strategy_snapshots = []
            raw_summaries = refresh_parent_report.get("strategies")
            strategy_summaries = raw_summaries if isinstance(raw_summaries, list) else []
            refresh_base_candidates = refresh_parent_report.get("candidates")
            if not isinstance(refresh_base_candidates, list):
                raise ValueError("原量化报告缺少可重排的候选列表")
        else:
            pairs = _published_strategies(session)
            strategy_snapshots = [
                (strategy, version, _strategy_snapshot(strategy, version))
                for strategy, version in pairs
            ]
            strategy_summaries = []
            refresh_base_candidates = []
        session.rollback()
        if not strategy_snapshots and not is_news_refresh:
            report = {
                "schema_version": "daily_research_quant_v1", "kind": "quant", "status": "empty",
                "slot": workflow.get("slot"),
                "refresh_parent_quant_task_id": str(refresh_parent_id) if refresh_parent_id else None,
                "strategy_scan_reused": is_news_refresh,
                "reason": "no_published_strategy", "target_trade_date": local_date,
                "requested_trade_date": scheduled_trade_date.isoformat(),
                "market_session_status": "CLOSED" if scheduled_market_closed else "OPEN",
                "used_latest_complete_market_data": uses_latest_complete_market_data,
                "market_as_of_trade_date": market_as_of_trade_date.isoformat(),
                "event_as_of": event_as_of.isoformat(), "event_assessment_ids": event_ids,
                "event_news_cutoff_at": event_cutoff_at.isoformat(),
                "news_research_dependency": news_dependency,
                "event_evidence_complete": bool(news_dependency.get("complete", True)),
                "risk_gate": effective_risk_gate,
                "market_outlook": news_market_outlook,
                "strategies": [], "candidates": [], "candidate_total": 0,
            }
            return _complete_quant(bundle, claimed, report, persist_artifact)

        if not is_news_refresh:
            strategy_summaries = []
        strategy_map: dict[uuid.UUID, dict[str, Any]] = {}
        for index, (strategy, version, snapshot) in enumerate(strategy_snapshots, start=1):
            strategy_map[version.id] = {
                "name": strategy.name,
                "version_no": version.version_no,
            }
            execution = QuantExecutionService(
                task_id=claimed.task_id,
                attempt_no=claimed.attempt_no,
                snapshot=snapshot,
                market_conn=market_conn,
                session=session,
                execution_control=execution_control,
                effective_trade_date=requested,
                on_progress=on_progress,
                scan_only=True,
            )
            summary = execution.run()
            strategy_summaries.append(summary)
            on_progress("量化策略扫描", index, len(strategy_snapshots))

        industries, sectors = _membership_maps(market_conn)
        scan_rows = list(session.scalars(
            select(QuantExecutionSignal).where(
                QuantExecutionSignal.task_id == claimed.task_id,
                QuantExecutionSignal.attempt_no == claimed.attempt_no,
                QuantExecutionSignal.signal_kind == "BUY",
                QuantExecutionSignal.error_code.is_(None),
            )
        ))
        # Materialize scan results and release both market and platform read
        # transactions before waiting on the independent 21:00 news run.
        scan_rows = [SimpleNamespace(
            id=int(row.id), strategy_version_id=row.strategy_version_id,
            score=row.score, ts_code=row.ts_code, reason=row.reason,
        ) for row in scan_rows]
        session.rollback()
        market_conn.rollback()
        market_conn.close()

        if workflow.get("trigger") == "scheduled" and workflow.get("slot") in {
            "quant_2100", "quant_news_refresh",
        }:
            on_progress("量化扫描完成，等待新闻判断归档", 0, 1)
            while news_dependency.get("active") and datetime.now(timezone.utc) < wait_until:
                execution_control.raise_if_inactive()
                time.sleep(5)
                news_dependency = _scheduled_news_dependency(
                    session, workflow=workflow, wait_until=wait_until,
                )
            on_progress("新闻依赖检查完成", 1, 1)
        event_as_of, event_cutoff_at = _event_snapshot_times(
            workflow=workflow, news_dependency=news_dependency,
        )
        news_market_outlook = news_dependency.get("market_outlook") or {}
        daily_news = news_dao.list_event_candidates(
            dbapi_connection(session), as_of=event_as_of,
            news_cutoff_at=event_cutoff_at, lookback_days=90, limit=1000,
        )
        total_events = int(daily_news[0].get("total_count") or 0) if daily_news else 0
        legacy_unassessed_count = news_dao.count_unassessed_legacy_events(
            dbapi_connection(session), as_of=event_cutoff_at, lookback_days=90,
        )
        session.rollback()
        event_ids = sorted({str(row["assessment_id"]) for row in daily_news})
        event_coverage = {
            "complete": total_events <= len(daily_news) and legacy_unassessed_count == 0,
            "candidate_total": total_events,
            "included_count": len(daily_news),
            "omitted_count": max(0, total_events - len(daily_news)),
            "legacy_unassessed_event_count": legacy_unassessed_count,
        }
        event_evidence_complete = bool(
            news_dependency.get("complete", True) and event_coverage["complete"]
        )
        effective_risk_gate = (
            str(news_market_outlook.get("risk_gate") or "caution")
            if event_evidence_complete else "block"
        )
        if effective_risk_gate not in {"normal", "caution", "block"}:
            effective_risk_gate = "caution"
        event_adjustments = _event_adjustments(
            daily_news,
            as_of=event_as_of,
            universe=universe,
            industries=industries,
            sectors=sectors,
        )
        if is_news_refresh:
            candidates, included_candidate_total = _reweight_candidate_rows(
                refresh_base_candidates,
                event_adjustments=event_adjustments,
                limit=1000,
            )
            candidate_total = int(refresh_parent_report.get("candidate_total") or included_candidate_total)
            parent_coverage = refresh_parent_report.get("candidate_coverage") or {}
            candidate_coverage_complete = (
                bool(parent_coverage.get("complete", True))
                and candidate_total <= len(candidates)
            )
        else:
            candidates, candidate_total = _build_candidate_rows(
                scan_rows, strategies=strategy_map, event_adjustments=event_adjustments,
                limit=1000,
            )
            candidate_coverage_complete = candidate_total <= len(candidates)
        deep_research_count = 0
        deep_research_failed_count = 0
        if stock_research is not None:
            selected_candidates = candidates[:10]
            for index, candidate in enumerate(selected_candidates, start=1):
                ticker = str(candidate["ticker"])
                context = _candidate_stock_research_context(
                    daily_news,
                    ticker=ticker,
                    cutoff_at=event_cutoff_at,
                    market_as_of_trade_date=market_as_of_trade_date,
                    risk_gate=effective_risk_gate,
                    market_outlook=news_market_outlook,
                    universe=universe,
                    industries=industries,
                    sectors=sectors,
                )
                on_progress("重点候选个股深研", index - 1, len(selected_candidates))
                try:
                    candidate["deep_research"] = _json_safe(stock_research(ticker, context))
                    deep_research_count += int(candidate["deep_research"].get("status") == "completed")
                except Exception as exc:
                    logger.exception("每日候选个股深研失败：%s", ticker)
                    candidate["deep_research"] = {
                        "status": "failed", "error": type(exc).__name__,
                    }
                    deep_research_failed_count += 1
                on_progress("重点候选个股深研", index, len(selected_candidates))
        failed = sum(int((row.get("summary") or {}).get("failed_count") or 0) for row in strategy_summaries)
        report = {
            "schema_version": "daily_research_quant_v1",
            "kind": "quant",
            "status": "partial" if (
                failed or deep_research_failed_count or not event_evidence_complete
                or refresh_market_data_fallback
                or not candidate_coverage_complete
            ) else "completed",
            "trigger": workflow.get("trigger", "scheduled"),
            "slot": workflow.get("slot"),
            "refresh_parent_quant_task_id": str(refresh_parent_id) if refresh_parent_id else None,
            "refresh_news_task_id": workflow.get("refresh_news_task_id"),
            "strategy_scan_reused": is_news_refresh,
            "target_trade_date": local_date,
            "requested_trade_date": scheduled_trade_date.isoformat(),
            "market_session_status": "CLOSED" if scheduled_market_closed else "OPEN",
            "used_latest_complete_market_data": uses_latest_complete_market_data,
            "market_as_of_trade_date": market_as_of_trade_date.isoformat(),
            "event_as_of": event_as_of.isoformat(),
            "event_news_cutoff_at": event_cutoff_at.isoformat(),
            "event_assessment_ids": event_ids,
            "news_research_dependency": news_dependency,
            "event_evidence_complete": event_evidence_complete,
            "event_coverage": event_coverage,
            "risk_gate": effective_risk_gate,
            "market_outlook": news_market_outlook,
            "strategy_count": int(
                refresh_parent_report.get("strategy_count", len(strategy_summaries))
                if is_news_refresh else len(strategy_summaries)
            ),
            "candidate_total": candidate_total,
            "candidate_count": len(candidates),
            "candidate_coverage": {
                "complete": candidate_coverage_complete,
                "included_count": len(candidates),
                "omitted_count": max(0, candidate_total - len(candidates)),
            },
            "deep_research_count": deep_research_count,
            "deep_research_failed_count": deep_research_failed_count,
            "strategies": strategy_summaries,
            "candidates": candidates,
            "warnings": ([f"扫描输入不完整，失败行 {failed}；排名仅供研究，未生成订单"] if failed else [])
                        + ([f"重点候选个股深研失败 {deep_research_failed_count} 只；原量化排序仍保留"] if deep_research_failed_count else [])
                        + ([f"目标日休市，使用最近完整行情日 {local_date} 重新扫描"] if scheduled_market_closed else [])
                        + ([f"量化刷新暂时无法重新读取行情水位，候选沿用原扫描日 {local_date}"]
                           if refresh_market_data_fallback else [])
                        + ([] if event_evidence_complete else [
                            (
                                f"新闻或事件证据不完整（新闻状态：{news_dependency.get('status')}，"
                                f"未结构化存量事件：{legacy_unassessed_count}）；风险门控封闭"
                            )
                        ])
                        + ([f"候选列表展示 {len(candidates)}/{candidate_total} 只，存在截断"]
                           if not candidate_coverage_complete else []),
            "position_planning": {
                "status": "out_of_scope",
                "reason": "一期交付候选排序与事件权重，不生成组合仓位或建议订单。",
            },
        }
        report = _json_safe(report)
    return _complete_quant(bundle, claimed, report, persist_artifact)


def _complete_quant(bundle, claimed, report: dict[str, Any], persist_artifact) -> dict[str, Any]:
    from backend.modules.daily_research.application.news_pipeline import _json_safe

    session = bundle.uow.session
    task = session.execute(
        select(AnalysisTask).where(AnalysisTask.id == claimed.task_id).with_for_update()
    ).scalar_one_or_none()
    if (task is None or task.status != TaskStatus.RUNNING.value
            or task.attempt_no != claimed.attempt_no or task.lease_token != claimed.lease_token):
        raise LeaseConflictError(f"量化任务 {claimed.task_id} 已失租，拒绝提交")
    actual_trade_date = report.get("target_trade_date")
    if actual_trade_date:
        task.effective_trade_date = date.fromisoformat(str(actual_trade_date)[:10])
        if task.requested_trade_date != task.effective_trade_date:
            task.date_correction = "使用最近完整共同行情水位"
    report = _json_safe(report)
    candidates = report.get("candidates") or []
    summary = (
        f"量化扫描 {report.get('strategy_count', len(report.get('strategies') or []))} 个策略，"
        f"筛选 {report.get('candidate_total', 0)} 只股票；状态：{report.get('status')}。"
    )
    artifact = AnalysisArtifact(
        report_json={"schema_version": "daily_research_v1", "daily_research": report},
        conclusion_summary=summary[:512],
        risk_flag=report.get("risk_gate") in {"caution", "block"},
        risk_hint=(
            "量化仅完成研究排序，缺少完整市场风险门控；不生成新增 BUY 建议"
            if report.get("risk_gate") in {"caution", "block"} else None
        ),
        decision={"candidate_count": report.get("candidate_total", 0),
                  "top_candidates": candidates[:20]},
        artifact_uri=None,
        checksum=None,
        duration_ms=None,
    )
    if persist_artifact is not None:
        artifact = persist_artifact(report, artifact)
    bundle.tasks.complete_task(
        claimed.task_id, claimed.attempt_no, claimed.lease_token, artifact, bundle.reports
    )
    return report
