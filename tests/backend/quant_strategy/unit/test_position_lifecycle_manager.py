# test-catalog-begin
# {
#   "purpose": "量化策略 / position_lifecycle_manager（持仓生命周期）",
#   "keywords": [
#     "量化策略",
#     "每日",
#     "复权因子",
#     "持仓生命周期",
#     "市场分析",
#     "仓位管理",
#     "止损",
#     "模板",
#     "position_lifecycle_manager",
#     "daily",
#     "factor",
#     "lifecycle",
#     "market",
#     "position",
#     "stop",
#     "templates"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/position_lifecycle_manager.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from datetime import date
from decimal import Decimal

import pytest

from backend.modules.quant_strategy.application.position_lifecycle_manager import (
    ExpectationStateInput,
    LifecycleStateInput,
    TrailingStateInput,
    evaluate_lifecycle_day,
)


def _state(template_id, **values):
    base = dict(
        template_id=template_id, initial_fill_price=Decimal("10"),
        initial_stop_price=Decimal("9"), risk_capacity_shares=Decimal("1000"),
        target_exposure_pct=Decimal("0.50"), profit_take_price=Decimal("12"),
    )
    base.update(values)
    return LifecycleStateInput(**base)


def _fact(**values):
    base = dict(
        close="10.5", high="10.6", ma5="10.3", prev_ma5="10.0",
        ma20="10.2", prev_ma20="10.1", ma60="9.5",
        boll_lower="9.5", boll_mid="10", boll_upper="10.3",
        macd="0.3", prev_macd="0.2", dif="0.1", prev_dif="-0.1",
        dea="0", prev_dea="0", rsi="55", prev_rsi="50",
        volume="200", volume_base="100",
    )
    base.update(values)
    return base


@pytest.mark.parametrize("template_id", [
    "ma_trend_cross_v1", "trend_pullback_v1", "boll_volume_breakout_v1",
    "macd_rsi_reversal_v1", "volume_surge_confirm_v1",
])
def test_five_standard_templates_confirm_to_full_target(template_id):
    result = evaluate_lifecycle_day(
        _state(template_id), trade_date=date(2026, 9, 22), fact=_fact(),
        actual_shares=Decimal("500"), observation_no=1,
    )
    assert result.reason_code == "TEMPLATE_CONFIRM_ADD"
    assert result.target_exposure_pct == Decimal("1.00")
    assert result.target_shares == Decimal("1000")


def test_market_wide_gap_blocks_add_but_keeps_holding_exit():
    protected = evaluate_lifecycle_day(
        _state("ma_trend_cross_v1"), trade_date=date(2026, 9, 22),
        fact=_fact(new_risk_allowed=False), actual_shares=Decimal("500"), observation_no=1,
    )
    assert protected.reason_code == "TEMPLATE_CONFIRM_ADD"
    assert protected.target_shares == Decimal("500")

    exit_decision = evaluate_lifecycle_day(
        _state("ma_trend_cross_v1"), trade_date=date(2026, 9, 22),
        fact=_fact(close="8.8", new_risk_allowed=False),
        actual_shares=Decimal("500"), observation_no=1,
    )
    assert exit_decision.reason_code == "INITIAL_STOP_LOSS"
    assert exit_decision.target_shares == 0


def test_arc_bottom_uses_frozen_neckline_and_only_next_complete_day():
    state = _state("arc_bottom_75a_v1", arc_neckline_price=Decimal("10.2"))
    first = evaluate_lifecycle_day(
        state, trade_date=date(2026, 9, 22), fact=_fact(close="10.5"),
        actual_shares=Decimal("500"), observation_no=1,
    )
    late = evaluate_lifecycle_day(
        state, trade_date=date(2026, 9, 23), fact=_fact(close="10.5"),
        actual_shares=Decimal("500"), observation_no=2,
    )
    broken = evaluate_lifecycle_day(
        state, trade_date=date(2026, 9, 22), fact=_fact(close="10.1"),
        actual_shares=Decimal("500"), observation_no=1,
    )
    assert first.reason_code == "TEMPLATE_CONFIRM_ADD"
    assert late.reason_code == "LIFECYCLE_HOLD"
    assert broken.reason_code == "ARC_BOTTOM_INVALID"
    assert broken.target_shares == 0


def test_trailing_stop_is_monotonic_and_new_stop_only_applies_next_day():
    trailing = TrailingStateInput(
        high_water_mark=Decimal("10"), active_stop_price=Decimal("9"),
        b=Decimal("1"), a=Decimal("2"), d=Decimal("0.08"),
    )
    advanced = evaluate_lifecycle_day(
        _state("ma_trend_cross_v1", confirmation_completed=True),
        trade_date=date(2026, 9, 22), fact=_fact(close="10.2", high="12"),
        actual_shares=Decimal("1000"), observation_no=2, trailing=trailing,
    )
    assert advanced.reason_code == "LIFECYCLE_HOLD"
    assert advanced.trailing_phase == "TRAILING"
    assert advanced.active_stop_price == Decimal("11.04")

    stopped = evaluate_lifecycle_day(
        _state("ma_trend_cross_v1", confirmation_completed=True),
        trade_date=date(2026, 9, 23), fact=_fact(close="11.04", high="11.2"),
        actual_shares=Decimal("1000"), observation_no=3,
        trailing=TrailingStateInput(
            high_water_mark=advanced.high_water_mark,
            active_stop_price=advanced.active_stop_price,
            phase=advanced.trailing_phase, b=Decimal("1"), a=Decimal("2"), d=Decimal("0.08"),
        ),
    )
    assert stopped.reason_code == "TRAILING_STOP_LOSS"
    assert stopped.target_shares == 0


