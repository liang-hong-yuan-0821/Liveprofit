# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle_source_selection（持仓生命周期、数据来源、参与范围选择）：Source scope and real FK/row locks, only in the random history test DB.",
#   "keywords": [
#     "量化策略",
#     "持仓生命周期",
#     "订单",
#     "投资组合",
#     "选择范围",
#     "交易信号",
#     "来源证据",
#     "任务",
#     "lifecycle_source_selection",
#     "lifecycle",
#     "order",
#     "portfolio",
#     "selection",
#     "signals",
#     "source",
#     "task"
#   ],
#   "covers": [
#     "backend/modules/analysis/infrastructure/models.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_strategy/application/errors.py",
#     "backend/modules/quant_strategy/application/lifecycle_source_selection.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py",
#     "backend/modules/quant_strategy/infrastructure/signals.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Source scope and real FK/row locks, only in the random history test DB."""
import json
import re
import uuid
from datetime import date
from decimal import Decimal

import pytest
from alembic import command
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from backend.modules.analysis.infrastructure.models import AnalysisTask
from backend.modules.investment_workspace.infrastructure.models import Portfolio
from backend.modules.quant_strategy.application.errors import (
    FillValidationError,
    LifecycleInvalidStateError,
    LifecycleNotFoundError,
    LifecycleRevisionConflictError,
)
from backend.modules.quant_strategy.application.lifecycle_source_selection import (
    SourceClearCommandRequest,
    select_source_clear_command,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    PositionIntent,
    PositionLifecycleState,
    SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.signals import QuantExecutionSignal
import tests.backend.quant_strategy.support.lifecycle_operation_schema as schema_tests

operation_db = schema_tests.operation_db


def _setup(operation_db, *, references=True, status="SUCCEEDED"):
    config, engine = operation_db
    first, lifecycle_ids, old_orders = schema_tests._seed(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        task = AnalysisTask(task_type="MARKET_WIDE", status=status, request_params={},
                            selected_layers=["position"], input_hash="c" * 64)
        second = Portfolio(name=f"source-{uuid.uuid4().hex}")
        unlocked = Portfolio(name=f"source-unlocked-{uuid.uuid4().hex}")
        session.add_all((task, second, unlocked))
        session.flush()
        signals = [QuantExecutionSignal(task_id=task.id, attempt_no=1, signal_kind="HOLDING", ts_code="000001.SZ")
                   for _ in range(5)]
        session.add_all(signals)
        session.flush()
        base = session.get(PositionLifecycleState, lifecycle_ids[0])
        third_lifecycle = PositionLifecycleState(
            portfolio_id=second.id, market="CN", symbol="000001.SZ", strategy_version_id=base.strategy_version_id,
            lifecycle_policy_version_id=base.lifecycle_policy_version_id, phase="INITIALIZED", state_version=3)
        session.add(third_lifecycle)
        session.flush()
        added_orders = [SuggestedOrder(
            portfolio_id=second.id, market="CN", symbol="000001.SZ", side="BUY", quantity=Decimal(100),
            limit_price=Decimal(10), reason_code="TEST", status="PROPOSED", revision=1,
            lifecycle_id=member, source_signal_id=signals[index + 2].id if references else None)
            for index, member in enumerate((None, third_lifecycle.id))]
        intents = [PositionIntent(lifecycle_id=identity, source_signal_id=signals[0].id if references else None,
                                  trade_date=date(2026, 9, 24), target_shares=Decimal(0), reason_code="TEST",
                                  state_version=2, status="ACTIVE", revision=1) for identity in lifecycle_ids]
        parked = SuggestedOrder(portfolio_id=first, market="CN", symbol="000001.SZ", side="SELL",
                                quantity=Decimal(100), limit_price=Decimal(10), reason_code="TEST", status="PROPOSED")
        session.add_all((*added_orders, *intents, parked))
        if references:
            for index, identity in enumerate(old_orders):
                session.get(SuggestedOrder, identity).source_signal_id = signals[index].id
        session.commit()
        data = {"task": task.id, "signal": signals[0].id, "signals": [row.id for row in signals], "portfolios": [first, second.id],
                "lifecycles": [*lifecycle_ids, third_lifecycle.id],
                "orders": [*old_orders, *(row.id for row in added_orders)],
                "intents": [row.id for row in intents], "parked": parked.id, "unlocked": unlocked.id}
    command.upgrade(config, "0053")
    return engine, data


def _clone_order(connection, identity, *, source, portfolio=None):
    overrides = {"id": str(uuid.uuid4()), "lifecycle_id": None, "intent_id": None, "source_signal_id": source}
    if portfolio is not None:
        overrides["portfolio_id"] = str(portfolio)
    connection.execute(text("""INSERT INTO suggested_orders SELECT (
        jsonb_populate_record(NULL::suggested_orders, to_jsonb(o) || CAST(:patch AS jsonb))).*
        FROM suggested_orders o WHERE id=:id"""), {"patch": json.dumps(overrides), "id": identity})


def test_entire_task_reference_scope_is_frozen_without_changes(operation_db):
    engine, data = _setup(operation_db)
    with sessionmaker(bind=engine)() as session:
        before = session.scalar(text("SELECT count(*) FROM lifecycle_business_commands"))
        request = SourceClearCommandRequest(data["task"], "删除-同一请求")
        selected = select_source_clear_command(session, request)
        assert selected.scope_only and selected.canonical_request == request.canonical_request()
        assert [row.portfolio_id for row in selected.portfolios] == sorted(data["portfolios"])
        assert {step.lifecycle_id for scope in selected.portfolios for step in scope.steps} == set(data["lifecycles"])
        assert {row.entity_id for scope in selected.portfolios for row in scope.orders} == set(data["orders"])
        assert {row.entity_id for scope in selected.portfolios for row in scope.intents} == set(data["intents"])
        assert sum(len(scope.steps) for scope in selected.portfolios) == 3  # shared references deduplicate
        assert sum(len(scope.manifest()["unbound_orders"]) for scope in selected.portfolios) == 1
        assert selected.signal_before_rows[0][0] == data["signal"]
        assert json.loads(selected.task_before_json)["status"] == "SUCCEEDED"
        for scope in selected.portfolios:
            assert len(scope.manifest_hash()) == 64
            assert [step["command_step_no"] for step in scope.manifest()["steps"]] == list(range(1, len(scope.steps) + 1))
            for row in (*scope.orders, *scope.intents):
                assert json.loads(row.before_json)["source_signal_id"] in data["signals"]
        assert session.scalar(text("SELECT count(*) FROM lifecycle_business_commands")) == before
        assert session.scalar(select(AnalysisTask.status).where(AnalysisTask.id == data["task"])) == "SUCCEEDED"
        session.rollback()


@pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "CANCELLED"])
def test_no_references_has_no_portfolio_command_even_when_task_has_signals(operation_db, status):
    engine, data = _setup(operation_db, references=False, status=status)
    with sessionmaker(bind=engine)() as session:
        selected = select_source_clear_command(session, SourceClearCommandRequest(data["task"], "empty"))
        assert selected.portfolios == () and len(selected.signal_before_rows) == 5 and selected.scope_only
        assert session.get(AnalysisTask, data["task"]) is not None


