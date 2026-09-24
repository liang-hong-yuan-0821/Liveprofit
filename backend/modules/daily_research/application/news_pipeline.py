"""Daily news capture, two-agent assessment and report snapshot assembly.

News rows are committed before model calls so raw evidence survives provider errors.
Assessment rows, the immutable report version and task completion are committed by the
worker's existing Unit of Work in one final transaction.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import select

from AI.eventStudy.review import news_dao
from AI.eventStudy.review.news_analysis import (
    EventAssessmentDraft,
    NewsAnalysisResult,
    build_news_analysis_agents,
)
from backend.modules.analysis.application.contracts import AnalysisArtifact, ClaimedTask
from backend.modules.analysis.application.errors import LeaseConflictError
from backend.modules.analysis.domain.enums import TaskStatus
from backend.modules.analysis.infrastructure.models import AnalysisTask

logger = logging.getLogger(__name__)
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_DEFAULT_NEWS_LIMIT = 100
_LOOKBACK_DAYS = 90
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = False
_NEWS_ANALYSIS_LOCK_KEY = "liveprofit.daily_research.news_analysis"


@dataclass(frozen=True)
class PreparedNewsResult:
    news: dict[str, Any]
    result: NewsAnalysisResult
    candidate_revisions: dict[tuple[int, str], int]
    candidate_first_published_at: dict[tuple[int, str], Any]
    candidate_recall_complete: bool = True
    assessment_embeddings: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class PreparedNewsRun:
    task_id: str
    cutoff_at: datetime
    captured: dict[str, Any]
    results: tuple[PreparedNewsResult, ...]
    event_candidates: tuple[dict[str, Any], ...]
    pending_at_start: int
    capture_after_cutoff: bool
    model_version: str | None
    prompt_version: str | None


def dbapi_connection(session):
    """Return the psycopg connection enlisted in the caller's SQLAlchemy transaction."""
    proxied = session.connection().connection
    driver = getattr(proxied, "driver_connection", None)
    if driver is None or not hasattr(driver, "execute"):
        raise RuntimeError("每日研究需要 SQLAlchemy psycopg driver connection")
    return driver


def _configured_identity(session) -> tuple[str, int, str, str]:
    """Fail closed if the legacy event-study DSN differs from the platform DB."""
    from sqlalchemy.engine import make_url
    from psycopg.conninfo import conninfo_to_dict
    from AI.eventStudy.collectors.config import pg_dsn

    platform_url = make_url(str(session.get_bind().url))
    event_db = conninfo_to_dict(pg_dsn())

    def host(value: Any) -> str:
        text = str(value or "localhost").strip().lower()
        return "127.0.0.1" if text in {"localhost", "::1"} else text

    platform = (
        host(platform_url.host),
        int(platform_url.port or 5432),
        str(platform_url.database or ""),
        str(platform_url.username or ""),
    )
    legacy = (
        host(event_db.get("host")),
        int(event_db.get("port") or 5432),
        str(event_db.get("dbname") or event_db.get("database") or ""),
        str(event_db.get("user") or ""),
    )
    if platform != legacy:
        raise RuntimeError(
            "每日新闻流程已停用：平台库与事件研究 PG 配置不一致；"
            "请先将两者指向同一 PostgreSQL 数据库"
        )
    return platform


def ensure_event_schema(session) -> None:
    """Upgrade the shared event-study schema once per process before DAO use.

    The schema belongs to the existing event-study database bootstrap. Run it on a
    separate psycopg connection so its commit cannot invalidate the worker's SQLAlchemy
    transaction; a PostgreSQL session advisory lock fences concurrent first startup.
    """
    global _SCHEMA_READY
    _configured_identity(session)
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        from AI.eventStudy.db.connection import get_connection, init_schema

        conn = get_connection()
        lock_key = "liveprofit.daily_research.event_schema"
        locked = False
        try:
            conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (lock_key,))
            locked = True
            if not init_schema(conn):
                raise RuntimeError("每日新闻表结构初始化失败")
            _SCHEMA_READY = True
        finally:
            if locked:
                try:
                    conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (lock_key,))
                    conn.commit()
                except Exception:
                    logger.exception("释放每日新闻 schema 初始化锁失败")
                    conn.rollback()
            conn.close()


class NewsAnalysisAdvisoryLock:
    """Session advisory lock held across model work to fence overlapping news batches."""

    def __init__(self, connection, key: str) -> None:
        self._connection = connection
        self._key = key
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            self._connection.execute(
                "SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (self._key,)
            )
            self._connection.commit()
        except Exception:
            logger.warning("释放每日新闻分析锁失败，连接关闭后 PostgreSQL 会自动释放", exc_info=True)
            try:
                self._connection.rollback()
            except Exception:
                pass
        finally:
            self._connection.close()


