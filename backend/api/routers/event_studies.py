"""事件研究路由（§3.2.1：同步有界预测 + 资产清单）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from backend.api.dependencies import ensure_trace_context
from backend.api.schemas.envelope import Envelope, EnvelopeMeta
from backend.api.schemas.event_studies import (
    AssetListData,
    EventStudyAssetDTO,
    PredictionData,
    PredictionRequest,
)
from backend.modules.event_study.application.contracts import EventStudyPredictionCommand

router = APIRouter(prefix="/api/v1", tags=["event-studies"])


@router.post("/event-studies/predictions", response_model=Envelope[PredictionData])
async def predict(
    payload: PredictionRequest,
    request: Request,
    trace_id: str = Depends(ensure_trace_context),
):
    service = request.app.state.event_study_service
    command = EventStudyPredictionCommand(
        event_text=payload.event_text,
        asset_ticker=payload.asset_ticker,
        window_type=payload.window_type,
        event_type=payload.event_type,
        event_subtype=payload.event_subtype,
        event_condition=payload.event_condition,
        save=payload.save,
        event_id=payload.event_id,
    )
    result = await service.predict(command)
    data = PredictionData(
        prediction=result.prediction,
        template_stats=result.template_stats,
        supplement_events=result.supplement_events,
        note=result.note,
    )
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=data, meta=meta).model_dump()


@router.get("/event-studies/assets", response_model=Envelope[AssetListData])
async def list_assets(request: Request, trace_id: str = Depends(ensure_trace_context)):
    service = request.app.state.event_study_service
    analysis_services = request.app.state.analysis_services
    # 资产读取为同步 DB 查询：经分析服务线程池执行，禁止默认 executor
    assets = await analysis_services.run(service.list_assets_sync)
    data = AssetListData(items=[EventStudyAssetDTO(**asset.__dict__) for asset in assets])
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=data, meta=meta).model_dump()