@pytest.mark.parametrize("status", ["PENDING", "RUNNING", "CANCEL_REQUESTED"])
def test_nonterminal_task_cannot_be_selected(operation_db, status):
    engine, data = _setup(operation_db, status=status)
    with sessionmaker(bind=engine)() as session, pytest.raises(LifecycleInvalidStateError, match="非可删除终态"):
        select_source_clear_command(session, SourceClearCommandRequest(data["task"], "active"))


def test_scope_refreshes_stale_identity_map_and_request_bytes_are_not_dynamic(operation_db):
    engine, data = _setup(operation_db)
    request = SourceClearCommandRequest(data["task"], "same")
    with sessionmaker(bind=engine)() as session:
        old = session.get(SuggestedOrder, data["orders"][0])
        with engine.begin() as other:
            other.execute(text("UPDATE suggested_orders SET revision=revision+1 WHERE id=:id"), {"id": old.id})
        selected = select_source_clear_command(session, request)
        result = next(row for scope in selected.portfolios for row in scope.orders if row.entity_id == old.id)
        assert result.revision == old.revision == 2
        assert selected.canonical_request == request.canonical_request()
        session.rollback()
        again = select_source_clear_command(session, request)
        assert again.canonical_request == selected.canonical_request
        assert {step.operation_id for scope in again.portfolios for step in scope.steps}.isdisjoint(
            step.operation_id for scope in selected.portfolios for step in scope.steps)


