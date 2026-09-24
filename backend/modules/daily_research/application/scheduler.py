"""Daily research schedule admission using the existing task/outbox lifecycle."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError

from backend.modules.analysis.application.errors import IdempotencyKeyReusedError
from backend.modules.analysis.application.task_lifecycle import (
    MESSAGE_TYPE_ANALYSIS_TASK,
)
from backend.modules.analysis.domain.enums import TaskStatus, TaskType
from backend.modules.analysis.infrastructure.models import (
    AnalysisReport,
    AnalysisTask,
    TaskOutbox,
)
from backend.shared.ids import new_uuid

logger = logging.getLogger(__name__)
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_SLOTS = (
    ("news_0900", time(9, 0), "news"),
    ("news_2100", time(21, 0), "news"),
    ("quant_2100", time(21, 0), "quant"),
)
_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _idempotency_key(day: str, slot: str) -> str:
    return f"daily-research-{day}-{slot}"


def _quant_refresh_idempotency_key(
    root_quant_task_id: UUID,
    news_task_id: UUID,
    root_scheduled_at: datetime,
) -> str:
    day_key = root_scheduled_at.astimezone(_SHANGHAI).strftime("%Y%m%d")
    return f"daily-research-{day_key}-quant-refresh-{root_quant_task_id.hex}-{news_task_id.hex}"


def _create_run(
    session,
    *,
    scheduled_at: datetime,
    slot: str,
    kind: str,
    trigger: str = "scheduled",
    idempotency_key: str | None = None,
    target_trade_date: date | None = None,
    workflow_extra: dict | None = None,
) -> bool:
    local = scheduled_at.astimezone(_SHANGHAI)
    key = idempotency_key or _idempotency_key(local.strftime("%Y%m%d"), slot)
    if not _IDEMPOTENCY_KEY_RE.fullmatch(key):
        raise ValueError("Idempotency-Key 必须为 1–128 个 URL-safe 字符")
    hash_input = {"kind": kind, "trigger": trigger, "slot": slot if trigger == "scheduled" else None}
    if target_trade_date is not None or workflow_extra:
        hash_input["target_trade_date"] = target_trade_date.isoformat() if target_trade_date else None
        hash_input["workflow_extra"] = workflow_extra or {}
    encoded = json.dumps(hash_input, sort_keys=True, separators=(",", ":"))
    expected_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    wait_until = scheduled_at.astimezone(_SHANGHAI).replace(microsecond=0)
    if kind == "quant":
        wait_until += timedelta(hours=1)
    else:
        # A competing news run owns the global analysis lock; preserve the accepted
        # task and retry briefly instead of failing a manual double-submit.
        wait_until += timedelta(minutes=30)
    existing = session.scalar(
        select(AnalysisTask.id).where(AnalysisTask.idempotency_key == key)
    )
    if existing is not None:
        task = session.get(AnalysisTask, existing)
        if trigger == "manual" and task is not None and task.input_hash != expected_hash:
            session.rollback()
            raise IdempotencyKeyReusedError("同一 Idempotency-Key 已用于不同每日研究输入")
        session.rollback()
        return False

    workflow = {
        "kind": kind,
        "slot": slot,
        "trigger": trigger,
        "scheduled_at": scheduled_at.isoformat(),
        "news_cutoff_at": scheduled_at.isoformat() if kind == "news" and trigger == "scheduled" else None,
        "wait_until": wait_until.astimezone(scheduled_at.tzinfo).isoformat(),
    }
    workflow.update(workflow_extra or {})
    task_id = new_uuid()
    now = datetime.now(_SHANGHAI)
    task = AnalysisTask(
        id=task_id,
        task_type=TaskType.DAILY_RESEARCH.value,
        status=TaskStatus.PENDING.value,
        request_params={"daily_research": workflow},
        selected_layers=["market", "sector"] if kind == "news" else ["screening", "position"],
        ticker=None,
        requested_trade_date=(target_trade_date or local.date()) if kind == "quant" else None,
        effective_trade_date=(target_trade_date or local.date()) if kind == "quant" else None,
        date_correction=None,
        input_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        idempotency_key=key,
        attempt_no=1,
        created_at=now,
        updated_at=now,
    )
    outbox = TaskOutbox(
        id=new_uuid(),
        task_id=task_id,
        attempt_no=1,
        message_type=MESSAGE_TYPE_ANALYSIS_TASK,
        payload={"task_id": str(task_id), "attempt_no": 1},
        status="PENDING",
        retry_count=0,
        trace_context={"trace_id": key},
        created_at=now,
        updated_at=now,
    )
    session.add(task)
    session.add(outbox)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        # Multiple Dispatcher processes can race on the unique idempotency key.
        replay = session.scalar(
            select(AnalysisTask).where(AnalysisTask.idempotency_key == key)
        )
        if replay is None:
            raise
        if trigger == "manual" and replay.input_hash != expected_hash:
            session.rollback()
            raise IdempotencyKeyReusedError("同一 Idempotency-Key 已用于不同每日研究输入")
        session.rollback()
        return False
    logger.info("每日研究已准入：slot=%s trigger=%s task_id=%s cutoff=%s", slot, trigger, task_id, workflow["news_cutoff_at"])
    return True


def create_manual_run(session, *, kind: str, now: datetime, idempotency_key: str) -> tuple[AnalysisTask, bool]:
    """Admit a manual news or quant run through the same task/outbox transaction."""
    if kind not in {"news", "quant"}:
        raise ValueError("kind 仅支持 news 或 quant")
    if now.tzinfo is None:
        raise ValueError("手动运行时间必须带时区")
    if not _IDEMPOTENCY_KEY_RE.fullmatch(idempotency_key or ""):
        raise ValueError("Idempotency-Key 必须为 1–128 个 URL-safe 字符")
    local = now.astimezone(_SHANGHAI).replace(microsecond=0)
    created = _create_run(
        session,
        scheduled_at=local,
        slot=f"manual_{kind}",
        kind=kind,
        trigger="manual",
        idempotency_key=idempotency_key,
    )
    task = session.scalar(select(AnalysisTask).where(AnalysisTask.idempotency_key == idempotency_key))
    if task is None:
        raise RuntimeError("每日研究任务提交后无法重新读取")
    session.rollback()
    return task, created


def ensure_due_runs(session_factory, now: datetime, *, enabled: bool = True) -> int:
    """Create at most today's due 09:00 and 21:00 runs; DB uniqueness fences replicas."""
    if not enabled:
        return 0
    if now.tzinfo is None:
        raise ValueError("Dispatcher 时间必须带时区")
    local_now = now.astimezone(_SHANGHAI)
    created = 0
    with session_factory() as session:
        for slot, scheduled_time, kind in _SLOTS:
            scheduled_local = datetime.combine(local_now.date(), scheduled_time, tzinfo=_SHANGHAI)
            if local_now >= scheduled_local:
                created += int(_create_run(
                    session,
                    scheduled_at=scheduled_local.astimezone(now.tzinfo),
                    slot=slot,
                    kind=kind,
                ))
    return created


