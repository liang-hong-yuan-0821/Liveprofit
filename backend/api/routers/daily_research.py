"""Daily research run admission and immutable batch report reads."""

from __future__ import annotations

import uuid
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import ValidationError
from sqlalchemy import and_, func, or_, select

from backend.api.cursors import decode_cursor, encode_cursor
from backend.api.dependencies import ensure_trace_context
from backend.api.schemas.daily_research import (
    AssessmentReviewData,
    AssessmentReviewRequest,
    AssessmentReviewResponseData,
    DailyResearchKind,
    DailyResearchManualData,
    DailyResearchManualRequest,
    DailyResearchRunDetail,
    DailyResearchRunDetailData,
    DailyResearchRunListData,
    DailyResearchRunSummary,
    DisputedAssessment,
    DisputedAssessmentListData,
)
from backend.api.schemas.envelope import Envelope, EnvelopeMeta
from backend.api.schemas.problem import ProblemError
from backend.modules.analysis.application.errors import IdempotencyKeyReusedError, TaskNotFoundError
from backend.modules.analysis.domain.enums import TaskType
from backend.modules.analysis.infrastructure.models import AnalysisReport, AnalysisTask
from backend.modules.daily_research.application.scheduler import create_manual_run
from backend.modules.daily_research.application.assessment_review import (
    AssessmentReviewConflict,
    AssessmentReviewNotFound,
    review_disputed_assessment,
)
from AI.eventStudy.review import news_dao
from backend.modules.daily_research.application.news_pipeline import dbapi_connection, ensure_event_schema

router = APIRouter(prefix="/api/v1/daily-research", tags=["daily-research"])


def _daily_workflow(task: AnalysisTask) -> dict:
    params = task.request_params if isinstance(task.request_params, dict) else {}
    value = params.get("daily_research")
    return value if isinstance(value, dict) else {}


def _report_block(report: AnalysisReport | None) -> dict | None:
    if report is None:
        return None
    payload = report.report_json if isinstance(report.report_json, dict) else {}
    value = payload.get("daily_research")
    return value if isinstance(value, dict) else None


def _summary(task: AnalysisTask, report: AnalysisReport | None) -> DailyResearchRunSummary:
    workflow = _daily_workflow(task)
    block = _report_block(report) or {}
    return DailyResearchRunSummary(
        task_id=task.id,
        kind=workflow.get("kind", "news"),
        trigger=workflow.get("trigger", "scheduled"),
        slot=workflow.get("slot"),
        status=task.status,
        report_status=block.get("status"),
        target_trade_date=task.effective_trade_date,
        scheduled_at=workflow.get("scheduled_at"),
        news_cutoff_at=workflow.get("news_cutoff_at"),
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        updated_at=task.updated_at,
        attempt_no=task.attempt_no,
        error_code=task.error_code,
        error_summary=task.error_summary,
        conclusion_summary=report.conclusion_summary if report is not None else None,
        has_report=report is not None,
    )


def _latest_report_subquery():
    return (
        select(
            AnalysisReport.task_id.label("task_id"),
            func.max(AnalysisReport.report_version).label("report_version"),
        )
        .group_by(AnalysisReport.task_id)
        .subquery()
    )


def _daily_task_filter(kind: DailyResearchKind | None = None):
    predicate = AnalysisTask.task_type == TaskType.DAILY_RESEARCH.value
    if kind is not None:
        predicate = and_(
            predicate,
            AnalysisTask.request_params["daily_research"]["kind"].as_string() == kind,
        )
    return predicate


def _meta(request: Request, next_cursor: str | None = None) -> EnvelopeMeta:
    return EnvelopeMeta(request_id=request.state.trace_id, next_cursor=next_cursor)


