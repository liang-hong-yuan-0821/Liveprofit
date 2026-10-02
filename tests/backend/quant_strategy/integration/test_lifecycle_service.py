# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle_service（持仓生命周期、服务）",
#   "keywords": [
#     "量化策略",
#     "执行准入",
#     "绑定",
#     "现金",
#     "并发",
#     "每日",
#     "策略族",
#     "费用",
#     "成交",
#     "不可变历史",
#     "交易意图",
#     "持仓生命周期",
#     "订单",
#     "投资组合",
#     "仓位管理",
#     "重放",
#     "版本修订",
#     "事务回滚",
#     "交易信号",
#     "来源证据",
#     "状态",
#     "止损",
#     "任务",
#     "未绑定",
#     "lifecycle_service",
#     "admission",
#     "bound",
#     "cash",
#     "concurrent",
#     "daily",
#     "family",
#     "fee",
#     "fill",
#     "immutable",
#     "intent",
#     "lifecycle",
#     "order",
#     "portfolio",
#     "position",
#     "replay",
#     "revision",
#     "rollback",
#     "signal",
#     "source",
#     "status",
#     "stop",
#     "task",
#     "unbound"
#   ],
#   "covers": [
#     "backend/modules/analysis/infrastructure/models.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_research/application/trial_executor.py",
#     "backend/modules/quant_strategy/application/daily_fact_input_proposals.py",
#     "backend/modules/quant_strategy/application/errors.py",
#     "backend/modules/quant_strategy/application/execution_constraints.py",
#     "backend/modules/quant_strategy/application/lifecycle_persisted_daily_inputs.py",
#     "backend/modules/quant_strategy/application/lifecycle_persisted_diagnosis.py",
#     "backend/modules/quant_strategy/application/lifecycle_persisted_intent_inputs.py",
#     "backend/modules/quant_strategy/application/lifecycle_projection_diff.py",
#     "backend/modules/quant_strategy/application/lifecycle_replay_inventory.py",
#     "backend/modules/quant_strategy/application/lifecycle_service.py",
#     "backend/modules/quant_strategy/application/ownership.py",
#     "backend/modules/quant_strategy/application/planning_account.py",
#     "backend/modules/quant_strategy/application/portfolio_drawdown_actions.py",
#     "backend/modules/quant_strategy/application/position_lifecycle_manager.py",
#     "backend/modules/quant_strategy/application/service.py",
#     "backend/modules/quant_strategy/application/strategy_admission.py",
#     "backend/modules/quant_strategy/domain/family_management.py",
#     "backend/modules/quant_strategy/domain/management_policies.py",
#     "backend/modules/quant_strategy/domain/templates.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py",
#     "backend/modules/quant_strategy/infrastructure/models.py",
#     "backend/modules/quant_strategy/infrastructure/repositories.py",
#     "backend/modules/quant_strategy/infrastructure/signals.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

from __future__ import annotations

from tests.backend.quant_strategy.support.lifecycle_service import (
    BUY_DAY,
    certified_stock_rules,
    _buy_context,
    _certify_buy_account,
    _seed_order,
    _seed_buy_lifecycles,
    _buy_day,
    _deferred_batch,
    _consume_deferred,
)

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import base64
import json
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace

import pytest

from backend.modules.quant_research.application.trial_executor import prepare_trial
from backend.modules.investment_workspace.infrastructure.models import Portfolio, PortfolioPosition
from backend.modules.analysis.infrastructure.models import AnalysisTask
from backend.modules.quant_strategy.application.errors import (
    FillValidationError,
    LifecycleInvalidStateError,
    LifecycleRevisionConflictError,
)
from backend.modules.quant_strategy.application.lifecycle_service import (
    LifecycleOrderService,
    LifecyclePolicyService,
    LifecycleStateService,
)
from backend.modules.quant_strategy.application.position_lifecycle_manager import (
    LifecycleStateInput, PositionLifecycleManager,
)
from backend.modules.quant_strategy.application.lifecycle_replay_inventory import inventory_lifecycle_replay
from backend.modules.quant_strategy.application.lifecycle_persisted_daily_inputs import load_local_daily_fact_inputs
from backend.modules.quant_strategy.application.lifecycle_persisted_intent_inputs import load_local_intent_definitions
from backend.modules.quant_strategy.application.lifecycle_persisted_diagnosis import diagnose_persisted_lifecycle
from backend.modules.quant_strategy.application.daily_fact_input_proposals import propose_daily_fact_input
from backend.modules.quant_strategy.application.lifecycle_projection_diff import load_current_lifecycle_projection
from backend.modules.quant_strategy.domain.management_policies import (
    ExitPolicyKind, InitialStopRule, ManagementPolicy, StopMode,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    OrderFillEvent,
    PositionDailyFact,
    PositionDailyFactInputProposal,
    PositionDailyFactRevision,
    PositionIntent,
    PositionExpectation,
    PositionLifecycleState,
    PositionTrailingStop,
    SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.models import QuantStrategy, QuantStrategyVersion
from backend.modules.quant_strategy.infrastructure.signals import QuantExecutionSignal











def test_fee_aware_fill_stage_stays_in_caller_transaction(env):
    factory = env["session_factory"]
    portfolio_id, order_id = _seed_order(factory, side="SELL")
    with factory() as session:
        session.get(Portfolio, portfolio_id).total_assets = Decimal("20000")
        session.commit()
    with factory() as writer:
        service = LifecycleOrderService(writer)
        order = service._lock_order(order_id, expected_revision=1)
        event = service._stage_confirm_fill_locked(
            order, qty=Decimal("10"), price=Decimal("10"),
            fill_trade_date=BUY_DAY, idempotency_key=f"stage-{order_id}",
            source="MANUAL", note="isolated transaction stage", fee=Decimal("1"))
        assert event.id is not None
        assert writer.get(Portfolio, portfolio_id).available_cash == Decimal("10099")
        with factory() as reader:
            assert reader.get(Portfolio, portfolio_id).available_cash == Decimal("10000")
            assert reader.get(SuggestedOrder, order_id).filled_quantity == 0
        writer.rollback()
    with factory() as reader:
        assert reader.get(Portfolio, portfolio_id).available_cash == Decimal("10000")
        assert reader.get(SuggestedOrder, order_id).filled_quantity == 0
        assert reader.query(OrderFillEvent).filter_by(order_id=order_id).count() == 0


def test_partial_fill_replay_correction_and_void_are_atomic(env):
    factory = env["session_factory"]
    portfolio_id, order_id = _seed_order(factory)
    with factory() as session:
        service = LifecycleOrderService(session)
        fill, order = service.confirm_fill(
            order_id, quantity="40", fill_price="9", fill_trade_date=date(2026, 9, 21),
            idempotency_key="confirm-1", expected_revision=1,
        )
        fill_id = fill.id
        assert order.status == "PARTIALLY_FILLED"
        assert order.filled_quantity == Decimal("40.0000")
        replay, replay_order = service.confirm_fill(
            order_id, quantity="40", fill_price="9", fill_trade_date=date(2026, 9, 21),
            idempotency_key="confirm-1", expected_revision=1,
        )
        assert replay.id == fill_id
        assert replay_order.revision == 2

    with factory() as session:
        corrected, order = LifecycleOrderService(session).correct_fill(
            fill_id, quantity="50", fill_price="8", fill_trade_date=date(2026, 9, 21),
            idempotency_key="correct-1", expected_revision=2,
        )
        assert corrected.event_type == "CORRECT"
        assert order.filled_quantity == Decimal("50.0000")
        assert order.revision == 3
        position = session.query(PortfolioPosition).filter_by(portfolio_id=portfolio_id).one()
        portfolio = session.get(Portfolio, portfolio_id)
        assert position.quantity == Decimal("50.0000")
        assert position.average_cost == Decimal("8.0000")
        assert portfolio.available_cash == Decimal("9600.0000")

    with factory() as session:
        voided, order = LifecycleOrderService(session).void_fill(
            corrected.id, idempotency_key="void-1", expected_revision=3,
            fill_trade_date=date(2026, 9, 22),
        )
        assert voided.event_type == "VOID"
        assert order.filled_quantity == Decimal("0.0000")
        assert order.status == "RECONCILIATION_REQUIRED"
        with pytest.raises(FillValidationError, match="可信券商终态回执"):
            LifecycleOrderService(session).set_order_status(
                order.id, status="CANCELLED", expected_revision=4)
        session.rollback()
        position = session.query(PortfolioPosition).filter_by(portfolio_id=portfolio_id).one()
        portfolio = session.get(Portfolio, portfolio_id)
        assert position.quantity == Decimal("0.0000")
        assert portfolio.available_cash == Decimal("10000.0000")


def test_fill_idempotency_key_rejects_other_order_operation_or_payload(env):
    factory = env["session_factory"]
    first_portfolio, first_order = _seed_order(factory, side="SELL")
    second_portfolio, second_order = _seed_order(factory, side="SELL")
    key = f"fill-identity-{uuid.uuid4().hex}"
    trade_day = date(2026, 9, 21)
    with factory() as session:
        session.get(Portfolio, first_portfolio).total_assets = Decimal("20000")
        session.get(Portfolio, second_portfolio).total_assets = Decimal("20000")
        session.commit()
    with factory() as session:
        fill, _ = LifecycleOrderService(session).confirm_fill(
            first_order, quantity="10", fill_price="9", fill_trade_date=trade_day,
            idempotency_key=key, expected_revision=1, note="original")
        original_id = fill.id
    with factory() as session:
        service = LifecycleOrderService(session)
        with pytest.raises(FillValidationError, match="idempotency_key"):
            service.confirm_fill(second_order, quantity="10", fill_price="9",
                                 fill_trade_date=trade_day, idempotency_key=key,
                                 expected_revision=1, note="original")
        with pytest.raises(FillValidationError, match="idempotency_key"):
            service.confirm_fill(first_order, quantity="11", fill_price="9",
                                 fill_trade_date=trade_day, idempotency_key=key,
                                 expected_revision=1, note="original")
        with pytest.raises(FillValidationError, match="idempotency_key"):
            service.correct_fill(original_id, quantity="10", fill_price="9",
                                 fill_trade_date=trade_day, idempotency_key=key,
                                 expected_revision=2, note="original")
        with pytest.raises(FillValidationError, match="idempotency_key"):
            service.void_fill(original_id, idempotency_key=key, expected_revision=2,
                              fill_trade_date=trade_day, note="original")
        replay, _ = service.confirm_fill(first_order, quantity="10", fill_price="9",
                                         fill_trade_date=trade_day, idempotency_key=key,
                                         expected_revision=1, note="original")
        assert replay.id == original_id
    with factory() as session:
        assert session.get(Portfolio, second_portfolio).available_cash == Decimal("10000")
        assert session.get(SuggestedOrder, second_order).filled_quantity == 0
        assert session.get(Portfolio, first_portfolio).available_cash == Decimal("10090")


@pytest.mark.parametrize("revision_kind,expected_quantity,expected_cash", (
    ("VOID", Decimal("100"), Decimal("20000")),
    ("CORRECT", Decimal("60"), Decimal("20400")),
))
def test_late_revision_of_completed_intent_quarantines_successor_without_losing_fill(
    env, revision_kind, expected_quantity, expected_cash,
):
    factory = env["session_factory"]
    portfolio_id, lifecycle_ids = _seed_buy_lifecycles(
        factory, cash="20000", symbols=("000001.SZ",))
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, lifecycle_ids[0])
        position = session.get(PortfolioPosition, lifecycle.position_id)
        position.quantity = Decimal("50")
        session.get(Portfolio, portfolio_id).available_cash = Decimal("20500")
        old_intent = PositionIntent(
            id=uuid.uuid4(), lifecycle_id=lifecycle.id, trade_date=BUY_DAY,
            target_shares=Decimal("50"), reason_code="OLD_TRIM",
            state_version=1, status="COMPLETED", revision=1)
        successor = PositionIntent(
            id=uuid.uuid4(), lifecycle_id=lifecycle.id, trade_date=BUY_DAY,
            target_shares=Decimal("0"), reason_code="NEXT_EXIT",
            state_version=1, status="ACTIVE", revision=1)
        old_order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id, position_id=position.id,
            lifecycle_id=lifecycle.id, intent_id=old_intent.id,
            market="CN", symbol="000001.SZ", side="SELL", quantity=Decimal("50"),
            filled_quantity=Decimal("50"), limit_price=Decimal("10"),
            reason_code="OLD_TRIM", status="FILLED", revision=1)
        next_order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id, position_id=position.id,
            lifecycle_id=lifecycle.id, intent_id=successor.id,
            market="CN", symbol="000001.SZ", side="SELL", quantity=Decimal("50"),
            filled_quantity=Decimal("0"), limit_price=Decimal("10"),
            reason_code="NEXT_EXIT", status="PROPOSED", revision=1)
        original = OrderFillEvent(
            id=uuid.uuid4(), order_id=old_order.id, portfolio_id=portfolio_id,
            position_id=position.id, event_type="CONFIRM", quantity=Decimal("50"),
            fill_price=Decimal("10"), fill_trade_date=BUY_DAY,
            source="MANUAL", idempotency_key=f"completed-{uuid.uuid4().hex}")
        session.add_all([old_intent, successor])
        session.flush()
        session.add_all([old_order, next_order])
        session.flush()
        session.add(original)
        session.commit()
        old_order_id, next_order_id = old_order.id, next_order.id
        old_intent_id, successor_id, fill_id = old_intent.id, successor.id, original.id
    with factory() as session:
        service = LifecycleOrderService(session)
        if revision_kind == "VOID":
            revision, order = service.void_fill(
                fill_id, idempotency_key=f"late-void-{uuid.uuid4().hex}",
                expected_revision=1, fill_trade_date=BUY_DAY)
        else:
            revision, order = service.correct_fill(
                fill_id, quantity="40", fill_price="10", fill_trade_date=BUY_DAY,
                idempotency_key=f"late-correct-{uuid.uuid4().hex}", expected_revision=1)
        assert revision.event_type == revision_kind
        assert order.status == "RECONCILIATION_REQUIRED"
    with factory() as session:
        assert session.get(PositionIntent, old_intent_id).status == "COMPLETED"
        assert session.get(PositionIntent, successor_id).status == "RECONCILIATION_REQUIRED"
        assert session.get(SuggestedOrder, old_order_id).status == "RECONCILIATION_REQUIRED"
        assert session.get(SuggestedOrder, next_order_id).status == "RECONCILIATION_REQUIRED"
        assert session.get(PortfolioPosition, lifecycle.position_id).quantity == expected_quantity
        assert session.get(Portfolio, portfolio_id).available_cash == expected_cash


