"""Modify-before selection for the single-order status command.

Selection only freezes the participants. It neither executes a status change,
authorizes BUY/SELL, writes a LIVE command, nor certifies continuous history.
Whole-portfolio, first-fill and delete-source selectors require their own scope.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.modules.quant_strategy.application.errors import (
    FillValidationError,
    LifecycleInvalidStateError,
    LifecycleNotFoundError,
    LifecycleRevisionConflictError,
)
from backend.modules.quant_strategy.application.planning_account import lock_portfolio
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    LifecyclePolicyVersion,
    PositionLifecycleState,
    SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.models import QuantStrategyVersion

SELECTOR_VERSION = "order-state:v1"
REQUESTED_STATUSES = frozenset({"EXECUTING", "REJECTED", "CANCELLED", "RECONCILIATION_REQUIRED", "SUPERSEDED"})
RESULTS = ("APPLIED", "NO_STATE_CHANGE", "BLOCKED")


def _canonical(payload):
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


@dataclass(frozen=True)
class OrderStatusCommandRequest:
    order_id: uuid.UUID
    status: str
    expected_revision: int
    request_key: str

    def __post_init__(self):
        if not isinstance(self.order_id, uuid.UUID):
            raise FillValidationError("命令订单ID必须为UUID")
        if not isinstance(self.status, str) or self.status not in REQUESTED_STATUSES:
            raise FillValidationError("不支持的命令订单状态")
        if type(self.expected_revision) is not int or self.expected_revision < 1:
            raise FillValidationError("命令expected_revision必须为正整数")
        if not isinstance(self.request_key, str) or not self.request_key.strip() or len(self.request_key) > 128:
            raise FillValidationError("命令request_key长度必须为1..128且非空白")

    def canonical_request(self):
        # Only original request fields: no current cash, status, snapshot or
        # preallocated operation UUID can change the request replay identity.
        return _canonical({"schema_version": 1, "command_kind": "ORDER_STATUS_CHANGED",
                           "order_id": str(self.order_id), "status": self.status,
                           "expected_revision": self.expected_revision, "request_key": self.request_key})


@dataclass(frozen=True)
class SelectedOrderStatusCommand:
    portfolio_id: uuid.UUID
    order_id: uuid.UUID
    order_revision: int
    lifecycle_id: uuid.UUID | None
    operation_id: uuid.UUID | None
    canonical_request: bytes
    request_hash: str
    order_before_json: str
    selector_version: str = SELECTOR_VERSION
    command_kind: str = "ORDER_STATUS_CHANGED"
    scope_only: bool = True

    def manifest(self):
        steps = [] if self.lifecycle_id is None else [{
            "operation_id": str(self.operation_id), "lifecycle_id": str(self.lifecycle_id),
            "command_step_no": 1, "operation_kind": "INTENT_OR_ORDER_CHANGED"}]
        unbound = [] if self.lifecycle_id is not None else [{
            "order_id": str(self.order_id), "original_revision": self.order_revision,
            "allowed_results": list(RESULTS)}]
        return {"steps": steps, "unbound_orders": unbound}

    def manifest_hash(self):
        return hashlib.sha256(_canonical(self.manifest())).hexdigest()


def select_order_status_command(session: Session, request: OrderStatusCommandRequest) -> SelectedOrderStatusCommand:
    """Independent selector; caller retains locks and owns commit/rollback.

    An existing command must be replay-checked before calling this selector.
    Missing/stale identities abort selection rather than emit an empty manifest.
    Plain read preloads are allowed; previous writes or row locks require a new
    transaction. Session.dirty alone cannot detect already-flushed mutations.
    """
    if not isinstance(request, OrderStatusCommandRequest):
        raise FillValidationError("需要类型化的原始订单状态请求")
    if session.new or session.dirty or session.deleted:
        raise LifecycleInvalidStateError("命令参与者必须在业务修改前选择；Session存在未提交变更")
    with session.no_autoflush:
        if session.scalar(text("SELECT pg_current_xact_id_if_assigned()::text")) is not None:
            raise LifecycleInvalidStateError("参与者选择前事务已有写入或行锁，须回滚重试")
        observed = session.execute(select(SuggestedOrder.portfolio_id, SuggestedOrder.lifecycle_id).where(
            SuggestedOrder.id == request.order_id)).one_or_none()
        if observed is None:
            raise LifecycleNotFoundError("建议订单不存在")
        portfolio_id, lifecycle_id = observed
        if lock_portfolio(session, portfolio_id) is None:
            raise LifecycleNotFoundError("订单组合不存在")
        if lifecycle_id is not None:
            owner = session.execute(select(
                PositionLifecycleState.portfolio_id, PositionLifecycleState.strategy_version_id,
                PositionLifecycleState.lifecycle_policy_version_id).where(PositionLifecycleState.id == lifecycle_id)).one_or_none()
            if owner is None or owner.portfolio_id != portfolio_id:
                raise LifecycleInvalidStateError("订单生命周期与组合归属不一致")
            # Portfolio -> strategy/policy -> lifecycle/order, refreshed after locks.
            version = session.scalar(select(QuantStrategyVersion).where(
                QuantStrategyVersion.id == owner.strategy_version_id).with_for_update().execution_options(populate_existing=True))
            policy = session.scalar(select(LifecyclePolicyVersion).where(
                LifecyclePolicyVersion.id == owner.lifecycle_policy_version_id).with_for_update().execution_options(populate_existing=True))
            if version is None or policy is None:
                raise LifecycleInvalidStateError("生命周期冻结策略或政策不存在")
            lifecycle = session.scalar(select(PositionLifecycleState).where(
                PositionLifecycleState.id == lifecycle_id).with_for_update().execution_options(populate_existing=True))
            if lifecycle is None or lifecycle.portfolio_id != portfolio_id or (
                    lifecycle.strategy_version_id != owner.strategy_version_id or
                    lifecycle.lifecycle_policy_version_id != owner.lifecycle_policy_version_id):
                raise LifecycleRevisionConflictError("生命周期归属在选择时变化，须整事务重试")
        order = session.scalar(select(SuggestedOrder).where(
            SuggestedOrder.id == request.order_id).with_for_update().execution_options(populate_existing=True))
        if order is None:
            raise LifecycleNotFoundError("建议订单不存在")
        if order.portfolio_id != portfolio_id or order.lifecycle_id != lifecycle_id:
            raise LifecycleRevisionConflictError("订单归属在选择时变化，须整事务重试")
        if order.revision != request.expected_revision:
            raise LifecycleRevisionConflictError("建议订单revision已变化，请刷新后重试")
        if lifecycle_id is not None and (order.market != lifecycle.market or order.symbol != lifecycle.symbol):
            raise LifecycleInvalidStateError("订单证券与生命周期归属不一致")
        # Original row is locked; no current-row-derived execution permission is
        # returned. Even impossible status requests have scope for BLOCKED facts.
        raw = session.scalar(text("SELECT row_to_json(o)::text FROM suggested_orders o WHERE id=:id"), {"id": request.order_id})
        canonical = request.canonical_request()
        return SelectedOrderStatusCommand(
            portfolio_id, order.id, order.revision, lifecycle_id,
            uuid.uuid4() if lifecycle_id is not None else None,
            canonical, hashlib.sha256(canonical).hexdigest(), raw)
