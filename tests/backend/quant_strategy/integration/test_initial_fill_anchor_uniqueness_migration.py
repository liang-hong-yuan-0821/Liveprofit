# test-catalog-begin
# {
#   "purpose": "量化策略 / initial_fill_anchor_uniqueness_migration（成交、迁移）：0048 -> 0049 uses a dedicated PostgreSQL database for anchor constraints.",
#   "keywords": [
#     "量化策略",
#     "成交",
#     "迁移",
#     "initial_fill_anchor_uniqueness_migration",
#     "fill",
#     "migration"
#   ],
#   "covers": [
#     "backend/bootstrap/settings.py",
#     "backend/migrations/versions/0048_fill_lifecycle_version_steps.py",
#     "backend/migrations/versions/0049_initial_fill_anchor_uniqueness.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py",
#     "backend/modules/quant_strategy/infrastructure/models.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""0048 -> 0049 uses a dedicated PostgreSQL database for anchor constraints."""

from tests.support.python.paths import PROJECT_ROOT

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.bootstrap.settings import CoreSettings
from backend.modules.investment_workspace.infrastructure.models import Portfolio
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    LifecyclePolicyVersion, OrderFillEvent, PositionLifecycleState, SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.models import QuantStrategy, QuantStrategyVersion


CONSTRAINT = "uq_position_lifecycle_initial_fill"


def _config(url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
    config.cmd_opts = type("CmdOpts", (), {"x": [f"db_url={url}"], "name": None})()
    return config


@pytest.fixture
def isolated_anchor_db():
    base_url = CoreSettings().resolved_database_url()
    if not base_url:
        pytest.skip("database URL unavailable")
    database_name = f"liveprofit_anchor_0049_test_{uuid.uuid4().hex[:8]}"
    url = make_url(base_url).set(database=database_name).render_as_string(hide_password=False)
    assert make_url(url).database == database_name
    assert make_url(base_url).database != database_name
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect():
            pass
    except Exception:
        admin.dispose()
        pytest.skip("PostgreSQL unavailable")
    created = False
    engine = None
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        config = _config(url)
        command.upgrade(config, "0048")
        engine = create_engine(url)
        yield config, engine
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{database_name}" WITH (FORCE)'))
        admin.dispose()


def _seed_duplicate_anchors(engine):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        portfolio = Portfolio(name=f"anchor-{uuid.uuid4().hex[:8]}")
        strategy = QuantStrategy(name=f"anchor-{uuid.uuid4().hex[:8]}")
        policy = LifecyclePolicyVersion(
            policy_key=f"anchor-{uuid.uuid4().hex[:8]}", version_no=1,
            status="PUBLISHED", required_fields=[], config={}, content_hash="a" * 64)
        session.add_all((portfolio, strategy, policy))
        session.flush()
        version = QuantStrategyVersion(
            strategy_id=strategy.id, version_no=1, status="PUBLISHED",
            source_code="pass", source_hash="b" * 64)
        session.add(version)
        session.flush()
        order = SuggestedOrder(
            portfolio_id=portfolio.id, market="CN", symbol="000001.SZ", side="BUY",
            quantity=Decimal(10), limit_price=Decimal(10), reason_code="TEST_ANCHOR",
            status="PROPOSED", revision=1)
        session.add(order)
        session.flush()
        fill = OrderFillEvent(
            order_id=order.id, portfolio_id=portfolio.id, event_type="CONFIRM",
            quantity=Decimal(10), fill_price=Decimal(10),
            fill_trade_date=date(2026, 9, 28), source="MANUAL",
            idempotency_key=f"anchor-{uuid.uuid4()}")
        session.add(fill)
        session.flush()

        def lifecycle(*, anchor, closed):
            return PositionLifecycleState(
                portfolio_id=portfolio.id, market="CN", symbol="000001.SZ",
                strategy_version_id=version.id, lifecycle_policy_version_id=policy.id,
                initial_fill_id=anchor, target_exposure_pct=Decimal("0.5"),
                target_shares=Decimal(10), phase="CLOSED" if closed else "ACTIVE",
                closed_at=datetime(2026, 9, 29, tzinfo=timezone.utc) if closed else None)

        old = lifecycle(anchor=fill.id, closed=True)
        current = lifecycle(anchor=fill.id, closed=False)
        null_one = lifecycle(anchor=None, closed=True)
        null_two = lifecycle(anchor=None, closed=True)
        session.add_all((old, current, null_one, null_two))
        session.commit()
        return fill.id, old.id, current.id


def _anchors(engine):
    with engine.connect() as connection:
        return tuple(connection.execute(text(
            "SELECT id, initial_fill_id FROM position_lifecycle_states ORDER BY id")))


def test_initial_fill_anchor_upgrade_preflight_constraint_and_downgrade(isolated_anchor_db):
    config, engine = isolated_anchor_db
    fill_id, old_id, current_id = _seed_duplicate_anchors(engine)
    before = _anchors(engine)
    assert len(before) == 4
    assert sum(anchor == fill_id for _, anchor in before) == 2
    assert sum(anchor is None for _, anchor in before) == 2

    with pytest.raises(RuntimeError, match="1 duplicate group"):
        command.upgrade(config, "0049")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0048"
    assert _anchors(engine) == before
    assert CONSTRAINT not in {
        row["name"] for row in inspect(engine).get_unique_constraints("position_lifecycle_states")}

    # A human decision about a historical duplicate is required in a real DB.
    # Here the isolated fixture explicitly clears one erroneous anchor.
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE position_lifecycle_states SET initial_fill_id=NULL WHERE id=:id"),
            {"id": current_id})
    resolved = _anchors(engine)
    assert sum(anchor == fill_id for _, anchor in resolved) == 1
    assert sum(anchor is None for _, anchor in resolved) == 3

    command.upgrade(config, "0049")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0049"
    assert CONSTRAINT in {
        row["name"] for row in inspect(engine).get_unique_constraints("position_lifecycle_states")}
    with pytest.raises(IntegrityError) as error:
        with engine.begin() as connection:
            connection.execute(text(
                "UPDATE position_lifecycle_states SET initial_fill_id=:fill WHERE id=:id"),
                {"fill": fill_id, "id": current_id})
    assert CONSTRAINT in str(error.value)
    assert _anchors(engine) == resolved
    assert dict(_anchors(engine))[old_id] == fill_id

    command.downgrade(config, "0048")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0048"
    assert _anchors(engine) == resolved
    assert CONSTRAINT not in {
        row["name"] for row in inspect(engine).get_unique_constraints("position_lifecycle_states")}
    command.upgrade(config, "0049")
    assert _anchors(engine) == resolved
