# test-catalog-begin
# {
#   "purpose": "量化策略 / daily_fact_revision_migration（版本修订、迁移）：0043 -> 0044 runs only in liveprofit_daily_fact_revision_test.",
#   "keywords": [
#     "量化策略",
#     "每日",
#     "迁移",
#     "版本修订",
#     "daily_fact_revision_migration",
#     "daily",
#     "migration",
#     "revision"
#   ],
#   "covers": [
#     "backend/bootstrap/settings.py",
#     "backend/migrations/versions/0043_account_fill_posting_corrections.py",
#     "backend/migrations/versions/0044_daily_fact_revisions.py",
#     "backend/migrations/versions/0045_daily_fact_input_proposals.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_strategy/application/daily_fact_input_proposals.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py",
#     "backend/modules/quant_strategy/infrastructure/models.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""0043 -> 0044 runs only in liveprofit_daily_fact_revision_test."""

from tests.support.python.paths import PROJECT_ROOT

import hashlib
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from backend.bootstrap.settings import CoreSettings
from backend.modules.quant_strategy.application.daily_fact_input_proposals import propose_daily_fact_input
from backend.modules.investment_workspace.infrastructure.models import Portfolio
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    LifecyclePolicyVersion, PositionDailyFact, PositionDailyFactInputProposal,
    PositionDailyFactRevision,
    PositionLifecycleState,
)
from backend.modules.quant_strategy.infrastructure.models import QuantStrategy, QuantStrategyVersion


TEST_DB_NAME = "liveprofit_daily_fact_revision_test"


def _test_url(base_url: str) -> str:
    main, sep, query = base_url.partition("?")
    prefix, _, _database = main.rpartition("/")
    return f"{prefix}/{TEST_DB_NAME}" + (f"?{query}" if sep else "")


