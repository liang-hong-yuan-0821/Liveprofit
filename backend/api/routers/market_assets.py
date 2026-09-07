"""资产目录路由（§2.6.1：前端按固定组序 US→KR→CN 分组，组内 display_order ASC）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from backend.api.dependencies import ensure_trace_context
from backend.api.schemas.envelope import Envelope, EnvelopeMeta
from backend.api.schemas.market import MarketAssetDTO, MarketAssetsData
from backend.modules.market_data.application.service import MarketDataService
from backend.modules.market_data.infrastructure.repositories import SqlAlchemyMarketUow

router = APIRouter(prefix="/api/v1", tags=["market-assets"])


@router.get("/market-assets", response_model=Envelope[MarketAssetsData])
async def list_market_assets(
    request: Request,
    enabled: bool = Query(default=True),
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services

    def _do():
        with SqlAlchemyMarketUow(services._container.sync_session_factory) as uow:
            return MarketDataService(uow).list_assets(enabled_only=enabled)

    assets = await services.run(_do)
    data = MarketAssetsData(items=[MarketAssetDTO(**asset.__dict__) for asset in assets])
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=data, meta=meta).model_dump()
