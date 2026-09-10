"""事件研究审核路由（方案：事件研究审核界面平台集成方案 3.1.1，6 端点）。

服务方法均为同步阻塞（复用 AI 侧 review_dao），统一经 API 分析服务线程池执行。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from backend.api.dependencies import ensure_trace_context
from backend.api.schemas.envelope import Envelope, EnvelopeMeta
from backend.api.schemas.event_study_review import (
    BatchSummary,
    ComputeData,
    ComputeRequest,
    ConfirmImpactsData,
    ConfirmImpactsRequest,
    ImpactDraftDTO,
    ImpactDraftListData,
    PendingEventDTO,
    PendingEventListData,
    PrelabelData,
    PrelabelRequest,
    RefreshData,
    ReviewBatchData,
    ReviewBatchRequest,
    ReviewRowResult,
)
from backend.modules.event_study.application.review_contracts import ReviewRowCommand

router = APIRouter(prefix="/api/v1/event-studies/review", tags=["event-studies"])


def _service(request: Request):
    return request.app.state.event_study_review_service


def _meta(request: Request) -> EnvelopeMeta:
    return EnvelopeMeta(request_id=request.state.trace_id)


@router.get("/pending-events", response_model=Envelope[PendingEventListData])
async def list_pending_events(request: Request, trace_id: str = Depends(ensure_trace_context)):
    service = _service(request)
    items = await request.app.state.analysis_services.run(service.list_pending_events)
    data = PendingEventListData(items=[PendingEventDTO(**d.__dict__) for d in items])
    return Envelope(data=data, meta=_meta(request)).model_dump()


@router.post("/prelabel", response_model=Envelope[PrelabelData])
async def prelabel(payload: PrelabelRequest, request: Request, trace_id: str = Depends(ensure_trace_context)):
    service = _service(request)
    result = await request.app.state.analysis_services.run(lambda: service.prelabel(payload.limit))
    data = PrelabelData(prelabeled=result.prelabeled, remaining=result.remaining)
    return Envelope(data=data, meta=_meta(request)).model_dump()


@router.post("/refresh", response_model=Envelope[RefreshData])
async def refresh(request: Request, trace_id: str = Depends(ensure_trace_context)):
    service = _service(request)
    result = await request.app.state.analysis_services.run(service.refresh_events)
    data = RefreshData(
        fetched=result.fetched, new_drafts=result.new_drafts, skipped_reason=result.skipped_reason
    )
    return Envelope(data=data, meta=_meta(request)).model_dump()


@router.post("/batch", response_model=Envelope[ReviewBatchData])
async def submit_batch(payload: ReviewBatchRequest, request: Request, trace_id: str = Depends(ensure_trace_context)):
    service = _service(request)
    commands = [ReviewRowCommand(**row.model_dump()) for row in payload.items]
    result = await request.app.state.analysis_services.run(lambda: service.submit_batch(commands))
    data = ReviewBatchData(
        results=[ReviewRowResult(**r.__dict__) for r in result.results],
        summary=BatchSummary(
            approved=result.approved, ignored=result.ignored, computed=result.computed
        ),
    )
    return Envelope(data=data, meta=_meta(request)).model_dump()


@router.post("/events/{event_id}/compute", response_model=Envelope[ComputeData])
async def compute(event_id: int, payload: ComputeRequest, request: Request, trace_id: str = Depends(ensure_trace_context)):
    service = _service(request)
    result = await request.app.state.analysis_services.run(
        lambda: service.compute_for_event(event_id, payload.operator)
    )
    data = ComputeData(event_id=result.event_id, status=result.status, message=result.message)
    return Envelope(data=data, meta=_meta(request)).model_dump()


@router.get("/impact-drafts", response_model=Envelope[ImpactDraftListData])
async def list_impact_drafts(request: Request, trace_id: str = Depends(ensure_trace_context)):
    service = _service(request)
    items = await request.app.state.analysis_services.run(service.list_impact_drafts)
    data = ImpactDraftListData(items=[ImpactDraftDTO(**d.__dict__) for d in items])
    return Envelope(data=data, meta=_meta(request)).model_dump()


@router.post("/impact-drafts/{event_id}/confirm", response_model=Envelope[ConfirmImpactsData])
async def confirm_impacts(
    event_id: int, payload: ConfirmImpactsRequest, request: Request, trace_id: str = Depends(ensure_trace_context)
):
    service = _service(request)
    result = await request.app.state.analysis_services.run(
        lambda: service.confirm_impacts(event_id, payload.tickers, payload.operator)
    )
    data = ConfirmImpactsData(event_id=result.event_id, inserted=result.inserted)
    return Envelope(data=data, meta=_meta(request)).model_dump()