@router.post("/runs", status_code=202, response_model=Envelope[DailyResearchManualData])
async def create_manual_research_run(
    payload: DailyResearchManualRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    trace_id: str = Depends(ensure_trace_context),
):
    if not idempotency_key:
        raise ProblemError(422, "VALIDATION_ERROR", "缺少 Idempotency-Key 请求头")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", idempotency_key):
        raise ProblemError(422, "VALIDATION_ERROR", "Idempotency-Key 必须为 1–128 个 URL-safe 字符")
    services = request.app.state.analysis_services

    def _do():
        with services.open() as bundle:
            task, created = create_manual_run(
                bundle.uow.session,
                kind=payload.kind,
                now=datetime.now(timezone.utc),
                idempotency_key=idempotency_key,
            )
            return {
                "task_id": task.id,
                "kind": payload.kind,
                "status": task.status,
                "trigger": "manual",
                "idempotent_replay": not created,
            }

    data = await services.run(_do)
    return Envelope(data=DailyResearchManualData(**data), meta=_meta(request)).model_dump()


@router.get("/runs", response_model=Envelope[DailyResearchRunListData])
async def list_research_runs(
    request: Request,
    kind: DailyResearchKind | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    trace_id: str = Depends(ensure_trace_context),
):
    try:
        before = decode_cursor(cursor) if cursor else None
    except (ValueError, TypeError, KeyError, ValidationError):
        raise ProblemError(422, "VALIDATION_ERROR", "cursor 非法") from None

    services = request.app.state.analysis_services

    def _do():
        with services.open() as bundle:
            session = bundle.uow.session
            stmt = select(AnalysisTask).where(_daily_task_filter(kind))
            if before is not None:
                before_at, before_id = before
                stmt = stmt.where(or_(
                    AnalysisTask.created_at < before_at,
                    and_(AnalysisTask.created_at == before_at, AnalysisTask.id < before_id),
                ))
            tasks = list(session.scalars(
                stmt.order_by(AnalysisTask.created_at.desc(), AnalysisTask.id.desc()).limit(limit + 1)
            ))
            page = tasks[:limit]
            if not page:
                return [], None
            latest = _latest_report_subquery()
            report_rows = session.execute(
                select(AnalysisReport)
                .join(latest, and_(
                    latest.c.task_id == AnalysisReport.task_id,
                    latest.c.report_version == AnalysisReport.report_version,
                ))
                .where(AnalysisReport.task_id.in_([task.id for task in page]))
            ).scalars()
            reports = {report.task_id: report for report in report_rows}
            next_cursor = None
            if len(tasks) > limit:
                last = page[-1]
                next_cursor = (last.created_at, last.id)
            return [_summary(task, reports.get(task.id)) for task in page], next_cursor

    rows, next_cursor_tuple = await services.run(_do)
    next_cursor = None
    if next_cursor_tuple is not None:
        next_cursor = encode_cursor(next_cursor_tuple[0], next_cursor_tuple[1])
    return Envelope(
        data=DailyResearchRunListData(items=rows),
        meta=_meta(request, next_cursor=next_cursor),
    ).model_dump()


