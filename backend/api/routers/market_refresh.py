"""Read freshness separately from admitting background collection."""
from fastapi import APIRouter, Depends, Request, Response

from backend.api.dependencies import ensure_trace_context
from backend.api.schemas.envelope import Envelope, EnvelopeMeta
from backend.api.schemas.market_refresh import Resource, RefreshRequest, RefreshStatusData, RefreshDecisionsData, RefreshJob
from backend.api.schemas.problem import ProblemError
from backend.modules.market_data.application.refresh_service import RefreshUnavailable, build_refresh_service

router = APIRouter(prefix="/api/v1/market-data", tags=["market-data"])


def service_for(request):
    state = request.app.state
    override = getattr(state, "market_refresh_service", None)
    if override is not None:
        return override
    from backend.workers.market_refresh import publish_refresh
    publisher = getattr(state, "market_refresh_publisher", None)
    if publisher is not None and hasattr(publisher, "send"):
        publisher = publisher.send
    if publisher is None:
        publisher = lambda job_id: publish_refresh(state.settings, job_id)
    return build_refresh_service(state.settings, state.container.redis_sync, state.market_conn, publisher=publisher)


@router.get("/refresh-status", response_model=Envelope[RefreshStatusData])
async def refresh_status(request: Request, trace_id: str = Depends(ensure_trace_context)):
    data = await request.app.state.analysis_services.run(service_for(request).status)
    return Envelope(data=data, meta=EnvelopeMeta(request_id=trace_id))


@router.post("/refresh", response_model=Envelope[RefreshDecisionsData])
async def refresh(body: RefreshRequest, request: Request, response: Response, trace_id: str = Depends(ensure_trace_context)):
    service = service_for(request)
    try:
        decisions = await request.app.state.analysis_services.run(lambda: service.ensure(body.resources, body.mode))
    except RefreshUnavailable as exc:
        raise ProblemError(503, "REFRESH_UNAVAILABLE", str(exc), retryable=True) from exc
    response.status_code = 202 if any(d["decision"] in ("QUEUED", "IN_PROGRESS") for d in decisions) else 200
    return Envelope(data={"decisions": decisions}, meta=EnvelopeMeta(request_id=trace_id))


@router.get("/refresh-jobs/{job_id}", response_model=Envelope[RefreshJob])
async def refresh_job(job_id: str, request: Request, trace_id: str = Depends(ensure_trace_context)):
    from redis.exceptions import RedisError
    service = service_for(request)
    try:
        job = await request.app.state.analysis_services.run(lambda: service.store.job(job_id))
    except RedisError as exc:
        raise ProblemError(503, "REFRESH_UNAVAILABLE", "更新服务暂不可用", retryable=True) from exc
    if job is None or job.get("resource") not in {resource.value for resource in Resource}:
        raise ProblemError(404, "REFRESH_JOB_NOT_FOUND", "更新记录不存在或已过期，请重新检查行情")
    return Envelope(data=service.public_job(job), meta=EnvelopeMeta(request_id=trace_id))
