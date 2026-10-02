# test-catalog-begin
# {
#   "purpose": "量化策略 / admission_service（服务）：Uses only conftest's isolated liveprofit_quant_strategy_test database.",
#   "keywords": [
#     "量化策略",
#     "执行准入",
#     "并发",
#     "策略族",
#     "历史审计",
#     "迁移",
#     "订单",
#     "重放",
#     "版本修订",
#     "admission_service",
#     "admission",
#     "concurrent",
#     "family",
#     "history",
#     "migration",
#     "order",
#     "replay",
#     "revision"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/strategy_admission.py",
#     "backend/modules/quant_strategy/infrastructure/admission_models.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py",
#     "backend/modules/quant_strategy/infrastructure/models.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Uses only conftest's isolated liveprofit_quant_strategy_test database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Barrier
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.modules.quant_strategy.application.strategy_admission import StrategyAdmissionService
from backend.modules.quant_strategy.infrastructure.admission_models import StrategyAdmissionEvent
from backend.modules.quant_strategy.infrastructure.models import QuantStrategy, QuantStrategyVersion
from backend.modules.quant_strategy.infrastructure.lifecycle_models import LifecyclePolicyVersion  # noqa: F401


def seed(factory):
    with factory() as session:
        strategy = QuantStrategy(id=uuid.uuid4(), name=f"admission-{uuid.uuid4().hex}", version=1)
        session.add(strategy)
        session.flush()
        version = QuantStrategyVersion(id=uuid.uuid4(), strategy_id=strategy.id, version_no=1,
                                       status="PUBLISHED", source_code="def strategy(context): return {}",
                                       source_hash="f"*64, version=1)
        session.add(version)
        session.commit()
        return version.id


def restrict(service, version_id, **changes):
    args = dict(family_id="trend", asset_scope="CN_STOCK", risk_profile="BALANCED",
                state="EXPERIMENTAL", reason="new candidate", request_key="initial", expected_revision=0)
    args.update(changes)
    return service.record_restriction(version_id, **args)


def read(service, version_id, at, **changes):
    args = dict(asset_scope="CN_STOCK", risk_profile="BALANCED", decision_at=at)
    args.update(changes)
    return service.read(version_id, **args)


def test_published_is_not_admission_and_restriction_replay_is_strict(env):
    factory = env["session_factory"]
    version = seed(factory)
    with factory() as session:
        service = StrategyAdmissionService(session)
        assert read(service, version, datetime.now(timezone.utc)).code == "ADMISSION_MISSING"
        first = restrict(service, version)
        session.commit()
        assert restrict(service, version).id == first.id
        with pytest.raises(ValueError, match="different content"):
            restrict(service, version, reason="changed")
        session.rollback()
        assert not read(service, version, datetime.now(timezone.utc)).allowed


@pytest.mark.parametrize("state", ["VALIDATED", "SHADOW", "ADVISORY"])
def test_publication_or_ordinary_writer_cannot_grant_positive_qualification(env, state):
    factory = env["session_factory"]
    version = seed(factory)
    with factory() as session:
        with pytest.raises(ValueError, match="verified evidence"):
            restrict(StrategyAdmissionService(session), version, state=state)


def test_scoped_asof_reader_does_not_leak_later_evidence_or_revive_expired_grant(env):
    factory = env["session_factory"]
    version = seed(factory)
    base = datetime.now(timezone.utc) - timedelta(days=3)
    # Trusted-producer rows are fixtures only; the application has no grant writer yet.
    with factory() as session:
        session.add(StrategyAdmissionEvent(
            strategy_version_id=version, family_id="trend", asset_scope="CN_STOCK", risk_profile="BALANCED",
            revision=1, state="ADVISORY", reason="fixture verified shadow", request_key="fixture",
            recorded_at=base, evidence_ref="test://shadow-report", evidence_sha256="a"*64,
            evidence_completed_at=base-timedelta(hours=1), valid_until=base+timedelta(days=1),
            net_expectancy_lower_bound=Decimal(".001"),
        ))
        session.commit()
    with factory() as session:
        service = StrategyAdmissionService(session)
        assert read(service, version, base).code == "ADMISSION_MISSING"
        assert read(service, version, base+timedelta(hours=1)).allowed
        assert read(service, version, base+timedelta(hours=1), asset_scope="CN_ETF").code == "ADMISSION_MISSING"
        assert read(service, version, base+timedelta(hours=1), risk_profile="AGGRESSIVE").code == "ADMISSION_MISSING"
        assert read(service, version, base+timedelta(days=1)).code == "ADMISSION_EXPIRED"
        restricted = restrict(service, version, state="SUSPENDED", reason="evidence invalidated",
                              request_key="suspend", expected_revision=1)
        session.commit()
        assert read(service, version, restricted.recorded_at+timedelta(seconds=1)).code == "ADMISSION_SUSPENDED"
        assert read(service, version, base+timedelta(hours=1)).allowed


