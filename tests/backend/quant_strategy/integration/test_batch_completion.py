# test-catalog-begin
# {
#   "purpose": "量化策略 / batch_completion（批次）：Only conftest's isolated liveprofit_quant_strategy_test is mutated.",
#   "keywords": [
#     "量化策略",
#     "批次",
#     "并发",
#     "每日",
#     "持仓生命周期",
#     "订单",
#     "重放",
#     "重试",
#     "事务回滚",
#     "交易信号",
#     "batch_completion",
#     "batch",
#     "concurrent",
#     "daily",
#     "lifecycle",
#     "order",
#     "replay",
#     "retry",
#     "rollback",
#     "signals"
#   ],
#   "covers": [
#     "backend/modules/analysis/infrastructure/models.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_strategy/application/batch_completion.py",
#     "backend/modules/quant_strategy/application/entry_batch.py",
#     "backend/modules/quant_strategy/application/family_batch.py",
#     "backend/modules/quant_strategy/application/lifecycle_batch.py",
#     "backend/modules/quant_strategy/application/lifecycle_service.py",
#     "backend/modules/quant_strategy/application/scan_manifest.py",
#     "backend/modules/quant_strategy/infrastructure/allocation_models.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py",
#     "backend/modules/quant_strategy/infrastructure/signals.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Only conftest's isolated liveprofit_quant_strategy_test is mutated."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from tests.backend.quant_strategy.support.entry_batch import setup as entry_setup, consume, CLOSES, DAY
from backend.modules.analysis.infrastructure.models import AnalysisTask
from backend.modules.investment_workspace.infrastructure.models import Portfolio
from backend.modules.quant_strategy.application.batch_completion import BatchCompletionService, recover_batches, ready_batch_ids
from backend.modules.quant_strategy.application.scan_manifest import ScanManifestService
from backend.modules.quant_strategy.application.entry_batch import EntryBatchService
from backend.modules.quant_strategy.application.family_batch import FamilyBatchService
from backend.modules.quant_strategy.application.lifecycle_batch import _intent
from backend.modules.quant_strategy.application.lifecycle_service import LifecycleOrderService
from backend.modules.quant_strategy.infrastructure.allocation_models import AllocationBatch, AllocationMember, AllocationExecution, AllocationOutcome
from backend.modules.quant_strategy.infrastructure.lifecycle_models import SuggestedOrder
from backend.modules.quant_strategy.infrastructure.signals import QuantExecutionSignal


def setup(env, *, expired=False):
    pid, versions, batch_id, ids = entry_setup(env)
    with env["session_factory"]() as session:
        if expired:
            old = session.get(AllocationBatch, batch_id)
            new = AllocationBatch(id=uuid4(), portfolio_id=pid, request_key=str(uuid4()), input_hash=old.input_hash,
                asset_scope=old.asset_scope, valuation_date=old.valuation_date, decision_at=old.decision_at-timedelta(days=1),
                status=old.status, account_snapshot=old.account_snapshot, result=old.result)
            session.add(new)
            session.flush()
            for member in session.scalars(select(AllocationMember).where(AllocationMember.batch_id == batch_id)):
                session.add(AllocationMember(batch_id=new.id, strategy_version_id=member.strategy_version_id,
                    family_id=member.family_id, admission_event_id=member.admission_event_id,
                    admission_code=member.admission_code, intent=member.intent))
            session.flush()
            batch_id = new.id
        scans = ScanManifestService(session).register(batch_id=batch_id,
            expected_portfolio_version=session.get(Portfolio, pid).version)
        for signal_id in ids:
            signal = session.get(QuantExecutionSignal, signal_id)
            signal.task_id = scans[signal.strategy_version_id]
            signal.execution_market = {**signal.execution_market, "batch_context": {
                "industry": {"industry_code": "801010", "name": "fixture"},
                "industry_bucket_available": True, "new_risk_allowed": True,
            }}
        for task_id in scans.values():
            session.get(AnalysisTask, task_id).status = "SUCCEEDED"
        session.commit()
        return pid, batch_id, scans, ids


def test_automatic_entries_rollback_recover_and_terminal_replay(env):
    pid, batch_id, scans, _ids = setup(env)
    factory = env["session_factory"]
    with factory() as session:
        outcome = BatchCompletionService(session).for_task(next(iter(scans.values())))
        assert outcome.status == "COMPLETED"
        session.rollback()
        assert session.get(AllocationOutcome, batch_id) is None
        assert session.get(AllocationExecution, batch_id) is None
        assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 0
    assert recover_batches(factory) >= 1  # lost callback repaired without re-scanning
    with factory() as session:
        outcome = session.get(AllocationOutcome, batch_id)
        assert outcome.reason_code == "ENTRY_COMPLETED"
        order = session.scalar(select(SuggestedOrder).where(SuggestedOrder.portfolio_id == pid))
        order.status = "CANCELLED"
        session.commit()
        assert BatchCompletionService(session).complete(batch_id).entry_receipt_id == batch_id
        assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 1
        assert batch_id not in ready_batch_ids(session)
        with pytest.raises(DBAPIError):
            session.execute(text("DELETE FROM quant_allocation_outcomes WHERE batch_id=:id"), {"id": batch_id})
        session.rollback()


@pytest.mark.parametrize("status", ["PENDING", "QUEUED", "RUNNING", "RETRYING"])
def test_nonterminal_members_wait_and_do_not_starve_ready_batches(env, status):
    _pid, batch_id, scans, _ids = setup(env)
    with env["session_factory"]() as session:
        task = session.get(AnalysisTask, next(iter(scans.values())))
        task.status = status
        session.commit()
        assert BatchCompletionService(session).complete(batch_id) is None
        assert batch_id not in ready_batch_ids(session)
        assert session.get(AllocationOutcome, batch_id) is None