def test_ma5_expectation_weakens_fulfills_and_times_out_on_observed_days_only():
    state = _state("ma5_pre_cross_v1")
    expectation = ExpectationStateInput(fill_trade_date=date(2026, 9, 21))
    weak = evaluate_lifecycle_day(
        state, trade_date=date(2026, 9, 22),
        fact=_fact(ma5="10", prev_ma5="10.0", ma20="10.2"),
        actual_shares=Decimal("500"), observation_no=1, expectation=expectation,
    )
    assert weak.reason_code == "TEMPLATE_WEAKEN"
    assert weak.target_shares == Decimal("200")

    fulfilled = evaluate_lifecycle_day(
        state, trade_date=date(2026, 9, 23), fact=_fact(ma5="10.3", ma20="10.2"),
        actual_shares=Decimal("500"), observation_no=2, expectation=expectation,
    )
    assert fulfilled.reason_code == "TEMPLATE_CONFIRM_ADD"
    assert fulfilled.expectation_status == "FULFILLED"

    timed_out = evaluate_lifecycle_day(
        state, trade_date=date(2026, 9, 25), fact=_fact(ma5="10.1", ma20="10.2"),
        actual_shares=Decimal("500"), observation_no=3, expectation=expectation,
    )
    assert timed_out.reason_code == "EXPECTATION_TIMEOUT"
    assert timed_out.expectation_status == "EXPIRED"
    assert timed_out.target_shares == 0


def test_ma5_missing_factor_does_not_consume_window_and_terminal_does_not_regress():
    state = _state("ma5_pre_cross_v1")
    missing = evaluate_lifecycle_day(
        state, trade_date=date(2026, 9, 22), fact=_fact(ma5=None),
        actual_shares=Decimal("500"), observation_no=1,
        expectation=ExpectationStateInput(fill_trade_date=date(2026, 9, 21)),
    )
    assert missing.expectation_status == "PENDING"
    assert missing.expectation_observed_days == 0
    assert missing.target_shares == Decimal("500")

    terminal = evaluate_lifecycle_day(
        _state("ma5_pre_cross_v1", confirmation_completed=True),
        trade_date=date(2026, 9, 25), fact=_fact(ma5="9", ma20="10", prev_ma5="8", prev_ma20="10"),
        actual_shares=Decimal("1000"), observation_no=4,
        expectation=ExpectationStateInput(
            fill_trade_date=date(2026, 9, 21), observed_trading_days=2, status="FULFILLED",
        ),
    )
    assert terminal.expectation_status == "FULFILLED"
    assert terminal.reason_code != "EXPECTATION_TIMEOUT"


def test_priority_stop_then_profit_then_weaken_then_confirm():
    state = _state("ma_trend_cross_v1", profit_take_price=Decimal("10.4"))
    stop = evaluate_lifecycle_day(
        state, trade_date=date(2026, 9, 22), fact=_fact(close="8.9"),
        actual_shares=Decimal("500"), observation_no=1,
    )
    profit = evaluate_lifecycle_day(
        state, trade_date=date(2026, 9, 22), fact=_fact(close="10.5", macd="0.1", prev_macd="0.2"),
        actual_shares=Decimal("1000"), observation_no=1,
    )
    assert stop.reason_code == "INITIAL_STOP_LOSS"
    assert profit.reason_code == "PROFIT_TARGET_TRIM"
    assert profit.profit_target_reached is True


def test_missing_daily_price_fails_closed_without_inventing_target():
    result = evaluate_lifecycle_day(
        _state("ma_trend_cross_v1"), trade_date=date(2026, 9, 22),
        fact=_fact(close=None), actual_shares=Decimal("500"), observation_no=1,
    )
    assert result.data_available is False
    assert result.reason_code == "LIFECYCLE_DATA_UNAVAILABLE"
    assert result.target_shares == Decimal("500")


def test_script_partial_sell_overrides_higher_lifecycle_target_with_absolute_target():
    result = evaluate_lifecycle_day(
        _state("ma_trend_cross_v1", confirmation_completed=True, target_exposure_pct=Decimal("1.00")),
        trade_date=date(2026, 9, 22), fact=_fact(script_target_shares="700"),
        actual_shares=Decimal("1000"), observation_no=2,
    )
    assert result.reason_code == "SCRIPT_SELL_PARTIAL"
    assert result.phase == "SCRIPT_REDUCE_PENDING"
    assert result.target_shares == Decimal("700")
