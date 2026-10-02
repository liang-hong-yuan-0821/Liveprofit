# test-catalog-begin
# {
#   "purpose": "量化策略 / initial_entry_completion（建仓）：Partial first-entry transitions in the dedicated quant_strategy test database.",
#   "keywords": [
#     "量化策略",
#     "成交",
#     "订单",
#     "数量",
#     "initial_entry_completion",
#     "fill",
#     "order",
#     "quantity"
#   ],
#   "covers": [
#     "backend/modules/analysis/infrastructure/models.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_strategy/application/errors.py",
#     "backend/modules/quant_strategy/application/lifecycle_service.py",
#     "backend/modules/quant_strategy/domain/family_management.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py",
#     "backend/modules/quant_strategy/infrastructure/models.py",
#     "backend/modules/quant_strategy/infrastructure/signals.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Partial first-entry transitions in the dedicated quant_strategy test database.

The directory's env fixture recreates only liveprofit_quant_strategy_test and
runs migrations there. These scenarios call no market provider or real LLM.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from backend.modules.analysis.infrastructure.models import AnalysisTask
from backend.modules.investment_workspace.infrastructure.models import Portfolio, PortfolioPosition
from backend.modules.quant_strategy.application.errors import LifecycleInvalidStateError
from backend.modules.quant_strategy.application.lifecycle_service import LifecycleOrderService, LifecyclePolicyService
from backend.modules.quant_strategy.domain.family_management import MACD_MEAN_REVERSION, TREND_3ATR
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    FillLifecycleVersionStep, LifecyclePolicyVersion, OrderFillEvent, PositionIntent,
    PositionLifecycleState, SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.models import QuantStrategy, QuantStrategyVersion
from backend.modules.quant_strategy.infrastructure.signals import QuantExecutionSignal


DAY = date(2026, 9, 21)
D = Decimal


def _seed_entry(factory, *, policy_kind="legacy", order_quantity="200"):
    with factory() as session:
        service = LifecyclePolicyService(session)
        if policy_kind == "legacy":
            policy = service.publish(
                policy_key=f"entry-{uuid.uuid4().hex}", required_fields=[],
                config={"template_id": "ma_trend_cross_v1", "reward_multiple": "2"},
            )
        else:
            policy = service.publish_management(TREND_3ATR if policy_kind == "trend" else MACD_MEAN_REVERSION)
        strategy = QuantStrategy(id=uuid.uuid4(), name=f"entry-{uuid.uuid4().hex}", version=1)
        portfolio = Portfolio(
            id=uuid.uuid4(), name=f"entry-{uuid.uuid4().hex}", version=1,
            total_assets=D("20000"), available_cash=D("20000"),
        )
        version = QuantStrategyVersion(
            id=uuid.uuid4(), strategy_id=strategy.id, version_no=1, status="PUBLISHED",
            source_code="def strategy(context): return {}", source_hash="e" * 64,
            template_id="macd_rsi_reversal_v1" if policy_kind == "macd" else "ma_trend_cross_v1",
            template_params={}, lifecycle_policy_version_id=policy.id, version=1,
        )
        task = AnalysisTask(
            id=uuid.uuid4(), task_type="MARKET_WIDE", status="SUCCESS", attempt_no=1,
            selected_layers=["position"], input_hash="c" * 64,
            request_params={"execution_snapshot": {"strategy": {
                "version_id": str(version.id), "lifecycle_policy": {
                    "id": str(policy.id), "content_hash": policy.content_hash, "config": policy.config,
                },
            }}},
        )
        session.add_all([portfolio, strategy, task])
        session.flush()
        session.add(version)
        session.flush()
        signal = QuantExecutionSignal(
            task_id=task.id, attempt_no=1, strategy_version_id=version.id,
            signal_kind="BUY", action="BUY", ts_code="000001.SZ", score=80, reason="entry",
            shares=D("200") if policy_kind == "macd" else D("400"), order_status="ELIGIBLE",
            order_entry_price=D("10"), order_stop_price=D("9"), order_take_price=D("12"),
        )
        session.add(signal)
        session.flush()
        order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio.id, source_signal_id=signal.id,
            market="CN", symbol="000001.SZ", side="BUY", quantity=D(order_quantity),
            filled_quantity=D(0), limit_price=D("10"), stop_price=D("9"),
            reserved_cash=D(order_quantity) * 10, reserved_risk=D(order_quantity),
            reason_code="INITIAL_ENTRY", status="PROPOSED", revision=1,
        )
        session.add(order)
        session.commit()
        return portfolio.id, order.id


