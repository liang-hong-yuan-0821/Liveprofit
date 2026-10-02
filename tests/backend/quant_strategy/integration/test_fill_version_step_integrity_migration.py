# test-catalog-begin
# {
#   "purpose": "量化策略 / fill_version_step_integrity_migration（成交、迁移）：0049 -> 0050 integrity cases run only in a randomly named isolated PG DB.",
#   "keywords": [
#     "量化策略",
#     "成交",
#     "历史审计",
#     "持仓生命周期",
#     "迁移",
#     "投资组合",
#     "未绑定",
#     "fill_version_step_integrity_migration",
#     "fill",
#     "history",
#     "lifecycle",
#     "migration",
#     "portfolio",
#     "unbound"
#   ],
#   "covers": [
#     "backend/bootstrap/settings.py",
#     "backend/migrations/versions/0049_initial_fill_anchor_uniqueness.py",
#     "backend/migrations/versions/0050_fill_version_step_integrity.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py",
#     "backend/modules/quant_strategy/infrastructure/models.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""0049 -> 0050 integrity cases run only in a randomly named isolated PG DB."""

from tests.support.python.paths import PROJECT_ROOT

import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from backend.bootstrap.settings import CoreSettings
from backend.modules.investment_workspace.infrastructure.models import Portfolio
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    LifecyclePolicyVersion,
    PositionLifecycleState,
    SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.models import (
    QuantStrategy,
    QuantStrategyVersion,
)



@pytest.fixture
def isolated_fillstep_db():
    base_url = CoreSettings().resolved_database_url()
    if not base_url:
        pytest.skip("database URL unavailable")
    database_name = f"liveprofit_fillstep_0050_test_{uuid.uuid4().hex[:12]}"
    url = make_url(base_url).set(database=database_name).render_as_string(hide_password=False)
    assert make_url(url).database == database_name
    assert make_url(base_url).database != database_name
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect():
            pass
    except DBAPIError:
        admin.dispose()
        pytest.skip("PostgreSQL unavailable")
    created = False
    engine = None
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
        config.cmd_opts = type("CmdOpts", (), {"x": [f"db_url={url}"], "name": None})()
        command.upgrade(config, "0049")
        engine = create_engine(url)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT current_database()")) == database_name
        yield config, engine
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{database_name}" WITH (FORCE)'))
        admin.dispose()


def _seed(engine):
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        portfolio = Portfolio(name=f"fillstep-{uuid.uuid4().hex[:8]}")
        other_portfolio = Portfolio(name=f"fillstep-other-{uuid.uuid4().hex[:8]}")
        strategy = QuantStrategy(name=f"fillstep-{uuid.uuid4().hex[:8]}")
        policy = LifecyclePolicyVersion(
            policy_key=f"fillstep-{uuid.uuid4().hex[:8]}", version_no=1,
            status="PUBLISHED", required_fields=[], config={}, content_hash="a" * 64)
        session.add_all((portfolio, other_portfolio, strategy, policy))
        session.flush()
        version = QuantStrategyVersion(
            strategy_id=strategy.id, version_no=1, status="PUBLISHED",
            source_code="pass", source_hash="b" * 64)
        session.add(version)
        session.flush()
        lifecycle = PositionLifecycleState(
            portfolio_id=portfolio.id, market="CN", symbol="000001.SZ",
            strategy_version_id=version.id, lifecycle_policy_version_id=policy.id,
            phase="ENTRY_PENDING", target_shares=Decimal(200), state_version=1)
        other_lifecycle = PositionLifecycleState(
            portfolio_id=portfolio.id, market="CN", symbol="000002.SZ",
            strategy_version_id=version.id, lifecycle_policy_version_id=policy.id,
            phase="ENTRY_PENDING", target_shares=Decimal(200), state_version=1)
        session.add_all((lifecycle, other_lifecycle))
        session.flush()
        order = SuggestedOrder(
            portfolio_id=portfolio.id, lifecycle_id=lifecycle.id,
            market="CN", symbol="000001.SZ", side="BUY", quantity=Decimal(200),
            limit_price=Decimal(10), reason_code="TEST_FILL_STEP", status="PROPOSED", revision=1)
        session.add(order)
        session.commit()
        return {"portfolio": portfolio.id, "other_portfolio": other_portfolio.id,
                    "lifecycle": lifecycle.id, "other_lifecycle": other_lifecycle.id, "order": order.id}