def test_legacy_revision_rejects_persisted_initial_fill_anchor_before_projection(env):
    factory = env["session_factory"]
    portfolio_id, lifecycle_ids = _seed_buy_lifecycles(
        factory, cash="20000", symbols=("000001.SZ",))
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, lifecycle_ids[0])
        order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id, position_id=lifecycle.position_id,
            lifecycle_id=lifecycle.id, market="CN", symbol="000001.SZ", side="BUY",
            quantity=Decimal("10"), filled_quantity=Decimal(0), limit_price=Decimal("10"),
            stop_price=Decimal("9"), reserved_cash=Decimal("101"),
            reason_code="ANCHOR_TEST", status="PROPOSED", revision=1)
        session.add(order)
        session.commit()
        order_id = order.id
    with factory() as session:
        fill, _ = LifecycleOrderService(session).confirm_fill(
            order_id, quantity="10", fill_price="10", fill_trade_date=BUY_DAY,
            idempotency_key=f"anchor-original-{uuid.uuid4().hex}", expected_revision=1)
        fill_id = fill.id
        session.get(PositionLifecycleState, lifecycle_ids[0]).initial_fill_id = fill_id
        session.commit()
    other_portfolio_id, _ = _seed_order(factory, side="SELL", quantity=Decimal("1"))
    with factory() as session:
        foreign_position_id = session.query(PortfolioPosition).filter_by(
            portfolio_id=other_portfolio_id).one().id
        unbound = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id, market="CN", symbol="000001.SZ",
            side="SELL", quantity=Decimal("1"), filled_quantity=Decimal("1"),
            limit_price=Decimal("10"), reserved_cash=Decimal(0), reserved_risk=Decimal(0),
            reason_code="AMBIGUOUS_HISTORY", status="FILLED", revision=1)
        session.add(unbound)
        session.flush()
        unrelated = OrderFillEvent(
            id=uuid.uuid4(), order_id=unbound.id, portfolio_id=portfolio_id,
            event_type="CONFIRM", quantity=Decimal("1"), fill_price=Decimal("10"),
            fill_trade_date=BUY_DAY, source="MANUAL",
            idempotency_key=f"ambiguous-{uuid.uuid4().hex}")
        session.add(unrelated)
        misbound_order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id,
            lifecycle_id=lifecycle_ids[0], market="CN", symbol="000002.SZ",
            side="SELL", quantity=Decimal("1"), filled_quantity=Decimal("1"),
            limit_price=Decimal("10"), reserved_cash=Decimal(0), reserved_risk=Decimal(0),
            reason_code="MISBOUND_HISTORY", status="FILLED", revision=1)
        session.add(misbound_order)
        session.flush()
        misbound = OrderFillEvent(
            id=uuid.uuid4(), order_id=misbound_order.id, portfolio_id=portfolio_id,
            event_type="CONFIRM", quantity=Decimal("1"), fill_price=Decimal("10"),
            fill_trade_date=BUY_DAY, source="MANUAL",
            idempotency_key=f"misbound-{uuid.uuid4().hex}")
        session.add(misbound)
        wrong_position_order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id,
            lifecycle_id=lifecycle_ids[0], position_id=foreign_position_id,
            market="CN", symbol="000001.SZ", side="SELL",
            quantity=Decimal("1"), filled_quantity=Decimal("1"),
            limit_price=Decimal("10"), reserved_cash=Decimal(0), reserved_risk=Decimal(0),
            reason_code="WRONG_POSITION_HISTORY", status="FILLED", revision=1)
        session.add(wrong_position_order)
        session.flush()
        wrong_position = OrderFillEvent(
            id=uuid.uuid4(), order_id=wrong_position_order.id,
            portfolio_id=portfolio_id, event_type="CONFIRM", quantity=Decimal("1"),
            fill_price=Decimal("10"), fill_trade_date=BUY_DAY, source="MANUAL",
            idempotency_key=f"wrong-position-{uuid.uuid4().hex}")
        session.add(wrong_position)
        lifecycle = session.get(PositionLifecycleState, lifecycle_ids[0])
        lifecycle.last_processed_trade_date = BUY_DAY + timedelta(days=1)
        malformed_day = PositionDailyFact(
            id=uuid.uuid4(), lifecycle_id=lifecycle.id, trade_date=BUY_DAY,
            price_basis="raw", data_as_of=datetime.now(timezone.utc),
            input_payload={}, input_hash="0" * 64, planning_result=None,
            rule_version="fixture", state_version_before=3,
            state_version_after=2, final_target_shares=Decimal("110"))
        session.add(malformed_day)
        session.commit()
    with factory() as session:
        inventory = inventory_lifecycle_replay(session, portfolio_id, lifecycle_ids[0])
        assert inventory.fill_event_ids == (fill_id,)
        assert set(inventory.ambiguous_fill_event_ids) == {
            unrelated.id, misbound.id, wrong_position.id}
        assert f"FILL_OWNERSHIP_AMBIGUOUS:{unrelated.id}" in inventory.issues
        assert f"ORDER_IDENTITY_MISMATCH:{misbound_order.id}" in inventory.issues
        assert f"ORDER_IDENTITY_MISMATCH:{wrong_position_order.id}" in inventory.issues
        assert f"DAILY_FACT_VERSION_INVALID:{malformed_day.id}:{BUY_DAY}" in inventory.issues
        assert f"DAILY_FACT_INPUT_HASH_MISMATCH:{malformed_day.id}:{BUY_DAY}" in inventory.issues
        assert f"PROCESSED_DAY_FACT_MISSING:{BUY_DAY + timedelta(days=1)}" in inventory.issues
        assert f"FILL_REPORT_BINDING_MISSING:{fill_id}" in inventory.issues
        assert not session.new and not session.dirty
        session.rollback()
    with factory() as session:
        service = LifecycleOrderService(session)
        with pytest.raises(LifecycleInvalidStateError, match="完整事件重放"):
            service.correct_fill(fill_id, quantity="9", fill_price="10",
                                 fill_trade_date=BUY_DAY,
                                 idempotency_key=f"anchor-correct-{uuid.uuid4().hex}",
                                 expected_revision=2)
        with pytest.raises(LifecycleInvalidStateError, match="完整事件重放"):
            service.void_fill(fill_id, fill_trade_date=BUY_DAY,
                              idempotency_key=f"anchor-void-{uuid.uuid4().hex}",
                              expected_revision=2)
        session.rollback()
    with factory() as session:
        assert session.get(Portfolio, portfolio_id).available_cash == Decimal("19900")
        assert session.get(PortfolioPosition, session.get(
            PositionLifecycleState, lifecycle_ids[0]).position_id).quantity == Decimal("110")
        assert session.get(SuggestedOrder, order_id).filled_quantity == Decimal("10")
        assert session.query(OrderFillEvent).filter_by(order_id=order_id).count() == 1


def test_generic_status_endpoint_cannot_execute_old_buy_without_fresh_gates(env):
    factory = env["session_factory"]
    _portfolio_id, buy_id = _seed_order(factory, side="BUY")
    with factory() as session:
        with pytest.raises(FillValidationError, match="通用状态接口不可放行"):
            LifecycleOrderService(session).set_order_status(
                buy_id, status="EXECUTING", expected_revision=1)
        session.rollback()
        assert session.get(SuggestedOrder, buy_id).status == "PROPOSED"
    _portfolio_id, sell_id = _seed_order(factory, side="SELL")
    with factory() as session:
        assert LifecycleOrderService(session).set_order_status(
            sell_id, status="EXECUTING", expected_revision=1).status == "EXECUTING"
        with pytest.raises(FillValidationError, match="可信券商终态回执"):
            LifecycleOrderService(session).set_order_status(
                sell_id, status="CANCELLED", expected_revision=2)
        session.rollback()
        assert session.get(SuggestedOrder, sell_id).status == "EXECUTING"