@pytest.mark.parametrize("prior", ["dirty", "flushed", "raw_sql", "row_lock"])
def test_prior_mutations_or_locks_cannot_be_used_as_before(operation_db, prior):
    engine, data = _setup(operation_db)
    with sessionmaker(bind=engine)() as session:
        if prior in {"dirty", "flushed"}:
            session.get(SuggestedOrder, data["orders"][0]).quantity += 1
            if prior == "flushed":
                session.flush()
                assert not session.dirty
        elif prior == "raw_sql":
            session.execute(text("UPDATE suggested_orders SET quantity=quantity+1 WHERE id=:id"), {"id": data["orders"][0]})
        else:
            session.execute(text("SELECT id FROM suggested_orders WHERE id=:id FOR UPDATE"), {"id": data["orders"][0]})
        with pytest.raises(LifecycleInvalidStateError):
            select_source_clear_command(session, SourceClearCommandRequest(data["task"], "late"))
        session.rollback()


def test_real_locks_have_global_tier_and_uuid_order(operation_db):
    engine, data = _setup(operation_db)
    locks = []

    def observe(_conn, _cursor, statement, parameters, _context, _many):
        if "FOR UPDATE" in statement:
            locks.append((re.search(r"FROM (\w+)", statement).group(1), statement, parameters))

    event.listen(engine, "before_cursor_execute", observe)
    try:
        with sessionmaker(bind=engine)() as session:
            select_source_clear_command(session, SourceClearCommandRequest(data["task"], "locks"))
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert [row[0] for row in locks] == ["portfolios", "portfolios", "quant_strategy_versions", "lifecycle_policy_versions",
                                       "position_lifecycle_states", "suggested_orders", "position_intents",
                                       "analysis_tasks", "quant_execution_signals"]
    assert [next(iter(row[2].values())) for row in locks[:2]] == sorted(data["portfolios"])
    assert all("ORDER BY" in row[1] for row in locks[2:])


@pytest.mark.parametrize("writer", ["task", "signal", "order", "intent", "new_order_fk", "new_signal_fk"])
def test_selected_sources_and_references_stay_locked_to_caller_end(operation_db, writer):
    engine, data = _setup(operation_db)
    with sessionmaker(bind=engine)() as session:
        select_source_clear_command(session, SourceClearCommandRequest(data["task"], "retain-locks"))
        with engine.connect() as other:
            other.execute(text("SET LOCAL lock_timeout='100ms'"))
            with pytest.raises(DBAPIError, match="lock timeout"):
                if writer == "new_order_fk":
                    _clone_order(other, data["orders"][0], source=data["signals"][-1], portfolio=data["unlocked"])
                elif writer == "new_signal_fk":
                    other.execute(text("""INSERT INTO quant_execution_signals(task_id,attempt_no,signal_kind,ts_code)
                        VALUES (:id,2,'HOLDING','000001.SZ')"""), {"id": data["task"]})
                else:
                    table, identity, column = {
                        "task": ("analysis_tasks", data["task"], "status"),
                        "signal": ("quant_execution_signals", data["signal"], "reason"),
                        "order": ("suggested_orders", data["orders"][0], "reason_code"),
                        "intent": ("position_intents", data["intents"][0], "reason_code"),
                    }[writer]
                    other.execute(text(f"UPDATE {table} SET {column}='TEST' WHERE id=:id"), {"id": identity})
            other.rollback()
        session.rollback()
    with engine.begin() as other:
        _clone_order(other, data["orders"][0], source=data["signals"][-1])  # caller rollback released the FK gate


