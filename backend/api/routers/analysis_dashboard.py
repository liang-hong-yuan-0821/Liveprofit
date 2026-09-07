"""AI 投研看板路由（§3.2.1：只读聚合投影，不返回 SSE/请求参数/租约/报告全文）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from backend.api.dependencies import ensure_trace_context
from backend.api.schemas.dashboard import (
    ActiveTaskDTO,
    AnalysisDashboardDTO,
    PendingActionDTO,
    RecentConclusionDTO,
    UnavailableBlockDTO,
)
from backend.api.schemas.envelope import Envelope, EnvelopeMeta

router = APIRouter(prefix="/api/v1", tags=["analysis-dashboard"])


@router.get("/analysis-dashboard", response_model=Envelope[AnalysisDashboardDTO])
async def get_dashboard(request: Request, trace_id: str = Depends(ensure_trace_context)):
    services = request.app.state.analysis_services

    def _do():
        with services.open() as bundle:
            return bundle.tasks.get_dashboard()

    dashboard = await services.run(_do)
    data = AnalysisDashboardDTO(
        pending_actions=[
            PendingActionDTO(
                kind=item.kind,
                task_id=item.task_id,
                task_type=item.task_type.value,
                ticker=item.ticker,
                effective_trade_date=item.effective_trade_date,
                updated_at=item.updated_at,
                error_code=item.error_code,
                error_summary=item.error_summary,
                unavailable_blocks=(
                    [UnavailableBlockDTO(**block) for block in item.unavailable_blocks]
                    if item.unavailable_blocks is not None
                    else None
                ),
                retryable=item.retryable,
            )
            for item in dashboard.pending_actions
        ],
        active_tasks=[
            ActiveTaskDTO(
                task_id=item.task_id,
                task_type=item.task_type.value,
                ticker=item.ticker,
                effective_trade_date=item.effective_trade_date,
                status=item.status.value,
                attempt_no=item.attempt_no,
                updated_at=item.updated_at,
                next_retry_at=item.next_retry_at,
            )
            for item in dashboard.active_tasks
        ],
        recent_conclusions=[
            RecentConclusionDTO(
                task_id=item.task_id,
                task_type=item.task_type.value,
                ticker=item.ticker,
                effective_trade_date=item.effective_trade_date,
                completed_at=item.completed_at,
                conclusion_summary=item.conclusion_summary,
                risk_flag=item.risk_flag,
                risk_hint=item.risk_hint,
                has_report=item.has_report,
                updated_at=item.updated_at,
            )
            for item in dashboard.recent_conclusions
        ],
        generated_at=dashboard.generated_at,
    )
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=data, meta=meta).model_dump()