def _incremental_bucket(local_now: datetime, interval_seconds: int) -> datetime:
    if interval_seconds <= 0:
        raise ValueError("news_analysis_interval_seconds 必须为正")
    day_start = datetime.combine(local_now.date(), time.min, tzinfo=_SHANGHAI)
    elapsed = int((local_now - day_start).total_seconds())
    return day_start + timedelta(seconds=(elapsed // interval_seconds) * interval_seconds)


def ensure_incremental_news_run(
    session_factory,
    now: datetime,
    *,
    interval_seconds: int = 1800,
    enabled: bool = True,
) -> bool:
    """Admit one scheduled AI pass per interval only while an eligible backlog exists."""
    if not enabled:
        return False
    if now.tzinfo is None:
        raise ValueError("Dispatcher 时间必须带时区")
    local_now = now.astimezone(_SHANGHAI).replace(microsecond=0)
    # The fixed reports own the exact 09:00/21:00 windows; avoid admitting a competing
    # interval report in their immediate start window.
    if any(abs((local_now - datetime.combine(local_now.date(), fixed, tzinfo=_SHANGHAI)).total_seconds()) < 300
           for fixed in (time(9, 0), time(21, 0))):
        return False
    bucket = _incremental_bucket(local_now, interval_seconds)
    bucket_key = bucket.strftime("%Y%m%d-%H%M%S")
    key = f"daily-research-{local_now:%Y%m%d}-news-incremental-{bucket_key}"
    if not _IDEMPOTENCY_KEY_RE.fullmatch(key):
        raise ValueError("interval idempotency key 超出允许长度")

    from AI.eventStudy.review import news_dao
    from backend.modules.daily_research.application.news_pipeline import (
        dbapi_connection,
        ensure_event_schema,
    )

    with session_factory() as session:
        active = session.scalar(
            select(AnalysisTask.id)
            .where(
                AnalysisTask.task_type == TaskType.DAILY_RESEARCH.value,
                AnalysisTask.request_params["daily_research"]["kind"].as_string() == "news",
                AnalysisTask.status.in_([
                    TaskStatus.PENDING.value, TaskStatus.QUEUED.value,
                    TaskStatus.RUNNING.value, TaskStatus.RETRYING.value,
                    TaskStatus.CANCEL_REQUESTED.value,
                ]),
            )
            .limit(1)
        )
        session.rollback()
        if active is not None:
            return False
        ensure_event_schema(session)
        try:
            pending = news_dao.count_unprocessed_news(
                dbapi_connection(session), as_of=local_now.astimezone(timezone.utc),
            )
        finally:
            session.rollback()
        if pending <= 0:
            return False
        return _create_run(
            session,
            scheduled_at=local_now,
            slot="news_incremental",
            kind="news",
            trigger="scheduled",
            idempotency_key=key,
        )


def _needs_quant_news_refresh(report_json: dict) -> bool:
    block = report_json.get("daily_research") if isinstance(report_json, dict) else None
    if not isinstance(block, dict) or block.get("kind") != "quant" or block.get("status") != "partial":
        return False
    if block.get("reason") == "QUANT_DATA_NOT_READY" or not block.get("strategies"):
        return False
    dependency = block.get("news_research_dependency")
    return bool(
        isinstance(dependency, dict)
        and dependency.get("complete") is False
        and isinstance(dependency.get("pending_news_count"), int)
        and dependency["pending_news_count"] > 0
    )


def ensure_quant_news_refresh_run(
    session_factory,
    now: datetime,
    *,
    enabled: bool = True,
) -> int:
    """Re-run candidate weighting when a later news batch closes a partial quant run."""
    if not enabled:
        return 0
    if now.tzinfo is None:
        raise ValueError("Dispatcher 时间必须带时区")
    local_now = now.astimezone(_SHANGHAI).replace(microsecond=0)
    now_utc = local_now.astimezone(timezone.utc)
    latest_reports = (
        select(
            AnalysisReport.task_id.label("task_id"),
            func.max(AnalysisReport.report_version).label("report_version"),
        )
        .group_by(AnalysisReport.task_id)
        .subquery()
    )
    with session_factory() as session:
        quant_rows = list(session.execute(
            select(AnalysisTask, AnalysisReport)
            .join(latest_reports, latest_reports.c.task_id == AnalysisTask.id)
            .join(AnalysisReport, and_(
                AnalysisReport.task_id == latest_reports.c.task_id,
                AnalysisReport.report_version == latest_reports.c.report_version,
            ))
            .where(
                AnalysisTask.task_type == TaskType.DAILY_RESEARCH.value,
                AnalysisTask.status == TaskStatus.SUCCEEDED.value,
                AnalysisTask.created_at >= now_utc - timedelta(hours=36),
                AnalysisTask.request_params["daily_research"]["kind"].as_string() == "quant",
                AnalysisTask.request_params["daily_research"]["slot"].as_string().in_(
                    ["quant_2100", "quant_news_refresh"]
                ),
            )
            .order_by(AnalysisTask.created_at.desc())
            .limit(8)
        ).all())
        session.rollback()

        for quant_task, quant_report in quant_rows:
            quant_payload = quant_report.report_json if isinstance(quant_report.report_json, dict) else {}
            if not _needs_quant_news_refresh(quant_payload):
                continue
            later_news_rows = list(session.execute(
                select(AnalysisTask, AnalysisReport)
                .join(latest_reports, latest_reports.c.task_id == AnalysisTask.id)
                .join(AnalysisReport, and_(
                    AnalysisReport.task_id == latest_reports.c.task_id,
                    AnalysisReport.report_version == latest_reports.c.report_version,
                ))
                .where(
                    AnalysisTask.task_type == TaskType.DAILY_RESEARCH.value,
                    AnalysisTask.status == TaskStatus.SUCCEEDED.value,
                    AnalysisTask.created_at >= now_utc - timedelta(hours=36),
                    AnalysisReport.generated_at > quant_report.generated_at,
                    AnalysisTask.request_params["daily_research"]["kind"].as_string() == "news",
                    AnalysisTask.request_params["daily_research"]["slot"].as_string().in_(
                        ["news_0900", "news_2100", "news_incremental"]
                    ),
                )
                .order_by(AnalysisReport.generated_at.desc())
                .limit(8)
            ).all())
            session.rollback()
            news_task = next((
                task for task, report in later_news_rows
                if isinstance(report.report_json, dict)
                and isinstance(report.report_json.get("daily_research"), dict)
                and report.report_json["daily_research"].get("status") == "completed"
            ), None)
            if news_task is None:
                continue

            from AI.eventStudy.review import news_dao
            from backend.modules.daily_research.application.news_pipeline import (
                dbapi_connection,
                ensure_event_schema,
            )

            ensure_event_schema(session)
            try:
                pending_news = news_dao.count_unprocessed_news(
                    dbapi_connection(session), as_of=now_utc,
                )
            finally:
                session.rollback()
            if pending_news != 0:
                continue

            workflow = (quant_task.request_params or {}).get("daily_research") or {}
            try:
                root_quant_id = UUID(str(
                    workflow.get("refresh_parent_quant_task_id") or quant_task.id
                ))
            except (ValueError, TypeError):
                logger.warning("量化刷新父批次 ID 无效，跳过 task_id=%s", quant_task.id)
                continue
            root_quant_task = session.get(AnalysisTask, root_quant_id)
            if root_quant_task is None:
                continue
            root_workflow = (root_quant_task.request_params or {}).get("daily_research") or {}
            try:
                original_scheduled_at = datetime.fromisoformat(
                    str(root_workflow["scheduled_at"]).replace("Z", "+00:00")
                )
                if original_scheduled_at.tzinfo is None:
                    raise ValueError("scheduled_at must include a timezone")
                target_trade_date = (
                    root_quant_task.effective_trade_date or quant_task.effective_trade_date
                )
                if target_trade_date is None and quant_payload.get("target_trade_date"):
                    target_trade_date = date.fromisoformat(
                        str(quant_payload["target_trade_date"])[:10]
                    )
                if target_trade_date is None:
                    raise ValueError("refresh target trade date is missing")
            except (KeyError, TypeError, ValueError):
                logger.warning(
                    "量化刷新父批次缺少有效的运行时点或交易日，跳过 task_id=%s",
                    quant_task.id,
                )
                continue
            key = _quant_refresh_idempotency_key(root_quant_id, news_task.id, original_scheduled_at)
            created = _create_run(
                session,
                scheduled_at=local_now,
                slot="quant_news_refresh",
                kind="quant",
                trigger="scheduled",
                idempotency_key=key,
                target_trade_date=target_trade_date,
                workflow_extra={
                    "refresh_parent_quant_task_id": str(root_quant_id),
                    "refresh_news_task_id": str(news_task.id),
                    "news_cutoff_at": now_utc.isoformat(),
                },
            )
            if created:
                logger.info(
                    "新闻完成后已创建量化排序刷新：parent=%s news=%s new=%s",
                    root_quant_id, news_task.id, key,
                )
                return 1
    return 0