def test_retirement_and_family_binding_cannot_be_reset(env):
    factory = env["session_factory"]
    version = seed(factory)
    with factory() as session:
        service = StrategyAdmissionService(session)
        restrict(service, version, state="RETIRED")
        session.commit()
        with pytest.raises(ValueError, match="retired"):
            restrict(service, version, state="SUSPENDED", request_key="reopen", expected_revision=1)
        session.rollback()
        with pytest.raises(ValueError, match="across scopes"):
            restrict(service, version, family_id="other", asset_scope="CN_ETF", request_key="other")


@pytest.mark.parametrize("operation", ["UPDATE strategy_admission_events SET reason='rewrite' WHERE id=:id",
                                       "DELETE FROM strategy_admission_events WHERE id=:id"])
def test_database_rejects_history_mutation(env, operation):
    factory = env["session_factory"]
    version = seed(factory)
    with factory() as session:
        event = restrict(StrategyAdmissionService(session), version)
        session.commit()
        with pytest.raises(DBAPIError, match="append-only"):
            session.execute(text(operation), {"id": event.id})
        session.rollback()


def test_concurrent_expected_revision_writers_do_not_overwrite(env):
    factory = env["session_factory"]
    version = seed(factory)
    barrier = Barrier(2)

    def write(key):
        with factory() as session:
            barrier.wait(timeout=5)
            try:
                restrict(StrategyAdmissionService(session), version, request_key=key)
                session.commit()
                return "ok"
            except ValueError as exc:
                session.rollback()
                assert "revision conflict" in str(exc)
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, ["first", "second"])) == ["conflict", "ok"]


def test_database_rejects_advisory_without_evidence(env):
    factory = env["session_factory"]
    version = seed(factory)
    with factory() as session:
        session.add(StrategyAdmissionEvent(strategy_version_id=version, family_id="trend", asset_scope="CN_STOCK",
                                          risk_profile="BALANCED", revision=1, state="ADVISORY",
                                          reason="invalid grant", request_key="bad"))
        with pytest.raises(DBAPIError):
            session.flush()
        session.rollback()


def test_admission_orm_and_migration_constraints_match(env):
    from sqlalchemy import inspect
    table = StrategyAdmissionEvent.__table__
    with env["session_factory"]() as session:
        inspector = inspect(session.connection())
        expected_unique = {c.name for c in table.constraints if c.__class__.__name__ == "UniqueConstraint"}
        expected_check = {c.name for c in table.constraints if c.__class__.__name__ == "CheckConstraint"}
        assert {c["name"] for c in inspector.get_unique_constraints(table.name)} == expected_unique
        assert {c["name"] for c in inspector.get_check_constraints(table.name)} == expected_check
        assert {i.name for i in table.indexes} <= {i["name"] for i in inspector.get_indexes(table.name)}


def test_live_gate_serializes_restrictions_until_order_transaction_ends(env):
    factory = env["session_factory"]
    version = seed(factory)
    with factory() as order_session:
        service = StrategyAdmissionService(order_session)
        result = service.gate_new_risk(version, asset_scope="CN_STOCK", risk_profile="BALANCED")
        assert result["code"] == "ADMISSION_MISSING"
        assert result["decision_at"]
        with factory() as writer:
            writer.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError, match="lock timeout"):
                restrict(StrategyAdmissionService(writer), version, state="SUSPENDED")
            writer.rollback()
        order_session.commit()
    with factory() as writer:
        restrict(StrategyAdmissionService(writer), version, state="SUSPENDED")
        writer.commit()
    with factory() as session:
        result = StrategyAdmissionService(session).gate_new_risk(
            version, asset_scope="CN_STOCK", risk_profile="BALANCED")
        assert result["code"] == "ADMISSION_SUSPENDED"
        assert result["revision"] == 1
        assert result["event_id"]
