# test-catalog-begin
# {
#   "purpose": "量化策略 / scan_manifest：Registration tests only write conftest's liveprofit_quant_strategy_test.",
#   "keywords": [
#     "量化策略",
#     "并发",
#     "重放",
#     "任务",
#     "scan_manifest",
#     "concurrent",
#     "replay",
#     "task"
#   ],
#   "covers": [
#     "backend/modules/analysis/infrastructure/models.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_strategy/application/errors.py",
#     "backend/modules/quant_strategy/application/family_batch.py",
#     "backend/modules/quant_strategy/application/scan_manifest.py",
#     "backend/modules/quant_strategy/infrastructure/allocation_models.py",
#     "backend/modules/quant_strategy/infrastructure/models.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Registration tests only write conftest's liveprofit_quant_strategy_test."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from tests.backend.quant_strategy.support.family_batch import seed, args
from backend.modules.analysis.infrastructure.models import AnalysisTask, TaskOutbox
from backend.modules.investment_workspace.infrastructure.models import Portfolio
from backend.modules.quant_strategy.application.family_batch import FamilyBatchService
from backend.modules.quant_strategy.application.scan_manifest import ScanManifestService
from backend.modules.quant_strategy.infrastructure.allocation_models import AllocationScan
from backend.modules.quant_strategy.infrastructure.models import QuantStrategyVersion
from backend.modules.quant_strategy.application.errors import StrategyVersionNotPublishedError


def setup(env):
    pid, versions = seed(env)
    with env["session_factory"]() as session:
        batch = FamilyBatchService(session).project(**args(pid, versions))
        version = session.get(Portfolio, pid).version
        session.commit()
        return batch.id, pid, version, versions


def register(session, batch_id, version):
    return ScanManifestService(session).register(batch_id=batch_id, expected_portfolio_version=version)


def test_manifest_is_atomic_and_replay_retains_current_task_attempt(env):
    batch_id, pid, version, versions = setup(env)
    factory = env["session_factory"]
    with factory() as session:
        scans = register(session, batch_id, version)
        assert set(scans) == set(versions)
        with factory() as observer:
            assert observer.scalar(select(AllocationScan).where(AllocationScan.batch_id == batch_id)) is None
            assert not list(observer.scalars(select(AnalysisTask).where(AnalysisTask.id.in_(scans.values()))))
        session.rollback()
        assert session.scalar(select(AllocationScan).where(AllocationScan.batch_id == batch_id)) is None
        scans = register(session, batch_id, version)
        session.commit()
        for vid, tid in scans.items():
            task = session.get(AnalysisTask, tid)
            frozen = task.request_params["execution_snapshot"]
            assert frozen["new_risk_mode"] == "FAMILY_BATCH"
            assert frozen["strategy"]["version_id"] == str(vid)
            assert frozen["portfolio"]["id"] == str(pid)
            assert task.requested_trade_date == task.effective_trade_date
            assert session.scalar(select(TaskOutbox).where(TaskOutbox.task_id == tid)).attempt_no == 1
            task.status, task.attempt_no = "RETRYING", 2
        session.get(Portfolio, pid).version += 1
        session.commit()
        assert register(session, batch_id, version) == scans
        with pytest.raises(ValueError, match="version conflict"):
            register(session, batch_id, version + 1)


def test_registration_failure_leaves_no_partial_tasks_or_manifest(env):
    batch_id, _pid, version, versions = setup(env)
    factory = env["session_factory"]
    with factory() as session:
        # Fail the second staged member after the first task/outbox were flushed.
        session.get(QuantStrategyVersion, sorted(versions)[1]).status = "DRAFT"
        session.commit()
        before = session.query(AnalysisTask).count(), session.query(TaskOutbox).count()
        with pytest.raises(StrategyVersionNotPublishedError):
            register(session, batch_id, version)
        session.rollback()
        assert (session.query(AnalysisTask).count(), session.query(TaskOutbox).count()) == before
        assert session.scalar(select(AllocationScan).where(AllocationScan.batch_id == batch_id)) is None


def test_concurrent_registration_has_one_task_per_member(env):
    batch_id, _pid, version, _versions = setup(env)
    factory, barrier = env["session_factory"], Barrier(2)

    def run(_):
        with factory() as session:
            barrier.wait(timeout=10)
            result = register(session, batch_id, version)
            session.commit()
            return result

    with ThreadPoolExecutor(2) as pool:
        first, second = list(pool.map(run, range(2)))
    assert first == second
    with factory() as session:
        assert session.query(AllocationScan).filter_by(batch_id=batch_id).count() == 2
        assert session.query(TaskOutbox).filter(TaskOutbox.task_id.in_(first.values())).count() == 2
        with pytest.raises(DBAPIError):
            session.execute(text("DELETE FROM quant_allocation_scans WHERE batch_id=:id"), {"id": batch_id})
        session.rollback()


def test_registration_rejects_wrong_account_version_without_task(env):
    batch_id, _pid, version, _versions = setup(env)
    with env["session_factory"]() as session:
        with pytest.raises(ValueError, match="version conflict"):
            register(session, batch_id, version + 1)
        session.rollback()
        assert session.scalar(select(AllocationScan).where(AllocationScan.batch_id == batch_id)) is None