def test_first_real_fill_initializes_bound_lifecycle_and_not_before(env, certified_stock_rules):
    from backend.modules.quant_strategy.application.execution_constraints import (
        ExecutionConstraintEvaluator, ExecutionPolicy, next_execution_session,
    )
    calendar = ExecutionConstraintEvaluator(ExecutionPolicy()).calendar
    add_day = BUY_DAY
    add_execution_day = next_execution_session(add_day, calendar=calendar)
    trim_day = next_execution_session(add_execution_day, calendar=calendar)
    trim_fill_day = next_execution_session(trim_day, calendar=calendar)
    replay_day = next_execution_session(trim_fill_day, calendar=calendar)
    factory = env["session_factory"]
    with factory() as session:
        policy = LifecyclePolicyService(session).publish(
            policy_key=f"ma5-{uuid.uuid4().hex[:8]}", required_fields=["ma5", "ma20"],
            config={
                "template_id": "ma5_pre_cross_v1", "reward_multiple": "2.2",
                "confirmation_window_trading_days": 3,
                "trailing_stop": {"b": "1", "a": "2", "d": "0.08"},
            },
        )
    with factory() as session:
        strategy = QuantStrategy(id=uuid.uuid4(), name=f"seed-{uuid.uuid4().hex[:8]}", version=1)
        version = QuantStrategyVersion(
            id=uuid.uuid4(), strategy_id=strategy.id, version_no=1, status="PUBLISHED",
            source_code="def strategy(context):\n return {}", source_hash="b" * 64,
            template_id="ma5_pre_cross_v1", template_params={"reward_multiple": "2.2"},
            lifecycle_policy_version_id=policy.id, version=1,
        )
        portfolio = Portfolio(
            id=uuid.uuid4(), name=f"seed-p-{uuid.uuid4().hex[:8]}", version=1,
            total_assets=Decimal("20000"), available_cash=Decimal("20000"),
        )
        task = AnalysisTask(
            id=uuid.uuid4(), task_type="MARKET_WIDE", status="SUCCESS",
            request_params={"execution_snapshot": {"strategy": {
                "version_id": str(version.id),
                "lifecycle_policy": {
                    "id": str(policy.id), "content_hash": policy.content_hash,
                    "config": policy.config,
                },
            }}},
            selected_layers=["position"], input_hash="c" * 64, attempt_no=1,
        )
        session.add_all([strategy, portfolio, task])
        session.flush()
        session.add(version)
        session.flush()
        signal = QuantExecutionSignal(
            task_id=task.id, attempt_no=1, signal_kind="BUY", ts_code="000001.SZ",
            action="BUY", score=80, reason="seed", shares=Decimal("1000"),
            order_status="ELIGIBLE", order_entry_price=Decimal("10"),
            order_stop_price=Decimal("9"), order_take_price=Decimal("12.2"),
        )
        session.add(signal)
        session.flush()
        order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio.id, source_signal_id=signal.id,
            market="CN", symbol="000001.SZ", side="BUY", industry_code="801010",
            quantity=Decimal("500"), filled_quantity=Decimal(0),
            limit_price=Decimal("10"), stop_price=Decimal("9"),
            reserved_cash=Decimal("5000"), reserved_risk=Decimal("500"),
            reason_code="INITIAL_ENTRY", status="PROPOSED", revision=1,
        )
        session.add(order)
        session.commit()
        order_id, portfolio_id = order.id, portfolio.id

    with factory() as session:
        assert session.query(PositionLifecycleState).filter_by(portfolio_id=portfolio_id).count() == 0
        _fill, updated = LifecycleOrderService(session).confirm_fill(
            order_id, quantity="500", fill_price="10", fill_trade_date=date(2026, 9, 21),
            idempotency_key=f"seed-fill-{uuid.uuid4().hex}", expected_revision=1,
        )
        assert updated.lifecycle_id is not None

    with factory() as session:
        lifecycle = session.query(PositionLifecycleState).filter_by(portfolio_id=portfolio_id).one()
        assert lifecycle.initial_fill_price == Decimal("10.0000")
        assert lifecycle.initial_stop_price == Decimal("9.0000")
        assert lifecycle.risk_capacity_shares == Decimal("1000.0000")
        assert lifecycle.target_shares == Decimal("500.0000")
        assert lifecycle.phase == "INITIALIZED"
        assert lifecycle.profit_take_price == Decimal("12.2000")
        trailing = session.query(PositionTrailingStop).filter_by(lifecycle_id=lifecycle.id).one()
        assert trailing.active_stop_price == Decimal("9.0000")
        expectation = session.query(PositionExpectation).filter_by(lifecycle_id=lifecycle.id).one()
        assert expectation.window_trading_days == 3
        assert expectation.status == "PENDING"
        lifecycle_id = lifecycle.id

    fact = {
        "close": "10.5", "high": "10.6", "ma5": "10.3", "prev_ma5": "10.0",
        "ma20": "10.2", "prev_ma20": "10.1", "ma60": "9.5",
        "macd": "0.3", "prev_macd": "0.2",
        "execution_market": {"trade_date": add_day.isoformat(), "raw_close": "10.5",
                             "raw_amount": "100000", "adv20_amount": "100000",
                             "up_limit": "11.5", "is_st": False, "is_suspended": False},
    }
    with factory() as session:
        _certify_buy_account(session, portfolio_id, add_day)
        daily, intent, add_order, decision = PositionLifecycleManager(session).process_day(
            lifecycle_id, trade_date=add_day, fact=fact,
            buy_context=_buy_context(add_day),
            data_as_of=datetime.now(timezone.utc),
        )
        assert decision.reason_code == "TEMPLATE_CONFIRM_ADD"
        assert intent.target_shares == Decimal("1000.0000")
        assert add_order.quantity == Decimal("500.0000")
        assert add_order.reserved_cash > add_order.quantity * add_order.limit_price
        assert add_order.industry_code == "801010"
        assert add_order.earliest_execution_trade_date == add_execution_day
        assert daily.planning_result["projection"]["order_status"] == "ELIGIBLE"
        add_order_id, daily_id = add_order.id, daily.id

    with factory() as session:
        replay, replay_intent, replay_order, replay_decision = PositionLifecycleManager(session).process_day(
            lifecycle_id, trade_date=add_day, fact=fact,
            buy_context=_buy_context(add_day),
            data_as_of=datetime.now(timezone.utc),
        )
        assert replay.id == daily_id
        assert replay_order.id == add_order_id
        assert replay_decision is None

    with factory() as session:
        LifecycleOrderService(session).confirm_fill(
            add_order_id, quantity="500", fill_price="10.5", fill_trade_date=add_execution_day,
            idempotency_key=f"add-fill-{uuid.uuid4().hex}", expected_revision=1,
        )
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        assert lifecycle.confirmation_completed is True
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        assert lifecycle.confirmation_completed is True
        assert lifecycle.phase == "CONFIRMED"

    profit_fact = {**fact, "close": "12.3", "high": "12.5", "available_sell_quantity": "1000",
                   "execution_market": {"trade_date": trim_day.isoformat(), "raw_close": "12.3",
                                        "down_limit": "10", "is_suspended": False}}
    with factory() as session:
        _daily, _intent, trim_order, decision = PositionLifecycleManager(session).process_day(
            lifecycle_id, trade_date=trim_day, fact=profit_fact,
            data_as_of=datetime.now(timezone.utc),
        )
        assert decision.reason_code == "PROFIT_TARGET_TRIM"
        assert trim_order.side == "SELL"
        assert trim_order.quantity == Decimal("500.0000")
        trim_order_id = trim_order.id
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        assert lifecycle.profit_target_reached is True
        assert lifecycle.profit_trim_completed is False
    with factory() as session:
        LifecycleOrderService(session).confirm_fill(
            trim_order_id, quantity="200", fill_price="12.3", fill_trade_date=trim_fill_day,
            idempotency_key=f"trim-partial-{uuid.uuid4().hex}", expected_revision=1,
        )
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        assert lifecycle.profit_trim_completed is False
    with factory() as session:
        LifecycleOrderService(session).confirm_fill(
            trim_order_id, quantity="300", fill_price="12.3", fill_trade_date=trim_fill_day,
            idempotency_key=f"trim-final-{uuid.uuid4().hex}", expected_revision=2,
        )
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        assert lifecycle.profit_trim_completed is True
        assert lifecycle.phase == "PROFIT_PROTECTED"

    barrier = Barrier(2)

    def process_same_day():
        with factory() as session:
            barrier.wait()
            daily, _intent, _order, _decision = PositionLifecycleManager(session).process_day(
                lifecycle_id, trade_date=replay_day,
                fact={**profit_fact, "execution_market": {
                    **profit_fact["execution_market"], "trade_date": replay_day.isoformat()}},
                data_as_of=datetime.now(timezone.utc),
            )
            return daily.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        daily_ids = list(pool.map(lambda _index: process_same_day(), range(2)))
    assert len(set(daily_ids)) == 1
    with factory() as session:
        assert session.query(PositionDailyFact).filter_by(
            lifecycle_id=lifecycle_id, trade_date=replay_day,
        ).count() == 1


def test_revision_conflict_and_double_reversal_fail_closed(env):
    factory = env["session_factory"]
    _portfolio_id, order_id = _seed_order(factory)
    with factory() as session:
        service = LifecycleOrderService(session)
        fill, _ = service.confirm_fill(
            order_id, quantity="10", fill_price="10", fill_trade_date=date(2026, 9, 21),
            idempotency_key="confirm-conflict", expected_revision=1,
        )
    with factory() as session:
        with pytest.raises(LifecycleRevisionConflictError):
            LifecycleOrderService(session).confirm_fill(
                order_id, quantity="10", fill_price="10", fill_trade_date=date(2026, 9, 21),
                idempotency_key="other-request", expected_revision=1,
            )
    with factory() as session:
        service = LifecycleOrderService(session)
        service.void_fill(
            fill.id, idempotency_key="void-once", expected_revision=2,
            fill_trade_date=date(2026, 9, 22),
        )
    with factory() as session:
        with pytest.raises(LifecycleInvalidStateError):
            LifecycleOrderService(session).void_fill(
                fill.id, idempotency_key="void-twice", expected_revision=3,
                fill_trade_date=date(2026, 9, 22),
            )


def test_rejected_fill_rolls_back_every_projection(env):
    factory = env["session_factory"]
    portfolio_id, order_id = _seed_order(
        factory, quantity=Decimal("100"), cash=Decimal("50"),
    )
    with factory() as session:
        with pytest.raises(FillValidationError, match="可用现金"):
            LifecycleOrderService(session).confirm_fill(
                order_id, quantity="10", fill_price="10",
                fill_trade_date=date(2026, 9, 21),
                idempotency_key="insufficient-cash", expected_revision=1,
            )

    with factory() as session:
        order = session.get(SuggestedOrder, order_id)
        portfolio = session.get(Portfolio, portfolio_id)
        positions = session.query(PortfolioPosition).filter_by(portfolio_id=portfolio_id).all()
        assert order.filled_quantity == Decimal("0.0000")
        assert order.status == "PROPOSED"
        assert order.revision == 1
        assert portfolio.available_cash == Decimal("50.0000")
        assert positions == []
        assert LifecycleOrderService(session).list_fills(order_id) == []


def test_policy_versions_are_immutable_and_trailing_config_validated(env):
    factory = env["session_factory"]
    with factory() as session:
        service = LifecyclePolicyService(session)
        v1 = service.publish(
            policy_key="ma_trend_cross_v1", required_fields=["ma20", "ma60"],
            config={"trailing_stop": {"b": "1", "a": "2", "d": "0.1"}},
        )
        same = service.publish(
            policy_key="ma_trend_cross_v1", required_fields=["ma60", "ma20"],
            config={"trailing_stop": {"b": "1", "a": "2", "d": "0.1"}},
        )
        v2 = service.publish(
            policy_key="ma_trend_cross_v1", required_fields=["ma20", "ma60"],
            config={"trailing_stop": {"b": "1", "a": "2", "d": "0.2"}},
        )
        assert same.id == v1.id
        assert v2.version_no == 2


def test_typed_management_policy_uses_immutable_lifecycle_version(env):
    key = f"mgmt_{uuid.uuid4().hex[:8]}"
    policy = ManagementPolicy(
        key, ExitPolicyKind.TRAILING,
        InitialStopRule(StopMode.FRACTION, Decimal("0.06")),
        trailing_atr_multiple=Decimal("3.0"),
    )
    with env["session_factory"]() as session:
        service = LifecyclePolicyService(session)
        first = service.publish_management(policy)
        assert service.read_management(first) == policy
        assert service.publish_management(policy).id == first.id
        changed = ManagementPolicy(
            key, ExitPolicyKind.TRAILING, policy.initial_stop,
            trailing_atr_multiple=Decimal("3.5"),
        )
        second = service.publish_management(changed)
        assert second.id != first.id and second.version_no == first.version_no + 1
        assert service.read_management(first) == policy
        with pytest.raises(FillValidationError):
            service.publish(policy_key=key, required_fields=[], config={
                "management_policy": {**policy.to_config(), "unexpected": True},
            })


