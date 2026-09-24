"""Lifecycle audit and manual/broker fill journal API.  No endpoint places an order."""

import base64
import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import and_, or_, select

from backend.api.dependencies import ensure_trace_context
from backend.api.schemas.envelope import Envelope, EnvelopeMeta
from backend.api.schemas.lifecycle import (
    FillConfirmRequest,
    FillCorrectRequest,
    FillMutationData,
    FillVoidRequest,
    OrderFillEventDTO,
    OrderFillListData,
    OrderStatusRequest,
    LifecyclePolicyCreateRequest,
    LifecyclePolicyListData,
    LifecyclePolicyVersionDTO,
    PositionDailyFactDTO,
    PositionDailyFactPageData,
    PositionIntentDTO,
    PositionIntentPageData,
    PositionLifecycleDTO,
    PositionLifecyclePageData,
    SuggestedOrderDTO,
    SuggestedOrderListData,
)
from backend.api.schemas.problem import ProblemError
from backend.modules.investment_workspace.infrastructure.models import PortfolioPosition
from backend.modules.quant_strategy.application.lifecycle_service import LifecycleOrderService, LifecyclePolicyService
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    OrderFillEvent,
    PositionDailyFact,
    PositionExpectation,
    PositionIntent,
    PositionLifecycleState,
    PositionTrailingStop,
    SuggestedOrder,
)

router = APIRouter(prefix="/api/v1", tags=["position-lifecycle"])


def _meta(request: Request) -> EnvelopeMeta:
    return EnvelopeMeta(request_id=request.state.trace_id)


