# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle_command_selection（持仓生命周期、命令、参与范围选择）：Scope-only selection; all data and lock checks in a random isolated DB.",
#   "keywords": [
#     "量化策略",
#     "持仓生命周期",
#     "订单",
#     "重试",
#     "版本修订",
#     "选择范围",
#     "lifecycle_command_selection",
#     "lifecycle",
#     "order",
#     "retry",
#     "revision",
#     "selection"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/errors.py",
#     "backend/modules/quant_strategy/application/lifecycle_command_selection.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Scope-only selection; all data and lock checks in a random isolated DB."""
import json
import re
import uuid

import pytest
from alembic import command
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from backend.modules.quant_strategy.application.errors import (
    LifecycleInvalidStateError,
    LifecycleNotFoundError,
    LifecycleRevisionConflictError,
)
from backend.modules.quant_strategy.application.lifecycle_command_selection import (
    OrderStatusCommandRequest,
    select_order_status_command,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    SuggestedOrder,
)
import tests.backend.quant_strategy.support.lifecycle_operation_schema as schema_tests

operation_db = schema_tests.operation_db
_seed = schema_tests._seed


@pytest.mark.parametrize("unbound", [False, True])
def test_selector_freezes_exact_participant_without_writing_or_committing(operation_db, unbound):
    config, engine = operation_db
    portfolio, _, orders = _seed(engine)
    if unbound:
        with engine.begin() as connection:
            connection.execute(text("UPDATE suggested_orders SET lifecycle_id=NULL WHERE id=:id"), {"id": orders[0]})
    command.upgrade(config, "0051")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    request = OrderStatusCommandRequest(orders[0], "CANCELLED", 1, f"select-{uuid.uuid4().hex}")
    with factory() as session:
        selected = select_order_status_command(session, request)
        assert selected.portfolio_id == portfolio and selected.scope_only
        manifest = selected.manifest()
        assert len(manifest["steps"]) == (0 if unbound else 1)
        assert len(manifest["unbound_orders"]) == (1 if unbound else 0)
        assert json.loads(selected.order_before_json)["revision"] == 1
        assert selected.canonical_request == request.canonical_request()
        assert session.in_transaction()
        with factory() as other:
            other.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError, match="lock timeout"):
                other.execute(text("UPDATE suggested_orders SET revision=revision+1 WHERE id=:id"), {"id": orders[0]})
        # No command is persisted and no status mutation is authorized.
        assert session.scalar(text("SELECT count(*) FROM lifecycle_business_commands")) == 1
        assert session.get(SuggestedOrder, orders[0]).status == "FILLED"
        session.rollback()
    with factory() as session:
        assert session.get(SuggestedOrder, orders[0]).revision == 1


def test_selector_refreshes_stale_orm_and_rejects_old_request_revision(operation_db):
    config, engine = operation_db
    _, _, orders = _seed(engine)
    command.upgrade(config, "0051")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        stale = session.get(SuggestedOrder, orders[0])
        with engine.begin() as connection:
            connection.execute(text("UPDATE suggested_orders SET revision=2 WHERE id=:id"), {"id": orders[0]})
        selected = select_order_status_command(session, OrderStatusCommandRequest(orders[0], "CANCELLED", 2, "fresh"))
        assert selected.order_revision == stale.revision == 2
        session.rollback()
        with pytest.raises(LifecycleRevisionConflictError):
            select_order_status_command(session, OrderStatusCommandRequest(orders[0], "CANCELLED", 1, "stale"))


def test_missing_order_and_dirty_session_cannot_emit_empty_success_scope(operation_db):
    config, engine = operation_db
    _, _, orders = _seed(engine)
    command.upgrade(config, "0051")
    with sessionmaker(bind=engine)() as session:
        with pytest.raises(LifecycleNotFoundError):
            select_order_status_command(session, OrderStatusCommandRequest(uuid.uuid4(), "CANCELLED", 1, "missing"))
        session.rollback()
        order = session.get(SuggestedOrder, orders[0])
        order.quantity += 1
        with pytest.raises(LifecycleInvalidStateError, match="业务修改前"):
            select_order_status_command(session, OrderStatusCommandRequest(orders[0], "CANCELLED", 1, "dirty"))
        session.rollback()
        assert session.get(SuggestedOrder, orders[0]).quantity == 200


@pytest.mark.parametrize("prior_action", ["flushed", "raw_sql", "row_lock"])
def test_selector_refuses_preexisting_transaction_writes_or_row_locks(operation_db, prior_action):
    config, engine = operation_db
    _, _, orders = _seed(engine)
    command.upgrade(config, "0051")
    factory = sessionmaker(bind=engine)
    with factory() as session:
        if prior_action == "flushed":
            session.get(SuggestedOrder, orders[0]).quantity += 1
            session.flush()
            assert not session.dirty
        elif prior_action == "raw_sql":
            session.execute(text("UPDATE suggested_orders SET quantity=quantity+1 WHERE id=:id"), {"id": orders[0]})
        else:
            session.execute(text("SELECT id FROM suggested_orders WHERE id=:id FOR UPDATE"), {"id": orders[0]})
        with pytest.raises(LifecycleInvalidStateError, match="事务已有写入或行锁"):
            select_order_status_command(session, OrderStatusCommandRequest(orders[0], "CANCELLED", 1, "late-selection"))
        session.rollback()
    with factory() as session:
        assert session.get(SuggestedOrder, orders[0]).quantity == 200


def test_selector_acquires_real_locks_in_global_order(operation_db):
    config, engine = operation_db
    _, _, orders = _seed(engine)
    command.upgrade(config, "0051")
    locks = []

    def observe(_conn, _cursor, statement, _parameters, _context, _many):
        if "FOR UPDATE" in statement:
            locks.append(re.search(r"FROM (\w+)", statement).group(1))

    event.listen(engine, "before_cursor_execute", observe)
    try:
        with sessionmaker(bind=engine)() as session:
            select_order_status_command(session, OrderStatusCommandRequest(orders[0], "CANCELLED", 1, "lock-order"))
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert locks == ["portfolios", "quant_strategy_versions", "lifecycle_policy_versions",
                     "position_lifecycle_states", "suggested_orders"]


def test_order_rebound_after_initial_read_requires_whole_transaction_retry(operation_db):
    config, engine = operation_db
    _, lifecycles, orders = _seed(engine)
    command.upgrade(config, "0051")
    switched = False

    def rebind(_conn, _cursor, statement, _parameters, _context, _many):
        nonlocal switched
        if not switched and "SELECT suggested_orders.portfolio_id, suggested_orders.lifecycle_id" in statement:
            switched = True
            with engine.begin() as other:
                other.execute(text("UPDATE suggested_orders SET lifecycle_id=:lifecycle WHERE id=:id"),
                              {"id": orders[0], "lifecycle": lifecycles[1]})

    event.listen(engine, "after_cursor_execute", rebind)
    try:
        with sessionmaker(bind=engine)() as session:
            with pytest.raises(LifecycleRevisionConflictError, match="订单归属在选择时变化"):
                select_order_status_command(session, OrderStatusCommandRequest(orders[0], "CANCELLED", 1, "rebound"))
            session.rollback()
    finally:
        event.remove(engine, "after_cursor_execute", rebind)
    assert switched