def test_active_intent_cross_day_reuse_and_daily_fact_freeze(env):
    factory = env["session_factory"]
    with factory() as session:
        policy = LifecyclePolicyService(session).publish(
            policy_key=f"intent_{uuid.uuid4().hex[:8]}", required_fields=[], config={},
        )
    with factory() as session:
        strategy = QuantStrategy(id=uuid.uuid4(), name=f"s-{uuid.uuid4().hex[:8]}", version=1)
        version = QuantStrategyVersion(
            id=uuid.uuid4(), strategy_id=strategy.id, version_no=1, status="PUBLISHED",
            source_code="def strategy(context):\n return {}", source_hash="a" * 64,
            lifecycle_policy_version_id=policy.id, version=1,
        )
        portfolio = Portfolio(
            id=uuid.uuid4(), name=f"life-{uuid.uuid4().hex[:8]}", version=1,
            total_assets=Decimal("10000"), available_cash=Decimal("10000"),
            risk_per_trade_pct=Decimal("0.01"), min_risk_reward_ratio=Decimal("2"),
            max_total_position_pct=Decimal("0.8"), max_single_stock_pct=Decimal("0.1"),
            max_sector_pct=Decimal("0.3"), max_portfolio_open_risk_pct=Decimal("0.06"),
            max_sector_open_risk_pct=Decimal("0.03"), max_daily_new_risk_pct=Decimal("0.02"),
            max_drawdown_pct=Decimal("0.1"), max_daily_loss_pct=Decimal("0.03"),
        )
        lifecycle = PositionLifecycleState(
            id=uuid.uuid4(), portfolio_id=portfolio.id, market="CN", symbol="000001.SZ",
            strategy_version_id=version.id, lifecycle_policy_version_id=policy.id,
            target_exposure_pct=Decimal("0.5"), target_shares=Decimal("500"),
            phase="INITIAL", state_version=1,
        )
        session.add_all([strategy, portfolio])
        session.flush()
        session.add(version)
        session.flush()
        session.add(lifecycle)
        session.commit()
        lifecycle_id = lifecycle.id

    with factory() as session:
        service = LifecycleStateService(session)
        first, created = service.get_or_create_intent(
            lifecycle_id, trade_date=date(2026, 9, 21), target_shares="500", reason_code="INITIAL_ENTRY",
        )
        reused, created_again = service.get_or_create_intent(
            lifecycle_id, trade_date=date(2026, 9, 22), target_shares="500", reason_code="INITIAL_ENTRY",
        )
        assert created is True and created_again is False and reused.id == first.id
        frozen_intents = load_local_intent_definitions(
            session, portfolio_id=portfolio.id, lifecycle_id=lifecycle_id)
        assert frozen_intents.status == "LOCAL_CANDIDATE"
        assert frozen_intents.definitions[0].intent_id == first.id
        assert frozen_intents.definitions[0].target_shares == Decimal(500)

        fact, fact_created = service.get_or_create_daily_fact(
            lifecycle_id, trade_date=date(2026, 9, 21), price_basis="raw",
            data_as_of=datetime(2026, 9, 21, tzinfo=timezone.utc),
            input_payload={"close": "10.00", "ma20": "9.50"}, rule_version="v1",
            state_version_before=1, state_version_after=1, final_target_shares="500",
        )
        replay, replay_created = service.get_or_create_daily_fact(
            lifecycle_id, trade_date=date(2026, 9, 21), price_basis="raw",
            data_as_of=datetime(2026, 9, 21, tzinfo=timezone.utc),
            input_payload={"close": "10.00", "ma20": "9.50"}, rule_version="v1",
            state_version_before=1, state_version_after=1, final_target_shares="500",
        )
        assert fact_created is True and replay_created is False and replay.id == fact.id
        with pytest.raises(LifecycleInvalidStateError):
            service.get_or_create_daily_fact(
                lifecycle_id, trade_date=date(2026, 9, 21), price_basis="raw",
                data_as_of=datetime(2026, 9, 21, tzinfo=timezone.utc),
                input_payload={"close": "10.01"}, rule_version="v1",
                state_version_before=1, state_version_after=1, final_target_shares="500",
            )
        session.flush()
        from sqlalchemy import select
        revisions = list(session.scalars(select(PositionDailyFactRevision).where(
            PositionDailyFactRevision.daily_fact_id == fact.id,
        ).order_by(PositionDailyFactRevision.revision_no)))
        assert len(revisions) == 1
        assert revisions[0].planning_result is None
        fact.planning_result = {"stage": "AWAITING_BATCH"}
        session.flush()
        revisions = list(session.scalars(select(PositionDailyFactRevision).where(
            PositionDailyFactRevision.daily_fact_id == fact.id,
        ).order_by(PositionDailyFactRevision.revision_no)))
        assert len(revisions) == 2
        assert revisions[1].previous_revision_id == revisions[0].id
        assert revisions[1].planning_result == {"stage": "AWAITING_BATCH"}
        fact.updated_at = datetime.now(timezone.utc)
        session.flush()
        assert session.scalar(select(PositionDailyFactRevision).where(
            PositionDailyFactRevision.daily_fact_id == fact.id,
        ).order_by(PositionDailyFactRevision.revision_no.desc())).id == revisions[1].id
        with pytest.raises(Exception), session.begin_nested():
            session.add(PositionDailyFactRevision(
                id=uuid.uuid4(), daily_fact_id=fact.id, revision_no=3,
                previous_revision_id=revisions[1].id, baseline_origin="LIVE",
                lifecycle_id=fact.lifecycle_id, trade_date=fact.trade_date,
                price_basis=fact.price_basis, data_as_of=fact.data_as_of,
                input_payload=fact.input_payload, input_hash=fact.input_hash,
                planning_result=fact.planning_result, rule_version=fact.rule_version,
                state_version_before=fact.state_version_before,
                state_version_after=fact.state_version_after,
                final_target_shares=fact.final_target_shares,
                recorded_at=datetime.now(timezone.utc),
            ))
            session.flush()
        with pytest.raises(Exception), session.begin_nested():
            revisions[0].planning_result = {"stage": "TAMPERED"}
            session.flush()
        session.commit()

    barrier = Barrier(2)

    def update_current(worker: str) -> None:
        with factory() as worker_session:
            current = worker_session.get(PositionDailyFact, fact.id)
            barrier.wait(timeout=5)
            current.planning_result = {"stage": "BATCH_COMPLETED", "worker": worker}
            worker_session.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(update_current, worker) for worker in ("one", "two")]
        for future in futures:
            future.result(timeout=10)
    with factory() as session:
        current = session.get(PositionDailyFact, fact.id)
        chain = list(session.scalars(select(PositionDailyFactRevision).where(
            PositionDailyFactRevision.daily_fact_id == fact.id,
        ).order_by(PositionDailyFactRevision.revision_no)))
        assert [row.revision_no for row in chain] == [1, 2, 3, 4]
        assert [row.previous_revision_id for row in chain[1:]] == [row.id for row in chain[:-1]]
        assert chain[-1].planning_result == current.planning_result
        assert session.query(PositionDailyFact).filter_by(
            lifecycle_id=lifecycle_id, trade_date=date(2026, 9, 21)).count() == 1
        daily_inputs = load_local_daily_fact_inputs(
            session, portfolio_id=portfolio.id, lifecycle_id=lifecycle_id,
            calendar_dates=(date(2026, 9, 21),),
            calendar_source_ref="isolated-fixture:declared-calendar")
        assert daily_inputs.status == "LOCAL_CANDIDATE"
        assert daily_inputs.days[0].revision_id == chain[-1].id
        assert daily_inputs.days[0].fact == current.input_payload
        assert "CALENDAR_SOURCE_UNCERTIFIED" in daily_inputs.issues
        mismatched_calendar = load_local_daily_fact_inputs(
            session, portfolio_id=portfolio.id, lifecycle_id=lifecycle_id,
            calendar_dates=(date(2026, 9, 21), date(2026, 9, 22)),
            calendar_source_ref="isolated-fixture:declared-calendar")
        assert mismatched_calendar.status == "UNKNOWN" and mismatched_calendar.days == ()
        assert "DAILY_FACT_CALENDAR_MISMATCH" in mismatched_calendar.issues
        from sqlalchemy import text
        from sqlalchemy.exc import DBAPIError
        with factory() as writer:
            writer.execute(text("SET LOCAL lock_timeout = '100ms'"))
            writer_fact = writer.get(PositionDailyFact, fact.id)
            writer_fact.planning_result = {"stage": "CONCURRENT_DIRECT_UPDATE"}
            with pytest.raises(DBAPIError, match="lock timeout"):
                writer.flush()
            writer.rollback()
        proposal_kwargs = dict(
            portfolio_id=portfolio.id, lifecycle_id=lifecycle_id,
            trade_date=date(2026, 9, 21), request_key="source-correction-1",
            input_payload={"close": "11.00", "ma20": "9.50"}, price_basis="raw",
            data_as_of=datetime(2026, 9, 21, tzinfo=timezone.utc),
            reason_code="SOURCE_PRICE_CORRECTION",
            source_ref="isolated-fixture:corrected-bar",
            source_sha256="d" * 64,
        )
        proposal, created = propose_daily_fact_input(session, **proposal_kwargs)
        assert created is True
        assert proposal.base_revision_id == chain[-1].id
        assert session.get(PositionDailyFact, fact.id).input_payload["close"] == "10.00"
        session.commit()
    with factory() as session:
        replay, created = propose_daily_fact_input(session, **proposal_kwargs)
        assert created is False and replay.id == proposal.id
        inventory = inventory_lifecycle_replay(session, portfolio.id, lifecycle_id)
        assert inventory.input_proposal_ids == (proposal.id,)
        assert f"DAILY_FACT_INPUT_PROPOSAL_PENDING:{proposal.id}:{date(2026, 9, 21)}" in inventory.issues
        blocked_daily = load_local_daily_fact_inputs(
            session, portfolio_id=portfolio.id, lifecycle_id=lifecycle_id,
            calendar_dates=(date(2026, 9, 21),),
            calendar_source_ref="isolated-fixture:declared-calendar")
        assert blocked_daily.status == "UNKNOWN" and blocked_daily.days == ()
        assert any(issue.startswith("DAILY_FACT_INPUT_PROPOSAL_PENDING:")
                   for issue in blocked_daily.issues)
        with pytest.raises(ValueError, match="changed content"):
            propose_daily_fact_input(session, **{**proposal_kwargs, "source_sha256": "e" * 64})
        with pytest.raises(ValueError, match="already has"):
            propose_daily_fact_input(session, **{**proposal_kwargs, "request_key": "other-request"})
        with pytest.raises(Exception), session.begin_nested():
            stored = session.get(PositionDailyFactInputProposal, proposal.id)
            stored.reason_code = "TAMPERED"
            session.flush()
    with factory() as session:
        current = load_current_lifecycle_projection(
            session, portfolio_id=portfolio.id, lifecycle_id=lifecycle_id)
        assert current.position_quantity is None
        assert current.position_average_cost is None
        assert current.target_shares == Decimal(500)
        assert not session.new and not session.dirty
    with factory() as session:
        changed_intent = session.get(PositionIntent, first.id)
        changed_intent.target_shares = Decimal(600)
        changed_intent.revision += 1
        session.flush()
        changed_definitions = load_local_intent_definitions(
            session, portfolio_id=portfolio.id, lifecycle_id=lifecycle_id)
        assert changed_definitions.status == "UNKNOWN" and changed_definitions.definitions == ()
        assert f"INTENT_DEFINITION_CHANGED:{first.id}" in changed_definitions.issues
        session.rollback()


@pytest.mark.parametrize("kind", ["trend", "macd", "macd_reserved"])
def test_typed_family_fill_daily_management_and_partial_exit(env, kind, certified_stock_rules):
    from backend.modules.quant_strategy.domain.family_management import TREND_3ATR, MACD_MEAN_REVERSION
    factory = env["session_factory"]
    typed = TREND_3ATR if kind == "trend" else MACD_MEAN_REVERSION
    template_id = "ma_trend_cross_v1" if kind == "trend" else "macd_rsi_reversal_v1"
    entry_qty = Decimal(500) if kind == "trend" else Decimal(1000)
    with factory() as session:
        policy = LifecyclePolicyService(session).publish_management(typed)
    with factory() as session:
        strategy = QuantStrategy(id=uuid.uuid4(), name=f"seed-{uuid.uuid4().hex[:8]}", version=1)
        version = QuantStrategyVersion(
            id=uuid.uuid4(), strategy_id=strategy.id, version_no=1, status="PUBLISHED",
            source_code="def strategy(context):\n return {}", source_hash="b" * 64,
            template_id=template_id, template_params={"reward_multiple": "2.2"},
            lifecycle_policy_version_id=policy.id, version=1,
        )
        portfolio = Portfolio(
            id=uuid.uuid4(), name=f"seed-p-{uuid.uuid4().hex[:8]}", version=1,
            total_assets=Decimal("30000"), available_cash=Decimal("20000"),
        )
        task = AnalysisTask(
            id=uuid.uuid4(), task_type="MARKET_WIDE", status="SUCCESS",
            request_params={"execution_snapshot": {"strategy": {
                "version_id": str(version.id),
                "lifecycle_policy": {
                    "id": str(policy.id), "content_hash": policy.content_hash,
                    "config": policy.config,
                },
            }}},
            selected_layers=["position"], input_hash="c" * 64, attempt_no=1,
        )
        session.add_all([strategy, portfolio, task])
        session.flush()
        session.add(version)
        session.flush()
        signal = QuantExecutionSignal(
            task_id=task.id, attempt_no=1, signal_kind="BUY", ts_code="000001.SZ",
            action="BUY", score=80, reason="seed", shares=Decimal("1000"),
            order_status="ELIGIBLE", order_entry_price=Decimal("10"),
            order_stop_price=Decimal("9"), order_take_price=Decimal("12.2"),
        )
        session.add(signal)
        session.flush()
        order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio.id, source_signal_id=signal.id,
            market="CN", symbol="000001.SZ", side="BUY",
            quantity=entry_qty, filled_quantity=Decimal(0),
            limit_price=Decimal("10"), stop_price=Decimal("9"),
            reserved_cash=entry_qty * 10, reserved_risk=entry_qty,
            reason_code="INITIAL_ENTRY", status="PROPOSED", revision=1,
        )
        session.add(order)
        session.commit()
        order_id, portfolio_id = order.id, portfolio.id


    with factory() as session:
        _, order = LifecycleOrderService(session).confirm_fill(
            order_id, quantity=entry_qty, fill_price="10", fill_trade_date=date(2026, 9, 21),
            idempotency_key=f"typed-entry-{uuid.uuid4().hex}", expected_revision=1,
        )
        lifecycle_id = order.lifecycle_id
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        assert lifecycle.target_shares == entry_qty
        assert lifecycle.target_exposure_pct == typed.initial_exposure
        assert lifecycle.profit_take_price is None
        if kind == "trend":
            trailing = session.query(PositionTrailingStop).filter_by(lifecycle_id=lifecycle_id).one()
            assert trailing.config_snapshot == {"mode": "HIGHEST_CLOSE_ATR", "multiple": "3"}
    if kind == "macd_reserved":
        with factory() as session:
            lifecycle = session.get(PositionLifecycleState, lifecycle_id)
            other = SuggestedOrder(
                id=uuid.uuid4(), portfolio_id=portfolio_id, position_id=lifecycle.position_id,
                market="CN", symbol="000001.SZ", side="SELL", quantity=Decimal(600),
                filled_quantity=Decimal(0), limit_price=Decimal(11), reserved_cash=Decimal(0),
                reserved_risk=Decimal(0), reason_code="MANUAL_EXIT", status="PROPOSED", revision=1,
            )
            session.add(other)
            session.commit()
            LifecycleOrderService(session).confirm_fill(
                other.id, quantity="200", fill_price="11", fill_trade_date=date(2026, 9, 21),
                idempotency_key=f"external-partial-{uuid.uuid4().hex}", expected_revision=1,
            )
    # A missing day is persisted for audit but does not become a holding session.
    with factory() as session:
        _, _, _, unavailable = PositionLifecycleManager(session).process_day(
            lifecycle_id, trade_date=date(2026, 9, 22), fact={"close": None},
            data_as_of=datetime(2026, 9, 22, tzinfo=timezone.utc),
        )
        assert not unavailable.data_available
    decision_day = BUY_DAY if kind == "trend" else date(2026, 9, 23)
    fact = {"close": "11", "atr": "0.5", "ma20": "11",
            "execution_market": {"trade_date": decision_day.isoformat(), "raw_close": "11", "down_limit": "9", "is_suspended": False,
                                 "raw_amount": "100000", "adv20_amount": "100000",
                                 "up_limit": "12", "is_st": False},
            "available_sell_quantity": "800" if kind == "macd_reserved" else "1000"}
    with factory() as session:
        _certify_buy_account(session, portfolio_id, decision_day)
        daily, intent, order, decision = PositionLifecycleManager(session).process_day(
            lifecycle_id, trade_date=decision_day, fact=fact,
            buy_context=_buy_context(decision_day, "11"),
            data_as_of=(datetime.now(timezone.utc) if kind == "trend"
                        else datetime(2026, 9, 23, tzinfo=timezone.utc)),
        )
        assert decision.reason_code == ("ADD_AT_R" if kind == "trend" else "CLOSE_GE_MA20")
        if kind == "trend":
            assert order is None
            assert daily.planning_result["projection"]["order_status"] == "BUY_REJECTED_RR"
            assert intent.target_shares == Decimal(1000)
            assert not session.get(PositionLifecycleState, lifecycle_id).confirmation_completed
            return
        assert order.quantity == (Decimal(500) if kind == "trend" else Decimal(400) if kind == "macd_reserved" else Decimal(1000))
        next_order_id = order.id
    with factory() as session:
        LifecycleOrderService(session).confirm_fill(
            next_order_id, quantity="200", fill_price="11", fill_trade_date=date(2026, 9, 23),
            idempotency_key=f"typed-partial-{uuid.uuid4().hex}", expected_revision=1,
        )
        assert not session.get(PositionLifecycleState, lifecycle_id).confirmation_completed
    with factory() as session:
        # Reuse the executing intent across days, including sticky exit reason changes.
        fact = {**fact, "execution_market": {**fact["execution_market"], "trade_date": "2026-09-24"},
                "available_sell_quantity": "600" if kind == "macd_reserved" else "800"}
        _, intent, order, decision = PositionLifecycleManager(session).process_day(
            lifecycle_id, trade_date=date(2026, 9, 24), fact=fact,
            data_as_of=datetime(2026, 9, 24, tzinfo=timezone.utc),
        )
        assert intent.reason_code == ("ADD_AT_R" if kind == "trend" else "CLOSE_GE_MA20")
    with factory() as session:
        order = session.get(SuggestedOrder, next_order_id)
        LifecycleOrderService(session).confirm_fill(
            next_order_id, quantity=order.quantity-order.filled_quantity, fill_price="11",
            fill_trade_date=date(2026, 9, 24), idempotency_key=f"typed-full-{uuid.uuid4().hex}",
            expected_revision=order.revision,
        )
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        if kind == "trend":
            assert lifecycle.confirmation_completed
        elif kind == "macd_reserved":
            assert lifecycle.closed_at is None
            assert session.get(PortfolioPosition, lifecycle.position_id).quantity == Decimal(400)
        else:
            assert lifecycle.closed_at is not None and lifecycle.phase == "CLOSED"