def _fill(factory, order_id, quantity, *, key=None):
    with factory() as session:
        order = session.get(SuggestedOrder, order_id)
        event, order = LifecycleOrderService(session).confirm_fill(
            order_id, quantity=quantity, fill_price="10", fill_trade_date=DAY,
            idempotency_key=key or f"entry-fill-{uuid.uuid4().hex}", expected_revision=order.revision,
        )
        return event.id, order.lifecycle_id


@pytest.mark.parametrize("policy_kind", ("legacy", "trend", "macd"))
def test_two_partial_entry_fills_complete_once_and_preserve_initial_anchor(env, policy_kind):
    factory = env["session_factory"]
    portfolio_id, order_id = _seed_entry(factory, policy_kind=policy_kind)
    initial_id, lifecycle_id = _fill(factory, order_id, "100")
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        assert lifecycle.phase == "ENTRY_PENDING"
        assert lifecycle.target_shares == 200
        assert lifecycle.state_version == 1
    replay_key = f"entry-complete-{uuid.uuid4().hex}"
    second_id, _ = _fill(factory, order_id, "100", key=replay_key)
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        order = session.get(SuggestedOrder, order_id)
        assert lifecycle.phase == "INITIALIZED"
        assert lifecycle.initial_fill_id == initial_id
        assert lifecycle.initial_fill_price == 10
        assert lifecycle.state_version == 2
        assert order.intent_id is None
        assert order.status == "FILLED"
        assert session.get(Portfolio, portfolio_id).available_cash == 18000
        step = session.get(FillLifecycleVersionStep, second_id)
        assert (step.origin, step.version_before, step.version_after) == ("LOCAL_CAUSAL", 1, 2)
        replay, _ = LifecycleOrderService(session).confirm_fill(
            order_id, quantity="100", fill_price="10", fill_trade_date=DAY,
            idempotency_key=replay_key, expected_revision=1,
        )
        assert replay.id == second_id
        assert lifecycle.state_version == 2
        assert session.query(OrderFillEvent).filter_by(order_id=order_id).count() == 2


def test_partial_entry_below_target_stays_pending(env):
    factory = env["session_factory"]
    _, order_id = _seed_entry(factory)
    _, lifecycle_id = _fill(factory, order_id, "100")
    _fill(factory, order_id, "50")
    with factory() as session:
        assert session.get(PositionLifecycleState, lifecycle_id).phase == "ENTRY_PENDING"
        assert session.get(SuggestedOrder, order_id).filled_quantity == 150
        assert session.get(SuggestedOrder, order_id).status == "PARTIALLY_FILLED"


@pytest.mark.parametrize("archive_policy", (False, True))
@pytest.mark.parametrize("delete_source", (False, True))
def test_later_entry_completion_uses_frozen_policy(env, archive_policy, delete_source):
    factory = env["session_factory"]
    _, order_id = _seed_entry(factory)
    initial_id, lifecycle_id = _fill(factory, order_id, "100")
    with factory() as session:
        order = session.get(SuggestedOrder, order_id)
        signal = session.get(QuantExecutionSignal, order.source_signal_id)
        if delete_source:
            session.delete(session.get(AnalysisTask, signal.task_id))
        if archive_policy:
            lifecycle = session.get(PositionLifecycleState, lifecycle_id)
            session.get(LifecyclePolicyVersion, lifecycle.lifecycle_policy_version_id).status = "ARCHIVED"
        session.commit()
    _fill(factory, order_id, "100")
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        assert (session.get(SuggestedOrder, order_id).source_signal_id is None) == delete_source
        assert lifecycle.initial_fill_id == initial_id
        assert lifecycle.phase == "INITIALIZED"


