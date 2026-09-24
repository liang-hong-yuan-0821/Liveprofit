"""市场数据路由（§2.6.1：指数 K 线与热门概念——目录端点已删，前端写死清单）。

market_conn 经 request.app.state 注入（main.py lifespan 赋值）；构造不再传 uow
（决策 13：SQLAlchemy 侧无读模型）。
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request

from backend.api.dependencies import ensure_trace_context
from backend.api.schemas.envelope import Envelope, EnvelopeMeta
from backend.api.schemas.market import (
    BarDTO,
    BarsData,
    BarsAssetInfo,
    ConceptTreeNodeDTO,
    ConceptTreeData,
    DailyChangeDTO,
    HotConceptDTO,
    HotConceptsData,
    TrendPointDTO,
    TrendSeriesDTO,
    TrendsData,
)
from backend.modules.market_data.application.service import (
    BOARD_INDEXES,
    CAP_TIER_INDEXES,
    MarketDataService,
)

router = APIRouter(prefix="/api/v1", tags=["market-data"])

HEAT_ALGORITHM_VERSION = "heat_v1"


def _bars_data(bars_dto, market: str) -> BarsData:
    """BarsDTO → BarsData（indices/stocks 两个 get_bars 端点共用）。"""
    return BarsData(
        asset=BarsAssetInfo(
            # market = 请求形参回显（非表字段），仅回显不参与查询与校验
            market=market,
            symbol=bars_dto.asset.symbol,
            name=bars_dto.asset.name,
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


async def _run_get_bars(request: Request, symbol: str, market: str, interval: str,
                       from_: date, to: date, factor_policy: str = "ensure"):
    """indices/stocks bars 端点共用执行体（get_bars 已放宽 stock，板块概念Treemap方案 3.3）。

    stock_factor_fetcher 经 app.state 注入（测试覆盖点；生产不赋值 → 服务层
    默认 default_stock_factor_fetcher，m7 个股因子按需拉取）。
    """
    services = request.app.state.analysis_services
    calendar = request.app.state.market_calendar
    market_conn = request.app.state.market_conn
    stock_factor_fetcher = getattr(request.app.state, "stock_factor_fetcher", None)
    from backend.api.routers.market_refresh import service_for
    refresh = service_for(request)
    factor_gate = getattr(request.app.state, "market_factor_gate", refresh.store.factor_gate)

    def _do():
        return MarketDataService(
            calendar=calendar,
            clock=getattr(request.app.state, "market_clock", None),
            market_conn=market_conn,
            stock_factor_fetcher=stock_factor_fetcher,
            factor_gate=factor_gate,
            factor_changed=refresh.store.changed,
            publish_lag_seconds=refresh.policy.publish_lag_seconds,
        ).get_bars(
            market=market, symbol=symbol, interval=interval, from_date=from_, to_date=to,
            factor_policy=factor_policy,
        )

    return await services.run(_do)


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
    bars_dto = await _run_get_bars(request, symbol, market, interval, from_, to)
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=_bars_data(bars_dto, market), meta=meta).model_dump(by_alias=True)


@router.get("/market-data/stocks/{symbol}/bars", response_model=Envelope[BarsData])
async def stock_bars(
    symbol: str,
    request: Request,
    market: str = Query(),
    interval: str = Query(),
    from_: date = Query(alias="from"),
    to: date = Query(),
    factor_policy: Literal["ensure", "cache_only"] = Query(default="ensure"),
    trace_id: str = Depends(ensure_trace_context),
):
    """个股 K 线（板块概念Treemap方案 3.3）：复用 get_bars（instrument_type 放宽 stock）。"""
    bars_dto = await _run_get_bars(request, symbol, market, interval, from_, to, factor_policy)
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=_bars_data(bars_dto, market), meta=meta).model_dump(by_alias=True)


@router.get("/market-data/concepts/{sector_code}/bars", response_model=Envelope[BarsData])
async def sector_bars(
    sector_code: str,
    request: Request,
    market: str = Query(),
    source: str = Query(default="dc"),
    interval: str = Query(),
    from_: date = Query(alias="from"),
    to: date = Query(),
    trace_id: str = Depends(ensure_trace_context),
):
    """概念 K 线（板块概念Treemap方案 3.3）：读 market.sector_daily（source 缺省 'dc'）。"""
    services = request.app.state.analysis_services
    calendar = request.app.state.market_calendar
    market_conn = request.app.state.market_conn
    if interval != "1d":
        from backend.api.schemas.problem import ProblemError

        raise ProblemError(422, "INTERVAL_NOT_SUPPORTED", f"概念 K 线首期仅支持 interval=1d：{interval}")

    def _do():
        return MarketDataService(calendar=calendar, market_conn=market_conn,
                                 clock=getattr(request.app.state, "market_clock", None),
                                 publish_lag_seconds=request.app.state.settings.market_refresh.publish_lag_seconds).get_sector_bars(
            market=market, source=source, sector_code=sector_code,
            from_date=from_, to_date=to,
        )

    bars_dto = await services.run(_do)
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=_bars_data(bars_dto, market), meta=meta).model_dump(by_alias=True)


@router.get("/market-data/concepts/tree", response_model=Envelope[ConceptTreeData])
async def concept_tree(
    request: Request,
    market: str = Query(),
    interval: str = Query(),
    limit: int = Query(ge=1, le=30),
    as_of: date | None = Query(default=None),
    trace_id: str = Depends(ensure_trace_context),
):
    """Latest actual sector date by default; explicit as_of reads exactly that day."""
    services = request.app.state.analysis_services
    calendar = request.app.state.market_calendar
    market_conn = request.app.state.market_conn
    if interval != "1d":
        from backend.api.schemas.problem import ProblemError

        raise ProblemError(422, "INTERVAL_NOT_SUPPORTED", f"概念树首期仅支持 interval=1d：{interval}")

    def _do():
        return MarketDataService(calendar=calendar, market_conn=market_conn,
                                 clock=getattr(request.app.state, "market_clock", None),
                                 publish_lag_seconds=request.app.state.settings.market_refresh.publish_lag_seconds).get_concept_tree(
            market=market, as_of=as_of, limit=limit)

    result = await services.run(_do)
    data = ConceptTreeData(
        as_of=result.as_of, requested_as_of=result.requested_as_of,
        date_mode=result.date_mode, coverage=result.coverage,
        algorithm_version=HEAT_ALGORITHM_VERSION,
        result_status=result.result_status,
        items=[ConceptTreeNodeDTO(**item) for item in result.items],
        source="dc",  # 与热度口径一致（m5 定稿同 hot 端点）
        source_updated_at=result.source_updated_at,
        freshness_status=result.freshness_status,
    )
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=data, meta=meta).model_dump()


@router.get("/market-data/concepts/hot", response_model=Envelope[HotConceptsData])
async def hot_concepts(
    request: Request,
    market: str = Query(),
    interval: str = Query(),
    limit: int = Query(ge=1, le=30),
    as_of: date | None = Query(default=None),
    trace_id: str = Depends(ensure_trace_context),
):  # 首期 top_n≤30 全量返回：契约无 cursor（与 m4 收敛一致），meta.next_cursor 恒 null
    services = request.app.state.analysis_services
    calendar = request.app.state.market_calendar
    market_conn = request.app.state.market_conn
    if interval != "1d":
        from backend.api.schemas.problem import ProblemError

        raise ProblemError(422, "INTERVAL_NOT_SUPPORTED", f"热门概念首期仅支持 interval=1d：{interval}")

    def _do():
        return MarketDataService(calendar=calendar, market_conn=market_conn,
                                 clock=getattr(request.app.state, "market_clock", None),
                                 publish_lag_seconds=request.app.state.settings.market_refresh.publish_lag_seconds).get_hot_concepts(
            market=market, as_of=as_of, limit=limit)

    result = await services.run(_do)
    data = HotConceptsData(
        as_of=result.as_of, requested_as_of=result.requested_as_of,
        date_mode=result.date_mode, coverage=result.coverage,
        algorithm_version=HEAT_ALGORITHM_VERSION,
        result_status=result.result_status,
        items=[
            HotConceptDTO(
                sector_code=item["sector_code"],
                sector_name=item["sector_name"],
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
                heat_window_rows=item["heat_window_rows"],
            )
            for item in result.items
        ],
        source="dc",  # 与热度口径一致（m5 定稿：原硬编码 "akshare" 随改）
        source_updated_at=result.source_updated_at,
        freshness_status=result.freshness_status,
    )
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=data, meta=meta).model_dump()


def _trends_data(trends_dto) -> TrendsData:
    """TrendsDTO → TrendsData（两趋势端点共用，同 _bars_data 先例）。"""
    return TrendsData(
        from_=trends_dto.from_date, to=trends_dto.to_date,
        series=[TrendSeriesDTO(symbol=s.symbol, name=s.name,
                               points=[TrendPointDTO(**point) for point in s.points])
                for s in trends_dto.series],
        as_of=trends_dto.as_of,
        freshness_status=trends_dto.freshness_status,
    )


async def _run_trends(request: Request, indexes: list[tuple[str, str]],
                      from_: date, to: date):
    """趋势端点共用执行体（sector_bars 同款局部 _do 闭包；_run_get_bars 绑定
    get_bars，不可复用）。"""
    services = request.app.state.analysis_services
    calendar = request.app.state.market_calendar
    market_conn = request.app.state.market_conn

    def _do():
        return MarketDataService(calendar=calendar, market_conn=market_conn,
                                 clock=getattr(request.app.state, "market_clock", None),
                                 publish_lag_seconds=request.app.state.settings.market_refresh.publish_lag_seconds).get_index_trends(
            indexes=indexes, from_date=from_, to_date=to)

    return await services.run(_do)


@router.get("/market-data/trends/cap-tiers", response_model=Envelope[TrendsData])
async def cap_tier_trends(
    request: Request,
    from_: date = Query(alias="from"),
    to: date = Query(),
    trace_id: str = Depends(ensure_trace_context),
):
    """市值分层趋势（趋势对比面板方案 4.2）：沪深300/中证500/中证1000/中证2000
    四序列，前端按共同首日=100 归一。"""
    trends_dto = await _run_trends(request, CAP_TIER_INDEXES, from_, to)
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=_trends_data(trends_dto), meta=meta).model_dump(by_alias=True)


@router.get("/market-data/trends/boards", response_model=Envelope[TrendsData])
async def board_trends(
    request: Request,
    from_: date = Query(alias="from"),
    to: date = Query(),
    trace_id: str = Depends(ensure_trace_context),
):
    """市场板趋势（趋势对比面板方案 4.2）：上证综指/创业板指/科创50 官方指数
    三序列，前端按共同首日=100 归一。"""
    trends_dto = await _run_trends(request, BOARD_INDEXES, from_, to)
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=_trends_data(trends_dto), meta=meta).model_dump(by_alias=True)