def _encode_cursor(scope: str, created_at: datetime, item_id: uuid.UUID) -> str:
    raw = json.dumps({"s": scope, "t": created_at.isoformat(), "i": str(item_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(raw: str | None, scope: str) -> tuple[datetime, uuid.UUID] | None:
    if raw is None:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode((raw + "=" * (-len(raw) % 4)).encode()).decode())
        if payload.get("s") != scope:
            raise ValueError
        return datetime.fromisoformat(payload["t"]), uuid.UUID(payload["i"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise ProblemError(422, "VALIDATION_ERROR", "cursor 非法或不属于当前审计范围") from None


def _after(model, cursor: tuple[datetime, uuid.UUID] | None):
    if cursor is None:
        return None
    created_at, item_id = cursor
    return or_(model.created_at < created_at, and_(model.created_at == created_at, model.id < item_id))


def _next_cursor(scope: str, rows: list, limit: int) -> tuple[list, str | None]:
    has_more = len(rows) > limit
    page = rows[:limit]
    return page, _encode_cursor(scope, page[-1].created_at, page[-1].id) if has_more and page else None


@router.get("/lifecycle-policy-versions", response_model=Envelope[LifecyclePolicyListData])
async def list_policy_versions(request: Request, trace_id: str = Depends(ensure_trace_context)):
    services = request.app.state.analysis_services

    def _do():
        with services._container.sync_session_factory() as session:
            return [LifecyclePolicyVersionDTO.model_validate(x) for x in LifecyclePolicyService(session).list_versions()]

    items = await services.run(_do)
    return Envelope(data=LifecyclePolicyListData(items=items), meta=_meta(request)).model_dump()


@router.post("/lifecycle-policy-versions", status_code=201, response_model=Envelope[LifecyclePolicyVersionDTO])
async def publish_policy_version(payload: LifecyclePolicyCreateRequest, request: Request, trace_id: str = Depends(ensure_trace_context)):
    services = request.app.state.analysis_services

    def _do():
        with services._container.sync_session_factory() as session:
            item = LifecyclePolicyService(session).publish(**payload.model_dump())
            return LifecyclePolicyVersionDTO.model_validate(item)

    item = await services.run(_do)
    return Envelope(data=item, meta=_meta(request)).model_dump()


@router.get("/portfolios/{portfolio_id}/suggested-orders", response_model=Envelope[SuggestedOrderListData])
async def list_orders(
    portfolio_id: uuid.UUID,
    request: Request,
    position_id: uuid.UUID | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services
    scope = f"orders:{portfolio_id}:{position_id or '*'}"
    after = _decode_cursor(cursor, scope)

    def _do():
        with services._container.sync_session_factory() as session:
            stmt = select(SuggestedOrder).where(SuggestedOrder.portfolio_id == portfolio_id)
            if position_id is not None:
                stmt = stmt.where(SuggestedOrder.position_id == position_id)
            condition = _after(SuggestedOrder, after)
            if condition is not None:
                stmt = stmt.where(condition)
            return list(session.scalars(
                stmt.order_by(SuggestedOrder.created_at.desc(), SuggestedOrder.id.desc()).limit(limit + 1)
            ))

    rows = await services.run(_do)
    rows, next_cursor = _next_cursor(scope, rows, limit)
    items = [SuggestedOrderDTO.model_validate(item) for item in rows]
    meta = EnvelopeMeta(request_id=request.state.trace_id, next_cursor=next_cursor)
    return Envelope(data=SuggestedOrderListData(items=items, next_cursor=next_cursor), meta=meta).model_dump()


@router.get("/suggested-orders/{order_id}/fills", response_model=Envelope[OrderFillListData])
async def list_fills(
    order_id: uuid.UUID,
    request: Request,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services
    scope = f"fills:{order_id}"
    after = _decode_cursor(cursor, scope)

    def _do():
        with services._container.sync_session_factory() as session:
            stmt = select(OrderFillEvent).where(OrderFillEvent.order_id == order_id)
            condition = _after(OrderFillEvent, after)
            if condition is not None:
                stmt = stmt.where(condition)
            return list(session.scalars(
                stmt.order_by(OrderFillEvent.created_at.desc(), OrderFillEvent.id.desc()).limit(limit + 1)
            ))

    rows = await services.run(_do)
    rows, next_cursor = _next_cursor(scope, rows, limit)
    items = [OrderFillEventDTO.model_validate(item) for item in rows]
    meta = EnvelopeMeta(request_id=request.state.trace_id, next_cursor=next_cursor)
    return Envelope(data=OrderFillListData(items=items, next_cursor=next_cursor), meta=meta).model_dump()


@router.get("/portfolios/{portfolio_id}/position-lifecycles", response_model=Envelope[PositionLifecyclePageData])
async def list_position_lifecycles(
    portfolio_id: uuid.UUID,
    request: Request,
    position_id: uuid.UUID | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services
    scope = f"lifecycles:{portfolio_id}:{position_id or '*'}"
    after = _decode_cursor(cursor, scope)

    def _do():
        with services._container.sync_session_factory() as session:
            stmt = (
                select(PositionLifecycleState, PortfolioPosition.quantity, PositionTrailingStop, PositionExpectation)
                .outerjoin(PortfolioPosition, PortfolioPosition.id == PositionLifecycleState.position_id)
                .outerjoin(PositionTrailingStop, PositionTrailingStop.lifecycle_id == PositionLifecycleState.id)
                .outerjoin(PositionExpectation, PositionExpectation.lifecycle_id == PositionLifecycleState.id)
                .where(PositionLifecycleState.portfolio_id == portfolio_id)
            )
            if position_id is not None:
                stmt = stmt.where(PositionLifecycleState.position_id == position_id)
            condition = _after(PositionLifecycleState, after)
            if condition is not None:
                stmt = stmt.where(condition)
            return list(session.execute(
                stmt.order_by(PositionLifecycleState.created_at.desc(), PositionLifecycleState.id.desc())
                .limit(limit + 1)
            ))

    rows = await services.run(_do)
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = (
        _encode_cursor(scope, rows[-1][0].created_at, rows[-1][0].id) if has_more and rows else None
    )
    items = []
    for lifecycle, actual_shares, trailing, expectation in rows:
        items.append(PositionLifecycleDTO(
            id=lifecycle.id, portfolio_id=lifecycle.portfolio_id, position_id=lifecycle.position_id,
            market=lifecycle.market, symbol=lifecycle.symbol,
            strategy_version_id=lifecycle.strategy_version_id,
            lifecycle_policy_version_id=lifecycle.lifecycle_policy_version_id,
            actual_shares=actual_shares or 0,
            initial_fill_price=lifecycle.initial_fill_price,
            initial_stop_price=lifecycle.initial_stop_price,
            risk_capacity_shares=lifecycle.risk_capacity_shares,
            target_exposure_pct=lifecycle.target_exposure_pct,
            target_shares=lifecycle.target_shares, phase=lifecycle.phase,
            profit_take_price=lifecycle.profit_take_price,
            profit_target_reached=lifecycle.profit_target_reached,
            confirmation_completed=lifecycle.confirmation_completed,
            profit_trim_completed=lifecycle.profit_trim_completed,
            arc_neckline_price=lifecycle.arc_neckline_price,
            active_stop_price=trailing.active_stop_price if trailing else lifecycle.initial_stop_price,
            high_water_mark=trailing.high_water_mark if trailing else None,
            trailing_phase=trailing.phase if trailing else None,
            expectation_status=expectation.status if expectation else None,
            expectation_observed_days=expectation.observed_trading_days if expectation else None,
            expectation_window_days=expectation.window_trading_days if expectation else None,
            last_processed_trade_date=lifecycle.last_processed_trade_date,
            state_version=lifecycle.state_version, closed_at=lifecycle.closed_at,
            created_at=lifecycle.created_at, updated_at=lifecycle.updated_at,
        ))
    meta = EnvelopeMeta(request_id=request.state.trace_id, next_cursor=next_cursor)
    return Envelope(data=PositionLifecyclePageData(items=items, next_cursor=next_cursor), meta=meta).model_dump()


@router.get("/position-lifecycles/{lifecycle_id}/intents", response_model=Envelope[PositionIntentPageData])
async def list_position_intents(
    lifecycle_id: uuid.UUID,
    request: Request,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services
    scope = f"intents:{lifecycle_id}"
    after = _decode_cursor(cursor, scope)

    def _do():
        with services._container.sync_session_factory() as session:
            stmt = select(PositionIntent).where(PositionIntent.lifecycle_id == lifecycle_id)
            condition = _after(PositionIntent, after)
            if condition is not None:
                stmt = stmt.where(condition)
            return list(session.scalars(
                stmt.order_by(PositionIntent.created_at.desc(), PositionIntent.id.desc()).limit(limit + 1)
            ))

    rows = await services.run(_do)
    rows, next_cursor = _next_cursor(scope, rows, limit)
    items = [PositionIntentDTO.model_validate(row) for row in rows]
    meta = EnvelopeMeta(request_id=request.state.trace_id, next_cursor=next_cursor)
    return Envelope(data=PositionIntentPageData(items=items, next_cursor=next_cursor), meta=meta).model_dump()


@router.get("/position-lifecycles/{lifecycle_id}/daily-facts", response_model=Envelope[PositionDailyFactPageData])
async def list_position_daily_facts(
    lifecycle_id: uuid.UUID,
    request: Request,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    trace_id: str = Depends(ensure_trace_context),
):
    services = request.app.state.analysis_services
    scope = f"daily-facts:{lifecycle_id}"
    after = _decode_cursor(cursor, scope)

    def _do():
        with services._container.sync_session_factory() as session:
            stmt = select(PositionDailyFact).where(PositionDailyFact.lifecycle_id == lifecycle_id)
            condition = _after(PositionDailyFact, after)
            if condition is not None:
                stmt = stmt.where(condition)
            return list(session.scalars(
                stmt.order_by(PositionDailyFact.created_at.desc(), PositionDailyFact.id.desc()).limit(limit + 1)
            ))

    rows = await services.run(_do)
    rows, next_cursor = _next_cursor(scope, rows, limit)
    items = [PositionDailyFactDTO.model_validate(row) for row in rows]
    meta = EnvelopeMeta(request_id=request.state.trace_id, next_cursor=next_cursor)
    return Envelope(data=PositionDailyFactPageData(items=items, next_cursor=next_cursor), meta=meta).model_dump()


@router.post("/suggested-orders/{order_id}/fills", status_code=201, response_model=Envelope[FillMutationData])
async def confirm_fill(order_id: uuid.UUID, payload: FillConfirmRequest, request: Request, trace_id: str = Depends(ensure_trace_context)):
    services = request.app.state.analysis_services

    def _do():
        with services._container.sync_session_factory() as session:
            fill, order = LifecycleOrderService(session).confirm_fill(order_id, **payload.model_dump())
            return FillMutationData(fill=OrderFillEventDTO.model_validate(fill), order=SuggestedOrderDTO.model_validate(order))

    data = await services.run(_do)
    return Envelope(data=data, meta=_meta(request)).model_dump()


@router.post("/order-fills/{fill_id}/correct", status_code=201, response_model=Envelope[FillMutationData])
async def correct_fill(fill_id: uuid.UUID, payload: FillCorrectRequest, request: Request, trace_id: str = Depends(ensure_trace_context)):
    services = request.app.state.analysis_services

    def _do():
        with services._container.sync_session_factory() as session:
            fill, order = LifecycleOrderService(session).correct_fill(fill_id, **payload.model_dump())
            return FillMutationData(fill=OrderFillEventDTO.model_validate(fill), order=SuggestedOrderDTO.model_validate(order))

    data = await services.run(_do)
    return Envelope(data=data, meta=_meta(request)).model_dump()


@router.post("/order-fills/{fill_id}/void", status_code=201, response_model=Envelope[FillMutationData])
async def void_fill(fill_id: uuid.UUID, payload: FillVoidRequest, request: Request, trace_id: str = Depends(ensure_trace_context)):
    services = request.app.state.analysis_services

    def _do():
        with services._container.sync_session_factory() as session:
            fill, order = LifecycleOrderService(session).void_fill(fill_id, **payload.model_dump())
            return FillMutationData(fill=OrderFillEventDTO.model_validate(fill), order=SuggestedOrderDTO.model_validate(order))

    data = await services.run(_do)
    return Envelope(data=data, meta=_meta(request)).model_dump()


@router.patch("/suggested-orders/{order_id}/status", response_model=Envelope[SuggestedOrderDTO])
async def set_order_status(order_id: uuid.UUID, payload: OrderStatusRequest, request: Request, trace_id: str = Depends(ensure_trace_context)):
    services = request.app.state.analysis_services

    def _do():
        with services._container.sync_session_factory() as session:
            order = LifecycleOrderService(session).set_order_status(order_id, **payload.model_dump())
            return SuggestedOrderDTO.model_validate(order)

    data = await services.run(_do)
    return Envelope(data=data, meta=_meta(request)).model_dump()