def test_archived_policy_still_rejects_a_new_first_fill(env):
    factory = env["session_factory"]
    portfolio_id, order_id = _seed_entry(factory)
    with factory() as session:
        signal = session.get(QuantExecutionSignal, session.get(SuggestedOrder, order_id).source_signal_id)
        version = session.get(QuantStrategyVersion, signal.strategy_version_id)
        session.get(LifecyclePolicyVersion, version.lifecycle_policy_version_id).status = "ARCHIVED"
        session.commit()
    with pytest.raises(LifecycleInvalidStateError, match="已发布版本不一致"):
        _fill(factory, order_id, "100")
    with factory() as session:
        assert session.get(Portfolio, portfolio_id).available_cash == 20000
        assert session.get(SuggestedOrder, order_id).filled_quantity == 0
        assert session.query(OrderFillEvent).filter_by(order_id=order_id).count() == 0


@pytest.mark.parametrize("wrong_identity", ("anchor_order", "new_order", "snapshot_version"))
def test_archived_policy_exception_requires_original_entry_and_frozen_identity(env, wrong_identity):
    factory = env["session_factory"]
    portfolio_id, order_id = _seed_entry(factory)
    initial_id, lifecycle_id = _fill(factory, order_id, "100")
    with factory() as session:
        original = session.get(SuggestedOrder, order_id)
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        policy = session.get(LifecyclePolicyVersion, lifecycle.lifecycle_policy_version_id)
        policy.status = "ARCHIVED"
        if wrong_identity in {"anchor_order", "new_order"}:
            original_signal = session.get(QuantExecutionSignal, original.source_signal_id)
            other_signal = QuantExecutionSignal(
                task_id=original_signal.task_id, attempt_no=1,
                strategy_version_id=original_signal.strategy_version_id,
                signal_kind="BUY", action="BUY", ts_code="000001.SZ", score=80,
                reason="other entry", shares=D(200), order_status="ELIGIBLE",
                order_entry_price=D(10), order_stop_price=D(9), order_take_price=D(12),
            )
            session.add(other_signal)
            session.flush()
            other = SuggestedOrder(
                id=uuid.uuid4(), portfolio_id=portfolio_id, position_id=original.position_id,
                lifecycle_id=lifecycle_id, source_signal_id=other_signal.id,
                market="CN", symbol="000001.SZ", side="BUY", quantity=D(100),
                filled_quantity=D(0), limit_price=D(10), stop_price=D(9),
                reserved_cash=D(1000), reserved_risk=D(100), reason_code="OTHER_ENTRY",
                status="PROPOSED", revision=1,
            )
            session.add(other)
            session.flush()
            if wrong_identity == "anchor_order":
                # Explicit corrupt-anchor fixture: the other order's immutable
                # event cannot establish the original order's entry identity.
                wrong_anchor = OrderFillEvent(
                    id=uuid.uuid4(), order_id=other.id, portfolio_id=portfolio_id,
                    position_id=original.position_id, event_type="CONFIRM", quantity=D(100),
                    fill_price=D(10), fill_trade_date=DAY, source="MANUAL",
                    idempotency_key=f"wrong-anchor-{uuid.uuid4().hex}",
                )
                session.add(wrong_anchor)
                session.flush()
                lifecycle.initial_fill_id = wrong_anchor.id
            else:
                order_id = other.id
        else:
            signal = session.get(QuantExecutionSignal, original.source_signal_id)
            version = session.get(QuantStrategyVersion, lifecycle.strategy_version_id)
            other_version = QuantStrategyVersion(
                id=uuid.uuid4(), strategy_id=version.strategy_id, version_no=2, status="PUBLISHED",
                source_code=version.source_code, source_hash="f" * 64,
                template_id=version.template_id, template_params={},
                lifecycle_policy_version_id=policy.id, version=1,
            )
            session.add(other_version)
            session.flush()
            signal.strategy_version_id = other_version.id
            task = session.get(AnalysisTask, signal.task_id)
            task.request_params = {"execution_snapshot": {"strategy": {
                "version_id": str(other_version.id), "lifecycle_policy": {
                    "id": str(policy.id), "content_hash": policy.content_hash, "config": policy.config,
                },
            }}}
        session.commit()
    with pytest.raises(LifecycleInvalidStateError, match="已发布版本不一致"):
        _fill(factory, order_id, "100")
    with factory() as session:
        assert session.get(Portfolio, portfolio_id).available_cash == 19000
        assert session.get(PositionLifecycleState, lifecycle_id).phase == "ENTRY_PENDING"
        assert session.get(PositionLifecycleState, lifecycle_id).state_version == 1
        assert session.get(OrderFillEvent, initial_id) is not None