def _config(url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
    config.cmd_opts = type("CmdOpts", (), {"x": [f"db_url={url}"], "name": None})()
    return config


def test_existing_daily_fact_backfill_and_verified_downgrade(tmp_path, monkeypatch):
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
        command.upgrade(config, "0043")
        engine = create_engine(url)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            portfolio = Portfolio(id=uuid.uuid4(), name=f"revision-{uuid.uuid4().hex[:8]}")
            strategy = QuantStrategy(id=uuid.uuid4(), name=f"revision-{uuid.uuid4().hex[:8]}")
            policy = LifecyclePolicyVersion(
                id=uuid.uuid4(), policy_key=f"revision-{uuid.uuid4().hex[:8]}",
                version_no=1, status="PUBLISHED", required_fields=[], config={},
                content_hash="a" * 64,
            )
            session.add_all([portfolio, strategy, policy])
            session.flush()
            version = QuantStrategyVersion(
                id=uuid.uuid4(), strategy_id=strategy.id, version_no=1,
                status="PUBLISHED", source_code="def strategy(context): return {}",
                source_hash="b" * 64, lifecycle_policy_version_id=policy.id,
            )
            session.add(version)
            session.flush()
            lifecycle = PositionLifecycleState(
                id=uuid.uuid4(), portfolio_id=portfolio.id, market="CN", symbol="000001.SZ",
                strategy_version_id=version.id, lifecycle_policy_version_id=policy.id,
            )
            session.add(lifecycle)
            session.flush()
            fact = PositionDailyFact(
                id=uuid.uuid4(), lifecycle_id=lifecycle.id, trade_date=date(2026, 9, 21),
                price_basis="raw", data_as_of=datetime(2026, 9, 21, tzinfo=timezone.utc),
                input_payload={"close": "10.00"},
                input_hash=hashlib.sha256(b'{"close":"10.00"}').hexdigest(),
                planning_result={"stage": "BEFORE_MIGRATION"}, rule_version="v1",
                state_version_before=1, state_version_after=1,
                final_target_shares=Decimal("100"),
            )
            session.add(fact)
            session.commit()
            fact_id = fact.id

        command.upgrade(config, "0044")
        with factory() as session:
            baseline = session.scalar(select(PositionDailyFactRevision).where(
                PositionDailyFactRevision.daily_fact_id == fact_id))
            assert baseline.revision_no == 1
            assert baseline.previous_revision_id is None
            assert baseline.baseline_origin == "MIGRATED"
            assert baseline.input_payload == {"close": "10.00"}
            assert baseline.planning_result == {"stage": "BEFORE_MIGRATION"}
            current = session.get(PositionDailyFact, fact_id)
            current.planning_result = {"stage": "AFTER_MIGRATION"}
            session.commit()

        command.upgrade(config, "0045")
        with factory() as session:
            latest = session.scalar(select(PositionDailyFactRevision).where(
                PositionDailyFactRevision.daily_fact_id == fact_id,
            ).order_by(PositionDailyFactRevision.revision_no.desc()))
            equivalent_bytes = b'{ "close": "10.00" }'
            with pytest.raises(Exception, match="base or change is invalid"), session.begin_nested():
                session.add(PositionDailyFactInputProposal(
                    id=uuid.uuid4(), daily_fact_id=fact_id,
                    base_revision_id=latest.id, request_key="equivalent-bytes",
                    proposed_price_basis="raw",
                    proposed_data_as_of=datetime(2026, 9, 21, tzinfo=timezone.utc),
                    proposed_input_payload={"close": "10.00"},
                    canonical_input_bytes=equivalent_bytes,
                    proposed_input_hash=hashlib.sha256(equivalent_bytes).hexdigest(),
                    reason_code="SOURCE_PRICE_CORRECTION",
                    source_ref="fixture:equivalent", source_sha256="d" * 64,
                ))
                session.flush()
            with pytest.raises(Exception, match="payload digest is invalid"), session.begin_nested():
                session.add(PositionDailyFactInputProposal(
                    id=uuid.uuid4(), daily_fact_id=fact_id,
                    base_revision_id=latest.id, request_key="forged-proposal",
                    proposed_price_basis="raw",
                    proposed_data_as_of=datetime(2026, 9, 21, tzinfo=timezone.utc),
                    proposed_input_payload={"close": "11.00"},
                    canonical_input_bytes=b'{"close":"11.00"}',
                    proposed_input_hash="f" * 64,
                    reason_code="SOURCE_PRICE_CORRECTION",
                    source_ref="fixture:forged", source_sha256="d" * 64,
                ))
                session.flush()
            proposal, created = propose_daily_fact_input(
                session, portfolio_id=portfolio.id, lifecycle_id=lifecycle.id,
                trade_date=date(2026, 9, 21), request_key="migration-proposal",
                input_payload={"close": "11.00"}, price_basis="raw",
                data_as_of=datetime(2026, 9, 21, tzinfo=timezone.utc),
                reason_code="SOURCE_PRICE_CORRECTION", source_ref="fixture:corrected-bar",
                source_sha256="d" * 64,
            )
            assert created and proposal.daily_fact_id == fact_id
            session.commit()
        monkeypatch.delenv("LIVEPROFIT_DAILY_FACT_INPUT_PROPOSAL_EXPORT_PATH", raising=False)
        with pytest.raises(RuntimeError, match="proposal export path required"):
            command.downgrade(config, "0044")
        with factory() as session:
            current = session.get(PositionDailyFact, fact_id)
            current.planning_result = {"stage": "NEW_PROPOSAL_BASE"}
            session.commit()
        proposal_export = tmp_path / "daily_fact_input_proposals.jsonl"
        monkeypatch.setenv("LIVEPROFIT_DAILY_FACT_INPUT_PROPOSAL_EXPORT_PATH", str(proposal_export))
        with factory() as writer:
            second, created = propose_daily_fact_input(
                writer, portfolio_id=portfolio.id, lifecycle_id=lifecycle.id,
                trade_date=date(2026, 9, 21), request_key="migration-proposal-2",
                input_payload={"close": "12.00"}, price_basis="raw",
                data_as_of=datetime(2026, 9, 21, tzinfo=timezone.utc),
                reason_code="SOURCE_PRICE_CORRECTION", source_ref="fixture:second-bar",
                source_sha256="e" * 64,
            )
            assert created and second.base_revision_id != proposal.base_revision_id
            with ThreadPoolExecutor(max_workers=1) as pool:
                downgrade = pool.submit(command.downgrade, config, "0044")
                blocked = False
                try:
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        with engine.connect() as observer:
                            blocked = observer.scalar(text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity
                                    WHERE datname = current_database()
                                      AND wait_event_type = 'Lock'
                                      AND query LIKE 'LOCK TABLE position_daily_fact_input_proposals%')
                            """))
                        if blocked:
                            break
                        time.sleep(0.05)
                finally:
                    writer.commit()
                assert blocked, "proposal downgrade must wait for the in-flight insert"
                downgrade.result(timeout=10)
        proposal_header, *proposal_rows = proposal_export.read_bytes().splitlines(keepends=True)
        assert json.loads(proposal_header)["rows"] == 2
        assert json.loads(proposal_header)["sha256"] == hashlib.sha256(b"".join(proposal_rows)).hexdigest()

        monkeypatch.delenv("LIVEPROFIT_DAILY_FACT_REVISION_EXPORT_PATH", raising=False)
        with pytest.raises(RuntimeError, match="export path required"):
            command.downgrade(config, "0043")
        export_path = tmp_path / "daily_fact_revisions.jsonl"
        monkeypatch.setenv("LIVEPROFIT_DAILY_FACT_REVISION_EXPORT_PATH", str(export_path))
        with factory() as writer:
            current = writer.get(PositionDailyFact, fact_id)
            current.planning_result = {"stage": "CONCURRENT_BEFORE_DOWNGRADE"}
            writer.flush()  # Holds the parent write lock until commit.
            with ThreadPoolExecutor(max_workers=1) as pool:
                downgrade = pool.submit(command.downgrade, config, "0043")
                blocked = False
                try:
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        with engine.connect() as observer:
                            blocked = observer.scalar(text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity
                                    WHERE datname = current_database()
                                      AND wait_event_type = 'Lock'
                                      AND query LIKE 'LOCK TABLE position_daily_facts%')
                            """))
                        if blocked:
                            break
                        time.sleep(0.05)
                finally:
                    writer.commit()
                assert blocked, "downgrade must wait for the in-flight daily fact writer"
                downgrade.result(timeout=10)
        header, *rows = export_path.read_bytes().splitlines(keepends=True)
        manifest = json.loads(header)
        assert manifest["rows"] == 4
        assert manifest["sha256"] == hashlib.sha256(b"".join(rows)).hexdigest()
        assert [json.loads(row)["revision_no"] for row in rows] == [1, 2, 3, 4]
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM position_daily_facts WHERE id=:id"),
                                     {"id": fact_id}) == 1
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB_NAME}" WITH (FORCE)'))
        admin.dispose()