@pytest.mark.parametrize("race", ["new_portfolio", "new_member", "rebound", "cleared", "rerun"])
def test_independent_race_is_rejected_without_partial_scope(operation_db, race):
    engine, data = _setup(operation_db)
    changed = False

    def interleave(_conn, _cursor, statement, _parameters, _context, _many):
        nonlocal changed
        early = "FROM position_intents JOIN position_lifecycle_states" in statement
        late = "FROM position_intents" in statement and "FOR UPDATE" in statement
        if changed or not (late if race in {"new_member", "rerun"} else early):
            return
        changed = True
        with engine.begin() as other:
            other.execute(text("SET LOCAL lock_timeout='250ms'"))
            if race == "new_portfolio":
                identity = uuid.uuid4()
                other.execute(text("INSERT INTO portfolios(id,name) VALUES (:id,'race')"), {"id": identity})
                _clone_order(other, data["orders"][0], source=data["signals"][-1], portfolio=identity)
            elif race == "new_member":
                other.execute(text("UPDATE suggested_orders SET source_signal_id=:source WHERE id=:id"),
                              {"source": data["signals"][-1], "id": data["parked"]})
            elif race == "rebound":
                other.execute(text("UPDATE suggested_orders SET lifecycle_id=:lc WHERE id=:id"),
                              {"id": data["orders"][0], "lc": data["lifecycles"][1]})
            elif race == "cleared":
                other.execute(text("UPDATE suggested_orders SET source_signal_id=NULL WHERE id=:id"), {"id": data["orders"][0]})
            else:
                other.execute(text("UPDATE analysis_tasks SET status='PENDING' WHERE id=:id"), {"id": data["task"]})

    event.listen(engine, "after_cursor_execute", interleave)
    try:
        with sessionmaker(bind=engine)() as session:
            with pytest.raises((LifecycleRevisionConflictError, LifecycleInvalidStateError)):
                select_source_clear_command(session, SourceClearCommandRequest(data["task"], "race"))
            session.rollback()
    finally:
        event.remove(engine, "after_cursor_execute", interleave)
    assert changed


def test_missing_task_and_repeatable_read_refuse_selection(operation_db):
    engine, data = _setup(operation_db)
    with sessionmaker(bind=engine)() as session, pytest.raises(LifecycleNotFoundError):
        select_source_clear_command(session, SourceClearCommandRequest(uuid.uuid4(), "absent"))
    with sessionmaker(bind=engine)() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        with pytest.raises(LifecycleInvalidStateError, match="READ COMMITTED"):
            select_source_clear_command(session, SourceClearCommandRequest(data["task"], "snapshot"))


def test_driver_autocommit_cannot_return_scope_with_released_locks(operation_db):
    engine, data = _setup(operation_db)
    with (sessionmaker(bind=engine.execution_options(isolation_level="AUTOCOMMIT"))() as session,
          pytest.raises(LifecycleInvalidStateError, match="AUTOCOMMIT")):
        select_source_clear_command(session, SourceClearCommandRequest(data["task"], "no-transaction"))


@pytest.mark.parametrize("task,key", [("not-uuid", "key"), (uuid.UUID(int=0), ""), (uuid.UUID(int=0), "  "),
                                    (uuid.UUID(int=0), "a" * 129), (uuid.UUID(int=0), True)])
def test_original_request_has_strict_identity_and_key(task, key):
    with pytest.raises(FillValidationError):
        SourceClearCommandRequest(task, key)