@pytest.mark.parametrize("change", (
    "reconciliation", "exit_phase", "exit_target", "different_exposure", "different_target",
    "missing_anchor", "active_intent", "executing_intent", "reconciliation_intent", "other_active_order",
))
def test_late_entry_fill_does_not_overwrite_new_target_or_reconciliation(env, change):
    factory = env["session_factory"]
    portfolio_id, order_id = _seed_entry(factory)
    _, lifecycle_id = _fill(factory, order_id, "100")
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        order = session.get(SuggestedOrder, order_id)
        if change == "reconciliation":
            order.status = "RECONCILIATION_REQUIRED"
        elif change == "exit_phase":
            lifecycle.phase = "EXIT_PENDING"
        elif change == "exit_target":
            lifecycle.target_shares = D(0)
            lifecycle.target_exposure_pct = D(0)
        elif change == "different_target":
            lifecycle.target_shares = D(100)
        elif change == "different_exposure":
            lifecycle.target_exposure_pct = D(1)
        elif change == "missing_anchor":
            lifecycle.initial_fill_id = None
        elif change.endswith("_intent"):
            status = {"active_intent": "ACTIVE", "executing_intent": "EXECUTING",
                      "reconciliation_intent": "RECONCILIATION_REQUIRED"}[change]
            session.add(PositionIntent(
                id=uuid.uuid4(), lifecycle_id=lifecycle_id, trade_date=DAY,
                target_shares=D(0), reason_code="INITIAL_STOP_LOSS",
                state_version=1, status=status, revision=1,
            ))
        else:
            session.add(SuggestedOrder(
                id=uuid.uuid4(), portfolio_id=portfolio_id, position_id=order.position_id,
                lifecycle_id=lifecycle_id, market="CN", symbol="000001.SZ", side="SELL",
                quantity=D(100), filled_quantity=D(0), limit_price=D(9), stop_price=D(9),
                reserved_cash=D(0), reserved_risk=D(0), reason_code="INITIAL_STOP_LOSS",
                status="RECONCILIATION_REQUIRED", revision=1,
            ))
        session.commit()
    _fill(factory, order_id, "100")
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        assert lifecycle.phase == ("EXIT_PENDING" if change == "exit_phase" else "ENTRY_PENDING")
        assert lifecycle.state_version == 2
        assert session.get(SuggestedOrder, order_id).filled_quantity == 200
        assert session.get(Portfolio, portfolio_id).available_cash == 18000
        if change == "reconciliation":
            assert session.get(SuggestedOrder, order_id).status == "RECONCILIATION_REQUIRED"


