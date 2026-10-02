# test-catalog-begin
# {
#   "purpose": "量化策略 / entry_batch（建仓、批次）",
#   "keywords": [
#     "量化策略",
#     "批次",
#     "绑定",
#     "现金",
#     "认证证书",
#     "并发",
#     "执行",
#     "策略族",
#     "成交",
#     "不可变历史",
#     "证券数据",
#     "持仓生命周期",
#     "订单",
#     "投资组合",
#     "事务回滚",
#     "品种规则",
#     "交易信号",
#     "entry_batch",
#     "batch",
#     "bound",
#     "cash",
#     "certificate",
#     "concurrent",
#     "execution",
#     "family",
#     "fill",
#     "immutable",
#     "instrument",
#     "lifecycle",
#     "order",
#     "portfolio",
#     "rollback",
#     "rule",
#     "signal"
#   ],
#   "covers": [
#     "backend/modules/analysis/infrastructure/models.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_research/application/trial_executor.py",
#     "backend/modules/quant_strategy/application/certified_instrument_rules.py",
#     "backend/modules/quant_strategy/application/entry_batch.py",
#     "backend/modules/quant_strategy/application/family_batch.py",
#     "backend/modules/quant_strategy/application/lifecycle_batch.py",
#     "backend/modules/quant_strategy/application/lifecycle_service.py",
#     "backend/modules/quant_strategy/application/order_materialization.py",
#     "backend/modules/quant_strategy/application/strategy_admission.py",
#     "backend/modules/quant_strategy/application/target_trial_binding.py",
#     "backend/modules/quant_strategy/domain/portfolio_targets.py",
#     "backend/modules/quant_strategy/infrastructure/allocation_models.py",
#     "backend/modules/quant_strategy/infrastructure/instrument_rule_certificates.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py",
#     "backend/modules/quant_strategy/infrastructure/models.py",
#     "backend/modules/quant_strategy/infrastructure/signals.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

import tests.backend.quant_strategy.support.entry_batch as entry_batch
"""Joint entry consumer tests use only conftest's isolated PG database."""

from tests.backend.quant_strategy.support.entry_batch import (
    CLOSES,
    INDUSTRIES,
    certified_rule,
    setup,
    consume,
)
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import base64
import json
from decimal import Decimal as D
from hashlib import sha256
from uuid import uuid4, UUID
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from sqlalchemy import select

from tests.backend.quant_strategy.support.family_batch import seed, args
from backend.modules.analysis.infrastructure.models import AnalysisTask
from backend.modules.investment_workspace.infrastructure.models import Portfolio
from backend.modules.quant_strategy.application.family_batch import FamilyBatchService
from backend.modules.quant_strategy.application.entry_batch import EntryBatchService
from backend.modules.quant_strategy.application.strategy_admission import StrategyAdmissionService
from backend.modules.quant_strategy.infrastructure.allocation_models import AllocationExecution
from backend.modules.quant_strategy.infrastructure.signals import QuantExecutionSignal
from backend.modules.quant_strategy.infrastructure.lifecycle_models import SuggestedOrder, PositionLifecycleState
from backend.modules.quant_strategy.application.lifecycle_service import LifecyclePolicyService, LifecycleOrderService
from backend.modules.quant_strategy.infrastructure.models import QuantStrategyVersion
from backend.modules.quant_research.application.trial_executor import prepare_trial
from backend.modules.quant_strategy.application.target_trial_binding import TargetTrialBindingService









def test_current_day_signed_rule_certificate_creates_bound_joint_buy(env, monkeypatch):
    from backend.modules.quant_strategy.application.family_batch import CN_TIME
    from backend.modules.quant_strategy.infrastructure.instrument_rule_certificates import (
        InstrumentRuleCertificateRepository,
    )
    from tests.backend.quant_strategy.support.instrument_rule_certificates import _signed, KEY

    monkeypatch.setitem(setup.__globals__, "DAY", datetime.now(CN_TIME).date())
    monkeypatch.setenv("LIVEPROFIT_RULE_REVIEW_KEYS", json.dumps({
        "rule-reviewer": base64.b64encode(KEY).decode(),
    }))
    pid, _, batch_id, ids = setup(env)
    with env["session_factory"]() as session:
        certificate_id, observation, rule_capture, identity_capture, signature, rule = _signed(
            session, symbol="000001.SZ", step=100)
        InstrumentRuleCertificateRepository(session).issue(
            certificate_id=certificate_id, observation_id=observation.id,
            rule_capture_id=rule_capture.id, identity_capture_id=identity_capture.id,
            reviewer_id="rule-reviewer", interpretation_note="verified fixture",
            identity_symbol=rule.symbol, identity_asset_type="stock",
            identity_locator="fixture identity row 1", review_signature=signature,
        )
        session.commit()
    with env["session_factory"]() as session:
        from backend.modules.quant_strategy.application.certified_instrument_rules import live_authorizations
        assert "000001.SZ" in live_authorizations(
            session, symbols={"000001.SZ"}, decision_date=entry_batch.DAY)
        receipt = consume(session, batch_id, ids)
        session.commit()
        eligible = [row for row in receipt.result["signals"] if row["order_status"] == "ELIGIBLE"]
        assert len(eligible) == 1, receipt.result["signals"]
        order = session.get(SuggestedOrder, UUID(eligible[0]["order_id"]))
        assert order.rule_certificate_id == certificate_id and order.side == "BUY"
        assert order.rule_authorized_at <= order.decision_at
        assert order.decision_at.astimezone(CN_TIME).date() == entry_batch.DAY