def test_typed_pre_cross_counts_only_complete_non_suspended_observations(env):
    from backend.modules.quant_strategy.domain.family_management import TREND_3ATR
    factory = env["session_factory"]
    typed = TREND_3ATR
    kind = "trend"
    template_id = "ma5_pre_cross_v1"
    entry_qty = Decimal(500)
    with factory() as session:
        policy = LifecyclePolicyService(session).publish_management(typed)
    with factory() as session:
        strategy = QuantStrategy(id=uuid.uuid4(), name=f"seed-{uuid.uuid4().hex[:8]}", version=1)
        version = QuantStrategyVersion(
            id=uuid.uuid4(), strategy_id=strategy.id, version_no=1, status="PUBLISHED",
            source_code="def strategy(context):\n return {}", source_hash="b" * 64,
            template_id=template_id, template_params={"reward_multiple": "2.2"},
            lifecycle_policy_version_id=policy.id, version=1,
        )
        portfolio = Portfolio(
            id=uuid.uuid4(), name=f"seed-p-{uuid.uuid4().hex[:8]}", version=1,
            total_assets=Decimal("30000"), available_cash=Decimal("20000"),
        )
        task = AnalysisTask(
            id=uuid.uuid4(), task_type="MARKET_WIDE", status="SUCCESS",
            request_params={"execution_snapshot": {"strategy": {
                "version_id": str(version.id),
                "lifecycle_policy": {
                    "id": str(policy.id), "content_hash": policy.content_hash,
                    "config": policy.config,
                },
            }}},
            selected_layers=["position"], input_hash="c" * 64, attempt_no=1,
        )
        session.add_all([strategy, portfolio, task])
        session.flush()
        session.add(version)
        session.flush()
        signal = QuantExecutionSignal(
            task_id=task.id, attempt_no=1, signal_kind="BUY", ts_code="000001.SZ",
            action="BUY", score=80, reason="seed", shares=Decimal("1000"),
            order_status="ELIGIBLE", order_entry_price=Decimal("10"),
            order_stop_price=Decimal("9"), order_take_price=Decimal("12.2"),
        )
        session.add(signal)
        session.flush()
        order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio.id, source_signal_id=signal.id,
            market="CN", symbol="000001.SZ", side="BUY",
            quantity=entry_qty, filled_quantity=Decimal(0),
            limit_price=Decimal("10"), stop_price=Decimal("9"),
            reserved_cash=entry_qty * 10, reserved_risk=entry_qty,
            reason_code="INITIAL_ENTRY", status="PROPOSED", revision=1,
        )
        session.add(order)
        session.commit()
        order_id, portfolio_id = order.id, portfolio.id


    with factory() as session:
        _, order = LifecycleOrderService(session).confirm_fill(
            order_id, quantity=entry_qty, fill_price="10", fill_trade_date=date(2026, 9, 21),
            idempotency_key=f"typed-entry-{uuid.uuid4().hex}", expected_revision=1,
        )
        lifecycle_id = order.lifecycle_id
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        assert lifecycle.target_shares == entry_qty
        assert lifecycle.target_exposure_pct == typed.initial_exposure
        assert lifecycle.profit_take_price is None
        if kind == "trend":
            trailing = session.query(PositionTrailingStop).filter_by(lifecycle_id=lifecycle_id).one()
            assert trailing.config_snapshot == {"mode": "HIGHEST_CLOSE_ATR", "multiple": "3"}

    facts = [
        {"close": "10", "atr": "0.5", "ma20": "11"},
        {"close": "10", "atr": "0.5", "ma5": "10", "ma20": "11", "is_suspended": True},
        {"close": "10", "atr": "0.5", "ma5": "10", "ma20": "11"},
    ]
    for day, fact in zip((22, 23, 24), facts):
        with factory() as session:
            _, _, _, decision = PositionLifecycleManager(session).process_day(
                lifecycle_id, trade_date=date(2026, 9, day), fact=fact,
                data_as_of=datetime(2026, 9, day, tzinfo=timezone.utc),
            )
    assert decision.expectation_status == "PENDING"
    assert decision.expectation_observed_days == 1


def test_bind_typed_policy_requires_matching_template_and_preserves_legacy_source(env):
    from backend.modules.quant_strategy.domain.family_management import TREND_3ATR, MACD_MEAN_REVERSION
    from backend.modules.quant_strategy.domain.templates import get_template
    from backend.modules.quant_strategy.application.service import QuantStrategyService
    from backend.modules.quant_strategy.application.errors import StrategyVersionInvalidStateError
    from backend.modules.quant_strategy.infrastructure.repositories import SqlAlchemyQuantStrategyUnitOfWork
    factory = env["session_factory"]
    with factory() as session:
        trend = LifecyclePolicyService(session).publish_management(TREND_3ATR)
        macd = LifecyclePolicyService(session).publish_management(MACD_MEAN_REVERSION)
    source, params = get_template("ma_trend_cross_v1").render()
    with SqlAlchemyQuantStrategyUnitOfWork(factory) as uow:
        service = QuantStrategyService(uow)
        strategy = service.create(f"typed-bind-{uuid.uuid4().hex}", source_code=source,
                                  template_id="ma_trend_cross_v1", template_params=params)
        draft = service.get_draft(strategy.id)
        with pytest.raises(StrategyVersionInvalidStateError):
            service.bind_lifecycle_policy(strategy.id, draft.id, macd.id, draft.version)
        bound = service.bind_lifecycle_policy(strategy.id, draft.id, trend.id, draft.version)
        assert bound.lifecycle_policy_version_id == trend.id and bound.source_code == source






@pytest.mark.parametrize("has_active_lifecycle", (False, True))
@pytest.mark.parametrize("source_mode", ("signal_only", "snapshot_only", "both"))
def test_portfolio_trial_policy_cannot_enter_legacy_first_fill(
    env, has_active_lifecycle, source_mode,
):
    factory = env["session_factory"]
    portfolio_id, order_id = _seed_order(factory)
    with factory() as session:
        policy = LifecyclePolicyService(session).publish_portfolio_trial(
            prepare_trial("stock_medium_momentum:60:10:60"))
        policy_id, policy_hash = policy.id, policy.content_hash
    with factory() as session:
        strategy = QuantStrategy(id=uuid.uuid4(), name=f"portfolio-guard-{uuid.uuid4().hex}", version=1)
        session.add(strategy)
        session.flush()
        version = QuantStrategyVersion(
            id=uuid.uuid4(), strategy_id=strategy.id, version_no=1, status="PUBLISHED",
            source_code="def strategy(context): return {}", source_hash="a" * 64,
            template_id="ma_trend_cross_v1", lifecycle_policy_version_id=policy_id, version=1,
        )
        task = AnalysisTask(
            id=uuid.uuid4(), task_type="MARKET_WIDE", status="SUCCESS",
            request_params=({"execution_snapshot": {"strategy": {
                "version_id": str(version.id),
                **({"lifecycle_policy": {"id": str(policy_id), "content_hash": policy_hash}}
                   if source_mode == "both" else {}),
            }}} if source_mode != "signal_only" else {}),
            selected_layers=["position"], input_hash="d" * 64, attempt_no=1,
        )
        session.add_all([version, task])
        session.flush()
        signal = QuantExecutionSignal(
            task_id=task.id, attempt_no=1,
            strategy_version_id=version.id if source_mode != "snapshot_only" else None,
            signal_kind="BUY", ts_code="000001.SZ",
            action="BUY", score=80, reason="isolated guard", shares=Decimal(100),
            order_status="ELIGIBLE", order_entry_price=Decimal(10),
            order_stop_price=Decimal(8), order_take_price=Decimal(14),
        )
        session.add(signal)
        session.flush()
        session.get(SuggestedOrder, order_id).source_signal_id = signal.id
        if has_active_lifecycle:
            position = PortfolioPosition(
                id=uuid.uuid4(), portfolio_id=portfolio_id, market="CN", symbol="000001.SZ",
                quantity=Decimal(100), average_cost=Decimal(9), active_stop_price=Decimal(8),
            )
            session.add(position)
            session.flush()
            session.add(PositionLifecycleState(
                id=uuid.uuid4(), portfolio_id=portfolio_id, position_id=position.id,
                market="CN", symbol="000001.SZ", strategy_version_id=version.id,
                lifecycle_policy_version_id=policy_id, target_exposure_pct=Decimal(".5"),
                target_shares=Decimal(100), phase="INITIALIZED", state_version=1,
            ))
        session.commit()
    with factory() as session:
        with pytest.raises(LifecycleInvalidStateError, match="组合试验政策尚未接入"):
            LifecycleOrderService(session).confirm_fill(
                order_id, quantity=100, fill_price=10, fill_trade_date=date(2026, 9, 21),
                idempotency_key=f"portfolio-guard-{uuid.uuid4().hex}", expected_revision=1,
            )
        session.rollback()
    with factory() as session:
        assert session.get(SuggestedOrder, order_id).filled_quantity == 0
        assert session.query(OrderFillEvent).filter_by(order_id=order_id).count() == 0
        positions = session.query(PortfolioPosition).filter_by(portfolio_id=portfolio_id).all()
        assert len(positions) == int(has_active_lifecycle)
        if positions:
            assert positions[0].quantity == 100
        assert session.get(Portfolio, portfolio_id).available_cash == 10000


@pytest.mark.parametrize("replay", (False, True))
def test_portfolio_trial_policy_cannot_enter_legacy_daily_evaluator(env, replay):
    factory = env["session_factory"]
    _, lifecycle_ids = _seed_buy_lifecycles(factory)
    lifecycle_id = lifecycle_ids[0]
    if replay:
        with factory() as session:
            _buy_day(session, lifecycle_id)
    with factory() as session:
        policy = LifecyclePolicyService(session).publish_portfolio_trial(
            prepare_trial("stock_medium_momentum:60:10:60"))
        policy_id = policy.id
    with factory() as session:
        life = session.get(PositionLifecycleState, lifecycle_id)
        session.get(QuantStrategyVersion, life.strategy_version_id).lifecycle_policy_version_id = policy_id
        life.lifecycle_policy_version_id = policy_id
        session.commit()
    with factory() as session:
        with pytest.raises(FillValidationError, match="组合试验政策尚未接入"):
            _buy_day(session, lifecycle_id)
        session.rollback()
    with factory() as session:
        assert session.query(PositionDailyFact).filter_by(lifecycle_id=lifecycle_id).count() == int(replay)


@pytest.mark.parametrize("side", ("BUY", "SELL"))
def test_active_portfolio_policy_blocks_manual_buy_but_preserves_sell(env, side):
    factory = env["session_factory"]
    portfolio_id, lifecycle_ids = _seed_buy_lifecycles(factory, symbols=("000001.SZ",))
    lifecycle_id = lifecycle_ids[0]
    with factory() as session:
        policy = LifecyclePolicyService(session).publish_portfolio_trial(
            prepare_trial("stock_medium_momentum:60:10:60"))
        policy_id = policy.id
    with factory() as session:
        life = session.get(PositionLifecycleState, lifecycle_id)
        session.get(QuantStrategyVersion, life.strategy_version_id).lifecycle_policy_version_id = policy_id
        life.lifecycle_policy_version_id = policy_id
        order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id, market="CN", symbol="000001.SZ",
            side=side, quantity=Decimal(100), filled_quantity=Decimal(0),
            limit_price=Decimal(10), stop_price=Decimal(9),
            reserved_cash=Decimal(1000) if side == "BUY" else Decimal(0),
            reserved_risk=Decimal(100) if side == "BUY" else Decimal(0),
            reason_code="isolated-policy-guard", status="PROPOSED", revision=1,
        )
        session.add(order)
        session.commit()
        order_id = order.id
    with factory() as session:
        if side == "BUY":
            with pytest.raises(LifecycleInvalidStateError, match="组合试验政策尚未接入"):
                LifecycleOrderService(session).confirm_fill(
                    order_id, quantity=100, fill_price=10, fill_trade_date=date(2026, 9, 22),
                    idempotency_key=f"policy-no-signal-{uuid.uuid4().hex}", expected_revision=1,
                )
            session.rollback()
        else:
            LifecycleOrderService(session).confirm_fill(
                order_id, quantity=100, fill_price=10, fill_trade_date=date(2026, 9, 22),
                idempotency_key=f"policy-no-signal-{uuid.uuid4().hex}", expected_revision=1,
            )
    with factory() as session:
        assert session.get(SuggestedOrder, order_id).filled_quantity == (0 if side == "BUY" else 100)
        assert session.query(OrderFillEvent).filter_by(order_id=order_id).count() == (0 if side == "BUY" else 1)
        assert session.query(PortfolioPosition).filter_by(
            portfolio_id=portfolio_id, symbol="000001.SZ").one().quantity == (100 if side == "BUY" else 0)
        assert session.get(Portfolio, portfolio_id).available_cash == (2500 if side == "BUY" else 3500)