def _insert_fill(connection, ids, fill_id=None, *, portfolio=None):
    fill_id = fill_id or uuid.uuid4()
    connection.execute(text("""INSERT INTO order_fill_events
        (id, order_id, portfolio_id, event_type, quantity, fill_price,
         fill_trade_date, source, idempotency_key)
        VALUES (:fill, :order, :portfolio, 'CONFIRM', 100, 10,
                :trade_date, 'MANUAL', :key)"""), {
        "fill": fill_id, "order": ids["order"], "portfolio": portfolio or ids["portfolio"],
        "trade_date": date(2026, 9, 28), "key": f"fillstep-{fill_id}"})
    return fill_id


def _advance(connection, ids, fill_id):
    connection.execute(text("SELECT set_config('liveprofit.fill_event_id', :id, true)"),
                       {"id": str(fill_id)})
    connection.execute(text("""UPDATE position_lifecycle_states SET state_version=state_version+1
        WHERE id=:id"""), {"id": ids["lifecycle"]})


def _steps(engine):
    with engine.connect() as connection:
        return list(connection.execute(text("""SELECT fill_event_id, lifecycle_id,
            version_before, version_after, origin
            FROM fill_lifecycle_version_steps ORDER BY fill_event_id""")))


def test_capture_sequence_multiple_fills_and_unattributed_survive_downgrade(isolated_fillstep_db):
    config, engine = isolated_fillstep_db
    ids = _seed(engine)
    # Preserve pre-0050 valid rows, including a local update-before-fill capture.
    old_fill = uuid.uuid4()
    with engine.begin() as connection:
        _advance(connection, ids, old_fill)
        _insert_fill(connection, ids, old_fill)
    previous = _steps(engine)
    command.upgrade(config, "0050")
    assert _steps(engine) == previous

    first, second = uuid.uuid4(), uuid.uuid4()
    with engine.begin() as connection:
        _advance(connection, ids, first)
        _insert_fill(connection, ids, first)
        _advance(connection, ids, second)
        _insert_fill(connection, ids, second)
    with engine.begin() as connection:
        unattributed = _insert_fill(connection, ids)
    rows = {row[0]: row[2:] for row in _steps(engine)}
    assert rows == {
        old_fill: (1, 2, "LOCAL_CAUSAL"), first: (2, 3, "LOCAL_CAUSAL"),
        second: (3, 4, "LOCAL_CAUSAL"), unattributed: (None, None, "UNATTRIBUTED")}
    before_downgrade = _steps(engine)
    command.downgrade(config, "0049")
    assert _steps(engine) == before_downgrade
    command.upgrade(config, "0050")
    assert _steps(engine) == before_downgrade


@pytest.mark.parametrize("origin,before,after", [
    ("LOCAL_CAUSAL", 1, 2), ("UNATTRIBUTED", None, None),
])
def test_direct_insert_rejected_even_with_spoofed_fill_setting(
        isolated_fillstep_db, origin, before, after):
    config, engine = isolated_fillstep_db
    ids = _seed(engine)
    command.upgrade(config, "0050")
    fill_id = uuid.uuid4()
    with pytest.raises(DBAPIError, match="require parent-row capture"), engine.begin() as connection:
        connection.execute(text("SELECT set_config('liveprofit.fill_event_id', :id, true)"),
                           {"id": str(fill_id)})
        connection.execute(text("""INSERT INTO fill_lifecycle_version_steps
            (fill_event_id, lifecycle_id, version_before, version_after, origin)
            VALUES (:fill, :lifecycle, :before, :after, :origin)"""),
            {"fill": fill_id, "lifecycle": ids["lifecycle"], "before": before, "after": after, "origin": origin})
    assert _steps(engine) == []


