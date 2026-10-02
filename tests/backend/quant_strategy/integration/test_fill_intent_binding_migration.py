# test-catalog-begin
# {
#   "purpose": "量化策略 / fill_intent_binding_migration（成交、迁移）：0046 -> 0047 is tested only in liveprofit_fill_binding_test.",
#   "keywords": [
#     "量化策略",
#     "成交",
#     "交易意图",
#     "迁移",
#     "fill_intent_binding_migration",
#     "fill",
#     "intent",
#     "migration"
#   ],
#   "covers": [
#     "backend/bootstrap/settings.py",
#     "backend/migrations/versions/0046_position_intent_revisions.py",
#     "backend/migrations/versions/0047_fill_intent_binding.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_strategy/application/lifecycle_replay_inventory.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py",
#     "backend/modules/quant_strategy/infrastructure/models.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""0046 -> 0047 is tested only in liveprofit_fill_binding_test."""

from tests.support.python.paths import PROJECT_ROOT

import hashlib
import json
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.bootstrap.settings import CoreSettings
from backend.modules.quant_strategy.application.lifecycle_replay_inventory import inventory_lifecycle_replay
from backend.modules.investment_workspace.infrastructure.models import Portfolio
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    LifecyclePolicyVersion, OrderFillEvent, PositionIntent, PositionLifecycleState,
    SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.models import QuantStrategy, QuantStrategyVersion


TEST_DB_NAME = "liveprofit_fill_binding_test"


def _test_url(base_url: str) -> str:
    main, sep, query = base_url.partition("?")
    prefix, _, _database = main.rpartition("/")
    return f"{prefix}/{TEST_DB_NAME}" + (f"?{query}" if sep else "")


def _config(url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
    config.cmd_opts = type("CmdOpts", (), {"x": [f"db_url={url}"], "name": None})()
    return config


def test_legacy_fill_unknown_and_future_binding_is_frozen(tmp_path, monkeypatch):
    base_url = CoreSettings().resolved_database_url()
    if not base_url:
        pytest.skip("database URL unavailable")
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect():
            pass
    except Exception:
        admin.dispose()
        pytest.skip("PostgreSQL unavailable")
    url = _test_url(base_url)
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    engine = None
    try:
        config = _config(url)
        command.upgrade(config, "0046")
        engine = create_engine(url)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            portfolio = Portfolio(id=uuid.uuid4(), name=f"fill-binding-{uuid.uuid4().hex[:8]}")
            strategy = QuantStrategy(id=uuid.uuid4(), name=f"fill-binding-{uuid.uuid4().hex[:8]}")
            policy = LifecyclePolicyVersion(
                id=uuid.uuid4(), policy_key=f"fill-binding-{uuid.uuid4().hex[:8]}",
                version_no=1, status="PUBLISHED", required_fields=[], config={},
                content_hash="a" * 64)
            session.add_all((portfolio, strategy, policy))
            session.flush()
            version = QuantStrategyVersion(
                id=uuid.uuid4(), strategy_id=strategy.id, version_no=1,
                status="PUBLISHED", source_code="def strategy(context): return {}",
                source_hash="b" * 64, lifecycle_policy_version_id=policy.id)
            session.add(version)
            session.flush()
            lifecycle = PositionLifecycleState(
                id=uuid.uuid4(), portfolio_id=portfolio.id, market="CN", symbol="000001.SZ",
                strategy_version_id=version.id, lifecycle_policy_version_id=policy.id)
            session.add(lifecycle)
            session.flush()
            intent = PositionIntent(
                id=uuid.uuid4(), lifecycle_id=lifecycle.id, trade_date=date(2026, 9, 21),
                target_shares=Decimal(200), reason_code="TEMPLATE_CONFIRM_ADD",
                state_version=1, status="ACTIVE", revision=1)
            session.add(intent)
            session.flush()
            order = SuggestedOrder(
                id=uuid.uuid4(), portfolio_id=portfolio.id, lifecycle_id=lifecycle.id,
                intent_id=intent.id, market="CN", symbol="000001.SZ", side="BUY",
                quantity=Decimal(200), limit_price=Decimal(10),
                reason_code="TEMPLATE_CONFIRM_ADD", status="PROPOSED", revision=1)
            session.add(order)
            session.flush()
            old_id = uuid.uuid4()
            session.execute(text("""INSERT INTO order_fill_events
                (id, order_id, portfolio_id, event_type, quantity, fill_price,
                 fill_trade_date, source, idempotency_key)
                VALUES (:id, :order, :portfolio, 'CONFIRM', 100, 10,
                        '2026-09-21', 'MANUAL', :key)"""), {
                "id": old_id, "order": order.id, "portfolio": portfolio.id,
                "key": f"old-{old_id}",
            })
            session.commit()
            order_id, intent_id, lifecycle_id = order.id, intent.id, lifecycle.id

        command.upgrade(config, "0047")
        with factory() as session:
            old = session.get(OrderFillEvent, old_id)
            assert old.binding_origin == "MIGRATED"
            assert old.intent_id_at_fill is None and old.lifecycle_id_at_fill is None
            new_id = uuid.uuid4()
            new = OrderFillEvent(
                id=new_id, order_id=order_id, portfolio_id=portfolio.id,
                event_type="CONFIRM", quantity=Decimal(100), fill_price=Decimal(10),
                fill_trade_date=date(2026, 9, 21), source="MANUAL",
                idempotency_key=f"new-{new_id}",
                lifecycle_id_at_fill=uuid.uuid4(), intent_id_at_fill=uuid.uuid4(),
                binding_origin="MIGRATED")
            session.add(new)
            session.commit()
        with factory() as session:
            new = session.get(OrderFillEvent, new_id)
            assert new.binding_origin == "LIVE"
            assert new.lifecycle_id_at_fill == lifecycle_id
            assert new.intent_id_at_fill == intent_id
            rebound = session.get(SuggestedOrder, order_id)
            rebound.intent_id = None
            rebound.lifecycle_id = None
            rebound.symbol = "999999.SZ"
            session.commit()
        with factory() as session:
            assert session.get(OrderFillEvent, new_id).intent_id_at_fill == intent_id
            inventory = inventory_lifecycle_replay(session, portfolio.id, lifecycle_id)
            assert new_id in inventory.ambiguous_fill_event_ids
            assert f"FILL_OWNERSHIP_AMBIGUOUS:{new_id}" in inventory.issues
            session.rollback()
            with pytest.raises(Exception, match="immutable"), session.begin_nested():
                session.get(OrderFillEvent, new_id).note = "tampered"
                session.flush()
            session.rollback()

        export_path = tmp_path / "fill_bindings.jsonl"
        monkeypatch.setenv("LIVEPROFIT_FILL_INTENT_BINDING_EXPORT_PATH", str(export_path))
        command.downgrade(config, "0046")
        header, *rows = export_path.read_bytes().splitlines(keepends=True)
        manifest = json.loads(header)
        assert manifest["rows"] == 2
        assert manifest["sha256"] == hashlib.sha256(b"".join(rows)).hexdigest()
        exported = {json.loads(row)["id"]: json.loads(row) for row in rows}
        assert exported[str(old_id)]["binding_origin"] == "MIGRATED"
        assert exported[str(new_id)]["intent_id_at_fill"] == str(intent_id)
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
        admin.dispose()
