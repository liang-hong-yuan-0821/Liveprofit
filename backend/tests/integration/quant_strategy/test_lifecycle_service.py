from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal
from threading import Barrier

import pytest

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
from backend.modules.quant_strategy.application.position_lifecycle_manager import PositionLifecycleManager
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    PositionDailyFact,
    PositionExpectation,
    PositionLifecycleState,
    PositionTrailingStop,
    SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.models import QuantStrategy, QuantStrategyVersion
from backend.modules.quant_strategy.infrastructure.signals import QuantExecutionSignal


def _seed_order(session_factory, *, side="BUY", quantity=Decimal("100"), cash=Decimal("10000")):
    with session_factory() as session:
        portfolio = Portfolio(
            id=uuid.uuid4(), name=f"p-{uuid.uuid4().hex[:8]}", version=1,
            total_assets=Decimal("10000"), available_cash=cash,
            risk_per_trade_pct=Decimal("0.01"), min_risk_reward_ratio=Decimal("2"),
            max_total_position_pct=Decimal("0.8"), max_single_stock_pct=Decimal("0.1"),
            max_sector_pct=Decimal("0.3"), max_portfolio_open_risk_pct=Decimal("0.06"),
            max_sector_open_risk_pct=Decimal("0.03"), max_daily_new_risk_pct=Decimal("0.02"),
            max_drawdown_pct=Decimal("0.1"), max_daily_loss_pct=Decimal("0.03"),
        )
        session.add(portfolio)
        if side == "SELL":
            session.add(PortfolioPosition(
                id=uuid.uuid4(), portfolio_id=portfolio.id, market="CN", symbol="000001.SZ",
                quantity=quantity, average_cost=Decimal("7"), active_stop_price=Decimal("6"),
            ))
        order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio.id, market="CN", symbol="000001.SZ",
            industry_code="801010", side=side, quantity=quantity, filled_quantity=Decimal(0),
            limit_price=Decimal("10"), stop_price=Decimal("8"),
            reserved_cash=quantity * Decimal("10") if side == "BUY" else Decimal(0),
            reserved_risk=quantity * Decimal("2") if side == "BUY" else Decimal(0),
            reason_code="TEST", status="PROPOSED", revision=1,
        )
        session.add(order)
        session.commit()
        return portfolio.id, order.id


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
        assert order.status == "PROPOSED"
        position = session.query(PortfolioPosition).filter_by(portfolio_id=portfolio_id).one()
        portfolio = session.get(Portfolio, portfolio_id)
        assert position.quantity == Decimal("0.0000")
        assert portfolio.available_cash == Decimal("10000.0000")


def test_first_real_fill_initializes_bound_lifecycle_and_not_before(env):
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
            market="CN", symbol="000001.SZ", side="BUY",
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
            order_id, quantity="200", fill_price="10", fill_trade_date=date(2026, 9, 21),
            idempotency_key=f"seed-fill-{uuid.uuid4().hex}", expected_revision=1,
        )
        assert updated.lifecycle_id is not None

    with factory() as session:
        lifecycle = session.query(PositionLifecycleState).filter_by(portfolio_id=portfolio_id).one()
        assert lifecycle.initial_fill_price == Decimal("10.0000")
        assert lifecycle.initial_stop_price == Decimal("9.0000")
        assert lifecycle.risk_capacity_shares == Decimal("1000.0000")
        assert lifecycle.target_shares == Decimal("500.0000")
        assert lifecycle.phase == "ENTRY_PENDING"
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
    }
    with factory() as session:
        daily, intent, add_order, decision = PositionLifecycleManager(session).process_day(
            lifecycle_id, trade_date=date(2026, 9, 22), fact=fact,
            data_as_of=datetime(2026, 9, 22, tzinfo=timezone.utc),
        )
        assert decision.reason_code == "TEMPLATE_CONFIRM_ADD"
        assert intent.target_shares == Decimal("1000.0000")
        assert add_order.quantity == Decimal("500.0000")
        add_order_id, daily_id = add_order.id, daily.id

    with factory() as session:
        replay, replay_intent, replay_order, replay_decision = PositionLifecycleManager(session).process_day(
            lifecycle_id, trade_date=date(2026, 9, 22), fact=fact,
            data_as_of=datetime(2026, 9, 22, tzinfo=timezone.utc),
        )
        assert replay.id == daily_id
        assert replay_order.id == add_order_id
        assert replay_decision is None

    with factory() as session:
        LifecycleOrderService(session).confirm_fill(
            add_order_id, quantity="500", fill_price="10.5", fill_trade_date=date(2026, 9, 23),
            idempotency_key=f"add-fill-{uuid.uuid4().hex}", expected_revision=1,
        )
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        assert lifecycle.confirmation_completed is False
    with factory() as session:
        LifecycleOrderService(session).confirm_fill(
            order_id, quantity="300", fill_price="10", fill_trade_date=date(2026, 9, 23),
            idempotency_key=f"initial-rest-{uuid.uuid4().hex}", expected_revision=2,
        )
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        assert lifecycle.confirmation_completed is True
        assert lifecycle.phase == "CONFIRMED"

    profit_fact = {**fact, "close": "12.3", "high": "12.5"}
    with factory() as session:
        _daily, _intent, trim_order, decision = PositionLifecycleManager(session).process_day(
            lifecycle_id, trade_date=date(2026, 9, 24), fact=profit_fact,
            data_as_of=datetime(2026, 9, 24, tzinfo=timezone.utc),
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
            trim_order_id, quantity="200", fill_price="12.3", fill_trade_date=date(2026, 9, 25),
            idempotency_key=f"trim-partial-{uuid.uuid4().hex}", expected_revision=1,
        )
    with factory() as session:
        lifecycle = session.get(PositionLifecycleState, lifecycle_id)
        assert lifecycle.profit_trim_completed is False
    with factory() as session:
        LifecycleOrderService(session).confirm_fill(
            trim_order_id, quantity="300", fill_price="12.3", fill_trade_date=date(2026, 9, 25),
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
                lifecycle_id, trade_date=date(2026, 9, 26), fact=profit_fact,
                data_as_of=datetime(2026, 9, 26, tzinfo=timezone.utc),
            )
            return daily.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        daily_ids = list(pool.map(lambda _index: process_same_day(), range(2)))
    assert len(set(daily_ids)) == 1
    with factory() as session:
        assert session.query(PositionDailyFact).filter_by(
            lifecycle_id=lifecycle_id, trade_date=date(2026, 9, 26),
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
