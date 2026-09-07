"""组合路由（§2.6.3：组合/持仓 CRUD、upsert、revision 并发）。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request

from backend.api.cursors import decode_cursor, encode_cursor
from backend.api.dependencies import ensure_trace_context
from backend.api.schemas.envelope import DeleteResultData, Envelope, EnvelopeMeta
from backend.api.schemas.problem import ProblemError
from backend.api.schemas.workspace import (
    PortfolioCreateRequest,
    PortfolioDTO,
    PortfolioListData,
    PortfolioPositionDTO,
    PortfolioPositionMutationData,
    PortfolioPositionsData,
    PortfolioUpdateRequest,
    PositionUpsertRequest,
)
from backend.modules.investment_workspace.application.portfolios import PortfolioService
from backend.modules.investment_workspace.domain.values import InstrumentRef
from backend.modules.investment_workspace.infrastructure.repositories import (
    SqlAlchemyWorkspaceUnitOfWork,
)
from backend.api.dependencies import open_workspace_uow as _open_uow

router = APIRouter(prefix="/api/v1", tags=["portfolios"])


@router.get("/portfolios", response_model=Envelope[PortfolioListData])
async def list_portfolios(
    request: Request,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services
    try:
        decoded = decode_cursor(cursor) if cursor else None
    except (ValueError, TypeError, KeyError):
        raise ProblemError(422, "VALIDATION_ERROR", "cursor 非法") from None

    def _do():
        with _open_uow(services) as uow:
            return PortfolioService(uow).list(limit=limit, before=decoded)

    items, next_tuple = await services.run(_do)
    next_cursor = encode_cursor(next_tuple[0], next_tuple[1]) if next_tuple else None
    data = PortfolioListData(items=[PortfolioDTO(**item.__dict__) for item in items])
    meta = EnvelopeMeta(request_id=request.state.trace_id, next_cursor=next_cursor)
    return Envelope(data=data, meta=meta).model_dump()


@router.post("/portfolios", status_code=201, response_model=Envelope[PortfolioDTO])
async def create_portfolio(
    payload: PortfolioCreateRequest,
    request: Request,
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services

    def _do():
        with _open_uow(services) as uow:
            return PortfolioService(uow).create(payload.name)

    dto = await services.run(_do)
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=PortfolioDTO(**dto.__dict__), meta=meta).model_dump()


@router.patch("/portfolios/{portfolio_id}", response_model=Envelope[PortfolioDTO])
async def rename_portfolio(
    portfolio_id: uuid.UUID,
    payload: PortfolioUpdateRequest,
    request: Request,
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services

    def _do():
        with _open_uow(services) as uow:
            return PortfolioService(uow).rename(portfolio_id, payload.name, payload.expected_version)

    dto = await services.run(_do)
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=PortfolioDTO(**dto.__dict__), meta=meta).model_dump()


@router.delete("/portfolios/{portfolio_id}", response_model=Envelope[DeleteResultData])
async def delete_portfolio(
    portfolio_id: uuid.UUID,
    request: Request,
    expected_version: int = Query(ge=1),
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services

    def _do():
        with _open_uow(services) as uow:
            PortfolioService(uow).delete(portfolio_id, expected_version)
            return None

    await services.run(_do)
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=DeleteResultData(resource_id=str(portfolio_id)), meta=meta).model_dump()


@router.get("/portfolios/{portfolio_id}/positions", response_model=Envelope[PortfolioPositionsData])
async def list_positions(
    portfolio_id: uuid.UUID, request: Request, trace_id: str = Depends(ensure_trace_context)
):  # 组内列表为本地小列表：全量返回（契约无 cursor/limit），next_cursor 恒 null
    services = request.app.state.analysis_services

    def _do():
        with _open_uow(services) as uow:
            return PortfolioService(uow).list_positions(portfolio_id)

    items, revision = await services.run(_do)
    data = PortfolioPositionsData(
        portfolio_id=portfolio_id,
        portfolio_revision=revision,
        items=[PortfolioPositionDTO(**item.__dict__) for item in items],
    )
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=data, meta=meta).model_dump()


@router.put("/portfolios/{portfolio_id}/positions/{market}/{symbol}", response_model=Envelope[PortfolioPositionMutationData])
async def upsert_position(
    portfolio_id: uuid.UUID,
    market: str,
    symbol: str,
    payload: PositionUpsertRequest,
    request: Request,
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services
    instrument = InstrumentRef(market=market, symbol=symbol)

    def _do():
        with _open_uow(services) as uow:
            return PortfolioService(uow).upsert_position(
                portfolio_id, instrument, payload.quantity, payload.average_cost,
                payload.expected_portfolio_revision,
            )

    result = await services.run(_do)
    data = PortfolioPositionMutationData(
        position=PortfolioPositionDTO(**result.position.__dict__),
        portfolio_revision=result.portfolio_revision,
    )
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=data, meta=meta).model_dump()


@router.delete("/portfolios/{portfolio_id}/positions/{market}/{symbol}", response_model=Envelope[DeleteResultData])
async def remove_position(
    portfolio_id: uuid.UUID,
    market: str,
    symbol: str,
    request: Request,
    expected_portfolio_revision: int = Query(ge=1),
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services
    instrument = InstrumentRef(market=market, symbol=symbol)

    def _do():
        with _open_uow(services) as uow:
            PortfolioService(uow).remove_position(portfolio_id, instrument, expected_portfolio_revision)
            return None

    await services.run(_do)
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    # resource_id 指向被删除的持仓自身（复合键），而非父组合
    return Envelope(data=DeleteResultData(resource_id=f"{market}:{symbol}"), meta=meta).model_dump()
