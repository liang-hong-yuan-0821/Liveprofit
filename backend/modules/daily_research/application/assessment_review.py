"""Human review and immutable revision creation for disputed event assessments."""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import date, datetime, timezone
from typing import Any

from AI.eventStudy.review import news_dao
from AI.eventStudy.review.review_dao import (
    EventScopeValidationError,
    RouteExistence,
    normalize_scope_ref,
    resolve_scope_fields,
)
from backend.modules.analysis.application.errors import IdempotencyKeyReusedError
from backend.modules.analysis.infrastructure.models import AnalysisTask
from backend.modules.daily_research.application.news_pipeline import (
    dbapi_connection,
    ensure_event_schema,
)
from backend.modules.daily_research.application.scheduler import create_manual_run

_ALLOWED_STAGES = {"rumor", "published", "pending", "implemented", "cancelled", "unknown"}
_ALLOWED_DIRECTIONS = {"bullish", "bearish", "neutral", "mixed", "unknown"}
_ALLOWED_CONDITIONS = {"超预期", "符合预期", "低于预期", "不适用", "未知"}
_REQUIRED_HORIZONS = {1, 5, 20}


class AssessmentReviewNotFound(LookupError):
    pass


class AssessmentReviewConflict(RuntimeError):
    pass


def _finite_number(value: Any, *, field: str, low: float | None = None,
                   high: float | None = None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} 必须是有限数值")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是有限数值") from exc
    if not math.isfinite(number) or (low is not None and number < low) or (high is not None and number > high):
        raise ValueError(f"{field} 超出有效范围")
    return number


def validate_assessment_labels(
    labels: dict[str, Any], *, evidence: list[dict[str, Any]], conn,
) -> dict[str, Any]:
    """Validate editable labels against the existing event-study contract."""
    if not isinstance(labels, dict):
        raise ValueError("标签必须是 JSON 对象")
    normalized = deepcopy(labels)
    fact = normalized.get("fact")
    targets = normalized.get("targets")
    if not isinstance(fact, dict) or not isinstance(targets, list) or not 1 <= len(targets) <= 20:
        raise ValueError("标签必须包含 fact 对象和 1–20 个 targets")
    if fact.get("stage") not in _ALLOWED_STAGES:
        raise ValueError("事实阶段枚举非法")
    fact["event_type"] = str(fact.get("event_type") or "").strip()
    if not fact["event_type"] or len(fact["event_type"]) > 128:
        raise ValueError("事件类型不能为空且不能超过 128 字符")
    fact["event_subtype"] = str(fact.get("event_subtype") or "").strip() or None
    condition = fact.get("event_condition") or "未知"
    if condition not in _ALLOWED_CONDITIONS:
        raise ValueError("事件预期条件枚举非法")
    fact["event_condition"] = condition
    importance = fact.get("importance", 3)
    if isinstance(importance, bool) or not str(importance).isdigit() or not 1 <= int(importance) <= 5:
        raise ValueError("importance 必须是 1–5 整数")
    fact["importance"] = int(importance)
    for field in ("expected_value", "actual_value", "previous_value"):
        fact[field] = _finite_number(fact.get(field), field=field)
    valid_until = fact.get("valid_until")
    if valid_until:
        try:
            fact["valid_until"] = date.fromisoformat(str(valid_until)[:10]).isoformat()
        except ValueError as exc:
            raise ValueError("valid_until 必须是 YYYY-MM-DD 日期") from exc
    else:
        fact["valid_until"] = None

    allowed_evidence = {
        str(row.get("evidence_id")) for row in evidence
        if isinstance(row, dict) and row.get("evidence_id")
    }
    route_scopes: set[str] = set()
    existence = RouteExistence(conn)
    for target in targets:
        if not isinstance(target, dict):
            raise ValueError("targets 中的对象结构非法")
        raw_scope = str(target.get("scope") or "")
        raw_refs = target.get("scope_refs") or []
        try:
            scope, refs = resolve_scope_fields(
                {"event_scope": raw_scope, "affected_scope_refs": raw_refs}, existence
            )
        except EventScopeValidationError as exc:
            raise ValueError(str(exc)) from exc
        route_scopes.add(scope)
        raw_target = str(target.get("target") or "").strip()
        if scope == "market":
            if raw_target != "market:CN" or refs:
                raise ValueError("market 目标必须是 market:CN 且不能带引用")
        else:
            canonical_target = normalize_scope_ref(raw_target)
            if canonical_target is None or refs != [canonical_target]:
                raise ValueError("每个 sector/stock 目标必须对应一个合法且已存在的 scope_ref")
        target["scope"] = scope
        target["scope_refs"] = refs
        horizon_rows = target.get("horizons")
        if not isinstance(horizon_rows, list) or len(horizon_rows) != len(_REQUIRED_HORIZONS):
            raise ValueError("每个目标都必须包含 1/5/20 交易日预测")
        horizons: dict[int, dict[str, Any]] = {}
        for horizon in horizon_rows:
            if not isinstance(horizon, dict):
                raise ValueError("期限标签结构非法")
            try:
                raw_days = horizon.get("trading_days")
                if isinstance(raw_days, bool) or (
                    isinstance(raw_days, float) and not raw_days.is_integer()
                ):
                    raise ValueError
                trading_days = int(raw_days)
            except (TypeError, ValueError) as exc:
                raise ValueError("trading_days 必须是 1、5 或 20") from exc
            if trading_days not in _REQUIRED_HORIZONS or trading_days in horizons:
                raise ValueError("每个目标必须且只能包含 1、5、20 交易日")
            direction = horizon.get("direction")
            if direction not in _ALLOWED_DIRECTIONS:
                raise ValueError("期限方向枚举非法")
            horizon["strength"] = _finite_number(horizon.get("strength"), field="strength", low=0, high=1)
            horizon["confidence"] = _finite_number(horizon.get("confidence"), field="confidence", low=0, high=1)
            if horizon["strength"] is None or horizon["confidence"] is None:
                raise ValueError("期限预测必须填写 strength 和 confidence")
            refs_used = horizon.get("evidence_ids")
            if not isinstance(refs_used, list) or not set(map(str, refs_used)) <= allowed_evidence:
                raise ValueError("期限预测引用了不存在的原文证据")
            if direction != "unknown" and not refs_used:
                raise ValueError("方向性预测必须引用原文证据")
            reason = str(horizon.get("reason") or "").strip()
            if not reason or len(reason) > 2000:
                raise ValueError("期限预测理由不能为空且不能超过 2000 字符")
            horizon["reason"] = reason
            invalidations = horizon.get("invalidations") or []
            if not isinstance(invalidations, list) or len(invalidations) > 5:
                raise ValueError("失效条件必须是 0–5 条数组")
            horizon["invalidations"] = [str(value).strip() for value in invalidations if str(value).strip()]
            horizons[trading_days] = horizon
        target["horizons"] = [horizons[days] for days in sorted(horizons)]
    if len(route_scopes) != 1:
        raise ValueError("同一事实的影响对象不能跨 market/sector/stock 作用域")
    return normalized


