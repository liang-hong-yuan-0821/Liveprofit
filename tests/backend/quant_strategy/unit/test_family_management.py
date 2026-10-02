# test-catalog-begin
# {
#   "purpose": "量化策略 / family_management",
#   "keywords": [
#     "量化策略",
#     "策略族",
#     "成交",
#     "持仓生命周期",
#     "订单",
#     "family_management",
#     "family",
#     "fill",
#     "lifecycle",
#     "order"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/position_lifecycle_manager.py",
#     "backend/modules/quant_strategy/domain/family_management.py",
#     "backend/modules/quant_strategy/domain/management_policies.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from dataclasses import replace
from decimal import Decimal as D

import pytest

from backend.modules.quant_strategy.domain.family_management import (
    TREND_3ATR, MACD_MEAN_REVERSION, FamilyManagementState, evaluate_family_management,
)
from backend.modules.quant_strategy.domain.management_policies import ManagementPolicy

STATE = FamilyManagementState(D(10), D(9), D(10), D(9), D("0.5"))


def run(state=STATE, policy=TREND_3ATR, **kwargs):
    values = dict(close=D(11), atr=D("0.5"), valid_sessions_after_fill=1)
    values.update(kwargs)
    return evaluate_family_management(policy, state, **values)


def test_frozen_policy_roundtrip_and_one_r_add():
    policy = ManagementPolicy.from_config(TREND_3ATR.to_config())
    result = run(policy=policy)
    assert result.target_exposure == 1 and result.reason == "ADD_AT_R"
    assert result.highest_close == 11 and result.active_stop == D("9.5")
    assert run(replace(STATE, add_filled=True)).reason == "HOLD"
    assert run(valid_sessions_after_fill=0).target_exposure == D("0.5")
    assert run(new_risk_allowed=False).target_exposure == D("0.5")


def test_no_fixed_two_r_trim_and_trail_never_loosens():
    full = replace(STATE, target_exposure=D(1), add_filled=True,
                   highest_close=D(13), active_stop=D("11.5"))
    result = run(full, close=D(12), atr=D(2))
    assert result.target_exposure == 1
    assert result.active_stop == D("11.5")
    assert result.highest_close == 13


@pytest.mark.parametrize("close,reason", [(D(9), "INITIAL_STOP_LOSS"),
                                         (D("9.5"), "TRAILING_STOP_LOSS")])
def test_stops_work_with_missing_atr(close, reason):
    result = run(replace(STATE, active_stop=D("9.5")), close=close, atr=None)
    assert result.target_exposure == 0 and result.reason == reason


def test_missing_atr_blocks_add_and_does_not_mutate_trail():
    result = run(atr=None)
    assert not result.data_available and result.target_exposure == STATE.target_exposure
    assert result.highest_close == STATE.highest_close


def test_first_observed_close_is_not_the_intraday_fill_price():
    result = run(replace(STATE, initial_stop=D(8), active_stop=D(8), highest_close=None),
                 close=D("9.5"), atr=D("0.2"))
    assert result.highest_close == D("9.5") and result.active_stop == D("8.9")


def test_exit_target_is_sticky_but_missing_price_cannot_authorize_order():
    result = run(replace(STATE, target_exposure=D(0)), close=None)
    assert result.target_exposure == 0 and not result.data_available


def test_contracted_atr_exit_takes_precedence_over_add():
    result = run(replace(STATE, highest_close=D(13)), atr=D("0.1"))
    assert result.target_exposure == 0 and result.reason == "TRAILING_STOP_LOSS"


@pytest.mark.parametrize("state", [replace(STATE, exit_pending=True),
                                   replace(STATE, target_exposure=D(0))])
def test_pending_exit_cannot_reopen_even_with_missing_data(state):
    assert run(state, close=None).target_exposure == 0


