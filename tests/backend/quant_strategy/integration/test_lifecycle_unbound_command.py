# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle_unbound_command（持仓生命周期、未绑定命令、命令）",
#   "keywords": [
#     "量化策略",
#     "并发",
#     "重复请求",
#     "历史审计",
#     "持仓生命周期",
#     "订单",
#     "投资组合",
#     "重放",
#     "权限角色",
#     "事务回滚",
#     "表结构",
#     "未绑定",
#     "lifecycle_unbound_command",
#     "concurrent",
#     "duplicate",
#     "history",
#     "lifecycle",
#     "order",
#     "portfolio",
#     "replay",
#     "role",
#     "rollback",
#     "schema",
#     "unbound"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/lifecycle_command_selection.py",
#     "backend/modules/quant_strategy/application/lifecycle_unbound_command.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_command_replay.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_history_repository.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_operation_models.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

import tests.backend.quant_strategy.support.lifecycle_unbound_command as scenarios
"""0052 real DB captures, stage ownership and ACLs in random isolated databases."""

from tests.backend.quant_strategy.support.lifecycle_unbound_command import (
    operation_db,
    _setup,
)
import hashlib
import json
import uuid

import pytest
from alembic import command
from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from backend.modules.quant_strategy.application.lifecycle_command_selection import (
    OrderStatusCommandRequest,
)
from backend.modules.quant_strategy.application.lifecycle_unbound_command import (
    stage_unbound_order_status_command,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_command_replay import (
    load_order_status_command_replay,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_operation_models import (
    LifecycleBusinessCommand,
)
import tests.backend.quant_strategy.support.lifecycle_operation_schema as schema_tests





def test_every_real_a_b_a_update_is_captured_without_disabling_guards(operation_db):
    scenarios.assert_every_real_a_b_a_update_is_captured_without_disabling_guards(operation_db)


@pytest.mark.parametrize("mutation", ["quantity", "revision"])
def test_captured_disallowed_mutation_rolls_back_business_and_all_history(operation_db, mutation):
    _, engine, portfolio, order = _setup(operation_db)
    with engine.begin() as connection:
        connection.execute(text(f'''CREATE FUNCTION test_bad_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN NEW.{mutation} := NEW.{mutation}+1; RETURN NEW; END $$;
          CREATE TRIGGER test_bad_mutation BEFORE UPDATE ON suggested_orders
            FOR EACH ROW EXECUTE FUNCTION test_bad_mutation();'''))
    with sessionmaker(bind=engine)() as session:
        with pytest.raises(DBAPIError, match="permitted change"):
            stage_unbound_order_status_command(session, portfolio, OrderStatusCommandRequest(order, "CANCELLED", 1, "bad-trigger"), actor_ref="test")
        session.rollback()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT revision FROM suggested_orders WHERE id=:id"), {"id": order}) == 1
        assert connection.scalar(text("SELECT count(*) FROM lifecycle_business_commands WHERE request_key='bad-trigger'")) == 0
        assert connection.scalar(text("SELECT count(*) FROM quant_execution_operation_changes")) == 0
        assert connection.scalar(text("SELECT count(*) FROM quant_execution_operation_sources WHERE source_role='ORDER_STATUS_RESULT'")) == 0


def test_application_role_cannot_spoof_context_or_write_audit_facts(operation_db):
    scenarios.assert_application_role_cannot_spoof_context_or_write_audit_facts(operation_db)
@pytest.mark.parametrize("initial,side,requested,outcome,changes", [
    ("PROPOSED", "SELL", "CANCELLED", "APPLIED", 1),
    ("RECONCILIATION_REQUIRED", "SELL", "RECONCILIATION_REQUIRED", "NO_STATE_CHANGE", 0),
    ("EXECUTING", "SELL", "CANCELLED", "BLOCKED", 0),
    ("PROPOSED", "BUY", "EXECUTING", "BLOCKED", 0),
])
def test_stage_captures_real_result_and_caller_owns_commit(operation_db, initial, side, requested, outcome, changes):
    scenarios.assert_stage_captures_real_result_and_caller_owns_commit(operation_db, initial, side, requested, outcome, changes)


def test_replay_uses_original_result_and_conflict_and_rollback_are_atomic(operation_db):
    _, engine, portfolio, order = _setup(operation_db)
    request = OrderStatusCommandRequest(order, "CANCELLED", 1, "same-original")
    factory = sessionmaker(bind=engine)
    with factory() as session:
        original = stage_unbound_order_status_command(session, portfolio, request, actor_ref="first")
        session.rollback()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT revision FROM suggested_orders WHERE id=:id"), {"id": order}) == 1
        assert connection.scalar(text("SELECT count(*) FROM lifecycle_business_commands WHERE request_key='same-original'")) == 0
    with factory() as session:
        original = stage_unbound_order_status_command(session, portfolio, request, actor_ref="first")
        session.commit()
    with engine.begin() as connection:
        # Transitional legacy writers can still change today's row. Historical
        # replay must not compare the old result with this newer projection.
        connection.execute(text("UPDATE suggested_orders SET revision=9 WHERE id=:id"), {"id": order})
    with factory() as session:
        replay = stage_unbound_order_status_command(session, portfolio, request, actor_ref="second")
        assert replay.replayed and replay.command_id == original.command_id and replay.content.result_revision == 2
        session.commit()
    with factory() as session:
        with pytest.raises(DBAPIError, match="request key conflicts"):
            stage_unbound_order_status_command(session, portfolio, OrderStatusCommandRequest(order, "REJECTED", 1, request.request_key), actor_ref="third")
        session.rollback()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM lifecycle_business_commands WHERE request_key='same-original'")) == 1
        assert connection.scalar(text("SELECT count(*) FROM quant_execution_operation_changes")) == 1


def test_concurrent_duplicate_waits_for_portfolio_and_replays_after_commit(operation_db):
    _, engine, portfolio, order = _setup(operation_db)
    request = OrderStatusCommandRequest(order, "CANCELLED", 1, "concurrent")
    factory = sessionmaker(bind=engine)
    with factory() as first, factory() as second:
        original = stage_unbound_order_status_command(first, portfolio, request, actor_ref="first")
        second.execute(text("SET LOCAL lock_timeout='100ms'"))
        with pytest.raises(DBAPIError, match="lock timeout"):
            stage_unbound_order_status_command(second, portfolio, request, actor_ref="second")
        second.rollback()
        first.commit()
        replay = stage_unbound_order_status_command(second, portfolio, request, actor_ref="second")
        assert replay.command_id == original.command_id and replay.replayed
        second.commit()


@pytest.mark.parametrize("mode", ["stale", "bound", "previous_write"])
def test_invalid_participant_or_previous_write_refuses_without_a_command(operation_db, mode):
    _, engine, portfolio, order = _setup(operation_db)
    if mode == "bound":
        with engine.begin() as connection:
            lifecycle = connection.scalar(text("SELECT id FROM position_lifecycle_states WHERE portfolio_id=:id LIMIT 1"), {"id": portfolio})
            connection.execute(text("UPDATE suggested_orders SET lifecycle_id=:lc WHERE id=:id"), {"lc": lifecycle, "id": order})
    with sessionmaker(bind=engine)() as session:
        if mode == "previous_write":
            session.execute(text("UPDATE suggested_orders SET revision=5 WHERE id=:id"), {"id": order})
        with pytest.raises(DBAPIError):
            stage_unbound_order_status_command(session, portfolio, OrderStatusCommandRequest(order, "CANCELLED", 2 if mode == "stale" else 1, "invalid"), actor_ref="test")
        session.rollback()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM lifecycle_business_commands WHERE request_key='invalid'")) == 0
        assert connection.scalar(text("SELECT revision FROM suggested_orders WHERE id=:id"), {"id": order}) == 1


def test_sealed_context_cannot_append_after_early_constraint_check_or_commit(operation_db):
    _, engine, portfolio, order = _setup(operation_db)
    request = OrderStatusCommandRequest(order, "CANCELLED", 1, "sealed")
    factory = sessionmaker(bind=engine)
    with factory() as session:
        staged = stage_unbound_order_status_command(session, portfolio, request, actor_ref="test")
        session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        # Each following write must still be refused, including same transaction.
        for spoof_context in (False, True):
            savepoint = session.begin_nested()
            with pytest.raises(DBAPIError, match="sealed"):
                if spoof_context:
                    session.execute(text("SELECT set_config('liveprofit.lifecycle_command',:cmd,true)"), {"cmd": str(staged.command_id)})
                session.execute(text("UPDATE suggested_orders SET revision=revision+1 WHERE id=:id"), {"id": order})
            savepoint.rollback()
        session.commit()
    with factory() as session:
        with pytest.raises(DBAPIError, match="transaction|sealed"):
            session.execute(text("SELECT set_config('liveprofit.lifecycle_command',:cmd,true)"), {"cmd": str(staged.command_id)})
            session.execute(text("UPDATE suggested_orders SET revision=revision+1 WHERE id=:id"), {"id": order})
        session.rollback()


def test_downgrade_preserves_live_facts_and_restores_closed_inserts(operation_db):
    config, engine, portfolio, order = _setup(operation_db)
    with sessionmaker(bind=engine)() as session:
        stage_unbound_order_status_command(session, portfolio, OrderStatusCommandRequest(order, "CANCELLED", 1, "keep-live"), actor_ref="test")
        session.commit()
    command.downgrade(config, "0051")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM lifecycle_business_commands WHERE request_key='keep-live'")) == 1
        assert connection.scalar(text("SELECT count(*) FROM quant_execution_operation_changes")) == 1
        assert connection.scalar(text("SELECT count(*) FROM quant_execution_operation_sources WHERE source_role='ORDER_STATUS_RESULT'")) == 1
    command.upgrade(config, "0052")


def test_timezone_rendering_does_not_change_physical_state_at_deferred_validation(operation_db):
    _, engine, portfolio, order = _setup(operation_db)
    request = OrderStatusCommandRequest(order, "CANCELLED", 1, "stable-timezone")
    with sessionmaker(bind=engine)() as session:
        session.execute(text("SET LOCAL TIME ZONE 'America/New_York'"))
        staged = stage_unbound_order_status_command(session, portfolio, request, actor_ref="test")
        session.execute(text("SET LOCAL TIME ZONE 'Asia/Shanghai'"))
        session.commit()
    with sessionmaker(bind=engine)() as session:
        session.execute(text("SET LOCAL TIME ZONE 'Pacific/Auckland'"))
        replay = load_order_status_command_replay(session, portfolio, request)
        assert replay.command_id == staged.command_id and replay.result_revision == 2


@pytest.mark.parametrize("change", ["UPDATE", "INSERT", "DELETE"])
def test_other_order_cannot_change_in_the_sealed_command_transaction(operation_db, change):
    _, engine, portfolio, order = _setup(operation_db)
    other = uuid.uuid4()
    insert_other = ("INSERT INTO suggested_orders (id,portfolio_id,market,symbol,side,quantity,filled_quantity,limit_price,"
                    "reserved_cash,reserved_risk,reason_code,status,revision,created_at,updated_at) "
                    "VALUES (:id,:portfolio,'CN','000002.SZ','SELL',100,0,10,0,0,'TEST','PROPOSED',1,now(),now())")
    if change != "INSERT":
        # A post-migration legacy order has no historical FK that could reject
        # DELETE before the capture guard itself is reached.
        with engine.begin() as connection:
            connection.execute(text(insert_other), {"id": other, "portfolio": portfolio})
    with sessionmaker(bind=engine)() as session:
        staged = stage_unbound_order_status_command(session, portfolio, OrderStatusCommandRequest(order, "CANCELLED", 1, "exclusive-scope"), actor_ref="test")
        session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        savepoint = session.begin_nested()
        with pytest.raises(DBAPIError, match="outside its scope"):
            sql = ("UPDATE suggested_orders SET revision=revision+1 WHERE id=:id" if change == "UPDATE"
                   else insert_other if change == "INSERT" else "DELETE FROM suggested_orders WHERE id=:id")
            session.execute(text(sql), {"id": other, "portfolio": portfolio})
        savepoint.rollback()
        session.commit()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT revision FROM suggested_orders WHERE id=:id"), {"id": other}) == (None if change == "INSERT" else 1)
        assert connection.scalar(text("SELECT count(*) FROM quant_execution_operation_changes WHERE business_command_id=:id"), {"id": staged.command_id}) == 1


@pytest.mark.parametrize("defect", ["missing_result", "empty_manifest", "wrong_order_manifest"])
def test_incomplete_or_forged_owner_header_cannot_commit(operation_db, defect):
    _, engine, portfolio, order = _setup(operation_db, status="EXECUTING")
    request = OrderStatusCommandRequest(order, "CANCELLED", 1, "valid-blocked")
    with sessionmaker(bind=engine)() as session:
        original = stage_unbound_order_status_command(session, portfolio, request, actor_ref="test")
        session.commit()
    table = LifecycleBusinessCommand.__table__
    with pytest.raises(DBAPIError, match="exactly one sealed result|single-unbound-order manifests|manifest"), engine.begin() as connection:
        head = dict(connection.execute(select(table).where(table.c.id == original.command_id)).mappings().one())
        head.update(id=uuid.uuid4(), command_seq=head["command_seq"]+1, previous_command_id=original.command_id,
                    previous_manifest_hash=head["manifest_hash"], request_key="incomplete")
        head.pop("recorded_at")
        new_request = OrderStatusCommandRequest(order, "CANCELLED", 1, "incomplete")
        head["canonical_request"] = new_request.canonical_request()
        head["request_hash"] = hashlib.sha256(head["canonical_request"]).hexdigest()
        if defect == "empty_manifest":
            head["expected_unbound_orders"] = []
            head["expected_unbound_order_count"] = 0
        elif defect == "wrong_order_manifest":
            head["expected_unbound_orders"][0]["order_id"] = str(uuid.uuid4())
        from backend.modules.quant_strategy.infrastructure.lifecycle_history_repository import (
            _hash,
        )

        head["manifest_hash"] = _hash({"steps": [], "unbound_orders": head["expected_unbound_orders"]})
        connection.execute(text("SELECT set_config('liveprofit.lifecycle_command',:cmd,true)"), {"cmd": str(head["id"])})
        connection.execute(insert(table).values(**head))
        assert connection.scalar(text("SELECT captured_transaction_id FROM lifecycle_business_commands WHERE id=:id"),
                                 {"id": head["id"]}) == connection.scalar(text("SELECT pg_current_xact_id()::text"))
        # Exit commits and fires the actual deferred completeness constraint.
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM lifecycle_business_commands WHERE request_key='incomplete'")) == 0


@pytest.mark.parametrize("field,value", [("expected_revision", True), ("schema_version", 2), ("expected_unbound_orders", [])])
def test_direct_entry_rejects_client_manifest_or_wrong_original_schema(operation_db, field, value):
    _, engine, portfolio, order = _setup(operation_db)
    raw = json.loads(OrderStatusCommandRequest(order, "CANCELLED", 1, "bad-original").canonical_request())
    raw[field] = value
    payload = json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    with sessionmaker(bind=engine)() as session:
        with pytest.raises(DBAPIError, match="original command"):
            session.execute(text("SELECT * FROM lc_record_unbound_order_status(:portfolio,:request,'test')"),
                            {"portfolio": portfolio, "request": payload})
        session.rollback()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM lifecycle_business_commands WHERE request_key='bad-original'")) == 0
