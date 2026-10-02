"""Freeze every live reference to a task before future controlled deletion.

This selector does not clear references, delete sources, write history or grant
execution permission. The caller retains all locks and owns the transaction.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.modules.analysis.infrastructure.models import AnalysisTask
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
    PositionIntent,
    PositionLifecycleState,
    SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.models import QuantStrategyVersion
from backend.modules.quant_strategy.infrastructure.signals import QuantExecutionSignal


@dataclass(frozen=True)
class SourceClearCommandRequest:
    task_id: uuid.UUID
    request_key: str

    def __post_init__(self):
        if not isinstance(self.task_id, uuid.UUID):
            raise FillValidationError("删源任务ID必须为UUID")
        if not isinstance(self.request_key, str) or not self.request_key.strip() or len(self.request_key) > 128:
            raise FillValidationError("命令request_key长度必须为1..128且非空白")

    def canonical_request(self):
        return _canonical({"schema_version": 1, "command_kind": "SOURCE_REFERENCE_CLEARED",
                           "task_id": str(self.task_id), "request_key": self.request_key})


@dataclass(frozen=True)
class SelectedSourceReference:
    entity_id: uuid.UUID
    lifecycle_id: uuid.UUID | None
    source_signal_id: int
    revision: int
    before_json: str


@dataclass(frozen=True)
class SelectedSourceStep:
    lifecycle_id: uuid.UUID
    operation_id: uuid.UUID
    state_version: int


@dataclass(frozen=True)
class SelectedSourcePortfolio:
    portfolio_id: uuid.UUID
    steps: tuple[SelectedSourceStep, ...]
    orders: tuple[SelectedSourceReference, ...]
    intents: tuple[SelectedSourceReference, ...]

    def manifest(self):
        return {"steps": [{"operation_id": str(step.operation_id), "lifecycle_id": str(step.lifecycle_id),
                           "command_step_no": index, "operation_kind": "SOURCE_REFERENCE_CLEARED"}
                          for index, step in enumerate(self.steps, 1)],
                "unbound_orders": [{"order_id": str(order.entity_id), "original_revision": order.revision,
                                    "allowed_results": list(RESULTS)} for order in self.orders
                                   if order.lifecycle_id is None]}

    def manifest_hash(self):
        return hashlib.sha256(_canonical(self.manifest())).hexdigest()


@dataclass(frozen=True)
class SelectedSourceClearCommand:
    task_id: uuid.UUID
    canonical_request: bytes
    request_hash: str
    task_before_json: str
    signal_before_rows: tuple[tuple[int, str], ...]
    portfolios: tuple[SelectedSourcePortfolio, ...]
    selector_version: str = "task-source-clear:v1"
    command_kind: str = "SOURCE_REFERENCE_CLEARED"
    scope_only: bool = True


def _references(session, task_id):
    signals = select(QuantExecutionSignal.id).where(QuantExecutionSignal.task_id == task_id)
    orders = tuple(session.execute(select(
        SuggestedOrder.id, SuggestedOrder.portfolio_id, SuggestedOrder.lifecycle_id,
        SuggestedOrder.source_signal_id).where(SuggestedOrder.source_signal_id.in_(signals))
        .order_by(SuggestedOrder.id)).tuples())
    intents = tuple(session.execute(select(
        PositionIntent.id, PositionLifecycleState.portfolio_id, PositionIntent.lifecycle_id,
        PositionIntent.source_signal_id).join(PositionLifecycleState,
                                             PositionIntent.lifecycle_id == PositionLifecycleState.id)
        .where(PositionIntent.source_signal_id.in_(signals)).order_by(PositionIntent.id)).tuples())
    return orders, intents


def _lock_rows(session, model, ids):
    if not ids:
        return {}
    rows = session.scalars(select(model).where(model.id.in_(ids)).order_by(model.id)
                           .with_for_update().execution_options(populate_existing=True)).all()
    result = {row.id: row for row in rows}
    if set(result) != set(ids):
        raise LifecycleRevisionConflictError("删源参与者在选择时消失，须整事务重试")
    return result


def _raw(session, table, identity):
    # Table names are fixed internal literals, never request values. Preserve
    # SQL NUMERIC and JSONB types in source text; this is not a persisted schema.
    return session.scalar(text(f"SELECT row_to_json(r)::text FROM {table} r WHERE id=:id"), {"id": identity})


def select_source_clear_command(session: Session, request: SourceClearCommandRequest) -> SelectedSourceClearCommand:
    """Select all portfolios once; any newly discovered participant aborts.

    Existing immutable commands need replay checking before selection. A task
    without references has zero portfolio scopes, never an empty LIVE command.
    This contract is limited to PostgreSQL READ COMMITTED transactions.
    """
    if not isinstance(request, SourceClearCommandRequest):
        raise FillValidationError("需要类型化的原始删源请求")
    if session.new or session.dirty or session.deleted:
        raise LifecycleInvalidStateError("删源参与者必须在业务修改前选择；Session存在未提交变更")
    with session.no_autoflush:
        if session.scalar(text("SELECT current_setting('transaction_isolation')")) != "read committed":
            raise LifecycleInvalidStateError("删源全集重查须使用READ COMMITTED事务")
        if session.scalar(text("SELECT pg_current_xact_id_if_assigned()::text")) is not None:
            raise LifecycleInvalidStateError("参与者选择前事务已有写入或行锁，须回滚重试")
        if session.get(AnalysisTask, request.task_id) is None:
            raise LifecycleNotFoundError("删源任务不存在")
        initial = _references(session, request.task_id)
        portfolio_ids = sorted({row[1] for rows in initial for row in rows})
        for identity in portfolio_ids:
            if lock_portfolio(session, identity) is None:
                raise LifecycleRevisionConflictError("删源组合在选择时消失，须整事务重试")
        selected = _references(session, request.task_id)
        if selected != initial:
            raise LifecycleRevisionConflictError("删源引用在组合锁前后变化，须整事务重试")
        lifecycle_ids = {row[2] for rows in selected for row in rows if row[2] is not None}
        owners = tuple(session.execute(select(
            PositionLifecycleState.id, PositionLifecycleState.portfolio_id,
            PositionLifecycleState.strategy_version_id, PositionLifecycleState.lifecycle_policy_version_id)
            .where(PositionLifecycleState.id.in_(lifecycle_ids)).order_by(PositionLifecycleState.id)).tuples())
        if {row[0] for row in owners} != lifecycle_ids:
            raise LifecycleInvalidStateError("删源生命周期归属不存在")
        _lock_rows(session, QuantStrategyVersion, {row[2] for row in owners})
        _lock_rows(session, LifecyclePolicyVersion, {row[3] for row in owners})
        lifecycles = _lock_rows(session, PositionLifecycleState, lifecycle_ids)
        for identity, portfolio_id, strategy_id, policy_id in owners:
            row = lifecycles[identity]
            if (row.portfolio_id, row.strategy_version_id, row.lifecycle_policy_version_id) != (
                    portfolio_id, strategy_id, policy_id):
                raise LifecycleRevisionConflictError("删源生命周期归属在取锁时变化，须整事务重试")
        orders = _lock_rows(session, SuggestedOrder, {row[0] for row in selected[0]})
        intents = _lock_rows(session, PositionIntent, {row[0] for row in selected[1]})
        task = _lock_rows(session, AnalysisTask, {request.task_id})[request.task_id]
        if task.status not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            raise LifecycleInvalidStateError("删源任务非可删除终态")
        # Lock task first at the concrete-source tier: blocks new signal FK
        # inserts; signal FOR UPDATE then blocks new order/intent FK references.
        signals = tuple(session.scalars(select(QuantExecutionSignal)
                                       .where(QuantExecutionSignal.task_id == request.task_id)
                                       .order_by(QuantExecutionSignal.id).with_for_update()
                                       .execution_options(populate_existing=True)).all())
        if _references(session, request.task_id) != selected:
            raise LifecycleRevisionConflictError("删源来源锁后发现未锁引用，须整事务重试")
        if session.scalar(text("SELECT pg_current_xact_id_if_assigned()::text")) is None:
            # Driver AUTOCOMMIT can still report READ COMMITTED while releasing
            # every FOR UPDATE lock at the end of its individual statement.
            raise LifecycleInvalidStateError("删源选择必须保留显式事务及行锁，不能使用AUTOCOMMIT")
        signal_ids = {row.id for row in signals}
        for row in orders.values():
            if row.source_signal_id not in signal_ids or type(row.revision) is not int or row.revision < 1:
                raise LifecycleInvalidStateError("删源订单来源或revision无效")
            if row.lifecycle_id is None:
                if row.intent_id is not None:
                    raise LifecycleInvalidStateError("未绑定删源订单携带意图，归属不明确")
            else:
                lifecycle = lifecycles[row.lifecycle_id]
                if (row.portfolio_id, row.market, row.symbol) != (
                        lifecycle.portfolio_id, lifecycle.market, lifecycle.symbol):
                    raise LifecycleInvalidStateError("删源订单与生命周期归属或证券不一致")
        for row in intents.values():
            if row.source_signal_id not in signal_ids or type(row.revision) is not int or row.revision < 1:
                raise LifecycleInvalidStateError("删源意图来源或revision无效")
        scopes = []
        for identity in portfolio_ids:
            scope_orders = tuple(SelectedSourceReference(row.id, row.lifecycle_id, row.source_signal_id,
                                                        row.revision, _raw(session, "suggested_orders", row.id))
                                 for row in orders.values() if row.portfolio_id == identity)
            scope_intents = tuple(SelectedSourceReference(row.id, row.lifecycle_id, row.source_signal_id,
                                                         row.revision, _raw(session, "position_intents", row.id))
                                  for row in intents.values() if lifecycles[row.lifecycle_id].portfolio_id == identity)
            members = {row.lifecycle_id for row in (*scope_orders, *scope_intents) if row.lifecycle_id is not None}
            steps = tuple(SelectedSourceStep(member, uuid.uuid4(), lifecycles[member].state_version)
                          for member in sorted(members))
            scopes.append(SelectedSourcePortfolio(identity, steps, scope_orders, scope_intents))
        canonical = request.canonical_request()
        return SelectedSourceClearCommand(
            request.task_id, canonical, hashlib.sha256(canonical).hexdigest(),
            _raw(session, "analysis_tasks", task.id),
            tuple((row.id, _raw(session, "quant_execution_signals", row.id)) for row in signals), tuple(scopes))