@pytest.mark.parametrize("link_case", ("closed", "other_symbol"))
def test_portfolio_policy_rejects_stale_or_wrong_order_lifecycle_link(env, link_case):
    factory = env["session_factory"]
    portfolio_id, lifecycle_ids = _seed_buy_lifecycles(
        factory, symbols=("000001.SZ", "000002.SZ") if link_case == "other_symbol"
        else ("000001.SZ",),
    )
    with factory() as session:
        policy = LifecyclePolicyService(session).publish_portfolio_trial(
            prepare_trial("stock_medium_momentum:60:10:60"))
        policy_id = policy.id
    with factory() as session:
        first = session.get(PositionLifecycleState, lifecycle_ids[0])
        session.get(QuantStrategyVersion, first.strategy_version_id).lifecycle_policy_version_id = policy_id
        first.lifecycle_policy_version_id = policy_id
        if link_case == "closed":
            first.closed_at = datetime(2026, 9, 21, tzinfo=timezone.utc)
        linked_id = lifecycle_ids[-1]
        order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id, lifecycle_id=linked_id,
            market="CN", symbol="000001.SZ", side="BUY", quantity=Decimal(100),
            filled_quantity=Decimal(0), limit_price=Decimal(10), stop_price=Decimal(9),
            reserved_cash=Decimal(1000), reserved_risk=Decimal(100),
            reason_code="isolated-stale-link", status="PROPOSED", revision=1,
        )
        session.add(order)
        session.commit()
        order_id = order.id
    with factory() as session:
        with pytest.raises(LifecycleInvalidStateError, match="订单生命周期与活动持仓不一致"):
            LifecycleOrderService(session).confirm_fill(
                order_id, quantity=100, fill_price=10, fill_trade_date=date(2026, 9, 22),
                idempotency_key=f"stale-link-{uuid.uuid4().hex}", expected_revision=1,
            )
        session.rollback()
    with factory() as session:
        assert session.get(SuggestedOrder, order_id).filled_quantity == 0
        assert session.query(OrderFillEvent).filter_by(order_id=order_id).count() == 0
        assert session.query(PortfolioPosition).filter_by(
            portfolio_id=portfolio_id, symbol="000001.SZ").one().quantity == 100
        assert session.get(Portfolio, portfolio_id).available_cash == 2500


def test_fill_rejects_signal_and_task_strategy_version_mismatch(env):
    factory = env["session_factory"]
    portfolio_id, order_id = _seed_order(factory)
    with factory() as session:
        policy = LifecyclePolicyService(session).publish(
            policy_key=f"legacy-mismatch-{uuid.uuid4().hex[:8]}", required_fields=[],
            config={"template_id": "ma_trend_cross_v1"},
        )
        policy_id, policy_hash = policy.id, policy.content_hash
    with factory() as session:
        strategy = QuantStrategy(id=uuid.uuid4(), name=f"mismatch-{uuid.uuid4().hex}", version=1)
        session.add(strategy)
        session.flush()
        versions = [QuantStrategyVersion(
            id=uuid.uuid4(), strategy_id=strategy.id, version_no=no, status="PUBLISHED",
            source_code="def strategy(context): return {}", source_hash="a" * 64,
            template_id="ma_trend_cross_v1", lifecycle_policy_version_id=policy_id, version=1,
        ) for no in (1, 2)]
        session.add_all(versions)
        session.flush()
        task = AnalysisTask(
            id=uuid.uuid4(), task_type="MARKET_WIDE", status="SUCCESS",
            request_params={"execution_snapshot": {"strategy": {
                "version_id": str(versions[1].id),
                "lifecycle_policy": {"id": str(policy_id), "content_hash": policy_hash},
            }}}, selected_layers=["position"], input_hash="e" * 64, attempt_no=1,
        )
        session.add(task)
        session.flush()
        signal = QuantExecutionSignal(
            task_id=task.id, attempt_no=1, strategy_version_id=versions[0].id,
            signal_kind="BUY", ts_code="000001.SZ", action="BUY", score=80,
            reason="version mismatch", shares=Decimal(100), order_status="ELIGIBLE",
            order_entry_price=Decimal(10), order_stop_price=Decimal(8),
            order_take_price=Decimal(14),
        )
        session.add(signal)
        session.flush()
        session.get(SuggestedOrder, order_id).source_signal_id = signal.id
        session.commit()
    with factory() as session:
        with pytest.raises(LifecycleInvalidStateError, match="信号与策略快照版本不一致"):
            LifecycleOrderService(session).confirm_fill(
                order_id, quantity=100, fill_price=10, fill_trade_date=date(2026, 9, 21),
                idempotency_key=f"mismatched-signal-{uuid.uuid4().hex}", expected_revision=1,
            )
        session.rollback()
    with factory() as session:
        assert session.get(SuggestedOrder, order_id).filled_quantity == 0
        assert session.query(OrderFillEvent).filter_by(order_id=order_id).count() == 0
        assert session.query(PortfolioPosition).filter_by(portfolio_id=portfolio_id).count() == 0
        assert session.get(Portfolio, portfolio_id).available_cash == 10000


def test_lifecycle_buy_concurrent_cash_reservations_are_serialized(env, certified_stock_rules):
    factory = env["session_factory"]
    portfolio_id, ids = _seed_buy_lifecycles(factory, day=BUY_DAY)
    barrier = Barrier(2)

    def plan(lifecycle_id):
        with factory() as session:
            # Preload an ORM aggregate to verify the lock refreshes stale identity-map data.
            session.get(Portfolio, portfolio_id)
            barrier.wait(timeout=5)
            daily, _, order, _ = _buy_day(session, lifecycle_id)
            return daily.planning_result["projection"]["order_status"], order.id if order else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(plan, ids))
    assert sorted(code for code, _ in results) == ["BUY_REJECTED_CASH", "ELIGIBLE"]
    with factory() as session:
        orders = session.query(SuggestedOrder).filter_by(portfolio_id=portfolio_id).all()
        assert len(orders) == 1 and orders[0].quantity == Decimal(200)
        assert orders[0].reserved_cash <= Decimal(2500)
        # The normal execution report persists this snapshot through standard JSON.
        import json
        from backend.modules.quant_strategy.application.planning_account import lock_portfolio, planning_account
        snapshot = planning_account(session, lock_portfolio(session, portfolio_id))["portfolio_snapshot"]
        json.dumps(snapshot)
        assert session.get(Portfolio, portfolio_id).available_cash == Decimal(2500)


def test_lifecycle_missing_context_rejection_is_frozen_on_replay(env):
    factory = env["session_factory"]
    _, ids = _seed_buy_lifecycles(factory, cash="20000", symbols=("000001.SZ",), day=BUY_DAY)
    with factory() as session:
        daily, intent, order, _ = _buy_day(session, ids[0], context=False)
        assert order is None and intent is not None
        assert daily.planning_result["projection"]["order_status"] == "BUY_REJECTED_CONTEXT"
        daily_id = daily.id
    with factory() as session:
        daily, _, order, decision = _buy_day(session, ids[0], context=True)
        assert daily.id == daily_id and order is None and decision is None
        assert session.query(SuggestedOrder).filter_by(lifecycle_id=ids[0]).count() == 0


def test_unbound_external_partial_buy_waits_for_reconciliation(env, certified_stock_rules):
    factory = env["session_factory"]
    portfolio_id, ids = _seed_buy_lifecycles(factory, cash="20000", symbols=("000001.SZ",), day=BUY_DAY)
    with factory() as session:
        session.add(SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id, lifecycle_id=ids[0], market="CN", symbol="000001.SZ",
            industry_code="801010", side="BUY", quantity=Decimal(900), filled_quantity=Decimal(100),
            limit_price=Decimal(10), stop_price=Decimal(9), reserved_cash=Decimal(9005),
            reserved_risk=Decimal(900), reason_code="EXTERNAL", status="PARTIALLY_FILLED", revision=2,
        ))
        session.commit()
    with factory() as session:
        daily, intent, order, _ = _buy_day(session, ids[0])
        assert intent is None and order is None
        assert daily.planning_result["stage"] == "AWAITING_ORDER_RECONCILIATION"
        old_order = session.query(SuggestedOrder).filter_by(lifecycle_id=ids[0]).one()
        assert old_order.status == "RECONCILIATION_REQUIRED"
        assert old_order.intent_id is None
        assert old_order.quantity - old_order.filled_quantity == Decimal(800)
        assert old_order.reserved_cash == Decimal(9005)


@pytest.mark.parametrize("status,filled,target", [
    ("PARTIALLY_FILLED", 50, 100),
    ("EXECUTING", 0, 0),
    ("PARTIALLY_FILLED", 50, 200),
    ("PARTIALLY_FILLED", 50, 500),
])
def test_conflicting_broker_facing_order_keeps_reservation_and_intent(
    env, status, filled, target,
):
    factory = env["session_factory"]
    portfolio_id, ids = _seed_buy_lifecycles(factory, cash="20000", symbols=("000001.SZ",))
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, ids[0])
        prior = PositionIntent(
            id=uuid.uuid4(), lifecycle_id=lifecycle.id, trade_date=BUY_DAY,
            target_shares=Decimal(500), reason_code="PRIOR", state_version=1,
            status="ACTIVE", revision=1,
        )
        order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id, position_id=lifecycle.position_id,
            lifecycle_id=lifecycle.id, intent_id=prior.id, market="CN", symbol=lifecycle.symbol,
            side="BUY", quantity=Decimal(300), filled_quantity=Decimal(filled),
            limit_price=Decimal(10), stop_price=Decimal(9), reserved_cash=Decimal(3005),
            reserved_risk=Decimal(300), reason_code="PRIOR", status=status, revision=1,
        )
        session.add_all([prior, order])
        if target == 0:
            unsubmitted = SuggestedOrder(
                id=uuid.uuid4(), portfolio_id=portfolio_id, position_id=lifecycle.position_id,
                lifecycle_id=lifecycle.id, intent_id=prior.id, market="CN", symbol=lifecycle.symbol,
                side="BUY", quantity=Decimal(100), filled_quantity=Decimal(0),
                limit_price=Decimal(10), stop_price=Decimal(9), reserved_cash=Decimal(1005),
                reserved_risk=Decimal(100), reason_code="PRIOR", status="PROPOSED", revision=1,
            )
            session.add(unsubmitted)
        session.commit()
        daily = SimpleNamespace(planning_result=None)
        returned, new_order = PositionLifecycleManager(session)._materialize_delta(
            lifecycle, session.get(PortfolioPosition, lifecycle.position_id),
            SimpleNamespace(target_shares=Decimal(target), reason_code="NEW"), BUY_DAY, {},
            PositionIntent=PositionIntent, SuggestedOrder=SuggestedOrder,
            portfolio=session.get(Portfolio, portfolio_id), buy_context=None, daily=daily,
        )
        session.commit()
        assert returned.id == prior.id and new_order is None
        assert daily.planning_result["stage"] == "AWAITING_ORDER_RECONCILIATION"
        assert session.get(SuggestedOrder, order.id).status == "RECONCILIATION_REQUIRED"
        assert session.get(PositionIntent, prior.id).status == "RECONCILIATION_REQUIRED"
        if target in (0, 500):
            if target == 0:
                assert session.get(SuggestedOrder, unsubmitted.id).status == "SUPERSEDED"
            fill, still_pending = LifecycleOrderService(session).confirm_fill(
                order.id, quantity=Decimal(10), fill_price=Decimal(10),
                fill_trade_date=BUY_DAY, idempotency_key=f"late-bound-fill-{uuid.uuid4()}",
                expected_revision=2,
            )
            assert fill.quantity == 10
            assert still_pending.status == "RECONCILIATION_REQUIRED"
            assert session.get(PositionIntent, prior.id).status == "RECONCILIATION_REQUIRED"
            assert session.get(PortfolioPosition, lifecycle.position_id).quantity == 110


