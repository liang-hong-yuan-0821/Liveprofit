"""Shared fixtures/builders for tests.backend.quant_strategy.integration.test_lifecycle_unbound_command; no test cases."""

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


operation_db = schema_tests.operation_db


def _setup(operation_db, *, status="PROPOSED", side="SELL"):
    config, engine = operation_db
    portfolio, _, orders = schema_tests._seed(engine)
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT current_database()")).startswith("liveprofit_lc_history_0051_test_")
        connection.execute(text("UPDATE suggested_orders SET lifecycle_id=NULL,status=:status,side=:side WHERE id=:id"),
                           {"id": orders[0], "status": status, "side": side})
    command.upgrade(config, "0052")
    return config, engine, portfolio, orders[0]


def assert_every_real_a_b_a_update_is_captured_without_disabling_guards(operation_db):
    _, engine, portfolio, order = _setup(operation_db)
    with engine.begin() as connection:
        connection.execute(text('''CREATE FUNCTION test_roundtrip() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN IF pg_trigger_depth()=1 AND NEW.status='CANCELLED' THEN
            UPDATE suggested_orders SET status='PROPOSED',revision=revision+1 WHERE id=NEW.id;
            UPDATE suggested_orders SET status='CANCELLED',revision=revision+1 WHERE id=NEW.id;
          END IF; RETURN NULL; END $$;
          CREATE TRIGGER zz_test_roundtrip AFTER UPDATE ON suggested_orders
            FOR EACH ROW EXECUTE FUNCTION test_roundtrip();'''))
    with sessionmaker(bind=engine)() as session:
        staged = stage_unbound_order_status_command(session, portfolio,
            OrderStatusCommandRequest(order, "CANCELLED", 1, "变化链-中文"), actor_ref="隔离验收")
        assert staged.content.change_count == 3 and staged.content.result_revision == 4
        rows = session.execute(text("SELECT change_seq,before_row->>'status',after_row->>'status' "
                                    "FROM quant_execution_operation_changes WHERE business_command_id=:id ORDER BY change_seq"),
                               {"id": staged.command_id}).all()
        assert rows == [(1, "PROPOSED", "CANCELLED"), (2, "CANCELLED", "PROPOSED"), (3, "PROPOSED", "CANCELLED")]
        session.commit()
    with sessionmaker(bind=engine)() as session:
        assert load_order_status_command_replay(session, portfolio,
            OrderStatusCommandRequest(order, "CANCELLED", 1, "变化链-中文")).change_count == 3


def assert_application_role_cannot_spoof_context_or_write_audit_facts(operation_db):
    _, engine, portfolio, order = _setup(operation_db)
    role = f"lc_command_test_{uuid.uuid4().hex[:12]}"
    created = False
    try:
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT current_database()")).startswith("liveprofit_lc_history_0051_test_")
            connection.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
            created = True
            connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
            connection.execute(text(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{role}"'))
        factory = sessionmaker(bind=engine)
        request = OrderStatusCommandRequest(order, "CANCELLED", 1, "app-role")
        with factory() as session:
            session.execute(text(f'SET LOCAL ROLE "{role}"'))
            with pytest.raises(DBAPIError, match="permission denied"):
                stage_unbound_order_status_command(session, portfolio, request, actor_ref="app")
            session.rollback()
        with engine.begin() as connection:
            connection.execute(text(f'GRANT EXECUTE ON FUNCTION lc_record_unbound_order_status(uuid,bytea,text) TO "{role}"'))
        with factory() as session:
            session.execute(text(f'SET LOCAL ROLE "{role}"'))
            staged = stage_unbound_order_status_command(session, portfolio, request, actor_ref="app")
            session.commit()
        with factory() as session:
            session.execute(text(f'SET LOCAL ROLE "{role}"'))
            with pytest.raises(DBAPIError, match="permission denied"):
                session.execute(text("SELECT lc_validate_unbound_command(:id,false)"), {"id": staged.command_id})
            session.rollback()
        # Even a mistaken INSERT grant cannot make a supplied GUC authority.
        with engine.begin() as connection:
            connection.execute(text(f'GRANT INSERT ON quant_execution_operation_sources TO "{role}"'))
            connection.execute(text(f'GRANT UPDATE ON suggested_orders TO "{role}"'))
        with factory() as session:
            session.execute(text(f'SET LOCAL ROLE "{role}"'))
            session.execute(text("SELECT set_config('liveprofit.lifecycle_command',:id,true)"), {"id": str(staged.command_id)})
            with pytest.raises(DBAPIError, match="controlled owner function"):
                session.execute(text("INSERT INTO quant_execution_operation_sources SELECT * FROM quant_execution_operation_sources WHERE business_command_id=:id"),
                                {"id": staged.command_id})
            session.rollback()
        with factory() as session:
            session.execute(text(f'SET LOCAL ROLE "{role}"'))
            session.execute(text("SELECT set_config('liveprofit.lifecycle_command',:id,true)"), {"id": str(staged.command_id)})
            with pytest.raises(DBAPIError, match="transaction|sealed"):
                session.execute(text("UPDATE suggested_orders SET revision=revision+1 WHERE id=:id"), {"id": order})
            session.rollback()
    finally:
        if created:
            with engine.begin() as connection:
                assert connection.scalar(text("SELECT current_database()")).startswith("liveprofit_lc_history_0051_test_")
                connection.execute(text(f'DROP OWNED BY "{role}"'))
                connection.execute(text(f'DROP ROLE "{role}"'))


def assert_stage_captures_real_result_and_caller_owns_commit(operation_db, initial, side, requested, outcome, changes):
    _, engine, portfolio, order = _setup(operation_db, status=initial, side=side)
    request = OrderStatusCommandRequest(order, requested, 1, "real-command")
    factory = sessionmaker(bind=engine)
    with factory() as session:
        staged = stage_unbound_order_status_command(session, portfolio, request, actor_ref="isolated-user")
        assert not staged.replayed and staged.content.outcome == outcome and staged.content.change_count == changes
        assert not staged.content.execution_authorized and not staged.content.continuous_history_known
        with engine.connect() as observer:
            assert observer.scalar(text("SELECT count(*) FROM lifecycle_business_commands WHERE id=:id"), {"id": staged.command_id}) == 0
            assert observer.scalar(text("SELECT status FROM suggested_orders WHERE id=:id"), {"id": order}) == initial
        session.commit()
    with factory() as session:
        replay = load_order_status_command_replay(session, portfolio, request)
        assert replay.command_id == staged.command_id and replay.outcome == outcome