def _operation_id(assessment_id: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(f"{assessment_id}:{idempotency_key}".encode("utf-8")).hexdigest()
    return f"human-review:{digest}"


def _request_hash(*, review_status: str, labels: dict[str, Any] | None, review_note: str) -> str:
    payload = json.dumps(
        {"review_status": review_status, "labels": labels, "review_note": review_note.strip()},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _outcome(assessment: dict[str, Any], task: AnalysisTask | None, *, replay: bool) -> dict[str, Any]:
    return {
        "assessment_id": str(assessment["assessment_id"]),
        "event_id": int(assessment["event_id"]),
        "fact_key": str(assessment["fact_key"]),
        "revision": int(assessment["revision"]),
        "review_status": assessment["review_status"],
        "task_id": str(task.id) if task is not None else None,
        "task_status": task.status if task is not None else "SUCCEEDED",
        "idempotent_replay": replay,
    }


def review_disputed_assessment(
    session,
    *,
    assessment_id: str,
    expected_revision: int,
    review_status: str,
    labels: dict[str, Any] | None,
    review_note: str,
    idempotency_key: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append a human revision and enqueue a news run in the same transaction."""
    if review_status not in {"accepted", "rejected", "retracted"}:
        raise ValueError("review_status 仅支持 accepted/rejected/retracted")
    note = str(review_note or "").strip()
    if not note:
        raise ValueError("review_note 不能为空")
    if review_status != "accepted" and labels is not None:
        raise ValueError("只有 accepted 更正可以提交 labels")
    ensure_event_schema(session)
    conn = dbapi_connection(session)
    selected = news_dao.get_assessment_for_review(conn, assessment_id)
    if selected is None:
        raise AssessmentReviewNotFound(f"事件判断不存在：{assessment_id}")

    operation_id = _operation_id(assessment_id, idempotency_key)
    request_hash = _request_hash(review_status=review_status, labels=labels, review_note=note)
    replay = news_dao.get_assessment_by_operation(
        conn, operation_id=operation_id,
        event_id=int(selected["event_id"]), fact_key=str(selected["fact_key"]),
    )
    if replay is not None:
        prior_review = (replay.get("labels") or {}).get("review") or {}
        human = prior_review.get("human") or {}
        if human.get("request_hash") != request_hash:
            raise IdempotencyKeyReusedError("同一 Idempotency-Key 已用于不同的事件判断更正")
        task = session.get(AnalysisTask, replay.get("task_id")) if replay.get("task_id") else None
        session.rollback()
        return _outcome(replay, task, replay=True)

    latest = news_dao.get_latest_assessment_for_fact(
        conn,
        event_id=int(selected["event_id"]),
        fact_key=str(selected["fact_key"]),
        for_update=True,
    )
    if (latest is None or str(latest["assessment_id"]) != str(assessment_id)
            or int(latest["revision"]) != int(expected_revision)
            or latest["review_status"] != "disputed"):
        raise AssessmentReviewConflict("该事件判断已被其他操作更新，请刷新后再复核")

    corrected_labels = labels if labels is not None else (selected.get("labels") or {})
    if review_status == "accepted":
        corrected_labels = validate_assessment_labels(
            corrected_labels, evidence=selected.get("evidence") or [], conn=conn,
        )
    else:
        corrected_labels = deepcopy(corrected_labels)
    previous_fact = (selected.get("labels") or {}).get("fact") or {}
    corrected_fact = corrected_labels.setdefault("fact", {})
    for snapshot_field in (
        "identity", "title", "fact_summary", "canonical_key", "event_id",
        "source_news_id", "source_url", "first_seen_at", "first_published_at",
    ):
        if snapshot_field not in corrected_fact and snapshot_field in previous_fact:
            corrected_fact[snapshot_field] = previous_fact[snapshot_field]
    review_block = corrected_labels.setdefault("review", {})
    if not isinstance(review_block, dict):
        raise ValueError("labels.review 必须是 JSON 对象")
    review_block["human"] = {
        "status": review_status,
        "note": note,
        "reviewed_at": (now or datetime.now(timezone.utc)).isoformat(),
        "supersedes_assessment_id": str(assessment_id),
        "request_hash": request_hash,
    }
    assessment = news_dao.append_assessment(
        conn,
        news_id=selected["news_id"],
        event_id=int(selected["event_id"]),
        fact_key=str(selected["fact_key"]),
        novelty=selected["novelty"],
        review_status=review_status,
        labels=corrected_labels,
        evidence=selected.get("evidence") or [],
        model_version="human-review",
        prompt_version="daily-research-review-v1",
        operation_id=operation_id,
        expected_revision=int(expected_revision),
        available_at=now or datetime.now(timezone.utc),
    )
    if review_status == "accepted":
        fact = corrected_labels["fact"]
        targets = corrected_labels["targets"]
        scope_refs = sorted({
            str(ref) for target in targets for ref in target.get("scope_refs", [])
        })
        news_dao.update_event_projection(
            conn,
            event_id=int(selected["event_id"]),
            title=selected.get("event_title") or selected["title"],
            content=selected.get("event_content") or selected["content"],
            event_type=fact["event_type"],
            event_subtype=fact.get("event_subtype"),
            event_condition=fact.get("event_condition"),
            importance=fact["importance"],
            source_url=selected.get("source_url"),
            event_scope=targets[0]["scope"],
            affected_scope_refs=scope_refs,
            expected_value=fact.get("expected_value"),
            actual_value=fact.get("actual_value"),
            previous_value=fact.get("previous_value"),
            status="accepted",
            review_origin="human",
        )
    news_dao.refresh_event_projection_status(conn, event_id=int(selected["event_id"]))
    run_key = "daily-review-" + hashlib.sha256(
        f"{assessment_id}:{idempotency_key}".encode("utf-8")
    ).hexdigest()[:64]
    task, _ = create_manual_run(
        session,
        kind="news",
        now=now or datetime.now(timezone.utc),
        idempotency_key=run_key,
    )
    return _outcome({
        "assessment_id": assessment.assessment_id,
        "event_id": assessment.event_id,
        "fact_key": assessment.fact_key,
        "revision": assessment.revision,
        "review_status": assessment.review_status,
        "task_id": task.id,
    }, task, replay=False)