def test_authorization_cannot_cross_midnight_before_order_creation(env, certified_rule, monkeypatch):
    from backend.modules.quant_strategy.application import order_materialization
    from backend.modules.quant_strategy.application.family_batch import CN_TIME

    pid, _, batch_id, ids = setup(env)
    certified_rule()
    following_day = entry_batch.DAY + timedelta(days=1)

    class AfterMidnight(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.combine(following_day, datetime.min.time(), CN_TIME).astimezone(timezone.utc)

    monkeypatch.setattr(order_materialization, "datetime", AfterMidnight)
    with env["session_factory"]() as session:
        receipt = consume(session, batch_id, ids)
        session.commit()
        assert any(row["order_status"] == "BUY_REJECTED_INSTRUMENT_RULE"
                   for row in receipt.result["signals"])
        assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 0


def test_joint_entry_prefers_evidence_over_cross_family_score_and_replays(env, certified_rule):
    pid, versions, batch_id, ids = setup(env)
    certified_rule()
    with env["session_factory"]() as session:
        receipt = consume(session, batch_id, ids)
        session.commit()
        rows = receipt.result["signals"]
        assert rows[0]["order_status"] == "BUY_REJECTED_FAMILY_OWNER"
        assert rows[1]["order_status"] == "ELIGIBLE"
        order = session.get(SuggestedOrder, UUID(rows[1]["order_id"]))
        assert order.source_signal_id == ids[1] and order.quantity * order.limit_price <= 11250
        order.status = "CANCELLED"
        session.commit()
        assert consume(session, batch_id, tuple(reversed(ids))).result == receipt.result
        assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 1


def test_missing_certified_instrument_rules_cannot_create_joint_buy(env):
    pid, _, batch_id, ids = setup(env)
    with env["session_factory"]() as session:
        receipt = consume(session, batch_id, ids)
        session.commit()
        assert any(row["order_status"] == "BUY_REJECTED_INSTRUMENT_RULE"
                   for row in receipt.result["signals"])
        assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 0


def test_old_persisted_signal_without_adv20_cannot_create_buy_order(env, certified_rule):
    pid, _, batch_id, ids = setup(env)
    certified_rule()
    with env["session_factory"]() as session:
        signal = session.get(QuantExecutionSignal, ids[1])
        old_market = dict(signal.execution_market)
        old_market.pop("adv20_amount")
        signal.execution_market = old_market
        session.commit()
        receipt = consume(session, batch_id, ids)
        session.commit()
        assert any(row["order_status"] == "BUY_REJECTED_LIQUIDITY"
                   for row in receipt.result["signals"])
        assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 0


def test_balanced_profile_with_over_budget_account_cannot_create_buy_order(env):
    pid, _, batch_id, ids = setup(env)
    with env["session_factory"]() as session:
        session.get(Portfolio, pid).risk_per_trade_pct = D(".01")
        session.commit()
        receipt = consume(session, batch_id, ids)
        session.commit()
        assert any(row["order_status"] == "BUY_REJECTED_PROFILE_BUDGET"
                   for row in receipt.result["signals"])
        assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 0


def test_old_entry_consumer_rejects_newly_bound_portfolio_trial_before_orders(env):
    pid, versions, batch_id, ids = setup(env)
    prepared = prepare_trial("stock_medium_momentum:60:10:60")
    with env["session_factory"]() as session:
        version = session.get(QuantStrategyVersion, versions[0])
        version.source_hash = sha256(version.source_code.encode()).hexdigest()
        policy = LifecyclePolicyService(session).publish_portfolio_trial(prepared)
        version.lifecycle_policy_version_id = policy.id
        session.flush()
        TargetTrialBindingService(session).bind(strategy_version_id=version.id,
            trial_id=prepared.spec.trial_id, asset_scope="CN_STOCK")
        session.commit()
        with pytest.raises(ValueError, match="frozen trial binding"):
            consume(session, batch_id, ids)
        session.rollback()
        assert session.get(AllocationExecution, batch_id) is None
        assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 0


@pytest.mark.parametrize("case", ["missing", "wrong_portfolio", "old_attempt", "running"])
def test_joint_entry_requires_complete_completed_matching_sources(env, case):
    _pid, _versions, batch_id, ids = setup(env)
    with env["session_factory"]() as session:
        if case == "missing":
            ids = ids[:1]
        else:
            signal = session.get(QuantExecutionSignal, ids[0])
            task = session.get(AnalysisTask, signal.task_id)
            if case == "wrong_portfolio":
                task.request_params = {"execution_snapshot": {"strategy": {"version_id": str(signal.strategy_version_id)},
                                                              "portfolio": {"id": str(uuid4())}}}
            elif case == "old_attempt":
                task.attempt_no = 2
            else:
                task.status = "RUNNING"
            session.commit()
        with pytest.raises(ValueError):
            consume(session, batch_id, ids)
        session.rollback()
        assert session.get(AllocationExecution, batch_id) is None


def test_joint_entry_rechecks_pause_and_keeps_terminal_rejection(env):
    pid, versions, batch_id, ids = setup(env)
    with env["session_factory"]() as session:
        StrategyAdmissionService(session).record_restriction(versions[0], family_id="low", asset_scope="CN_STOCK",
            risk_profile="BALANCED", state="SUSPENDED", reason="fixture", request_key="pause", expected_revision=1)
        session.commit()
        receipt = consume(session, batch_id, ids)
        session.commit()
        assert all(s["order_id"] is None for s in receipt.result["signals"])
        assert consume(session, batch_id, ids).result == receipt.result
        assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 0


def test_joint_entry_rollback_and_concurrent_consumption(env, certified_rule):
    pid, _versions, batch_id, ids = setup(env)
    certified_rule()
    factory = env["session_factory"]
    with factory() as session:
        consume(session, batch_id, ids)
        session.rollback()
        assert session.get(AllocationExecution, batch_id) is None
        assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 0
    barrier = Barrier(2)
    def run(_):
        with factory() as session:
            barrier.wait(timeout=5)
            receipt = consume(session, batch_id, ids)
            session.commit()
            return receipt.result
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, range(2)))
    assert results[0] == results[1]
    with factory() as session:
        assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 1