def test_shape_constraint_rejects_all_partial_null_forms(isolated_fillstep_db):
    config, engine = isolated_fillstep_db
    command.upgrade(config, "0050")
    # Copy the actual DB check to a temporary table so the insert-origin guard
    # cannot mask SQL three-valued CHECK behavior. No trigger is disabled.
    with engine.begin() as connection:
        connection.execute(text("CREATE TEMP TABLE step_shape (LIKE fill_lifecycle_version_steps INCLUDING CONSTRAINTS)"))
        for origin, before, after in [
            ("LOCAL_CAUSAL", None, None), ("LOCAL_CAUSAL", None, 2),
            ("LOCAL_CAUSAL", 1, None), ("LOCAL_CAUSAL", 0, 1),
            ("LOCAL_CAUSAL", 1, 3), ("UNATTRIBUTED", 1, None),
            ("UNATTRIBUTED", None, 2), ("UNATTRIBUTED", 1, 2),
        ]:
            with (pytest.raises(DBAPIError, match="ck_fill_lifecycle_version_step_shape"),
                  connection.begin_nested()):
                connection.execute(text("""INSERT INTO step_shape
                    VALUES (:fill, :lifecycle, :before, :after, :origin)"""),
                    {"fill": uuid.uuid4(), "lifecycle": uuid.uuid4(), "before": before, "after": after, "origin": origin})
        connection.execute(text("""INSERT INTO step_shape VALUES
            (:fill1, :lifecycle, 1, 2, 'LOCAL_CAUSAL'),
            (:fill2, :lifecycle, NULL, NULL, 'UNATTRIBUTED')"""),
            {"fill1": uuid.uuid4(), "fill2": uuid.uuid4(), "lifecycle": uuid.uuid4()})
        assert connection.scalar(text("SELECT count(*) FROM step_shape")) == 2


@pytest.mark.parametrize("case", ["unbound_fill", "other_lifecycle", "other_portfolio", "missing_fill"])
def test_wrong_or_missing_parent_rolls_back_lifecycle_and_step(isolated_fillstep_db, case):
    config, engine = isolated_fillstep_db
    ids = _seed(engine)
    command.upgrade(config, "0050")
    fill_id = uuid.uuid4()
    with pytest.raises(DBAPIError), engine.begin() as connection:
        if case in {"unbound_fill", "other_lifecycle"}:
            connection.execute(text("UPDATE suggested_orders SET lifecycle_id=:life WHERE id=:id"),
                {"life": None if case == "unbound_fill" else ids["other_lifecycle"], "id": ids["order"]})
        _advance(connection, ids, fill_id)
        if case != "missing_fill":
            _insert_fill(connection, ids, fill_id,
                         portfolio=ids["other_portfolio"] if case == "other_portfolio" else None)
    assert _steps(engine) == []
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT state_version FROM position_lifecycle_states WHERE id=:id"),
                                 {"id": ids["lifecycle"]}) == 1
        assert connection.scalar(text("SELECT count(*) FROM order_fill_events")) == 0


def test_unattributed_cross_portfolio_and_step_mutations_rejected(isolated_fillstep_db):
    config, engine = isolated_fillstep_db
    ids = _seed(engine)
    command.upgrade(config, "0050")
    with pytest.raises(DBAPIError, match="parent identity mismatch"), engine.begin() as connection:
        _insert_fill(connection, ids, portfolio=ids["other_portfolio"])
    with engine.begin() as connection:
        _insert_fill(connection, ids)
    before = _steps(engine)
    for sql in ["TRUNCATE fill_lifecycle_version_steps",
                "DELETE FROM fill_lifecycle_version_steps",
                "UPDATE fill_lifecycle_version_steps SET origin='UNATTRIBUTED'"]:
        with pytest.raises(DBAPIError, match="immutable"), engine.begin() as connection:
            connection.execute(text(sql))
        assert _steps(engine) == before