def acquire_news_analysis_lock(session) -> NewsAnalysisAdvisoryLock:
    """Acquire a cross-process lock so scheduled/manual/interval passes do not race."""
    _configured_identity(session)
    from AI.eventStudy.db.connection import get_connection
    from backend.modules.analysis.application.errors import RetryableAnalysisError

    conn = get_connection()
    key = _NEWS_ANALYSIS_LOCK_KEY
    try:
        acquired = conn.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", (key,)
        ).fetchone()[0]
    except Exception:
        conn.rollback()
        conn.close()
        raise
    if not acquired:
        conn.rollback()
        conn.close()
        raise RetryableAnalysisError(
            "另一条每日新闻分析正在处理同一来源队列，稍后重试",
            code="DAILY_NEWS_NOT_READY",
        )
    return NewsAnalysisAdvisoryLock(conn, key)


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _cutoff(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("news_cutoff_at 必须带时区")
    return value.astimezone(timezone.utc)


def capture_news(session, *, known_by: datetime | None = None) -> dict[str, Any]:
    """Fetch one source batch and durably persist its raw versions in the platform DB."""
    ensure_event_schema(session)
    from AI.eventStudy.collectors.event_crawler import fetch_source_batch

    batch = fetch_source_batch()
    conn = dbapi_connection(session)
    inserted = 0
    late_discovered = 0
    try:
        for item in batch.get("items") or []:
            was_inserted = bool(news_dao.persist_news(conn, item).inserted)
            inserted += int(was_inserted)
            if was_inserted and known_by is not None and _published_by_cutoff(
                item.get("published_at"), known_by,
            ):
                late_discovered += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    return _json_safe({
        key: value for key, value in batch.items() if key != "items"
    } | {
        "item_count": len(batch.get("items") or []),
        "inserted_count": inserted,
        "late_discovered_count": late_discovered,
    })


def _published_by_cutoff(value: Any, cutoff: datetime) -> bool:
    """Unknown publication times are coverage gaps in a strict scheduled snapshot."""
    if value is None:
        return True
    try:
        published = _cutoff(value) if isinstance(value, (str, datetime)) else None
    except (TypeError, ValueError):
        return True
    return published is None or published <= cutoff


def prepare_news_run(
    session,
    *,
    task_id: str,
    cutoff_at: datetime | str | None,
    on_progress: Callable[[str, int, int], None] | None = None,
    max_news: int = _DEFAULT_NEWS_LIMIT,
    agent_factory: Callable[[], Any] = build_news_analysis_agents,
    capture: bool = True,
) -> PreparedNewsRun:
    """Capture and analyze eligible news without keeping a database transaction open."""
    ensure_event_schema(session)
    scheduled_cutoff = _cutoff(cutoff_at) if cutoff_at is not None else None
    batch = capture_news(session, known_by=scheduled_cutoff) if capture else {
        "status": "not_requested", "items": [], "sources": [], "fetched_at": None,
        "coverage": {"enabled": 0, "succeeded": 0, "failed": 0},
        "late_discovered_count": 0,
    }
    # Manual runs freeze the news set immediately after their collector snapshot. Scheduled
    # runs keep the exact 09:00/21:00 cutoff supplied by the Dispatcher.
    cutoff = scheduled_cutoff or datetime.now(timezone.utc)
    capture_after_cutoff = int(batch.get("late_discovered_count") or 0) > 0
    conn = dbapi_connection(session)
    try:
        pending_count = news_dao.count_unprocessed_news(conn, as_of=cutoff)
        eligible = news_dao.list_unprocessed_news(conn, as_of=cutoff, limit=max_news)
        event_candidates = news_dao.list_event_candidates(
            conn, as_of=cutoff, news_cutoff_at=cutoff,
            lookback_days=_LOOKBACK_DAYS, limit=1000,
        )
    finally:
        # End the read transaction before any model call.
        session.rollback()
    selected = eligible
    if not selected:
        return PreparedNewsRun(
            str(task_id), cutoff, batch, (), tuple(event_candidates), pending_count,
            capture_after_cutoff, None, None
        )

    agents = agent_factory()
    prepared: list[PreparedNewsResult] = []
    for index, news in enumerate(selected, start=1):
        analysis_as_of = datetime.now(timezone.utc)
        conn = dbapi_connection(session)
        try:
            candidates = news_dao.list_event_candidates(
                conn,
                as_of=analysis_as_of,
                news_cutoff_at=cutoff,
                lookback_days=_LOOKBACK_DAYS,
                limit=1000,
                include_disputed=True,
            )
        finally:
            session.rollback()
        candidate_total = int(candidates[0].get("total_count") or 0) if candidates else 0
        query_embedding = None
        if candidate_total > 50:
            query_embedding, _ = _encode_event_vector(
                f"{news.get('title') or ''}\n{str(news.get('raw_content') or '')[:12000]}"
            )
            if query_embedding is not None:
                conn = dbapi_connection(session)
                try:
                    candidates = news_dao.list_event_candidates(
                        conn, as_of=analysis_as_of, news_cutoff_at=cutoff,
                        lookback_days=_LOOKBACK_DAYS, limit=1000,
                        include_disputed=True, query_embedding=query_embedding,
                    )
                finally:
                    session.rollback()
        vector_recall_available = bool(query_embedding) and bool(candidates) and all(
            bool(row.get("has_embedding")) for row in candidates
        )
        candidate_recall_complete = (
            candidate_total <= 50
            or (vector_recall_available and candidate_total <= len(candidates))
        )
        result = agents.analyze(
            news_id=str(news["news_id"]),
            news={
                "title": news["title"],
                "raw_content": news["raw_content"],
                "source": news.get("source_label") or news.get("source"),
                "published_at": news.get("published_at"),
            },
            candidates=candidates,
            candidate_recall_complete=candidate_recall_complete,
        )
        assessment_embeddings: dict[str, dict[str, Any]] = {}
        for draft in result.assessments:
            vector_text = " ".join((
                draft.identity.get("entity", ""), draft.identity.get("action", ""),
                draft.identity.get("reference_period", ""), draft.title, draft.fact_summary,
            ))
            vector, vector_model = _encode_event_vector(vector_text)
            if vector is not None and vector_model is not None:
                assessment_embeddings[str(draft.fact_key)] = {
                    "embedding": vector, "model": vector_model,
                    "text_hash": hashlib.sha256(vector_text.encode("utf-8")).hexdigest(),
                }
        candidate_revisions = {
            (int(row["event_id"]), str(row["fact_key"])): int(row["revision"])
            for row in candidates
        }
        candidate_first_published_at = {
            (int(row["event_id"]), str(row["fact_key"])): (
                ((row.get("labels") or {}).get("fact") or {}).get("first_published_at")
                or row.get("announced_at")
            )
            for row in candidates
        }
        prepared.append(PreparedNewsResult(
            news=news, result=result, candidate_revisions=candidate_revisions,
            candidate_first_published_at=candidate_first_published_at,
            candidate_recall_complete=candidate_recall_complete,
            assessment_embeddings=assessment_embeddings,
        ))
        if on_progress is not None:
            on_progress("新闻判新与双 Agent 打标", index, len(selected))

    return PreparedNewsRun(
        str(task_id), cutoff, batch, tuple(prepared), tuple(event_candidates), pending_count,
        capture_after_cutoff, getattr(agents, "model_version", None),
        getattr(agents, "prompt_version", None),
    )


def build_market_research_context(prepared: PreparedNewsRun) -> dict[str, Any]:
    """Freeze accepted market/sector facts for the daily analyst subgraphs."""
    events: dict[tuple[str, str], dict[str, Any]] = {}

    def add_event(*, key: tuple[str, str], event: dict[str, Any]) -> None:
        targets = event.get("targets") or []
        market_targets = [
            target for target in targets
            if isinstance(target, dict) and target.get("scope") == "market"
        ]
        sector_targets = [
            target for target in targets
            if isinstance(target, dict) and target.get("scope") == "sector"
        ]
        if not market_targets and not sector_targets:
            return
        events[key] = {
            **event,
            "market_target": bool(market_targets),
            "sector_targets": sector_targets,
            "market_targets": market_targets,
        }

    for row in prepared.event_candidates:
        labels = row.get("labels") or {}
        fact = labels.get("fact") or {}
        assessment_id = str(row.get("assessment_id") or "")
        event_id = str(row.get("event_id") or "")
        fact_key = str(row.get("fact_key") or "")
        if not event_id or not fact_key:
            continue
        add_event(
            key=(event_id, fact_key),
            event={
                "event_id": int(row["event_id"]),
                "assessment_id": assessment_id,
                "fact_key": fact_key,
                "revision": int(row.get("revision") or 1),
                "title": row.get("event_title") or row.get("title"),
                "fact_summary": row.get("event_content") or row.get("content"),
                "event_type": row.get("event_type"),
                "fact": fact,
                "targets": labels.get("targets") or [],
                "evidence": _compact_evidence(row.get("evidence") or []),
                "available_at": row.get("available_at"),
            },
        )

    for result in prepared.results:
        for draft in result.result.assessments:
            if draft.review_status != "accepted":
                continue
            labels = _draft_labels(draft, debate_rounds=result.result.debate_rounds)
            event_id = str(draft.event_id) if draft.event_id is not None else "new"
            add_event(
                key=(event_id, str(draft.fact_key or draft.canonical_key or "")),
                event={
                    "event_id": draft.event_id,
                    "assessment_id": None,
                    "fact_key": draft.fact_key,
                    "revision": None,
                    "title": draft.title,
                    "fact_summary": draft.fact_summary,
                    "event_type": draft.event_type,
                    "fact": labels.get("fact") or {},
                    "targets": labels.get("targets") or [],
                    "evidence": _compact_evidence(list(draft.evidence)),
                    "available_at": None,
                },
            )

    ordered = sorted(
        events.values(),
        key=lambda item: (str(item.get("fact", {}).get("first_published_at") or ""),
                          int(item.get("event_id") or 0)),
        reverse=True,
    )
    limit = 200
    return _json_safe({
        "cutoff_at": prepared.cutoff_at.isoformat(),
        "event_count": len(ordered),
        "omitted_event_count": max(0, len(ordered) - limit),
        "events": ordered[:limit],
    })


def _compact_evidence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact = []
    for row in rows[:3]:
        if not isinstance(row, dict):
            continue
        compact.append({
            key: row.get(key) for key in ("evidence_id", "quote", "source", "published_at")
            if row.get(key) is not None
        })
    return compact


def market_trade_date_for_cutoff(cutoff_at: datetime) -> date:
    """Use the latest complete A-share session available at the report cutoff."""
    from AI.dataflows.utils.trading_calendar import TradingCalendar

    local = cutoff_at.astimezone(_SHANGHAI)
    calendar = TradingCalendar()
    reference = local.date()
    if local.time() < time(15, 30):
        reference -= timedelta(days=1)
    return date.fromisoformat(calendar.get_last_trading_day(reference.isoformat()))


def _validate_draft_scope(conn, draft: EventAssessmentDraft) -> str | None:
    from AI.eventStudy.review.review_dao import (
        EventScopeValidationError, RouteExistence, resolve_scope_fields,
    )

    try:
        resolve_scope_fields(
            {"event_scope": draft.event_scope,
             "affected_scope_refs": list(draft.affected_scope_refs)},
            RouteExistence(conn),
        )
    except EventScopeValidationError as exc:
        return str(exc)
    return None


def _draft_labels(draft: EventAssessmentDraft, *, debate_rounds: int) -> dict[str, Any]:
    labels = dict(draft.labels)
    labels["review"] = {
        "debate_rounds": debate_rounds,
        "unresolved_reasons": list(draft.unresolved_reasons),
    }
    return labels


def _assessment_labels(
    draft: EventAssessmentDraft, *, debate_rounds: int, news: dict[str, Any],
    event_id: int, first_published_at: Any = None,
) -> dict[str, Any]:
    """Freeze fact identity and source-time metadata on the immutable assessment row."""
    labels = _draft_labels(draft, debate_rounds=debate_rounds)
    fact = labels.setdefault("fact", {})
    fact.update({
        "identity": dict(draft.identity),
        "title": draft.title,
        "fact_summary": draft.fact_summary,
        "canonical_key": draft.canonical_key,
        "event_id": int(event_id),
        "source_news_id": str(news["news_id"]),
        "source_url": news.get("source_url"),
        "first_seen_at": news.get("first_seen_at"),
        "first_published_at": first_published_at or news.get("published_at") or news.get("first_seen_at"),
    })
    return labels


def _encode_event_vector(text: str) -> tuple[list[float] | None, str | None]:
    """Best-effort bge-m3 vectorization; structured labels remain usable on failure."""
    try:
        from AI.eventStudy.processing.event_vectorizer import (
            VECTOR_DIM, VECTOR_MODEL, encode_text,
        )

        vector = encode_text(text)
        if len(vector) != VECTOR_DIM:
            return None, None
        return [float(value) for value in vector], str(VECTOR_MODEL)
    except Exception:  # noqa: BLE001 - vector retrieval is an optional recall aid
        logger.warning("每日事件向量化不可用，将使用结构化/词面候选召回", exc_info=True)
        return None, None


def _expected_assessment_revision(
    working_revisions: dict[tuple[int, str], int],
    candidate_revisions: dict[tuple[int, str], int],
    *,
    event_id: int,
    fact_key: str,
) -> int:
    """Return the version expected after earlier facts in this same commit."""
    key = (int(event_id), str(fact_key))
    if key not in working_revisions:
        working_revisions[key] = int(candidate_revisions.get(key, 0))
    return working_revisions[key]


def _forecast_dates(as_of: datetime) -> dict[int, dict[str, str | None]]:
    """Resolve 1/5/20 actual trading dates; report the calendar fallback source."""
    try:
        from AI.dataflows.utils.trading_calendar import TradingCalendar

        calendar = TradingCalendar()
        local_as_of = as_of.astimezone(_SHANGHAI)
        first = local_as_of.date()
        if local_as_of.time() >= time(9, 15):
            first += timedelta(days=1)
        dates: list[str] = []
        cursor = first
        while len(dates) < 20 and cursor <= first + timedelta(days=120):
            day = cursor.isoformat()
            if calendar.is_trading_day(day):
                dates.append(day)
            cursor += timedelta(days=1)
        if len(dates) < 20:
            return {h: {"start": dates[0] if dates else None, "end": None,
                        "calendar_source": calendar.data_source}
                    for h in (1, 5, 20)}
        return {
            horizon: {"start": dates[0], "end": dates[horizon - 1],
                      "calendar_source": calendar.data_source}
            for horizon in (1, 5, 20)
        }
    except Exception:
        logger.warning("未来预测交易日范围暂不可用", exc_info=True)
        return {h: {"start": None, "end": None, "calendar_source": "unavailable"}
                for h in (1, 5, 20)}


def _event_half_life_days(fact: dict[str, Any]) -> int:
    kind = " ".join(str(fact.get(key) or "") for key in ("event_type", "event_subtype")).casefold()
    if any(token in kind for token in ("业绩", "财报", "利润", "营收", "earnings")):
        return 30
    if any(token in kind for token in ("政策", "法规", "监管", "规划", "policy", "regulation")):
        return 60
    return 7


def _confidence_grade(value: float) -> str:
    if value >= 0.8:
        return "high"
    if value >= 0.6:
        return "medium"
    return "low"


def _as_day(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _event_reaction_index(announced_at: datetime, sessions: list[date]) -> int | None:
    """Map a known publication time to its first affected trading session."""
    local = announced_at.astimezone(_SHANGHAI)
    before_open = local.time() < time(9, 30)
    for index, session in enumerate(sessions):
        if session > local.date() or (before_open and session == local.date()):
            return index
    return None


def _validate_price_horizons(
    *,
    announced_at: datetime,
    sessions: list[date],
    target_prices: dict[date, list[float | None]],
    benchmark_prices: dict[date, float],
    as_of_trade_date: date,
    predicted_horizons: list[dict[str, Any]],
    expected_target_count: int = 1,
    direction_basis: str = "abnormal_return",
    benchmark_ticker: str = "000300.SH",
    minimum_coverage: float = 0.7,
) -> list[dict[str, Any]]:
    """Compare realized cumulative return with its benchmark on fixed session windows.

    Stock/sector targets are equally weighted across explicitly named constituents.
    A horizon with missing target observations remains data_insufficient; it never
    silently shortens the planned window.
    """
    event_index = _event_reaction_index(announced_at, sessions)
    predictions = {int(row.get("trading_days") or 0): row for row in predicted_horizons}
    output: list[dict[str, Any]] = []
    for horizon in (1, 5, 20):
        prediction = predictions.get(horizon) or {}
        base_index = (event_index - 1) if event_index is not None else None
        end_index = (event_index + horizon - 1) if event_index is not None else None
        target_day = sessions[end_index] if end_index is not None and end_index < len(sessions) else None
        if event_index is None or base_index is None:
            validation = {
                "status": "awaiting_event_session" if event_index is None else "data_insufficient",
                "reason": "事件影响窗口尚无完整的基准交易日与起始交易日",
            }
        elif target_day is None or target_day > as_of_trade_date:
            validation = {
                "status": "awaiting_window",
                "start_date": sessions[event_index].isoformat(),
                "end_date": target_day.isoformat() if target_day else None,
                "as_of_trade_date": as_of_trade_date.isoformat(),
            }
        else:
            base_day = sessions[base_index]
            base_rows = target_prices.get(base_day, [])
            end_rows = target_prices.get(target_day, [])
            target_count = max(expected_target_count, 1)
            paired = [
                (base, end) for base, end in zip(base_rows, end_rows)
                if base is not None and end is not None and base > 0
            ]
            coverage = len(paired) / target_count
            benchmark_base = benchmark_prices.get(base_day)
            benchmark_end = benchmark_prices.get(target_day)
            if (not base_rows or not end_rows or coverage < minimum_coverage
                    or benchmark_base is None or benchmark_end is None
                    or benchmark_base <= 0):
                validation = {
                    "status": "data_insufficient",
                    "start_date": sessions[event_index].isoformat(),
                    "end_date": target_day.isoformat(),
                    "coverage": round(coverage, 4),
                    "reason": "目标或基准行情缺失，未缩短窗口代替验证",
                }
            else:
                target_return = sum(end / base - 1 for base, end in paired) / len(paired)
                benchmark_return = benchmark_end / benchmark_base - 1
                abnormal_return = target_return - benchmark_return
                measured = target_return if direction_basis == "target_return" else abnormal_return
                actual_direction = (
                    "bullish" if measured > 0.005 else
                    "bearish" if measured < -0.005 else "neutral"
                )
                predicted_direction = str(prediction.get("direction") or "unknown")
                validation = {
                    "status": "validated",
                    "start_date": sessions[event_index].isoformat(),
                    "end_date": target_day.isoformat(),
                    "baseline_date": base_day.isoformat(),
                    "as_of_trade_date": as_of_trade_date.isoformat(),
                    "coverage": round(coverage, 4),
                    "target_return": round(target_return, 6),
                    "benchmark_ticker": benchmark_ticker,
                    "benchmark_return": round(benchmark_return, 6),
                    "abnormal_return": round(abnormal_return, 6),
                    "direction_basis": direction_basis,
                    "actual_direction": actual_direction,
                    "prediction_match": (
                        predicted_direction == actual_direction
                        if predicted_direction in {"bullish", "bearish", "neutral"} else None
                    ),
                }
        output.append({"trading_days": horizon, **validation})
    return output


def _attach_market_outcomes(
    forecasts: list[dict[str, Any]], *, market_conn, as_of_trade_date: date,
) -> None:
    """Attach read-only, cutoff-bounded price outcomes to persisted report snapshots."""
    stock_codes: set[str] = set()
    for event in forecasts:
        for target in event.get("targets") or []:
            if target.get("scope") == "stock":
                stock_codes.update(
                    str(ref).removeprefix("stock:")
                    for ref in target.get("scope_refs") or []
                    if str(ref).startswith("stock:")
                )
    requested_codes = stock_codes | {"000001.SH", "000300.SH"}
    if not requested_codes:
        return
    announced_days = [
        _as_day(event.get("first_published_at") or event.get("announced_at") or event.get("available_at"))
        for event in forecasts if event.get("first_published_at") or event.get("announced_at") or event.get("available_at")
    ]
    if not announced_days:
        return
    start_day = min(announced_days) - timedelta(days=14)
    rows = market_conn.execute(
        "SELECT d.ts_code, d.trade_date, d.close, "
        "COALESCE(a.adj_factor, CASE WHEN i.instrument_type = 'index' THEN 1.0 END) "
        "FROM market.instrument_daily d "
        "JOIN market.instrument i ON i.ts_code = d.ts_code "
        "LEFT JOIN market.adj_factor a ON a.ts_code = d.ts_code AND a.trade_date = d.trade_date "
        "WHERE d.ts_code = ANY(%s) AND d.trade_date BETWEEN %s AND %s "
        "ORDER BY d.trade_date, d.ts_code",
        (sorted(requested_codes), start_day, as_of_trade_date),
    ).fetchall()
    prices: dict[str, dict[date, float]] = defaultdict(dict)
    for ticker, trade_day, close, factor in rows:
        if close is None or factor is None:
            continue
        adjusted = float(close) * float(factor)
        if adjusted > 0:
            prices[str(ticker)][_as_day(trade_day)] = adjusted
    benchmark_ticker = "000300.SH"
    benchmark_prices = prices.get(benchmark_ticker, {})
    sessions = sorted(benchmark_prices)
    if not sessions:
        for event in forecasts:
            for target in event.get("targets") or []:
                target["outcome_status"] = "data_insufficient"
                target["outcome_reason"] = "截止日之前没有可用的市场基准日线"
            event["status"] = "data_insufficient"
        return

    for event in forecasts:
        announced_at = event.get("first_published_at") or event.get("announced_at") or event.get("available_at")
        if isinstance(announced_at, str):
            announced_at = datetime.fromisoformat(announced_at.replace("Z", "+00:00"))
        if announced_at.tzinfo is None:
            announced_at = announced_at.replace(tzinfo=timezone.utc)
        all_statuses: list[str] = []
        for target in event.get("targets") or []:
            scope = target.get("scope")
            refs = [str(ref) for ref in target.get("scope_refs") or []]
            if scope == "stock":
                codes = [ref.removeprefix("stock:") for ref in refs if ref.startswith("stock:")]
            elif scope == "market":
                codes = ["000001.SH"] if "000001.SH" in prices else [benchmark_ticker]
            else:
                # Historical sector membership is not available in the current schema;
                # current constituents would introduce survivorship/look-ahead bias.
                target["outcome_status"] = "data_insufficient"
                target["outcome_reason"] = "缺少事件时点的历史板块成分，跳过避免幸存者偏差"
                all_statuses.append("data_insufficient")
                continue
            target_price_rows = [prices.get(code, {}) for code in codes]
            target["outcome_codes"] = len(codes)
            if any(not row for row in target_price_rows) or not codes:
                target["outcome_status"] = "data_insufficient"
                target["outcome_reason"] = "目标标的缺少完整历史日线或复权因子"
                all_statuses.append("data_insufficient")
                continue
            aligned: dict[date, list[float | None]] = {}
            for session_day in sessions:
                aligned[session_day] = [
                    ticker_prices.get(session_day)
                    for ticker_prices in target_price_rows
                ]
            predicted = [
                item for item in target.get("horizons", []) if isinstance(item, dict)
            ]
            validations = _validate_price_horizons(
                announced_at=announced_at,
                sessions=sessions,
                target_prices=aligned,
                benchmark_prices=benchmark_prices,
                as_of_trade_date=as_of_trade_date,
                predicted_horizons=predicted,
                expected_target_count=len(codes),
                direction_basis="target_return" if scope == "market" else "abnormal_return",
                benchmark_ticker=benchmark_ticker,
            )
            target["horizons"] = [
                {**prediction, "validation": validation}
                for prediction, validation in zip(predicted, validations)
            ]
            statuses = [row["status"] for row in validations]
            target["outcome_status"] = (
                "validated" if statuses and all(status == "validated" for status in statuses)
                else "partial" if any(status == "validated" for status in statuses)
                else "awaiting_window" if statuses and all(status == "awaiting_window" for status in statuses)
                else "data_insufficient"
            )
            all_statuses.append(target["outcome_status"])
        event["outcome_as_of_trade_date"] = as_of_trade_date.isoformat()
        event["status"] = (
            "validated" if all_statuses and all(status == "validated" for status in all_statuses)
            else "partial" if any(status in {"validated", "partial"} for status in all_statuses)
            else "awaiting_window" if all_statuses and all(status == "awaiting_window" for status in all_statuses)
            else "data_insufficient"
        )


def _build_forecasts(
    conn, *, as_of: datetime, cutoff_at: datetime,
    market_conn=None, market_as_of_trade_date: date | None = None,
) -> tuple[list[dict], dict]:
    candidates = news_dao.list_event_candidates(
        conn, as_of=as_of, news_cutoff_at=cutoff_at,
        lookback_days=_LOOKBACK_DAYS, limit=1000,
    )
    candidate_total = int(candidates[0].get("total_count") or 0) if candidates else 0
    legacy_unassessed_count = news_dao.count_unassessed_legacy_events(
        conn, as_of=cutoff_at, lookback_days=_LOOKBACK_DAYS,
    )
    forecasts: list[dict[str, Any]] = []
    contributions: dict[int, list[dict[str, Any]]] = {1: [], 5: [], 20: []}
    for row in candidates:
        labels = row.get("labels") or {}
        fact = labels.get("fact") or {}
        targets = labels.get("targets") or []
        forecast = {
            "assessment_id": str(row["assessment_id"]),
            "event_id": int(row["event_id"]),
            "fact_key": row["fact_key"],
            "revision": int(row["revision"]),
            "title": row.get("event_title") or row["title"],
            "announced_at": row["announced_at"],
            "first_published_at": fact.get("first_published_at") or row["announced_at"],
            "available_at": row["available_at"],
            "stage": fact.get("stage", "unknown"),
            "expected_value": fact.get("expected_value"),
            "actual_value": fact.get("actual_value"),
            "previous_value": fact.get("previous_value"),
            "valid_until": fact.get("valid_until"),
            "validity_status": "active",
            "targets": targets,
            "evidence": row.get("evidence") or [],
            "status": "pending_validation",
        }
        forecasts.append(_json_safe(forecast))
        public_at = fact.get("first_published_at") or row.get("announced_at") or row["available_at"]
        if isinstance(public_at, str):
            public_at = datetime.fromisoformat(public_at.replace("Z", "+00:00"))
        if public_at.tzinfo is None:
            public_at = public_at.replace(tzinfo=timezone.utc)
        assessment_at = row["available_at"]
        if isinstance(assessment_at, str):
            assessment_at = datetime.fromisoformat(assessment_at.replace("Z", "+00:00"))
        if assessment_at.tzinfo is None:
            assessment_at = assessment_at.replace(tzinfo=timezone.utc)
        freshness_at = assessment_at if row.get("novelty") == "update" else public_at
        age_days = max(0.0, (as_of - freshness_at.astimezone(timezone.utc)).total_seconds() / 86400)
        valid_until = fact.get("valid_until")
        if valid_until:
            try:
                if as_of.astimezone(_SHANGHAI).date() > date.fromisoformat(str(valid_until)[:10]):
                    forecasts[-1]["validity_status"] = "expired"
                    continue
            except ValueError:
                pass  # malformed historical metadata is displayed but never treated as an expiry
        half_life_days = _event_half_life_days(fact)
        recency_weight = 0.5 ** (age_days / half_life_days)
        for target in targets:
            if target.get("scope") != "market" or target.get("target") != "market:CN":
                continue
            for horizon in target.get("horizons") or []:
                trading_days = int(horizon.get("trading_days") or 0)
                if trading_days not in contributions:
                    continue
                direction = horizon.get("direction", "unknown")
                if direction not in {"bullish", "bearish", "neutral", "mixed"}:
                    continue
                strength = float(horizon.get("strength") or 0)
                confidence = float(horizon.get("confidence") or 0)
                weight = strength * confidence * recency_weight
                if weight <= 0:
                    continue
                contributions[trading_days].append({
                    "event_id": int(row["event_id"]),
                    "assessment_id": str(row["assessment_id"]),
                    "title": row.get("event_title") or row["title"],
                    "direction": direction,
                    "weight": weight,
                    "confidence": confidence,
                    "confidence_weight": strength * recency_weight,
                    "reason": horizon.get("reason"),
                    "invalidations": horizon.get("invalidations") or [],
                    "evidence_refs": horizon.get("evidence_ids") or [],
                    "published_at": public_at,
                    "assessment_available_at": assessment_at,
                    "age_days": round(age_days, 2),
                    "half_life_days": half_life_days,
                })

    if market_conn is not None and market_as_of_trade_date is not None:
        try:
            _attach_market_outcomes(
                forecasts, market_conn=market_conn,
                as_of_trade_date=market_as_of_trade_date,
            )
        except Exception:
            logger.warning("事件价格兑现验证不可用，保留预测并标记 coverage gap", exc_info=True)
            for forecast in forecasts:
                forecast["status"] = "data_insufficient"
                for target in forecast.get("targets") or []:
                    target["outcome_status"] = "data_insufficient"
                    target["outcome_reason"] = "实际行情快照读取失败"
    else:
        for forecast in forecasts:
            forecast["status"] = "data_insufficient"
            forecast["outcome_as_of_trade_date"] = None
            for target in forecast.get("targets") or []:
                target["outcome_status"] = "data_insufficient"
                target["outcome_reason"] = "本次报告未能读取行情兑现快照"
    dates = _forecast_dates(as_of)
    outlook: dict[str, Any] = {"as_of": as_of.isoformat(), "horizons": {}}
    outlook["event_coverage"] = {
        "complete": candidate_total <= len(candidates) and legacy_unassessed_count == 0,
        "assessed_event_count": candidate_total,
        "included_count": len(candidates),
        "omitted_count": max(0, candidate_total - len(candidates)),
        "legacy_unassessed_event_count": legacy_unassessed_count,
    }
    for horizon, rows in contributions.items():
        bullish = sum(x["weight"] * (0.5 if x["direction"] == "mixed" else 1.0)
                      for x in rows if x["direction"] in {"bullish", "mixed"})
        bearish = sum(x["weight"] * (0.5 if x["direction"] == "mixed" else 1.0)
                      for x in rows if x["direction"] in {"bearish", "mixed"})
        if not rows:
            direction = "insufficient"
        elif bullish > bearish * 1.25:
            direction = "bullish"
        elif bearish > bullish * 1.25:
            direction = "bearish"
        elif bullish > 0 and bearish > 0:
            direction = "mixed"
        else:
            direction = "neutral"
        confidence = (
            sum(x["confidence"] * x["confidence_weight"] for x in rows) /
            sum(x["confidence_weight"] for x in rows)
            if rows and sum(x["confidence_weight"] for x in rows) else 0.0
        )
        drivers = sorted(rows, key=lambda x: x["weight"], reverse=True)[:3]
        invalidations = list(dict.fromkeys(
            condition for driver in drivers for condition in driver.get("invalidations", [])
        ))[:5]
        outlook["horizons"][str(horizon)] = {
            "trading_days": horizon,
            "direction": direction,
            "confidence": _confidence_grade(confidence),
            "evidence_confidence_score": round(confidence, 4),
            "bullish_weight": round(bullish, 4),
            "bearish_weight": round(bearish, 4),
            "date_range": dates[horizon],
            "drivers": drivers,
            "invalidations": invalidations,
            "evidence_status": "available" if rows else "insufficient",
            "event_count": len({(row["event_id"], row["assessment_id"]) for row in rows}),
        }
    outlook["risk_gate"] = "caution"
    outlook["risk_gate_reason"] = (
        "仅有新闻事件标签；缺少同一时点的市场技术状态和市场数据质量输入，"
        "因此不放行新增 BUY 风险预算。"
    )
    return forecasts, _json_safe(outlook)


def commit_news_run(
    bundle,
    *,
    prepared: PreparedNewsRun,
    claimed: ClaimedTask,
    market_agent_state: dict[str, Any] | None = None,
    market_agent_error: str | None = None,
    market_trade_date: date | None = None,
    market_conn=None,
    market_outcome_error: str | None = None,
    persist_artifact: Callable[[dict, AnalysisArtifact], AnalysisArtifact] | None = None,
) -> dict[str, Any]:
    """Persist accepted/disputed facts and finish the task/report atomically."""
    session = bundle.uow.session
    _configured_identity(session)
    task = session.execute(
        select(AnalysisTask).where(AnalysisTask.id == claimed.task_id).with_for_update()
    ).scalar_one_or_none()
    if (task is None or task.status != TaskStatus.RUNNING.value
            or task.attempt_no != claimed.attempt_no or task.lease_token != claimed.lease_token):
        raise LeaseConflictError(f"任务 {claimed.task_id} 已失租，拒绝提交每日新闻判断")

    conn = dbapi_connection(session)
    result_rows: list[dict[str, Any]] = []
    working_revisions: dict[tuple[int, str], int] = {}
    any_disputed = False
    for prepared_result in prepared.results:
        news = prepared_result.news
        result = prepared_result.result
        assessment_refs: list[str] = []
        event_refs: list[int] = []
        updated_drafts: list[EventAssessmentDraft] = []
        row_conflict_reasons: list[str] = []
        for original in result.assessments:
            draft = original
            reason = _validate_draft_scope(conn, draft)
            if reason and draft.review_status == "accepted":
                draft = replace(
                    draft, review_status="disputed",
                    unresolved_reasons=tuple((*draft.unresolved_reasons, f"目标校验失败：{reason}")),
                )
            announced_at = news.get("published_at") or news.get("first_seen_at")
            event_id, _, current_status, _ = news_dao.get_or_create_event(
                conn,
                canonical_key=draft.canonical_key or "",
                title=draft.title,
                content=draft.fact_summary,
                announced_at=announced_at,
                source_url=news.get("source_url"),
                event_type=draft.event_type,
                event_subtype=draft.event_subtype,
                event_condition=draft.event_condition,
                importance=draft.importance,
                event_scope=draft.event_scope,
                affected_scope_refs=list(draft.affected_scope_refs),
                first_seen_at=news.get("first_seen_at"),
                status="approved" if draft.review_status == "accepted" else "pending",
            )
            if current_status == "ignored" and draft.review_status == "accepted":
                draft = replace(
                    draft, review_status="disputed",
                    unresolved_reasons=tuple((*draft.unresolved_reasons,
                                               "该事实身份已由人工忽略，自动研判不能重新启用")),
                )
            revision_key = (int(event_id), str(draft.fact_key))
            expected_revision = _expected_assessment_revision(
                working_revisions,
                prepared_result.candidate_revisions,
                event_id=event_id,
                fact_key=draft.fact_key or "",
            )
            fact_first_published_at = (
                prepared_result.candidate_first_published_at.get(
                    (int(draft.event_id), str(draft.fact_key))
                ) if draft.event_id is not None else None
            ) or news.get("published_at") or news.get("first_seen_at")
            assessment_labels = _assessment_labels(
                draft, debate_rounds=result.debate_rounds, news=news,
                event_id=event_id, first_published_at=fact_first_published_at,
            )
            embedding_snapshot = prepared_result.assessment_embeddings.get(str(draft.fact_key)) or {}
            try:
                assessment = news_dao.append_assessment(
                    conn,
                    news_id=news["news_id"],
                    event_id=event_id,
                    fact_key=draft.fact_key or "",
                    novelty=draft.novelty,
                    review_status=draft.review_status,
                    labels=assessment_labels,
                    evidence=[{
                        **evidence,
                        "news_id": str(news["news_id"]),
                        "source": news.get("source_label") or news.get("source"),
                        "source_url": news.get("source_url"),
                        "published_at": news.get("published_at"),
                    } for evidence in draft.evidence],
                    model_version=prepared.model_version or "unknown",
                    prompt_version=prepared.prompt_version or "unknown",
                    operation_id=f"{prepared.task_id}:{news['news_id']}",
                    task_id=prepared.task_id,
                    expected_revision=expected_revision,
                    embedding=embedding_snapshot.get("embedding"),
                    embedding_model=embedding_snapshot.get("model"),
                    embedding_text_hash=embedding_snapshot.get("text_hash"),
                )
            except news_dao.AssessmentRevisionConflict as exc:
                # Preserve the stale conclusion as disputed audit evidence at the new
                # revision. It never updates the approved events projection.
                any_disputed = True
                conflict_draft = replace(
                    draft, review_status="disputed",
                    unresolved_reasons=tuple((*draft.unresolved_reasons, str(exc))),
                )
                conflict_labels = _assessment_labels(
                    conflict_draft, debate_rounds=result.debate_rounds, news=news,
                    event_id=event_id, first_published_at=fact_first_published_at,
                )
                conflict_labels.setdefault("review", {})["stale_revision_conflict"] = True
                conflict_assessment = news_dao.append_assessment(
                    conn,
                    news_id=news["news_id"],
                    event_id=event_id,
                    fact_key=draft.fact_key or "",
                    novelty=draft.novelty,
                    review_status="disputed",
                    labels=conflict_labels,
                    evidence=[{
                        **evidence,
                        "news_id": str(news["news_id"]),
                        "source": news.get("source_label") or news.get("source"),
                        "source_url": news.get("source_url"),
                        "published_at": news.get("published_at"),
                    } for evidence in draft.evidence],
                    model_version=prepared.model_version or "unknown",
                    prompt_version=prepared.prompt_version or "unknown",
                    operation_id=f"{prepared.task_id}:{news['news_id']}:stale",
                    task_id=prepared.task_id,
                    embedding=embedding_snapshot.get("embedding"),
                    embedding_model=embedding_snapshot.get("model"),
                    embedding_text_hash=embedding_snapshot.get("text_hash"),
                )
                working_revisions[revision_key] = conflict_assessment.revision
                assessment_refs.append(str(conflict_assessment.assessment_id))
                event_refs.append(event_id)
                updated_drafts.append(conflict_draft)
                row_conflict_reasons.append(str(exc))
                continue
            working_revisions[revision_key] = assessment.revision
            if draft.review_status == "accepted":
                news_dao.update_event_projection(
                    conn,
                    event_id=event_id,
                    title=draft.title,
                    content=draft.fact_summary,
                    event_type=draft.event_type,
                    event_subtype=draft.event_subtype,
                    event_condition=draft.event_condition,
                    importance=draft.importance,
                    source_url=news.get("source_url"),
                    event_scope=draft.event_scope,
                    affected_scope_refs=list(draft.affected_scope_refs),
                    expected_value=draft.expected_value,
                    actual_value=draft.actual_value,
                    previous_value=draft.previous_value,
                    status=draft.review_status,
                    embedding=embedding_snapshot.get("embedding"),
                )
            assessment_refs.append(str(assessment.assessment_id))
            event_refs.append(event_id)
            updated_drafts.append(draft)
            if draft.review_status != "accepted":
                any_disputed = True

        if any(draft.review_status == "disputed" for draft in updated_drafts) and result.status == "accepted":
            result = replace(
                result,
                status="disputed",
                assessments=tuple(updated_drafts),
                unresolved_reasons=tuple((*result.unresolved_reasons, *row_conflict_reasons)),
            )
        elif row_conflict_reasons:
            result = replace(
                result,
                unresolved_reasons=tuple((*result.unresolved_reasons, *row_conflict_reasons)),
            )
        row_status = result.status
        result_rows.append({
            "news_id": str(news["news_id"]),
            "source": news.get("source_label") or news.get("source"),
            "source_item_id": news.get("source_item_id"),
            "source_revision": news.get("source_revision"),
            "title": news.get("title"),
            "source_url": news.get("source_url"),
            "published_at": news.get("published_at"),
            "status": row_status,
            "novelty": result.novelty,
            "debate_rounds": result.debate_rounds,
            "llm_calls": result.llm_calls,
            "event_ids": sorted(set(event_refs)),
            "assessment_ids": assessment_refs,
            "unresolved_reasons": list(result.unresolved_reasons),
            "skipped_facts": list(result.skipped_facts),
            "candidate_recall_complete": prepared_result.candidate_recall_complete,
        })

    report_as_of = datetime.now(timezone.utc)
    forecasts, market_outlook = _build_forecasts(
        conn, as_of=report_as_of, cutoff_at=prepared.cutoff_at,
        market_conn=market_conn, market_as_of_trade_date=market_trade_date,
    )
    if market_trade_date is not None:
        task.effective_trade_date = market_trade_date
    if market_agent_state is not None:
        from AI.dataflows import market_features as mf

        state = market_agent_state if isinstance(market_agent_state, dict) else {}
        gate = str(state.get("risk_gate") or "caution")
        decision = mf.risk_gate_decision(
            state.get("global_risk_assessment"),
            state.get("market_regime"),
            state.get("market_data_quality"),
        )
        market_outlook["risk_gate"] = gate if gate in {"normal", "caution", "block"} else "caution"
        market_outlook["risk_gate_reason"] = decision["reason"]
        market_outlook["risk_gate_rule"] = decision["rule"]
        market_outlook["market_agents"] = _market_agent_snapshot(
            state, market_trade_date=market_trade_date,
        )
    else:
        market_outlook["risk_gate"] = "block" if market_agent_error else "caution"
        market_outlook["risk_gate_reason"] = (
            market_agent_error or "市场与板块 Agent 未运行，未形成同一时点的行情风险判断"
        )
        market_outlook["market_agents"] = {
            "status": "failed" if market_agent_error else "unavailable",
            "error": market_agent_error,
            "market_as_of_trade_date": market_trade_date.isoformat() if market_trade_date else None,
        }
    terminal_news = sum(
        row["status"] in {"accepted", "skipped"}
        or (row["status"] == "disputed" and bool(row.get("assessment_ids")))
        for row in result_rows
    )
    pending = max(0, prepared.pending_at_start - terminal_news)
    coverage = prepared.captured.get("coverage") or {}
    capture_status = str(prepared.captured.get("status") or "unknown")
    event_coverage = market_outlook.get("event_coverage") or {}
    if (capture_status in {"failed", "partial"} or prepared.capture_after_cutoff
            or pending or any_disputed or market_agent_error
            or not event_coverage.get("complete", False)
            or any(row["status"] == "failed" for row in result_rows)):
        run_status = "partial"
    else:
        run_status = "completed"
    report = {
        "schema_version": "daily_research_news_v1",
        "kind": "news",
        "status": run_status,
        "task_id": str(claimed.task_id),
        "news_cutoff_at": prepared.cutoff_at.isoformat(),
        "report_as_of": report_as_of.isoformat(),
        "captured_at": prepared.captured.get("fetched_at"),
        "source_status": capture_status,
        "capture_after_cutoff": prepared.capture_after_cutoff,
        "late_discovered_news_count": int(prepared.captured.get("late_discovered_count") or 0),
        "source_coverage": coverage,
        "sources": prepared.captured.get("sources") or [],
        "news_count": len(result_rows),
        "pending_news_count": pending,
        "news_results": result_rows,
        "event_forecast": forecasts,
        "event_coverage": event_coverage,
        "warnings": ([
            f"近{_LOOKBACK_DAYS}天结构化事件 {event_coverage.get('included_count', 0)}/"
            f"{event_coverage.get('assessed_event_count', 0)} 条纳入；遗漏 {event_coverage.get('omitted_count', 0)} 条，"
            f"另有 {event_coverage.get('legacy_unassessed_event_count', 0)} 条存量事件未结构化回标"
        ] if not event_coverage.get("complete", False) else []),
        "outcome_validation_status": _outcome_validation_status(
            forecasts, market_conn=market_conn, market_trade_date=market_trade_date,
        ),
        "outcome_validation_error": market_outcome_error,
        "market_outlook": market_outlook,
        "market_as_of_trade_date": market_trade_date.isoformat() if market_trade_date else None,
        "model_version": prepared.model_version,
        "prompt_version": prepared.prompt_version,
    }
    report = _json_safe(report)
    summary = _summary(report)
    artifact = AnalysisArtifact(
        report_json={"schema_version": "daily_research_v1", "daily_research": report},
        conclusion_summary=summary[:512],
        risk_flag=market_outlook.get("risk_gate") in {"caution", "block"},
        risk_hint=market_outlook.get("risk_gate_reason"),
        decision={"market_outlook": market_outlook},
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


def _outcome_validation_status(
    forecasts: list[dict[str, Any]], *, market_conn, market_trade_date: date | None,
) -> str:
    if not forecasts:
        return "no_events"
    if market_conn is None or market_trade_date is None:
        return "unavailable"
    statuses = [str(row.get("status") or "") for row in forecasts]
    if any(status in {"validated", "partial", "awaiting_window"} for status in statuses):
        return "available"
    return "data_insufficient"


def _market_agent_snapshot(state: dict[str, Any], *, market_trade_date: date | None) -> dict[str, Any]:
    """Select the established market/sector agent outputs for the frozen daily report."""
    return _json_safe({
        "status": "completed",
        "market_as_of_trade_date": market_trade_date.isoformat() if market_trade_date else None,
        "risk_gate": state.get("risk_gate"),
        "market_regime": state.get("market_regime"),
        "market_event_calendar": state.get("market_event_calendar"),
        "market_data_quality": state.get("market_data_quality"),
        "reports": {
            "cn_news": state.get("cn_news_report"),
            "cn_tech": state.get("cn_tech_report"),
            "sector_news": state.get("sector_news_report"),
            "sector_tech": state.get("sector_tech_report"),
            "sector_rotation": state.get("rotation_prediction_report"),
        },
        "sector_shortlist": state.get("sector_shortlist_structured") or [],
        "sector_events": state.get("sector_events") or [],
    })


def _summary(report: dict[str, Any]) -> str:
    next_day = report["market_outlook"]["horizons"]["1"]["direction"]
    return (f"新闻研判 {report['news_count']} 条，待处理 {report['pending_news_count']} 条；"
            f"下一交易日事件方向：{next_day}；运行状态：{report['status']}。")
