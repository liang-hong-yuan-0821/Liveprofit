"""自选路由（§2.6.3：分组/标的 CRUD、批量排序、revision 并发）。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request

from backend.api.cursors import decode_cursor, encode_cursor
from backend.api.dependencies import ensure_trace_context, open_workspace_uow
from backend.api.schemas.envelope import DeleteResultData, Envelope, EnvelopeMeta
from backend.api.schemas.problem import ProblemError
from backend.api.schemas.workspace import (
    WatchlistCreateRequest,
    WatchlistDTO,
    WatchlistItemCreateRequest,
    WatchlistItemDTO,
    WatchlistItemMutationData,
    WatchlistItemOrderUpdateRequest,
    WatchlistItemsData,
    WatchlistListData,
    WatchlistOrderData,
    WatchlistUpdateRequest,
)
from backend.modules.investment_workspace.application.watchlists import WatchlistService
from backend.modules.investment_workspace.domain.values import InstrumentRef

router = APIRouter(prefix="/api/v1", tags=["watchlists"])

_open_uow = open_workspace_uow


@router.get("/watchlists", response_model=Envelope[WatchlistListData])
async def list_watchlists(
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
            return WatchlistService(uow).list(limit=limit, before=decoded)

    items, next_tuple = await services.run(_do)
    next_cursor = encode_cursor(next_tuple[0], next_tuple[1]) if next_tuple else None
    data = WatchlistListData(items=[WatchlistDTO(**item.__dict__) for item in items])
    meta = EnvelopeMeta(request_id=request.state.trace_id, next_cursor=next_cursor)
    return Envelope(data=data, meta=meta).model_dump()


@router.post("/watchlists", status_code=201, response_model=Envelope[WatchlistDTO])
async def create_watchlist(
    payload: WatchlistCreateRequest,
    request: Request,
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services

    def _do():
        with _open_uow(services) as uow:
            return WatchlistService(uow).create(payload.name)

    dto = await services.run(_do)
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=WatchlistDTO(**dto.__dict__), meta=meta).model_dump()


@router.patch("/watchlists/{watchlist_id}", response_model=Envelope[WatchlistDTO])
async def rename_watchlist(
    watchlist_id: uuid.UUID,
    payload: WatchlistUpdateRequest,
    request: Request,
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services

    def _do():
        with _open_uow(services) as uow:
            return WatchlistService(uow).rename(watchlist_id, payload.name, payload.expected_version)

    dto = await services.run(_do)
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=WatchlistDTO(**dto.__dict__), meta=meta).model_dump()


@router.delete("/watchlists/{watchlist_id}", response_model=Envelope[DeleteResultData])
async def delete_watchlist(
    watchlist_id: uuid.UUID,
    request: Request,
    expected_version: int = Query(ge=1),
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services

    def _do():
        with _open_uow(services) as uow:
            WatchlistService(uow).delete(watchlist_id, expected_version)
            return None

    await services.run(_do)
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=DeleteResultData(resource_id=str(watchlist_id)), meta=meta).model_dump()


@router.get("/watchlists/{watchlist_id}/items", response_model=Envelope[WatchlistItemsData])
async def list_watchlist_items(
    watchlist_id: uuid.UUID, request: Request, trace_id: str = Depends(ensure_trace_context)
):  # 组内列表为本地小列表：全量返回（契约无 cursor/limit），next_cursor 恒 null
    services = request.app.state.analysis_services

    def _do():
        with _open_uow(services) as uow:
            return WatchlistService(uow).list_items(watchlist_id)

    items, revision = await services.run(_do)
    data = WatchlistItemsData(
        watchlist_id=watchlist_id,
        watchlist_revision=revision,
        items=[WatchlistItemDTO(**item.__dict__) for item in items],
    )
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=data, meta=meta).model_dump()


@router.post("/watchlists/{watchlist_id}/items", status_code=201, response_model=Envelope[WatchlistItemMutationData])
async def add_watchlist_item(
    watchlist_id: uuid.UUID,
    payload: WatchlistItemCreateRequest,
    request: Request,
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services

    def _do():
        with _open_uow(services) as uow:
            return WatchlistService(uow).add_item(
                watchlist_id, payload.market, payload.symbol, payload.expected_watchlist_revision
            )

    result = await services.run(_do)
    data = WatchlistItemMutationData(
        item=WatchlistItemDTO(**result.item.__dict__) if result.item else None,
        watchlist_revision=result.watchlist_revision,
    )
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=data, meta=meta).model_dump()


@router.put("/watchlists/{watchlist_id}/items/order", response_model=Envelope[WatchlistOrderData])
async def reorder_watchlist_items(
    watchlist_id: uuid.UUID,
    payload: WatchlistItemOrderUpdateRequest,
    request: Request,
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services
    ordered = [InstrumentRef(market=e.market, symbol=e.symbol) for e in payload.items]

    def _do():
        with _open_uow(services) as uow:
            return WatchlistService(uow).reorder(watchlist_id, payload.expected_watchlist_revision, ordered)

    items, revision = await services.run(_do)
    data = WatchlistOrderData(
        watchlist_id=watchlist_id,
        watchlist_revision=revision,
        items=[WatchlistItemDTO(**item.__dict__) for item in items],
    )
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=data, meta=meta).model_dump()


@router.delete("/watchlists/{watchlist_id}/items/{item_id}", response_model=Envelope[DeleteResultData])
async def remove_watchlist_item(
    watchlist_id: uuid.UUID,
    item_id: uuid.UUID,
    request: Request,
    expected_watchlist_revision: int = Query(ge=1),
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services

    def _do():
        with _open_uow(services) as uow:
            WatchlistService(uow).remove_item(watchlist_id, item_id, expected_watchlist_revision)
            return None

    await services.run(_do)
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=DeleteResultData(resource_id=str(item_id)), meta=meta).model_dump()