def test_joint_entry_preserves_initial_fraction_and_fill_lifecycle_origin(env, certified_rule):
    pid, versions, batch_id, ids = setup(env, lifecycle=True)
    certified_rule()
    with env["session_factory"]() as session:
        receipt = consume(session, batch_id, ids)
        session.commit()
        order = session.get(SuggestedOrder, UUID(receipt.result["signals"][1]["order_id"]))
        signal = session.get(QuantExecutionSignal, ids[1])
        assert order.quantity == (signal.shares * D(".5") // 100) * 100
        LifecycleOrderService(session).confirm_fill(order.id, idempotency_key="batch-fill", quantity=order.quantity,
            fill_price=order.limit_price, fill_trade_date=entry_batch.DAY, expected_revision=order.revision)
        lifecycle = session.scalar(select(PositionLifecycleState).where(PositionLifecycleState.portfolio_id == pid))
        assert lifecycle.strategy_version_id == versions[1]
        assert lifecycle.risk_capacity_shares <= signal.shares


def test_same_family_cannot_mix_tasks_with_different_execution_policies(env):
    from backend.modules.quant_strategy.infrastructure.allocation_models import AllocationMember
    from backend.modules.quant_strategy.application.lifecycle_batch import _intent
    from backend.modules.quant_strategy.domain.portfolio_targets import TargetLeg
    pid, versions, batch_id, ids = setup(env, same_symbol=False)
    with env["session_factory"]() as session:
        members = list(session.scalars(select(AllocationMember).where(AllocationMember.batch_id == batch_id)))
        intents = tuple(_intent(m.intent) for m in members)
        intents = tuple(replace(i, legs=(TargetLeg("000001.SZ", D(".5")), TargetLeg("000003.SZ", D(".5"))))
                        if i.strategy_version_id == versions[0] else i for i in intents)
        batch = FamilyBatchService(session).project(portfolio_id=pid, request_key="mixed-policy-fixture",
            asset_scope="CN_STOCK", expected_version_ids=versions, intents=intents, valuation_date=entry_batch.DAY, closes=CLOSES)
        original = session.get(QuantExecutionSignal, ids[0])
        task = AnalysisTask(id=uuid4(), task_type="MARKET_WIDE", status="SUCCEEDED", attempt_no=1, effective_trade_date=entry_batch.DAY,
            request_params={"execution_snapshot": {"strategy": {"version_id": str(versions[0])},
                "portfolio": {"id": str(pid)}, "execution_policy": {"slippage_bps": "999"}}},
            selected_layers=["position"], input_hash="b"*64)
        session.add(task)
        session.flush()
        other = QuantExecutionSignal(task_id=task.id, attempt_no=1, strategy_version_id=versions[0],
            ts_code="000003.SZ", signal_kind="BUY", action="BUY", score=D(80), entry_price=D(10), stop_loss=D(9),
            take_profit=D(13), valuation_price=D(10), signal_trade_date=entry_batch.DAY, signal_price_basis="qfq",
            execution_price_basis="raw", execution_market=dict(original.execution_market))
        session.add(other)
        session.commit()
        with pytest.raises(ValueError, match="complete unique entry signal coverage"):
            EntryBatchService(session).materialize(batch_id=batch.id,
                scan_attempts={versions[0]: (original.task_id, 1), versions[1]: (session.get(QuantExecutionSignal, ids[1]).task_id, 1)},
                closes={**CLOSES, "000003.SZ": D(10)}, industry_map=INDUSTRIES, industry_bucket_available=True)
        session.rollback()
        assert session.get(AllocationExecution, batch.id) is None


def test_zero_lot_after_initial_fraction_is_explicitly_rejected(env, certified_rule):
    pid, _versions, batch_id, ids = setup(env, lifecycle=True)
    certified_rule()
    with env["session_factory"]() as session:
        session.get(Portfolio, pid).risk_per_trade_pct = D(".0015")
        session.commit()
        receipt = consume(session, batch_id, ids)
        session.commit()
        winner = receipt.result["signals"][1]
        assert winner["order_status"] == "BUY_REJECTED_INITIAL_LOT" and winner["order_id"] is None
        assert session.query(SuggestedOrder).filter_by(portfolio_id=pid).count() == 0


def test_cash_only_family_still_requires_its_scan_to_finish(env):
    from backend.modules.quant_strategy.infrastructure.allocation_models import AllocationMember
    from backend.modules.quant_strategy.application.lifecycle_batch import _intent
    pid, versions, batch_id, ids = setup(env, same_symbol=False)
    with env["session_factory"]() as session:
        intents = tuple(_intent(m.intent) for m in session.scalars(select(AllocationMember).where(AllocationMember.batch_id == batch_id)))
        intents = tuple(replace(i, legs=(), cash_only=True) if i.strategy_version_id == versions[0] else i for i in intents)
        batch = FamilyBatchService(session).project(portfolio_id=pid, request_key="cash-family",
            asset_scope="CN_STOCK", expected_version_ids=versions, intents=intents, valuation_date=entry_batch.DAY, closes=CLOSES)
        task = session.get(AnalysisTask, session.get(QuantExecutionSignal, ids[0]).task_id)
        task.status = "RUNNING"
        session.commit()
        with pytest.raises(ValueError, match="completed matching current attempts"):
            consume(session, batch.id, ids)
        session.rollback()
        task.status = "SUCCEEDED"
        session.commit()
        receipt = consume(session, batch.id, ids)
        session.commit()
        assert len(receipt.result["scan_attempts"]) == 2
        assert len(receipt.result["signals"]) == 1


def test_receipt_is_immutable_and_rejects_different_input(env):
    from sqlalchemy import text, inspect
    from sqlalchemy.exc import DBAPIError
    _pid, _versions, batch_id, ids = setup(env)
    with env["session_factory"]() as session:
        consume(session, batch_id, ids)
        session.commit()
        scans = {s.strategy_version_id: (s.task_id, s.attempt_no) for s in
                 (session.get(QuantExecutionSignal, signal_id) for signal_id in ids)}
        with pytest.raises(ValueError, match="input conflict"):
            EntryBatchService(session).materialize(batch_id=batch_id, scan_attempts=scans,
                closes={**CLOSES, "000001.SZ": D(11)}, industry_map=INDUSTRIES, industry_bucket_available=True)
        with pytest.raises(DBAPIError, match="immutable"):
            session.execute(text("DELETE FROM quant_allocation_executions WHERE batch_id=:id"), {"id": batch_id})
        session.rollback()
        checks = inspect(session.connection()).get_check_constraints("quant_allocation_executions")
        assert {c["name"] for c in checks} == {"ck_allocation_execution_identity"}
