# test-catalog-begin
# {
#   "purpose": "量化策略 / intent_revision_migration（版本修订、迁移）：0045 -> 0046 is tested only in liveprofit_intent_revision_test.",
#   "keywords": [
#     "量化策略",
#     "并发",
#     "导出",
#     "交易意图",
#     "迁移",
#     "版本修订",
#     "intent_revision_migration",
#     "concurrent",
#     "export",
#     "intent",
#     "migration",
#     "revision"
#   ],
#   "covers": [
#     "backend/bootstrap/settings.py",
#     "backend/migrations/versions/0045_daily_fact_input_proposals.py",
#     "backend/migrations/versions/0046_position_intent_revisions.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py",
#     "backend/modules/quant_strategy/infrastructure/models.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""0045 -> 0046 is tested only in liveprofit_intent_revision_test."""

from tests.support.python.paths import PROJECT_ROOT

import hashlib
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from backend.bootstrap.settings import CoreSettings
from backend.modules.investment_workspace.infrastructure.models import Portfolio
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    LifecyclePolicyVersion, PositionIntent, PositionIntentRevision,
    PositionLifecycleState,
)
from backend.modules.quant_strategy.infrastructure.models import QuantStrategy, QuantStrategyVersion


TEST_DB_NAME = "liveprofit_intent_revision_test"


def _test_url(base_url: str) -> str:
    main, sep, query = base_url.partition("?")
    prefix, _, _database = main.rpartition("/")
    return f"{prefix}/{TEST_DB_NAME}" + (f"?{query}" if sep else "")


def _config(url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
    config.cmd_opts = type("CmdOpts", (), {"x": [f"db_url={url}"], "name": None})()
    return config


def test_intent_revision_backfill_and_concurrent_downgrade_export(tmp_path, monkeypatch):
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
        command.upgrade(config, "0045")
        engine = create_engine(url)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            portfolio = Portfolio(id=uuid.uuid4(), name=f"intent-revision-{uuid.uuid4().hex[:8]}")
            strategy = QuantStrategy(id=uuid.uuid4(), name=f"intent-revision-{uuid.uuid4().hex[:8]}")
            policy = LifecyclePolicyVersion(
                id=uuid.uuid4(), policy_key=f"intent-revision-{uuid.uuid4().hex[:8]}",
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
            session.commit()
            intent_id = intent.id

        with factory() as writer:
            pending = writer.get(PositionIntent, intent_id)
            pending.status = "EXECUTING"
            pending.revision = 2
            writer.flush()
            with ThreadPoolExecutor(max_workers=1) as pool:
                upgrade = pool.submit(command.upgrade, config, "0046")
                blocked = False
                try:
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        with engine.connect() as observer:
                            blocked = observer.scalar(text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity
                                    WHERE datname = current_database()
                                      AND wait_event_type = 'Lock'
                                      AND query LIKE 'LOCK TABLE position_intents%')
                            """))
                        if blocked:
                            break
                        time.sleep(0.05)
                finally:
                    writer.commit()
                assert blocked, "upgrade must wait before taking the migration baseline"
                upgrade.result(timeout=10)
        with factory() as session:
            baseline = session.scalar(select(PositionIntentRevision).where(
                PositionIntentRevision.intent_id == intent_id))
            assert baseline.revision_no == 1 and baseline.baseline_origin == "MIGRATED"
            assert baseline.target_shares == Decimal(200)
            assert baseline.status == "EXECUTING" and baseline.intent_revision == 2
            current = session.get(PositionIntent, intent_id)
            current.status = "RECONCILIATION_REQUIRED"
            current.revision = 3
            session.commit()
        with factory() as session:
            chain = list(session.scalars(select(PositionIntentRevision).where(
                PositionIntentRevision.intent_id == intent_id,
            ).order_by(PositionIntentRevision.revision_no)))
            assert [row.status for row in chain] == ["EXECUTING", "RECONCILIATION_REQUIRED"]
            assert chain[1].previous_revision_id == chain[0].id
            with pytest.raises(Exception, match="require parent-row mutation"), session.begin_nested():
                session.add(PositionIntentRevision(
                    id=uuid.uuid4(), intent_id=intent_id, revision_no=3,
                    previous_revision_id=chain[1].id, baseline_origin="MIGRATED",
                    lifecycle_id=chain[1].lifecycle_id, source_signal_id=None,
                    trade_date=chain[1].trade_date, target_shares=chain[1].target_shares,
                    reason_code=chain[1].reason_code, state_version=1,
                    status="COMPLETED", intent_revision=4,
                    recorded_at=chain[1].recorded_at))
                session.flush()
            with pytest.raises(Exception, match="immutable"), session.begin_nested():
                chain[0].status = "TAMPERED"
                session.flush()
            session.rollback()

        export_path = tmp_path / "intent_revisions.jsonl"
        monkeypatch.setenv("LIVEPROFIT_INTENT_REVISION_EXPORT_PATH", str(export_path))
        with factory() as writer:
            current = writer.get(PositionIntent, intent_id)
            current.status = "COMPLETED"
            current.revision = 4
            writer.flush()
            with ThreadPoolExecutor(max_workers=1) as pool:
                downgrade = pool.submit(command.downgrade, config, "0045")
                blocked = False
                try:
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        with engine.connect() as observer:
                            blocked = observer.scalar(text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity
                                    WHERE datname = current_database()
                                      AND wait_event_type = 'Lock'
                                      AND query LIKE 'LOCK TABLE position_intents%')
                            """))
                        if blocked:
                            break
                        time.sleep(0.05)
                finally:
                    writer.commit()
                assert blocked, "downgrade must wait for the intent writer"
                downgrade.result(timeout=10)
        header, *rows = export_path.read_bytes().splitlines(keepends=True)
        manifest = json.loads(header)
        assert manifest["rows"] == 3
        assert manifest["sha256"] == hashlib.sha256(b"".join(rows)).hexdigest()
        assert [json.loads(row)["revision_no"] for row in rows] == [1, 2, 3]
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM position_intents WHERE id=:id"),
                                     {"id": intent_id}) == 1
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
        admin.dispose()