def test_unrelated_buy_cannot_complete_original_entry_from_account_quantity(env):
    factory = env["session_factory"]
    portfolio_id, order_id = _seed_entry(factory)
    initial_id, lifecycle_id = _fill(factory, order_id, "100")
    with factory() as session:
        initial_order = session.get(SuggestedOrder, order_id)
        other = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id, position_id=initial_order.position_id,
            lifecycle_id=lifecycle_id, market="CN", symbol="000001.SZ", side="BUY",
            quantity=D(100), filled_quantity=D(0), limit_price=D(10), stop_price=D(9),
            reserved_cash=D(1000), reserved_risk=D(100), reason_code="UNRELATED_BUY",
            status="PROPOSED", revision=1,
        )
        session.add(other)
        session.commit()
        other_id = other.id
    _fill(factory, other_id, "100")
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        assert session.get(PortfolioPosition, lifecycle.position_id).quantity == 200
        assert lifecycle.phase == "ENTRY_PENDING"
        assert lifecycle.initial_fill_id == initial_id


def test_pending_entry_excess_is_preserved_and_quarantined(env):
    factory = env["session_factory"]
    portfolio_id, order_id = _seed_entry(factory, order_quantity="300")
    initial_id, lifecycle_id = _fill(factory, order_id, "100")
    second_id, _ = _fill(factory, order_id, "150")
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        order = session.get(SuggestedOrder, order_id)
        assert lifecycle.phase == "ENTRY_PENDING"
        assert lifecycle.initial_fill_id == initial_id
        assert lifecycle.state_version == 2
        assert order.status == "RECONCILIATION_REQUIRED"
        assert order.filled_quantity == 250
        assert session.get(PortfolioPosition, lifecycle.position_id).quantity == 250
        assert session.get(Portfolio, portfolio_id).available_cash == 17500
        assert session.get(OrderFillEvent, second_id).quantity == 150


def test_completed_entry_is_not_reclassified_by_a_later_fill(env):
    factory = env["session_factory"]
    _, order_id = _seed_entry(factory, order_quantity="300")
    initial_id, lifecycle_id = _fill(factory, order_id, "200")
    _fill(factory, order_id, "100")
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        assert lifecycle.phase == "INITIALIZED"
        assert lifecycle.initial_fill_id == initial_id
        assert session.get(SuggestedOrder, order_id).status == "FILLED"


def test_completion_uses_effective_corrected_fill_quantity(env):
    factory = env["session_factory"]
    _, order_id = _seed_entry(factory)
    initial_id, lifecycle_id = _fill(factory, order_id, "40")
    changed_id, _ = _fill(factory, order_id, "20")
    with factory() as session:
        order = session.get(SuggestedOrder, order_id)
        LifecycleOrderService(session).correct_fill(
            changed_id, quantity="30", fill_price="10", fill_trade_date=DAY,
            idempotency_key=f"entry-correct-{uuid.uuid4().hex}", expected_revision=order.revision,
        )
        assert order.filled_quantity == 70
        assert order.status == "RECONCILIATION_REQUIRED"
        # Isolated fixture for a completed reconciliation, not a production
        # authorization path: old 20 is superseded by corrected 30 in the journal.
        order.status = "PARTIALLY_FILLED"
        session.commit()
    _fill(factory, order_id, "130")
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        assert lifecycle.phase == "INITIALIZED"
        assert lifecycle.initial_fill_id == initial_id
        assert session.get(SuggestedOrder, order_id).filled_quantity == 200


def test_order_quantity_without_matching_journal_does_not_prove_completion(env):
    factory = env["session_factory"]
    portfolio_id, order_id = _seed_entry(factory)
    _, lifecycle_id = _fill(factory, order_id, "100")
    with factory() as session:
        # Corrupt only the mutable projection; the immutable prior fill is 100.
        session.get(SuggestedOrder, order_id).filled_quantity = D(150)
        session.commit()
    _fill(factory, order_id, "50")
    with factory() as session:
        assert session.get(PositionLifecycleState, lifecycle_id).phase == "ENTRY_PENDING"
        assert session.get(SuggestedOrder, order_id).status == "RECONCILIATION_REQUIRED"
        assert session.get(Portfolio, portfolio_id).available_cash == 18500
