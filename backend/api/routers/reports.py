"""报告路由（§3.2.1：只读最新成功版本，无版本 Query 参数）。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request

from backend.api.dependencies import ensure_trace_context
from backend.api.schemas.envelope import Envelope, EnvelopeMeta
from backend.api.schemas.reports import ReportDTO, ReportSectionDTO, ReportTaskInfo
from backend.modules.analysis.application.errors import ReportNotFoundError
from backend.modules.analysis.domain.enums import TaskType

router = APIRouter(prefix="/api/v1", tags=["reports"])


@router.get("/analysis-tasks/{task_id}/report", response_model=Envelope[ReportDTO])
async def get_report(task_id: uuid.UUID, request: Request, trace_id: str = Depends(ensure_trace_context)):
    services = request.app.state.analysis_services

    def _do():
        with services.open() as bundle:
            report = bundle.reports.get_latest_report(bundle.uow, task_id)  # 不存在 → ReportNotFoundError(404)
            task = bundle.uow.tasks.get(task_id)
            if task is None:
                raise ReportNotFoundError(f"任务 {task_id} 不存在")
            duration_ms = None
            if task.started_at is not None and task.finished_at is not None:
                duration_ms = int((task.finished_at - task.started_at).total_seconds() * 1000)
            return report, task, duration_ms

    report, task, duration_ms = await services.run(_do)
    report_json = report.report_json or {}
    sections = [
        ReportSectionDTO(
            block=section.get("block", "decision"),
            status=section.get("status", "UNAVAILABLE"),
            title=section.get("title"),
            summary=section.get("summary"),
            content=section.get("content"),
            charts=section.get("charts"),
            unavailable_reason=section.get("unavailable_reason"),
            retryable=section.get("retryable"),
        )
        for section in report_json.get("sections", [])
    ]
    data = ReportDTO(
        schema_version=report.schema_version,
        report_version=report.report_version,
        generated_at=report.generated_at,
        task=ReportTaskInfo(
            task_id=task_id,
            task_type=TaskType(task.task_type).value,
            ticker=task.ticker,
            effective_trade_date=task.effective_trade_date,
            duration_ms=duration_ms,
        ),
        sections=sections,
        data_sources=report_json.get("data_sources"),
        risk_note=report_json.get("risk_note"),
    )
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=data, meta=meta).model_dump()