@router.get("/runs/latest", response_model=Envelope[DailyResearchRunDetailData])
async def get_latest_research_run(
    request: Request,
    kind: DailyResearchKind = Query(...),
    event_id: int | None = Query(default=None, ge=1),
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services

    def _do():
        with services.open() as bundle:
            latest = _latest_report_subquery()
            stmt = (
                select(AnalysisTask, AnalysisReport)
                .join(latest, latest.c.task_id == AnalysisTask.id)
                .join(AnalysisReport, and_(
                    AnalysisReport.task_id == latest.c.task_id,
                    AnalysisReport.report_version == latest.c.report_version,
                ))
                .where(_daily_task_filter(kind))
            )
            if event_id is not None:
                stmt = stmt.where(AnalysisReport.report_json.contains({
                    "daily_research": {"event_forecast": [{"event_id": event_id}]}
                }))
            task, report = bundle.uow.session.execute(
                stmt.order_by(AnalysisReport.generated_at.desc(), AnalysisTask.id.desc()).limit(1)
            ).first() or (None, None)
            if task is None:
                return None
            return DailyResearchRunDetailData(item=DailyResearchRunDetail(
                run=_summary(task, report),
                report=_report_block(report),
                report_version=report.report_version if report is not None else None,
                report_generated_at=report.generated_at if report is not None else None,
            ))

    data = await services.run(_do)
    if data is None:
        return Envelope(data=DailyResearchRunDetailData(item=None), meta=_meta(request)).model_dump()
    return Envelope(data=data, meta=_meta(request)).model_dump()


@router.get("/runs/{task_id}", response_model=Envelope[DailyResearchRunDetailData])
async def get_research_run(
    task_id: uuid.UUID,
    request: Request,
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services

    def _do():
        with services.open() as bundle:
            session = bundle.uow.session
            task = session.scalar(select(AnalysisTask).where(
                AnalysisTask.id == task_id,
                AnalysisTask.task_type == TaskType.DAILY_RESEARCH.value,
            ))
            if task is None:
                raise TaskNotFoundError(f"每日研究批次不存在：{task_id}")
            report = session.scalar(
                select(AnalysisReport)
                .where(AnalysisReport.task_id == task_id)
                .order_by(AnalysisReport.report_version.desc())
                .limit(1)
            )
            return DailyResearchRunDetailData(item=DailyResearchRunDetail(
                run=_summary(task, report),
                report=_report_block(report),
                report_version=report.report_version if report is not None else None,
                report_generated_at=report.generated_at if report is not None else None,
            ))

    data = await services.run(_do)
    return Envelope(data=data, meta=_meta(request)).model_dump()


@router.get("/assessments/disputed", response_model=Envelope[DisputedAssessmentListData])
async def list_disputed_event_assessments(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    trace_id: str = Depends(ensure_trace_context),
):
    """List the current unresolved event labels backed by immutable PG assessments."""
    services = request.app.state.analysis_services

    def _do():
        with services.open() as bundle:
            session = bundle.uow.session
            ensure_event_schema(session)
            try:
                rows = news_dao.list_disputed_assessments(dbapi_connection(session), limit=limit)
                return DisputedAssessmentListData(items=[DisputedAssessment(
                    assessment_id=row["assessment_id"],
                    news_id=row["news_id"],
                    event_id=row["event_id"],
                    fact_key=row["fact_key"],
                    revision=row["revision"],
                    novelty=row["novelty"],
                    review_status=row["review_status"],
                    labels=row["labels"] or {},
                    evidence=row["evidence"] or [],
                    available_at=row["available_at"],
                    title=row["title"],
                    raw_content=row["content"],
                    source=row["source"],
                    source_label=row["source_label"],
                    source_url=row["source_url"],
                ) for row in rows])
            finally:
                session.rollback()

    data = await services.run(_do)
    return Envelope(data=data, meta=_meta(request)).model_dump()


@router.post(
    "/assessments/{assessment_id}/review",
    status_code=202,
    response_model=Envelope[AssessmentReviewResponseData],
)
async def review_disputed_event_assessment(
    assessment_id: uuid.UUID,
    payload: AssessmentReviewRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    trace_id: str = Depends(ensure_trace_context),
):
    if not idempotency_key or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", idempotency_key):
        raise ProblemError(422, "VALIDATION_ERROR", "缺少或非法的 Idempotency-Key 请求头")
    services = request.app.state.analysis_services

    def _do():
        with services.open() as bundle:
            session = bundle.uow.session
            result = review_disputed_assessment(
                session,
                assessment_id=str(assessment_id),
                expected_revision=payload.expected_revision,
                review_status=payload.review_status,
                labels=payload.labels,
                review_note=payload.review_note,
                idempotency_key=idempotency_key,
                now=datetime.now(timezone.utc),
            )
            return AssessmentReviewResponseData(item=AssessmentReviewData(**result))

    try:
        data = await services.run(_do)
    except AssessmentReviewNotFound as exc:
        raise ProblemError(404, "ASSESSMENT_NOT_FOUND", str(exc)) from exc
    except (AssessmentReviewConflict, news_dao.AssessmentRevisionConflict) as exc:
        raise ProblemError(409, "ASSESSMENT_REVISION_CONFLICT", str(exc)) from exc
    except IdempotencyKeyReusedError as exc:
        raise ProblemError(409, "IDEMPOTENCY_KEY_REUSED", str(exc)) from exc
    except ValueError as exc:
        raise ProblemError(422, "VALIDATION_ERROR", str(exc)) from exc
    return Envelope(data=data, meta=_meta(request)).model_dump()