def test_macd_exits_at_ma20_or_tenth_valid_session_without_adds():
    state = replace(STATE, target_exposure=D(1))
    assert run(state, MACD_MEAN_REVERSION, ma20=D(12)).reason == "HOLD"
    assert run(state, MACD_MEAN_REVERSION, ma20=D(11)).reason == "CLOSE_GE_MA20"
    assert run(state, MACD_MEAN_REVERSION, ma20=None,
               valid_sessions_after_fill=10).reason == "HOLDING_TIMEOUT"
    assert not run(state, MACD_MEAN_REVERSION, ma20=None,
                   valid_sessions_after_fill=9).data_available


def test_unknown_policy_or_invalid_state_is_rejected():
    with pytest.raises(ValueError):
        run(policy=replace(TREND_3ATR, trailing_atr_multiple=D(2)))
    with pytest.raises(ValueError):
        run(replace(STATE, fill_price=D("NaN")))
    with pytest.raises(ValueError):
        run(valid_sessions_after_fill=True)


def test_lifecycle_adapter_preserves_actual_shares_when_unavailable():
    from datetime import date
    from backend.modules.quant_strategy.application.position_lifecycle_manager import (
        LifecycleStateInput, evaluate_lifecycle_day,
    )
    state = LifecycleStateInput("ma_trend_cross_v1", D(10), D(9), D(1000))
    result = evaluate_lifecycle_day(
        state, trade_date=date(2026, 9, 24), fact={"close": 11},
        actual_shares=D(300), observation_no=1, management_policy=TREND_3ATR,
    )
    assert result.target_shares == 300 and not result.data_available
    result = evaluate_lifecycle_day(
        state, trade_date=date(2026, 9, 24),
        fact={"close": 11, "atr": .5, "new_risk_allowed": False},
        actual_shares=D(300), observation_no=1, management_policy=TREND_3ATR,
    )
    assert result.target_shares == 300
    suspended = evaluate_lifecycle_day(
        state, trade_date=date(2026, 9, 24),
        fact={"close": 11, "atr": .5, "is_suspended": True},
        actual_shares=D(300), observation_no=1, management_policy=TREND_3ATR,
    )
    assert suspended.target_shares == 300 and not suspended.data_available


@pytest.mark.parametrize("capacity", [D(0), D(-1000), D("NaN"), D("Infinity")])
def test_adapter_capacity_and_pre_cross_expectation(capacity):
    from datetime import date
    from backend.modules.quant_strategy.application.position_lifecycle_manager import (
        LifecycleStateInput, ExpectationStateInput, evaluate_lifecycle_day,
    )
    state = LifecycleStateInput("ma5_pre_cross_v1", D(10), D(9), D(1000))
    args = dict(trade_date=date(2026, 9, 24),
                fact={"close": 11, "atr": .5, "ma5": 10, "ma20": 11},
                actual_shares=D(500), observation_no=3, management_policy=TREND_3ATR,
                expectation=ExpectationStateInput(date(2026, 9, 21)))
    result = evaluate_lifecycle_day(state, **args)
    assert result.target_shares == 0 and result.expectation_status == "EXPIRED"
    assert result.expectation_observed_days == 3
    result = evaluate_lifecycle_day(replace(state, risk_capacity_shares=capacity), **args)
    assert result.target_shares == 500 and not result.data_available

    fulfilled = evaluate_lifecycle_day(state, **{**args, "fact": {**args["fact"], "ma5": 12}})
    assert fulfilled.expectation_status == "FULFILLED"
    missing = evaluate_lifecycle_day(state, **{**args, "fact": {**args["fact"], "ma5": None}})
    assert missing.expectation_observed_days == 0
    assert missing.target_shares == 500
    stopped = evaluate_lifecycle_day(state, **{**args, "fact": {**args["fact"], "close": 9}})
    assert stopped.reason_code == "INITIAL_STOP_LOSS"
    no_close = evaluate_lifecycle_day(state, **{**args, "fact": {**args["fact"], "close": None}})
    assert not no_close.data_available
    assert no_close.target_shares == 500
    assert no_close.expectation_status == "PENDING" and no_close.expectation_observed_days == 0