@pytest.mark.parametrize("prior_target,filled,late_quantity,expected_order_status", [
    (110, 50, 10, "RECONCILIATION_REQUIRED"),
    (500, 290, 10, "RECONCILIATION_REQUIRED"),
])
def test_reconciliation_late_fill_does_not_complete_old_intent(
    env, prior_target, filled, late_quantity, expected_order_status,
):
    factory = env["session_factory"]
    portfolio_id, ids = _seed_buy_lifecycles(factory, cash="20000", symbols=("000001.SZ",))
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, ids[0])
        prior = PositionIntent(
            id=uuid.uuid4(), lifecycle_id=lifecycle.id, trade_date=BUY_DAY,
            target_shares=Decimal(prior_target), reason_code="PRIOR", state_version=1,
            status="RECONCILIATION_REQUIRED", revision=2,
        )
        order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id, position_id=lifecycle.position_id,
            lifecycle_id=lifecycle.id, intent_id=prior.id, market="CN", symbol=lifecycle.symbol,
            side="BUY", quantity=Decimal(300), filled_quantity=Decimal(filled),
            limit_price=Decimal(10), stop_price=Decimal(9), reserved_cash=Decimal(3005),
            reserved_risk=Decimal(300), reason_code="PRIOR",
            status="RECONCILIATION_REQUIRED", revision=2,
        )
        session.add_all([prior, order])
        session.commit()
        _, updated = LifecycleOrderService(session).confirm_fill(
            order.id, quantity=Decimal(late_quantity), fill_price=Decimal(10),
            fill_trade_date=BUY_DAY, idempotency_key=f"late-recon-{uuid.uuid4()}",
            expected_revision=2,
        )
        assert updated.status == expected_order_status
        assert session.get(PositionIntent, prior.id).status == "RECONCILIATION_REQUIRED"
        assert session.get(PositionLifecycleState, lifecycle.id).phase != "CLOSED"


def test_unbound_broker_order_cannot_be_attached_to_new_lifecycle_intent(env):
    factory = env["session_factory"]
    portfolio_id, ids = _seed_buy_lifecycles(factory, cash="20000", symbols=("000001.SZ",))
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, ids[0])
        order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id, position_id=lifecycle.position_id,
            lifecycle_id=lifecycle.id, intent_id=None, market="CN", symbol=lifecycle.symbol,
            side="BUY", quantity=Decimal(100), filled_quantity=Decimal(0),
            limit_price=Decimal(10), stop_price=Decimal(9), reserved_cash=Decimal(1005),
            reserved_risk=Decimal(100), reason_code="IMPORT", status="EXECUTING", revision=1,
        )
        session.add(order)
        session.commit()
        daily = SimpleNamespace(planning_result=None)
        intent, new_order = PositionLifecycleManager(session)._materialize_delta(
            lifecycle, session.get(PortfolioPosition, lifecycle.position_id),
            SimpleNamespace(target_shares=Decimal(500), reason_code="NEW"), BUY_DAY, {},
            PositionIntent=PositionIntent, SuggestedOrder=SuggestedOrder,
            portfolio=session.get(Portfolio, portfolio_id), buy_context=None, daily=daily,
        )
        session.commit()
        assert intent is None and new_order is None
        assert daily.planning_result["stage"] == "AWAITING_ORDER_RECONCILIATION"
        assert session.get(SuggestedOrder, order.id).intent_id is None
        assert session.get(SuggestedOrder, order.id).status == "RECONCILIATION_REQUIRED"
        assert session.query(SuggestedOrder).filter_by(lifecycle_id=lifecycle.id).count() == 1


def test_direct_intent_replacement_rejects_bound_active_order(env):
    factory = env["session_factory"]
    portfolio_id, ids = _seed_buy_lifecycles(factory, cash="20000", symbols=("000001.SZ",))
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, ids[0])
        prior = PositionIntent(
            id=uuid.uuid4(), lifecycle_id=lifecycle.id, trade_date=BUY_DAY,
            target_shares=Decimal(500), reason_code="PRIOR", state_version=1,
            status="ACTIVE", revision=1,
        )
        order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id, position_id=lifecycle.position_id,
            lifecycle_id=lifecycle.id, intent_id=prior.id, market="CN", symbol=lifecycle.symbol,
            side="BUY", quantity=Decimal(100), filled_quantity=Decimal(0),
            limit_price=Decimal(10), stop_price=Decimal(9), reserved_cash=Decimal(1005),
            reserved_risk=Decimal(100), reason_code="PRIOR", status="EXECUTING", revision=1,
        )
        session.add_all([prior, order])
        session.commit()
        prior_id, order_id = prior.id, order.id
        with pytest.raises(LifecycleInvalidStateError, match="活动建议单尚未协调"):
            LifecycleStateService(session).get_or_create_intent(
                lifecycle.id, trade_date=BUY_DAY, target_shares=100, reason_code="NEW",
            )
        session.rollback()
    with factory() as session:
        assert session.get(PositionIntent, prior_id).status == "ACTIVE"
        assert session.get(SuggestedOrder, order_id).status == "EXECUTING"


def test_direct_intent_creation_rejects_unbound_broker_order(env):
    factory = env["session_factory"]
    portfolio_id, ids = _seed_buy_lifecycles(factory, cash="20000", symbols=("000001.SZ",))
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, ids[0])
        order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id, position_id=lifecycle.position_id,
            lifecycle_id=lifecycle.id, intent_id=None, market="CN", symbol=lifecycle.symbol,
            side="BUY", quantity=Decimal(100), filled_quantity=Decimal(0),
            limit_price=Decimal(10), stop_price=Decimal(9), reserved_cash=Decimal(1005),
            reserved_risk=Decimal(100), reason_code="IMPORT", status="EXECUTING", revision=1,
        )
        session.add(order)
        session.commit()
        with pytest.raises(LifecycleInvalidStateError, match="活动建议单尚未协调"):
            LifecycleStateService(session).get_or_create_intent(
                lifecycle.id, trade_date=BUY_DAY, target_shares=500, reason_code="NEW",
            )
        session.rollback()
    with factory() as session:
        assert session.query(PositionIntent).filter_by(lifecycle_id=ids[0]).count() == 0


@pytest.mark.parametrize("correction", ["void", "reduce"])
def test_reconciliation_full_late_fill_correction_keeps_quarantine(env, correction):
    factory = env["session_factory"]
    portfolio_id, ids = _seed_buy_lifecycles(factory, cash="20000", symbols=("000001.SZ",))
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, ids[0])
        prior = PositionIntent(
            id=uuid.uuid4(), lifecycle_id=lifecycle.id, trade_date=BUY_DAY,
            target_shares=Decimal(500), reason_code="PRIOR", state_version=1,
            status="RECONCILIATION_REQUIRED", revision=2,
        )
        order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id, position_id=lifecycle.position_id,
            lifecycle_id=lifecycle.id, intent_id=prior.id, market="CN", symbol=lifecycle.symbol,
            side="BUY", quantity=Decimal(300), filled_quantity=Decimal(290),
            limit_price=Decimal(10), stop_price=Decimal(9), reserved_cash=Decimal(3005),
            reserved_risk=Decimal(300), reason_code="PRIOR",
            status="RECONCILIATION_REQUIRED", revision=2,
        )
        session.add_all([prior, order])
        session.commit()
        fill, full_order = LifecycleOrderService(session).confirm_fill(
            order.id, quantity=Decimal(10), fill_price=Decimal(10),
            fill_trade_date=BUY_DAY, idempotency_key=f"late-full-{uuid.uuid4()}",
            expected_revision=2,
        )
        assert full_order.status == "RECONCILIATION_REQUIRED"
        if correction == "void":
            _, corrected = LifecycleOrderService(session).void_fill(
                fill.id, fill_trade_date=BUY_DAY,
                idempotency_key=f"late-void-{uuid.uuid4()}", expected_revision=3,
            )
        else:
            _, corrected = LifecycleOrderService(session).correct_fill(
                fill.id, quantity=Decimal(5), fill_price=Decimal(10),
                fill_trade_date=BUY_DAY, idempotency_key=f"late-correct-{uuid.uuid4()}",
                expected_revision=3,
            )
        assert corrected.status == "RECONCILIATION_REQUIRED"
        assert session.get(PositionIntent, prior.id).status == "RECONCILIATION_REQUIRED"
        assert corrected.quantity - corrected.filled_quantity > 0
        with pytest.raises(FillValidationError, match="可信券商终态回执"):
            LifecycleOrderService(session).set_order_status(
                order.id, status="CANCELLED", expected_revision=4,
            )
        session.rollback()


def test_fill_refreshes_preloaded_position_lifecycle_and_intent_after_other_commit(env, certified_stock_rules):
    from backend.modules.quant_strategy.infrastructure.lifecycle_models import PositionIntent
    factory = env["session_factory"]
    portfolio_id, ids = _seed_buy_lifecycles(factory, cash="20000", symbols=("000001.SZ",), day=BUY_DAY)
    with factory() as session:
        _, intent, order, _ = _buy_day(session, ids[0])
        order_id, intent_id = order.id, intent.id
    with factory() as stale:
        lifecycle = stale.get(PositionLifecycleState, ids[0])
        position = stale.get(PortfolioPosition, lifecycle.position_id)
        intent = stale.get(PositionIntent, intent_id)
        before_version = lifecycle.state_version
        assert position.quantity == Decimal(100)
        with factory() as fresh:
            LifecycleOrderService(fresh).confirm_fill(
                order_id, quantity="100", fill_price="10", fill_trade_date=date(2026, 9, 23),
                idempotency_key=f"refresh-first-{uuid.uuid4().hex}", expected_revision=1,
            )
        LifecycleOrderService(stale).confirm_fill(
            order_id, quantity="100", fill_price="10", fill_trade_date=date(2026, 9, 23),
            idempotency_key=f"refresh-second-{uuid.uuid4().hex}", expected_revision=2,
        )
        assert position.quantity == Decimal(300)
        assert lifecycle.state_version == before_version + 2
        assert intent.revision == 3
        assert stale.get(Portfolio, portfolio_id).available_cash == Decimal(18000)


def test_persisted_owner_blocks_unknown_external_buy_without_hiding_protection(env):
    from backend.modules.quant_strategy.application.ownership import collect_owner_versions
    from backend.modules.quant_strategy.application.planning_account import lock_portfolio
    factory = env["session_factory"]
    portfolio_id, ids = _seed_buy_lifecycles(factory, cash="20000", symbols=("000001.SZ",), day=BUY_DAY)
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, ids[0])
        lock_portfolio(session, portfolio_id)
        assert collect_owner_versions(session, portfolio_id) == {"000001.SZ": str(lifecycle.strategy_version_id)}
        external = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id, market="CN", symbol="000001.SZ",
            industry_code="801010", side="BUY", quantity=Decimal(100), filled_quantity=Decimal(0),
            limit_price=Decimal(10), stop_price=Decimal(9), reserved_cash=Decimal(1005),
            reserved_risk=Decimal(100), reason_code="UNKNOWN_IMPORT", status="PROPOSED", revision=1,
        )
        session.add(external)
        session.commit()
    with factory() as session:
        daily, intent, order, _ = _buy_day(session, ids[0])
        assert intent is not None and order is None
        assert daily.planning_result["projection"]["order_status"] == "BUY_REJECTED_OWNER"
        assert daily.planning_result["account"]["owner_versions"] == {"000001.SZ": None}
        # The unknown-source BUY is not reattributed to this lifecycle.
        assert session.get(SuggestedOrder, external.id).lifecycle_id is None


def test_pending_first_entry_claims_snapshot_owner_until_cancelled(env):
    from backend.modules.quant_strategy.application.ownership import collect_owner_versions
    from backend.modules.quant_strategy.application.planning_account import lock_portfolio
    factory = env["session_factory"]
    portfolio_id, order_id = _seed_order(factory)
    owner = uuid.uuid4()
    with factory() as session:
        task = AnalysisTask(id=uuid.uuid4(), task_type="MARKET_WIDE", status="SUCCESS",
                            request_params={"execution_snapshot": {"strategy": {"version_id": str(owner)}}},
                            selected_layers=["position"], input_hash="d"*64, attempt_no=1)
        session.add(task)
        session.flush()
        signal = QuantExecutionSignal(task_id=task.id, attempt_no=1, signal_kind="BUY",
                                      ts_code="000001.SZ", action="BUY", score=80, reason="legacy")
        session.add(signal)
        session.flush()
        session.get(SuggestedOrder, order_id).source_signal_id = signal.id
        session.commit()
    with factory() as session:
        lock_portfolio(session, portfolio_id)
        assert collect_owner_versions(session, portfolio_id) == {"000001.SZ": str(owner)}
        LifecycleOrderService(session).set_order_status(order_id, status="CANCELLED", expected_revision=1)
    with factory() as session:
        lock_portfolio(session, portfolio_id)
        assert collect_owner_versions(session, portfolio_id) == {}


def test_manual_holding_owner_is_unknown_and_closed_owner_is_not_reused(env):
    from backend.modules.quant_strategy.application.ownership import collect_owner_versions
    from backend.modules.quant_strategy.application.planning_account import lock_portfolio
    factory = env["session_factory"]
    portfolio_id, ids = _seed_buy_lifecycles(factory, symbols=("000001.SZ",))
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, ids[0])
        lifecycle.closed_at = datetime.now(timezone.utc)
        session.commit()
    with factory() as session:
        lock_portfolio(session, portfolio_id)
        assert collect_owner_versions(session, portfolio_id) == {"000001.SZ": None}


