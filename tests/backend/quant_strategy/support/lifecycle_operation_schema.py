"""Shared fixtures/builders for tests.backend.quant_strategy.integration.test_lifecycle_operation_schema; no test cases."""

from tests.support.python.paths import PROJECT_ROOT
import hashlib
import json
import uuid
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from backend.bootstrap.settings import CoreSettings
from backend.modules.investment_workspace.infrastructure.models import Portfolio
from backend.modules.quant_strategy.infrastructure.lifecycle_history_repository import (
    LifecycleHistoryIntegrityError,
    _verify_baseline,
    load_migration_baseline,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    LifecyclePolicyVersion,
    PositionExpectation,
    PositionLifecycleState,
    PositionTrailingStop,
    SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_operation_models import (
    _tables,
)
from backend.modules.quant_strategy.infrastructure.models import (
    QuantStrategy,
    QuantStrategyVersion,
)


ROOT = PROJECT_ROOT


TABLES = tuple(table.name for table in _tables)


LINKS = ("position_daily_fact_revisions", "position_intent_revisions", "fill_lifecycle_version_steps")


@pytest.fixture
def operation_db():
    base_url = CoreSettings().resolved_database_url()
    if not base_url:
        pytest.skip("database URL unavailable")
    name = f"liveprofit_lc_history_0051_test_{uuid.uuid4().hex[:12]}"
    url = make_url(base_url).set(database=name).render_as_string(hide_password=False)
    assert make_url(url).database == name and make_url(base_url).database != name
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
            connection.execute(text(f'CREATE DATABASE "{name}"'))
        created = True
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
        config.cmd_opts = type("CmdOpts", (), {"x": [f"db_url={url}"], "name": None})()
        command.upgrade(config, "0050")
        engine = create_engine(url)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT current_database()")) == name
        yield config, engine
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


def _seed(engine):
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        suffix = uuid.uuid4().hex[:8]
        portfolio = Portfolio(name=f"history-baseline-{suffix}")
        strategy = QuantStrategy(name=f"history-baseline-{suffix}")
        policy = LifecyclePolicyVersion(policy_key=f"history-baseline-{suffix}", version_no=1, status="PUBLISHED",
                                        required_fields=[], config={}, content_hash="a" * 64)
        session.add_all((portfolio, strategy, policy))
        session.flush()
        version = QuantStrategyVersion(strategy_id=strategy.id, version_no=1, status="PUBLISHED",
                                       source_code="pass", source_hash="b" * 64)
        session.add(version)
        session.flush()
        lifecycles = [PositionLifecycleState(
            portfolio_id=portfolio.id, market="CN", symbol="000001.SZ", strategy_version_id=version.id,
            lifecycle_policy_version_id=policy.id, phase=phase, state_version=state_version,
            closed_at=datetime(2026, 9, 21, tzinfo=timezone.utc) if phase == "CLOSED" else None,
            target_shares=Decimal(200), initial_fill_price=Decimal("10.1234"))
            for phase, state_version in (("CLOSED", 4), ("ENTRY_PENDING", 2))]
        session.add_all(lifecycles)
        session.flush()
        orders = [SuggestedOrder(
            portfolio_id=portfolio.id, lifecycle_id=lifecycles[0].id,
            market="CN", symbol="000001.SZ", side="SELL", quantity=Decimal(200),
            limit_price=Decimal("10.1234"), reason_code="TEST", status=status, revision=1)
            for status in ("FILLED", "RECONCILIATION_REQUIRED")]
        trailing = PositionTrailingStop(lifecycle_id=lifecycles[0].id, initial_stop_price=Decimal("9.1234"),
                                       high_water_mark=Decimal("11.1234"), active_stop_price=Decimal("10.1234"),
                                       config_snapshot={"multiple": 1.5, "nested": [True, 2.5]})
        expectation = PositionExpectation(lifecycle_id=lifecycles[0].id, fill_trade_date=date(2026, 9, 18),
                                          window_trading_days=5)
        session.add_all((*orders, trailing, expectation))
        session.commit()
        return portfolio.id, [item.id for item in lifecycles], [item.id for item in orders]


def _business_rows(engine):
    with engine.connect() as connection:
        return {table: list(connection.execute(text(f"SELECT row_to_json(r)::text FROM {table} r ORDER BY id")).scalars())
                for table in ("position_lifecycle_states", "suggested_orders", "position_trailing_stops", "position_expectations")}


def _shape_table(connection, name, *, temporary=True):
    prefix = "TEMP " if temporary else ""
    connection.execute(text(f"CREATE {prefix}TABLE shape (LIKE {name} INCLUDING CONSTRAINTS INCLUDING DEFAULTS)"))
    table = Table("shape", MetaData(), autoload_with=connection)
    for column in table.columns:
        if isinstance(column.type, JSON):
            column.type.none_as_null = True
    return table


def _required_values(table):
    values = {}
    for column in table.columns:
        if column.nullable or column.server_default is not None:
            continue
        kind = column.type
        if isinstance(kind, UUID):
            value = uuid.uuid4()
        elif isinstance(kind, (Integer, BigInteger, Numeric)):
            value = 1
        elif isinstance(kind, LargeBinary):
            value = b"{}"
        elif isinstance(kind, DateTime):
            value = datetime.now(timezone.utc)
        elif isinstance(kind, JSON):
            value = {}
        elif isinstance(kind, String):
            value = "a" * 64 if "hash" in column.name or "sha256" in column.name else "TEST"
        else:
            raise TypeError((column.name, kind))
        values[column.name] = value
    return values
