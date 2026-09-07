"""市场数据路由（§2.6.1：指数 K 线与热门概念）。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, Request

from backend.api.dependencies import ensure_trace_context
from backend.api.schemas.envelope import Envelope, EnvelopeMeta
from backend.api.schemas.market import (
    BarDTO,
    BarsData,
    BarsAssetInfo,
    DailyChangeDTO,
    HotConceptDTO,
    HotConceptsData,
)
from backend.modules.market_data.application.service import MarketDataService
from backend.modules.market_data.infrastructure.repositories import SqlAlchemyMarketUow

router = APIRouter(prefix="/api/v1", tags=["market-data"])

HEAT_ALGORITHM_VERSION = "heat_v1"


@router.get("/market-data/indices/{symbol}/bars", response_model=Envelope[BarsData])
async def index_bars(
    symbol: str,
    request: Request,
    market: str = Query(),
    interval: str = Query(),
    from_: date = Query(alias="from"),
    to: date = Query(),
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services
    calendar = request.app.state.market_calendar

    def _do():
        with SqlAlchemyMarketUow(services._container.sync_session_factory) as uow:
            return MarketDataService(uow, calendar=calendar).get_bars(
                market=market, symbol=symbol, interval=interval, from_date=from_, to_date=to
            )

    bars_dto = await services.run(_do)
    data = BarsData(
        asset=BarsAssetInfo(
            market=bars_dto.asset.market,
            symbol=bars_dto.asset.symbol,
            name=bars_dto.asset.name,
            currency=bars_dto.asset.currency,
            market_timezone=bars_dto.asset.market_timezone,
            supported_intervals=bars_dto.asset.supported_intervals,
        ),
        interval=bars_dto.interval,
        from_=bars_dto.from_date,
        to=bars_dto.to_date,
        bars=[BarDTO(**bar) for bar in bars_dto.bars],
        indicators=bars_dto.indicators,
        source=bars_dto.source,
        as_of=bars_dto.as_of,
        source_updated_at=bars_dto.source_updated_at,
        freshness_status=bars_dto.freshness_status,
        market_session_status=bars_dto.market_session_status,
        market_closed_reason=bars_dto.market_closed_reason,
    )
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=data, meta=meta).model_dump(by_alias=True)


@router.get("/market-data/concepts/hot", response_model=Envelope[HotConceptsData])
async def hot_concepts(
    request: Request,
    market: str = Query(),
    interval: str = Query(),
    from_: date = Query(alias="from"),
    to: date = Query(),
    limit: int = Query(ge=1, le=30),
    as_of: date | None = Query(default=None),
    trace_id: str = Depends(ensure_trace_context),
):  # 首期 top_n≤30 全量返回：契约无 cursor（与 m4 收敛一致），meta.next_cursor 恒 null
    services = request.app.state.analysis_services
    calendar = request.app.state.market_calendar
    if interval != "1d":
        from backend.api.schemas.problem import ProblemError

        raise ProblemError(422, "INTERVAL_NOT_SUPPORTED", f"热门概念首期仅支持 interval=1d：{interval}")

    def _do():
        with SqlAlchemyMarketUow(services._container.sync_session_factory) as uow:
            return MarketDataService(uow, calendar=calendar).get_hot_concepts(market=market, as_of=as_of, limit=limit)

    snapshot_date, items, result_status, freshness = await services.run(_do)
    data = HotConceptsData(
        as_of=snapshot_date,
        algorithm_version=HEAT_ALGORITHM_VERSION,
        result_status=result_status,
        items=[
            HotConceptDTO(
                concept_code=item["concept_code"],
                concept_name=item["concept_name"],
                rank=item["rank"],
                hotness_reason=item["hotness_reason"],
                period_return=item["period_return"],
                daily_changes=(
                    [DailyChangeDTO(**entry) for entry in item["daily_changes"]]
                    if item["daily_changes"]
                    else None
                ),
                updated_at=item["updated_at"],
                bars=[],
            )
            for item in items
        ],
        source="akshare",
        source_updated_at=items[0]["updated_at"] if items else None,
        freshness_status=freshness,
    )
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=data, meta=meta).model_dump()