@pytest.mark.parametrize("status", ["FAILED", "CANCELLED", "CANCEL_REQUESTED"])
def test_failed_member_blocks_whole_batch_permanently(env, status):
    pid, batch_id, scans, ids = setup(env)
    with env["session_factory"]() as session:
        task = session.get(AnalysisTask, next(iter(scans.values())))
        task.status = status
        session.commit()
        assert batch_id in ready_batch_ids(session)
        result = BatchCompletionService(session).complete(batch_id)
        assert result.reason_code == "SCAN_FAILED_OR_CANCELLED"
        session.commit()
        task.status = "SUCCEEDED"
        session.commit()
        assert BatchCompletionService(session).complete(batch_id).status == "BLOCKED"
        with pytest.raises(ValueError, match="terminally blocked"):
            consume(session, batch_id, ids)
        session.rollback()
        assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 0


def test_expired_batch_closes_even_when_retrying(env):
    _pid, batch_id, scans, _ids = setup(env, expired=True)
    with env["session_factory"]() as session:
        session.get(AnalysisTask, next(iter(scans.values()))).status = "RETRYING"
        session.commit()
        assert batch_id in ready_batch_ids(session)
        assert BatchCompletionService(session).complete(batch_id).reason_code == "BATCH_DECISION_EXPIRED"
        session.commit()


def test_retry_consumes_only_current_attempt_signals(env):
    _pid, batch_id, scans, ids = setup(env)
    with env["session_factory"]() as session:
        for task_id in scans.values():
            session.get(AnalysisTask, task_id).attempt_no = 2
        for signal_id in ids:
            session.get(QuantExecutionSignal, signal_id).attempt_no = 2
        session.commit()
        result = BatchCompletionService(session).complete(batch_id)
        session.commit()
        assert result.status == "COMPLETED"
        assert all(t["attempt_no"] == 2 for t in result.details["tasks"])


@pytest.mark.parametrize("case", ["missing_context", "conflict", "protection_only", "missing_signal", "wrong_mode"])
def test_invalid_scan_inputs_block_without_orders(env, case):
    pid, batch_id, scans, ids = setup(env)
    with env["session_factory"]() as session:
        signal = session.get(QuantExecutionSignal, ids[0])
        if case == "missing_signal":
            signal.attempt_no = 0
        elif case == "wrong_mode":
            task = session.get(AnalysisTask, signal.task_id)
            snap = {**task.request_params["execution_snapshot"], "new_risk_mode": "DIRECT"}
            task.request_params = {**task.request_params, "execution_snapshot": snap}
        else:
            market = dict(signal.execution_market)
            if case == "missing_context":
                market.pop("batch_context")
            elif case == "conflict":
                market["batch_context"] = {**market["batch_context"], "industry": {"industry_code": "other"}}
            else:
                market["batch_context"] = {**market["batch_context"], "new_risk_allowed": False}
            signal.execution_market = market
        session.commit()
        result = BatchCompletionService(session).complete(batch_id)
        session.commit()
        assert result.status == "BLOCKED"
        assert session.get(AllocationExecution, batch_id) is None
        assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 0


def test_consumer_value_error_rolls_back_partial_mutations_before_terminal(env, monkeypatch):
    _pid, batch_id, _scans, ids = setup(env)

    def fail(service, **kwargs):
        row = service.session.get(QuantExecutionSignal, ids[0])
        row.order_status = "ELIGIBLE"
        service.session.flush()
        raise ValueError("fixture late validation failure")

    monkeypatch.setattr(EntryBatchService, "materialize", fail)
    with env["session_factory"]() as session:
        assert BatchCompletionService(session).complete(batch_id).status == "BLOCKED"
        session.commit()
        assert session.get(QuantExecutionSignal, ids[0]).order_status is None


def test_concurrent_completion_creates_only_one_order(env):
    pid, batch_id, _scans, _ids = setup(env)
    barrier = Barrier(2)

    def run(_):
        with env["session_factory"]() as session:
            barrier.wait(timeout=10)
            result = BatchCompletionService(session).complete(batch_id)
            session.commit()
            return result.entry_receipt_id

    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(run, range(2))) == [batch_id, batch_id]
    with env["session_factory"]() as session:
        assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 1


def test_existing_lifecycle_without_owner_daily_still_blocks_automatic_entries(env):
    pid, versions, original_id, ids = entry_setup(env, lifecycle=True)
    with env["session_factory"]() as session:
        receipt = consume(session, original_id, ids)
        session.commit()
        order = session.get(SuggestedOrder, UUID(receipt.result["signals"][1]["order_id"]))
        LifecycleOrderService(session).confirm_fill(order.id, idempotency_key="coordination-fill",
            quantity=order.quantity, fill_price=order.limit_price, fill_trade_date=DAY, expected_revision=order.revision)
        members = list(session.scalars(select(AllocationMember).where(AllocationMember.batch_id == original_id)))
        batch = FamilyBatchService(session).project(portfolio_id=pid, request_key="after-fill",
            asset_scope="CN_STOCK", expected_version_ids=versions, intents=tuple(_intent(m.intent) for m in members),
            valuation_date=DAY, closes=CLOSES)
        scans = ScanManifestService(session).register(batch_id=batch.id,
            expected_portfolio_version=session.get(Portfolio, pid).version)
        for task_id in scans.values():
            session.get(AnalysisTask, task_id).status = "SUCCEEDED"
        session.commit()
        outcome = BatchCompletionService(session).complete(batch.id)
        assert outcome.reason_code == "BATCH_INPUT_REJECTED"
        assert outcome.details["message"] == "complete owner lifecycle day required"
        session.commit()
