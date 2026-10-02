"""Freeze the complete drawdown-command scope before any business mutation.

This is a selection contract, not a writer or a risk/source authorization. The
caller retains the transaction; a future DB coordinator must independently
derive and validate the same scope rather than trust a client manifest.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.modules.investment_workspace.infrastructure.models import PortfolioPosition
from backend.modules.quant_strategy.application.errors import (
    FillValidationError,
    LifecycleInvalidStateError,
    LifecycleNotFoundError,
    LifecycleRevisionConflictError,
)
from backend.modules.quant_strategy.application.lifecycle_command_selection import (
    RESULTS,
    _canonical,
)
from backend.modules.quant_strategy.application.planning_account import lock_portfolio
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    LifecyclePolicyVersion,
    PositionExpectation,
    PositionIntent,
    PositionLifecycleState,
    PositionTrailingStop,
    SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.models import QuantStrategyVersion

ACTIVE_ORDERS = frozenset({"PROPOSED", "EXECUTING", "PARTIALLY_FILLED", "RECONCILIATION_REQUIRED"})
ACTIVE_INTENTS = frozenset({"ACTIVE", "EXECUTING", "RECONCILIATION_REQUIRED"})


@dataclass(frozen=True)
class PortfolioDrawdownCommandRequest:
    portfolio_id: uuid.UUID
    valuation_date: date
    request_key: str

    def __post_init__(self):
        if not isinstance(self.portfolio_id, uuid.UUID):
            raise FillValidationError("组合ID必须为UUID")
        if type(self.valuation_date) is not date:
            raise FillValidationError("回撤命令必须指定估值日")
        if not isinstance(self.request_key, str) or not self.request_key.strip() or len(self.request_key) > 128:
            raise FillValidationError("命令request_key长度必须为1..128且非空白")

    def canonical_request(self):
        return _canonical({"schema_version": 1, "command_kind": "PORTFOLIO_DRAWDOWN",
                           "portfolio_id": str(self.portfolio_id),
                           "valuation_date": self.valuation_date.isoformat(), "request_key": self.request_key})


@dataclass(frozen=True)
class PortfolioScopeRow:
    entity_id: uuid.UUID
    revision: int | None
    before_json: str


@dataclass(frozen=True)
class PortfolioLifecycleScope:
    lifecycle_id: uuid.UUID
    operation_id: uuid.UUID
    state_version: int
    before_json: str
    orders: tuple[PortfolioScopeRow, ...]
    intents: tuple[PortfolioScopeRow, ...]
    stops: tuple[PortfolioScopeRow, ...]
    expectations: tuple[PortfolioScopeRow, ...]


@dataclass(frozen=True)
class SelectedPortfolioDrawdownCommand:
    portfolio_id: uuid.UUID
    canonical_request: bytes
    request_hash: str
    portfolio_before_json: str
    lifecycles: tuple[PortfolioLifecycleScope, ...]
    unbound_orders: tuple[PortfolioScopeRow, ...]
    positions: tuple[PortfolioScopeRow, ...]
    selector_version: str = "portfolio-drawdown:v1"
    command_kind: str = "PORTFOLIO_DRAWDOWN"
    scope_only: bool = True

    def manifest(self):
        return {"steps": [{"operation_id": str(row.operation_id), "lifecycle_id": str(row.lifecycle_id),
                           "command_step_no": index, "operation_kind": "INTENT_OR_ORDER_CHANGED"}
                          for index, row in enumerate(self.lifecycles, 1)],
                "unbound_orders": [{"order_id": str(row.entity_id), "original_revision": row.revision,
                                    "allowed_results": list(RESULTS)} for row in self.unbound_orders]}

    def manifest_hash(self):
        return hashlib.sha256(_canonical(self.manifest())).hexdigest()

    @property
    def has_history_participants(self):
        # An account with only unmanaged positions still has account evidence,
        # but must not be represented by an empty LIVE lifecycle command.
        return bool(self.lifecycles or self.unbound_orders)


def _lock_all(session, model, condition):
    return tuple(session.scalars(select(model).where(condition).order_by(model.id).with_for_update()
                                 .execution_options(populate_existing=True)).all())


def _row(session, model, row, revision=None):
    # Fixed model table names; preserve original SQL numeric/JSON types and UTC
    # time representation. Do not rebuild original text from ORM values.
    raw = session.scalar(text(f"SELECT row_to_json(r)::text FROM {model.__tablename__} r WHERE id=:id"),
                         {"id": row.id})
    if raw is None:
        raise LifecycleRevisionConflictError("组合参与者在冻结时消失，须整事务重试")
    return PortfolioScopeRow(row.id, revision, raw)


def _inventory(session, portfolio_id):
    return (
        tuple(session.execute(select(PositionLifecycleState.id, PositionLifecycleState.strategy_version_id,
                                     PositionLifecycleState.lifecycle_policy_version_id)
                              .where(PositionLifecycleState.portfolio_id == portfolio_id)
                              .order_by(PositionLifecycleState.id)).tuples()),
        tuple(session.scalars(select(SuggestedOrder.id).where(SuggestedOrder.portfolio_id == portfolio_id)
                              .order_by(SuggestedOrder.id))),
        tuple(session.scalars(select(PortfolioPosition.id).where(PortfolioPosition.portfolio_id == portfolio_id)
                              .order_by(PortfolioPosition.id))),
    )


def select_portfolio_drawdown_command(session: Session, request: PortfolioDrawdownCommandRequest):
    """Freeze open lifecycles and every pending BUY, including closed owners.

    All existing account lifecycle/order rows are locked, even inactive ones:
    otherwise a legacy writer could reactivate an excluded row after selection.
    Parent FK locks stop new members; child locks stop existing-row drift. This
    conservative scope cost must be measured before production deployment.
    Existing commands must be replay-checked by the future coordinator first.
    """
    if not isinstance(request, PortfolioDrawdownCommandRequest):
        raise FillValidationError("需要类型化的原始组合回撤请求")
    if session.new or session.dirty or session.deleted:
        raise LifecycleInvalidStateError("组合参与者必须在业务修改前选择；Session存在未提交变更")
    with session.no_autoflush:
        if session.scalar(text("SELECT current_setting('transaction_isolation')")) != "read committed":
            raise LifecycleInvalidStateError("组合全集重查须使用READ COMMITTED事务")
        if session.scalar(text("SELECT pg_current_xact_id_if_assigned()::text")) is not None:
            raise LifecycleInvalidStateError("参与者选择前事务已有写入或行锁，须回滚重试")
        # Fixed local representation, never a source visibility timestamp.
        session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        session.execute(text("SET LOCAL DateStyle = 'ISO, YMD'"))
        if lock_portfolio(session, request.portfolio_id) is None:
            raise LifecycleNotFoundError("回撤命令组合不存在")
        inventory = _inventory(session, request.portfolio_id)
        owners, order_ids, position_ids = inventory
        versions = _lock_all(session, QuantStrategyVersion, QuantStrategyVersion.id.in_({r[1] for r in owners}))
        policies = _lock_all(session, LifecyclePolicyVersion, LifecyclePolicyVersion.id.in_({r[2] for r in owners}))
        if len(versions) != len({r[1] for r in owners}) or len(policies) != len({r[2] for r in owners}):
            raise LifecycleInvalidStateError("组合生命周期冻结策略或政策不存在")
        lifecycles = _lock_all(session, PositionLifecycleState, PositionLifecycleState.portfolio_id == request.portfolio_id)
        if tuple((r.id, r.strategy_version_id, r.lifecycle_policy_version_id) for r in lifecycles) != owners:
            raise LifecycleRevisionConflictError("组合生命周期归属在取锁时变化，须整事务重试")
        lifecycle_ids = {r.id for r in lifecycles}
        # A corrupt foreign-account reference is not another legitimate scope:
        # discovering it cannot justify acquiring a second portfolio out of order.
        if session.scalar(select(SuggestedOrder.id).where(
                SuggestedOrder.lifecycle_id.in_(lifecycle_ids),
                SuggestedOrder.portfolio_id != request.portfolio_id).limit(1)) is not None:
            raise LifecycleInvalidStateError("其他组合订单引用当前生命周期，归属不一致")
        orders = _lock_all(session, SuggestedOrder, SuggestedOrder.portfolio_id == request.portfolio_id)
        stops = _lock_all(session, PositionTrailingStop, PositionTrailingStop.lifecycle_id.in_(lifecycle_ids))
        expectations = _lock_all(session, PositionExpectation, PositionExpectation.lifecycle_id.in_(lifecycle_ids))
        intents = _lock_all(session, PositionIntent, PositionIntent.lifecycle_id.in_(lifecycle_ids))
        positions = _lock_all(session, PortfolioPosition, PortfolioPosition.portfolio_id == request.portfolio_id)
        if (_inventory(session, request.portfolio_id) != inventory or tuple(r.id for r in orders) != order_ids
                or tuple(r.id for r in positions) != position_ids):
            raise LifecycleRevisionConflictError("组合参与者在取锁时变化，须整事务重试")
        if session.scalar(text("SELECT pg_current_xact_id_if_assigned()::text")) is None:
            raise LifecycleInvalidStateError("组合选择必须保留显式事务及行锁，不能使用AUTOCOMMIT")
        by_lifecycle = {r.id: r for r in lifecycles}
        by_intent = {r.id: r for r in intents}
        by_position = {r.id: r for r in positions}
        for lifecycle in lifecycles:
            if type(lifecycle.state_version) is not int or lifecycle.state_version < 1:
                raise LifecycleInvalidStateError("组合生命周期版本无效")
            if lifecycle.position_id is not None:
                position = by_position.get(lifecycle.position_id)
                if position is None or (position.market, position.symbol) != (lifecycle.market, lifecycle.symbol):
                    raise LifecycleInvalidStateError("组合生命周期持仓归属或证券不一致")
        for order in orders:
            if type(order.revision) is not int or order.revision < 1:
                raise LifecycleInvalidStateError("组合订单revision无效")
            if order.lifecycle_id is None:
                if order.intent_id is not None:
                    raise LifecycleInvalidStateError("未绑定组合订单携带意图，归属不明确")
            else:
                lifecycle = by_lifecycle.get(order.lifecycle_id)
                if lifecycle is None or (order.market, order.symbol) != (lifecycle.market, lifecycle.symbol):
                    raise LifecycleInvalidStateError("组合订单生命周期归属或证券不一致")
                if order.intent_id is not None:
                    intent = by_intent.get(order.intent_id)
                    if intent is None or intent.lifecycle_id != lifecycle.id:
                        raise LifecycleInvalidStateError("组合订单意图归属不一致")
        if any(type(r.revision) is not int or r.revision < 1 for r in intents):
            raise LifecycleInvalidStateError("组合意图revision无效")
        pending_buys = tuple(r for r in orders if r.side == "BUY" and r.status in ACTIVE_ORDERS
                             and r.quantity > r.filled_quantity)
        selected_ids = {r.id for r in lifecycles if r.closed_at is None}
        selected_ids.update(r.lifecycle_id for r in pending_buys if r.lifecycle_id is not None)
        scopes = []
        for lifecycle in lifecycles:
            if lifecycle.id not in selected_ids:
                continue
            scopes.append(PortfolioLifecycleScope(
                lifecycle.id, uuid.uuid4(), lifecycle.state_version,
                _row(session, PositionLifecycleState, lifecycle).before_json,
                tuple(_row(session, SuggestedOrder, r, r.revision) for r in orders
                      if r.lifecycle_id == lifecycle.id and r.status in ACTIVE_ORDERS),
                tuple(_row(session, PositionIntent, r, r.revision) for r in intents
                      if r.lifecycle_id == lifecycle.id and r.status in ACTIVE_INTENTS),
                tuple(_row(session, PositionTrailingStop, r) for r in stops if r.lifecycle_id == lifecycle.id),
                tuple(_row(session, PositionExpectation, r) for r in expectations if r.lifecycle_id == lifecycle.id),
            ))
        canonical = request.canonical_request()
        return SelectedPortfolioDrawdownCommand(
            request.portfolio_id, canonical, hashlib.sha256(canonical).hexdigest(),
            session.scalar(text("SELECT row_to_json(p)::text FROM portfolios p WHERE id=:id"),
                           {"id": request.portfolio_id}), tuple(scopes),
            tuple(_row(session, SuggestedOrder, r, r.revision) for r in pending_buys if r.lifecycle_id is None),
            tuple(_row(session, PortfolioPosition, r) for r in positions),
        )