@pytest.mark.parametrize("mismatch", ["version", "symbol", "malformed"])
def test_pending_order_inconsistent_source_evidence_is_unknown(env, mismatch):
    from backend.modules.quant_strategy.application.ownership import collect_owner_versions
    from backend.modules.quant_strategy.application.planning_account import lock_portfolio
    factory = env["session_factory"]
    portfolio_id, ids = _seed_buy_lifecycles(factory, symbols=("000001.SZ",))
    with factory() as session:
        version_id = session.get(PositionLifecycleState, ids[0]).strategy_version_id
        snapshot_id = str(uuid.uuid4()) if mismatch == "version" else "invalid" if mismatch == "malformed" else str(version_id)
        task = AnalysisTask(id=uuid.uuid4(), task_type="MARKET_WIDE", status="SUCCESS",
                            request_params={"execution_snapshot": {"strategy": {"version_id": snapshot_id}}},
                            selected_layers=["position"], input_hash="d"*64, attempt_no=1)
        session.add(task)
        session.flush()
        signal = QuantExecutionSignal(task_id=task.id, attempt_no=1, strategy_version_id=version_id,
                                      signal_kind="BUY", ts_code="000003.SZ" if mismatch == "symbol" else "000002.SZ",
                                      action="BUY", score=80, reason="inconsistent")
        session.add(signal)
        session.flush()
        session.add(SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio_id, source_signal_id=signal.id,
            market="CN", symbol="000002.SZ", industry_code="801010", side="BUY",
            quantity=Decimal(100), filled_quantity=Decimal(0), limit_price=Decimal(10),
            stop_price=Decimal(9), reserved_cash=Decimal(1005), reserved_risk=Decimal(100),
            reason_code="TEST", status="PROPOSED", revision=1,
        ))
        session.commit()
    with factory() as session:
        lock_portfolio(session, portfolio_id)
        assert collect_owner_versions(session, portfolio_id)["000002.SZ"] is None


@pytest.mark.parametrize("cause", ["missing_profile", "different_profile", "suspended"])
def test_live_admission_blocks_add_but_still_allows_stop_exit(env, cause):
    from backend.modules.quant_strategy.application.strategy_admission import StrategyAdmissionService
    factory = env["session_factory"]
    portfolio_id, ids = _seed_buy_lifecycles(factory, cash="20000", symbols=("000001.SZ",))
    with factory() as session:
        portfolio = session.get(Portfolio, portfolio_id)
        lifecycle = session.get(PositionLifecycleState, ids[0])
        if cause == "suspended":
            StrategyAdmissionService(session).record_restriction(
                lifecycle.strategy_version_id, family_id="trend", asset_scope="CN_STOCK", risk_profile="BALANCED",
                state="SUSPENDED", reason="fixture suspension", request_key="suspend", expected_revision=1,
            )
        else:
            portfolio.risk_profile = None if cause == "missing_profile" else "AGGRESSIVE"
        session.commit()
    with factory() as session:
        daily, _, order, _ = _buy_day(session, ids[0])
        assert order is None
        assert daily.planning_result["projection"]["order_status"] == "BUY_REJECTED_ADMISSION"
        assert daily.planning_result["admission"]["allowed"] is False
    with factory() as session:
        _, _, order, _ = PositionLifecycleManager(session).process_day(
            ids[0], trade_date=date(2026, 9, 23), data_as_of=datetime.now(timezone.utc),
            fact={"close": "8", "high": "8", "available_sell_quantity": "100",
                  "execution_market": {"trade_date": "2026-09-23", "raw_close": "8",
                                       "down_limit": "7", "is_suspended": False}},
        )
        assert order is not None and order.side == "SELL" and order.quantity == Decimal(100)


@pytest.mark.parametrize("available", [None, "invalid", "NaN", "-1", "101"])
def test_lifecycle_protective_sell_keeps_intent_when_sellable_fact_is_untrusted(env, available):
    factory = env["session_factory"]
    _, ids = _seed_buy_lifecycles(factory, cash="20000", symbols=("000001.SZ",))
    day = date(2026, 9, 23)
    with factory() as session:
        daily, intent, order, _ = PositionLifecycleManager(session).process_day(
            ids[0], trade_date=day, data_as_of=datetime.now(timezone.utc),
            fact={"close": "8", "high": "8", "available_sell_quantity": available,
                  "execution_market": {"trade_date": day.isoformat(), "raw_close": "8",
                                       "down_limit": "7", "is_suspended": False}},
        )
        assert intent is not None and order is None
        assert daily.planning_result["sell_status"] == "SELLABLE_QUANTITY_UNKNOWN"


def test_corrected_same_day_drawdown_pause_preserves_protective_sell(env):
    from backend.modules.quant_strategy.application.portfolio_drawdown_actions import PortfolioDrawdownActions

    factory = env["session_factory"]
    portfolio_id, ids = _seed_buy_lifecycles(factory, cash="20000", symbols=("000001.SZ",))
    day = date(2026, 9, 23)
    with factory() as session:
        portfolio = session.get(Portfolio, portfolio_id)
        portfolio.net_asset_value = Decimal(255000)
        portfolio.risk_facts_as_of = day
        event = PortfolioDrawdownActions(session).pause_if_full(portfolio_id, valuation_date=day)
        session.commit()
        portfolio.net_asset_value = Decimal(260000)
        session.commit()
    with factory() as session:
        _, _, order, _ = PositionLifecycleManager(session).process_day(
            ids[0], trade_date=day, data_as_of=datetime.now(timezone.utc),
            fact={"close": "8", "high": "8", "available_sell_quantity": "100",
                  "execution_market": {"trade_date": day.isoformat(), "raw_close": "8",
                                       "down_limit": "7", "is_suspended": False}},
        )
        assert order is not None and order.side == "SELL" and order.quantity == Decimal(100)
        assert PortfolioDrawdownActions(session).active_pause(portfolio_id).id == event.id


# Joint-family deferred execution uses the same isolated fixture and real planner.




def test_deferred_buy_waits_then_obeys_family_budget_and_replays(env, certified_stock_rules):
    factory = env["session_factory"]
    _pid, _lid, daily_id, batch_id = _deferred_batch(factory)
    with factory() as session:
        order = _consume_deferred(session, daily_id, batch_id)
        assert order is not None and order.side == "BUY"
        assert order.quantity == 100  # 2475 family target - 1000 held, with raw-price/slippage cap.
        assert order.quantity * order.limit_price <= 1400
        session.commit()
        assert _consume_deferred(session, daily_id, batch_id).id == order.id
        order.status = "CANCELLED"
        session.commit()
        replay = _consume_deferred(session, daily_id, batch_id)
        assert replay.id == order.id and replay.status == "CANCELLED"
        daily = session.get(PositionDailyFact, daily_id)
        assert daily.planning_result["stage"] == "BATCH_COMPLETED"
        assert daily.planning_result["allocation_batch_id"] != str(batch_id)


@pytest.mark.parametrize("change", ["suspend", "state", "filled"])
def test_deferred_buy_revalidates_waiting_changes_and_keeps_rejection(env, change):
    from backend.modules.quant_strategy.application.strategy_admission import StrategyAdmissionService
    factory = env["session_factory"]
    _pid, lid, daily_id, batch_id = _deferred_batch(factory)
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, lid)
        if change == "suspend":
            StrategyAdmissionService(session).record_restriction(lifecycle.strategy_version_id,
                family_id="trend", asset_scope="CN_STOCK", risk_profile="BALANCED", state="SUSPENDED",
                reason="fixture", request_key="pause", expected_revision=1)
        elif change == "state":
            lifecycle.state_version += 1
            lifecycle.target_shares = 0
        else:
            session.get(PortfolioPosition, lifecycle.position_id).quantity = lifecycle.target_shares
        session.commit()
        assert _consume_deferred(session, daily_id, batch_id) is None
        session.commit()
        assert _consume_deferred(session, daily_id, batch_id) is None
        assert session.get(PositionDailyFact, daily_id).planning_result["stage"] == "BATCH_COMPLETED"
        assert session.query(SuggestedOrder).filter_by(lifecycle_id=lid).count() == 0


def test_deferred_buy_consumption_rollback_is_atomic(env, certified_stock_rules):
    factory = env["session_factory"]
    _pid, lid, daily_id, batch_id = _deferred_batch(factory)
    with factory() as session:
        assert _consume_deferred(session, daily_id, batch_id) is not None
        session.rollback()
        assert session.get(PositionDailyFact, daily_id).planning_result["stage"] == "AWAITING_BATCH"
        assert session.query(SuggestedOrder).filter_by(lifecycle_id=lid).count() == 0
        assert _consume_deferred(session, daily_id, batch_id) is not None
        session.commit()


def test_deferred_buy_concurrent_consumers_materialize_once(env, certified_stock_rules):
    factory = env["session_factory"]
    _pid, lid, daily_id, batch_id = _deferred_batch(factory)
    barrier = Barrier(2)
    def consume(_):
        with factory() as session:
            barrier.wait(timeout=5)
            order = _consume_deferred(session, daily_id, batch_id)
            session.commit()
            return order.id
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(consume, range(2)))
    assert results[0] == results[1]
    with factory() as session:
        assert session.query(SuggestedOrder).filter_by(lifecycle_id=lid).count() == 1


def test_defer_buy_does_not_delay_protective_sell(env):
    factory = env["session_factory"]
    _pid, ids = _seed_buy_lifecycles(factory, cash="20000", symbols=("000001.SZ",))
    with factory() as session:
        daily, _intent, order, _decision = PositionLifecycleManager(session).process_day(
            ids[0], trade_date=date(2026, 9, 22), defer_buy=True,
            fact={"close": "8", "high": "8", "available_sell_quantity": "100",
                  "execution_market": {"trade_date": "2026-09-22", "raw_close": "8",
                                       "down_limit": "7", "is_suspended": False}},
            data_as_of=datetime(2026, 9, 22, tzinfo=timezone.utc))
        assert order is not None and order.side == "SELL" and order.quantity == 100
        assert (daily.planning_result or {}).get("stage") != "AWAITING_BATCH"


@pytest.mark.parametrize("bounds,eligible", [
    ((Decimal("8"), Decimal("9")), False),
    ((Decimal("9"), Decimal("10")), False),  # Slippage/tick exceeds signal close.
    ((Decimal("9"), Decimal("11")), True),
])
def test_deferred_buy_respects_raw_entry_interval_after_slippage(env, bounds, eligible,
                                                                  certified_stock_rules):
    factory = env["session_factory"]
    _pid, _lid, daily_id, batch_id = _deferred_batch(factory, bounds=bounds)
    with factory() as session:
        order = _consume_deferred(session, daily_id, batch_id)
        assert (order is not None) == eligible
        if eligible:
            assert bounds[0] <= order.limit_price <= bounds[1]
        else:
            assert session.get(PositionDailyFact, daily_id).planning_result["projection"]["order_status"] == "BUY_REJECTED_PRICE_RANGE"
        session.commit()


def test_deferred_buy_supersedes_old_order_above_new_target(env):
    factory = env["session_factory"]
    pid, ids = _seed_buy_lifecycles(factory, cash="20000", symbols=("000001.SZ",))
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, ids[0])
        order = SuggestedOrder(id=uuid.uuid4(), portfolio_id=pid, lifecycle_id=lifecycle.id,
            position_id=lifecycle.position_id, market="CN", symbol="000001.SZ", side="BUY",
            quantity=Decimal(1500), filled_quantity=Decimal(0), limit_price=Decimal(10), stop_price=Decimal(9),
            reserved_cash=Decimal(15000), reserved_risk=Decimal(1500), industry_code="801010",
            reason_code="OLD_TARGET", status="PROPOSED", revision=1)
        session.add(order)
        session.commit()
        daily, _intent, new_order, decision = _buy_day(session, ids[0], defer_buy=True)
        assert decision.target_shares < Decimal(1600)
        assert new_order is None and order.status == "SUPERSEDED"
        assert daily.planning_result["stage"] == "AWAITING_BATCH"


def test_persisted_lifecycle_diagnosis_keeps_missing_fill_and_daily_facts_unknown(env):
    factory = env["session_factory"]
    portfolio_id, lifecycle_ids = _seed_buy_lifecycles(
        factory, cash="20000", symbols=("000001.SZ",))
    with factory() as session:
        diagnosed = diagnose_persisted_lifecycle(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_ids[0],
            baseline_as_of=datetime(2026, 9, 20, tzinfo=timezone.utc),
            baseline_quantity=Decimal(0), baseline_total_cost=Decimal(0),
            baseline_source_ref="declared-empty-baseline",
            seed_state=LifecycleStateInput(
                template_id="ma_trend_cross_v1",
                initial_fill_price=Decimal(10), initial_stop_price=Decimal(9),
                risk_capacity_shares=Decimal(1000),
                target_exposure_pct=Decimal("0.50"),
                profit_take_price=Decimal(12)),
            seed_target_shares=Decimal(500), seed_trailing=None,
            seed_expectation=None, management_policy=None,
            calendar_dates=(BUY_DAY,), calendar_source_ref="declared-calendar",
            corporate_action_dates=(),
            corporate_action_source_ref="declared-action-coverage")
        assert diagnosed.status == "UNKNOWN" and diagnosed.diff is None
        assert "INTENT_DEFINITION_SET_EMPTY" in diagnosed.issues
        assert "INITIAL_FILL_MISSING" in diagnosed.issues
        assert not session.new and not session.dirty
        from sqlalchemy import text
        from sqlalchemy.exc import DBAPIError
        position_id = session.get(PositionLifecycleState, lifecycle_ids[0]).position_id
        with factory() as writer:
            writer.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(DBAPIError, match="lock timeout"):
                writer.execute(text("""UPDATE portfolio_positions
                    SET quantity = quantity + 1 WHERE id = :id"""),
                    {"id": position_id})
            writer.rollback()