def test_nonowner_with_only_truncate_grant_cannot_clear_history(isolated_fillstep_db):
    config, engine = isolated_fillstep_db
    ids = _seed(engine)
    command.upgrade(config, "0050")
    with engine.begin() as connection:
        _insert_fill(connection, ids)
    before = _steps(engine)
    role_name = f"liveprofit_fillstep_truncate_{uuid.uuid4().hex[:12]}"
    role_created = False
    try:
        with engine.begin() as connection:
            database_name = connection.scalar(text("SELECT current_database()"))
            assert database_name.startswith("liveprofit_fillstep_0050_test_")
            assert database_name == engine.url.database
            # A random cluster role is created through the isolated database
            # connection; no existing role or production grant is changed.
            connection.execute(text(
                f'CREATE ROLE "{role_name}" NOLOGIN NOSUPERUSER '
                "NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION"))
        role_created = True
        with engine.begin() as connection:
            connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role_name}"'))
            connection.execute(text(
                f'GRANT TRUNCATE ON TABLE public.fill_lifecycle_version_steps TO "{role_name}"'))
        with pytest.raises(DBAPIError, match="immutable"), engine.begin() as connection:
            connection.execute(text(f'SET LOCAL ROLE "{role_name}"'))
            assert connection.scalar(text("SELECT current_user")) == role_name
            role = connection.execute(text("""SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole
                FROM pg_roles WHERE rolname=current_user""")).one()
            assert tuple(role) == (False, False, False, False)
            assert connection.scalar(text("""SELECT pg_get_userbyid(relowner) <> current_user
                FROM pg_class WHERE oid='public.fill_lifecycle_version_steps'::regclass"""))
            assert connection.scalar(text("""SELECT has_table_privilege(
                current_user, 'public.fill_lifecycle_version_steps', 'TRUNCATE')"""))
            assert not connection.scalar(text("""SELECT has_table_privilege(
                current_user, 'public.fill_lifecycle_version_steps', 'SELECT')"""))
            connection.execute(text("TRUNCATE public.fill_lifecycle_version_steps"))
        assert _steps(engine) == before
    finally:
        if role_created:
            with engine.begin() as connection:
                connection.execute(text(
                    f'REVOKE TRUNCATE ON TABLE public.fill_lifecycle_version_steps FROM "{role_name}"'))
                connection.execute(text(f'REVOKE USAGE ON SCHEMA public FROM "{role_name}"'))
                connection.execute(text(f'DROP ROLE "{role_name}"'))
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT count(*) FROM pg_roles WHERE rolname=:name"),
                                         {"name": role_name}) == 0


@pytest.mark.parametrize("case", ["null_versions", "wrong_lifecycle", "wrong_portfolio"])
def test_upgrade_refuses_bad_existing_rows_without_mutating_facts(isolated_fillstep_db, case):
    config, engine = isolated_fillstep_db
    ids = _seed(engine)
    with engine.begin() as connection:
        if case == "wrong_portfolio":
            _insert_fill(connection, ids, portfolio=ids["other_portfolio"])
        elif case == "null_versions":
            # In 0049 a NULL-valued causal step can precede its correctly bound
            # fill. Its only invalidity is SQL CHECK's old NULL acceptance.
            fill_id = uuid.uuid4()
            connection.execute(text("""INSERT INTO fill_lifecycle_version_steps
                (fill_event_id, lifecycle_id, version_before, version_after, origin)
                VALUES (:fill, :lifecycle, NULL, NULL, 'LOCAL_CAUSAL')"""),
                {"fill": fill_id, "lifecycle": ids["lifecycle"]})
            _insert_fill(connection, ids, fill_id)
        else:
            # An unbound fill has no automatic step. 0049 permits these invalid
            # direct inserts; the upgrade must expose, never repair, the gap.
            connection.execute(text("UPDATE suggested_orders SET lifecycle_id=NULL WHERE id=:id"),
                               {"id": ids["order"]})
            fill_id = _insert_fill(connection, ids)
            connection.execute(text("""INSERT INTO fill_lifecycle_version_steps
                (fill_event_id, lifecycle_id, version_before, version_after, origin)
                VALUES (:fill, :lifecycle, :before, :after, 'LOCAL_CAUSAL')"""),
                {"fill": fill_id, "lifecycle": ids["lifecycle"], "before": 1, "after": 2})
    before = _steps(engine)
    with pytest.raises(RuntimeError, match="1 invalid row"):
        command.upgrade(config, "0050")
    assert _steps(engine) == before
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0049"


def test_unbound_first_fill_keeps_no_invented_step(isolated_fillstep_db):
    config, engine = isolated_fillstep_db
    ids = _seed(engine)
    command.upgrade(config, "0050")
    with engine.begin() as connection:
        connection.execute(text("UPDATE suggested_orders SET lifecycle_id=NULL WHERE id=:id"),
                           {"id": ids["order"]})
        _insert_fill(connection, ids)
    assert _steps(engine) == []
