# test-catalog-begin
# {
#   "purpose": "量化研究 / trial_holdings",
#   "keywords": [
#     "量化研究",
#     "每日",
#     "ETF",
#     "成交",
#     "来源观测",
#     "投资组合",
#     "风险",
#     "品种规则",
#     "停牌",
#     "trial_holdings",
#     "daily",
#     "etf",
#     "fill",
#     "observation",
#     "portfolio",
#     "risk",
#     "rule",
#     "suspension"
#   ],
#   "covers": [
#     "backend/modules/quant_research/application/trial_executor.py",
#     "backend/modules/quant_research/domain/trial_registry.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from backend.modules.quant_research.application.trial_executor import (
    prepare_trial, initialize_trial_holding, evaluate_trial_holding,
)
from backend.modules.quant_research.domain.trial_registry import preregistered_trials

DAY = date(2026, 9, 21)
BASIS = "adjusted-at-entry:2026-09-21"


def bars(prices, day=DAY):
    return tuple((day - timedelta(days=len(prices) - 1 - i), D(str(value))) for i, value in enumerate(prices))


def initial(trial_id):
    trial = prepare_trial(trial_id)
    return trial, initialize_trial_holding(trial, price_basis=BASIS, fill_date=DAY,
                                           fill_price=D(100), target_weight=D("0.1"),
                                           sigma20=D("0.01"), atr=D(2))


def evaluate(trial, state, prices, day=DAY, **kwargs):
    kwargs.setdefault("benchmark_bars", bars([90] * 199 + [100], day))
    return evaluate_trial_holding(trial, state, trade_date=day, price_basis=BASIS,
                                  bars=bars(prices, day), **kwargs)


@pytest.mark.parametrize("spec", [spec for spec in preregistered_trials() if spec.entry_variant == "GRID"],
                         ids=lambda spec: spec.trial_id)
def test_all_48_portfolio_candidates_initialize_and_protect_real_fill(spec):
    trial, state = initial(spec.trial_id)
    assert 0 < state.initial_stop < state.fill_price and state.valid_sessions == 0
    result = evaluate(trial, state, [state.initial_stop])
    assert result.can_execute and result.state.target_weight == 0
    assert result.reason == "INITIAL_STOP_LOSS"


def test_reversion_stop_formula_and_holding_timeout_ignore_fill_day_and_suspension():
    trial, state = initial("stock_short_reversion:2:1.5:3")
    assert state.initial_stop == D(100) * (1 - 2 * D("0.01") * D(3).sqrt())
    prices = [110] * 120 + [100]
    state = evaluate(trial, state, prices).state
    assert state.valid_sessions == 0
    state = evaluate(trial, state, prices, DAY + timedelta(days=1), suspended=True).state
    assert state.valid_sessions == 0
    for offset in (2, 3):
        result = evaluate(trial, state, prices, DAY + timedelta(days=offset))
        assert result.state.target_weight == D("0.1")
        state = result.state
    result = evaluate(trial, state, prices, DAY + timedelta(days=4))
    assert result.reason == "HOLDING_TIMEOUT" and result.cooldown_after_exit_fill == 5


def test_short_stop_clamps_sigma_and_rejects_invalid_risk_inputs():
    trial, _ = initial("stock_short_reversion:2:1.5:5")
    args = dict(price_basis=BASIS, fill_date=DAY, fill_price=D(100), target_weight=D("0.1"))
    assert initialize_trial_holding(trial, **args, sigma20=D(0)).initial_stop == 97
    assert initialize_trial_holding(trial, **args, sigma20=D(1)).initial_stop == 90
    with pytest.raises(ValueError):
        initialize_trial_holding(trial, **args, sigma20=None)


def test_missing_data_does_not_remove_known_stop_or_restart_exit():
    trial, state = initial("stock_medium_momentum:60:10:60")
    missing = evaluate_trial_holding(trial, state, trade_date=DAY, price_basis=BASIS, bars=())
    assert not missing.can_execute and missing.state.valid_sessions == 0
    stopped = evaluate(trial, missing.state, [89], DAY + timedelta(days=1))
    assert stopped.can_execute and stopped.state.target_weight == 0
    again = evaluate(trial, stopped.state, [120], DAY + timedelta(days=2))
    assert again.reason == "EXIT_PENDING" and again.state.target_weight == 0


def test_medium_peak_uses_first_close_not_intraday_fill_and_benchmark_failure_exits():
    trial, state = initial("stock_medium_momentum:60:10:60")
    result = evaluate(trial, state, [95])
    assert result.state.highest_close == 95
    state = evaluate(trial, result.state, [120], DAY + timedelta(days=1)).state
    assert state.active_stop == 108
    assert evaluate(trial, state, [107], DAY + timedelta(days=2)).state.target_weight == 0
    assert evaluate(trial, initial(trial.spec.trial_id)[1], [100],
                    benchmark_bars=bars([100] * 60)).reason == "BENCHMARK_TREND_LOST"


def test_dual_momentum_exits_daily_even_without_atr_and_never_loosens_stop():
    trial, state = initial("etf_dual_momentum:60:1:MONTHLY")
    result = evaluate(trial, state, [101] * 60 + [100])
    assert result.reason == "MOMENTUM_NONPOSITIVE" and result.can_execute
    rise = evaluate(trial, state, [100] * 60 + [120], atr=D(2))
    assert rise.state.active_stop == 114
    following = evaluate(trial, rise.state, [100] * 60 + [119], DAY + timedelta(days=1), atr=D(10))
    assert following.state.active_stop == 114


def test_defensive_daily_risk_can_only_reduce_and_full_reduction_is_sticky():
    trial, state = initial("etf_defensive_allocation:20:0.04:60")
    assert evaluate(trial, state, [100], defensive_target_weight=D("0.3")).state.target_weight == D("0.1")
    cut = evaluate(trial, state, [100], defensive_target_weight=D("0.05"))
    assert cut.can_execute and cut.state.target_weight == D("0.05")
    zero = evaluate(trial, cut.state, [100], DAY + timedelta(days=1), defensive_target_weight=D(0))
    assert zero.state.target_weight == 0
    assert evaluate(trial, zero.state, [120], DAY + timedelta(days=2),
                    defensive_target_weight=D("0.4")).state.target_weight == 0


def test_candidate_basis_and_observation_date_are_frozen():
    trial, state = initial("stock_short_reversion:2:1.5:3")
    with pytest.raises(ValueError):
        evaluate(prepare_trial("stock_short_reversion:2:1.5:5"), state, [100])
    with pytest.raises(ValueError):
        evaluate_trial_holding(trial, state, trade_date=DAY, price_basis="new-basis", bars=bars([100]))
    once = evaluate(trial, state, [100]).state
    with pytest.raises(ValueError):
        evaluate(trial, once, [100])


def test_registered_windows_change_the_daily_exit_rule():
    benchmark = bars([120] * 100 + [80] * 99 + [95])
    short, short_state = initial("stock_medium_momentum:60:10:60")
    long, long_state = initial("stock_medium_momentum:60:10:200")
    assert evaluate(short, short_state, [100], benchmark_bars=benchmark).reason == "HOLD"
    assert evaluate(long, long_state, [100], benchmark_bars=benchmark).reason == "BENCHMARK_TREND_LOST"
    prices = [120] * 120 + [90] * 60 + [100]
    short, short_state = initial("etf_dual_momentum:60:1:WEEKLY")
    long, long_state = initial("etf_dual_momentum:180:1:WEEKLY")
    assert evaluate(short, short_state, prices, atr=D(2), defensive_target_weight=D("0.1")).reason == "HOLD"
    assert evaluate(long, long_state, prices).reason == "MOMENTUM_NONPOSITIVE"
    prices = [130] * 60 + [80] * 60 + [100]
    short, short_state = initial("etf_defensive_allocation:20:0.04:60")
    long, long_state = initial("etf_defensive_allocation:20:0.04:120")
    assert evaluate(short, short_state, prices, atr=D(2), defensive_target_weight=D("0.1")).reason == "HOLD"
    assert evaluate(long, long_state, prices).reason == "ASSET_TREND_LOST"


def test_h3_and_h5_are_distinct_and_ma5_exit_does_not_wait_for_timeout():
    for horizon in (3, 5):
        trial, state = initial(f"stock_short_reversion:2:1.5:{horizon}")
        assert evaluate(trial, state, [95] * 4 + [100]).reason == "CLOSE_GE_MA5"
        for count in range(1, horizon + 1):
            result = evaluate(trial, state, [110] * 4 + [100], DAY + timedelta(days=count))
            assert (result.reason == "HOLDING_TIMEOUT") == (count == horizon)
            state = result.state


@pytest.mark.parametrize("missing", ["ma5", "benchmark"])
def test_incomplete_observations_never_expire_reversion_clock(missing):
    trial, state = initial("stock_short_reversion:2:1.5:3")
    for count in range(1, 5):
        kwargs = {"benchmark_bars": ()} if missing == "benchmark" else {}
        prices = [100] if missing == "ma5" else [110] * 4 + [100]
        result = evaluate(trial, state, prices, DAY + timedelta(days=count), **kwargs)
        assert result.state.valid_sessions == 0 and not result.can_execute
        assert result.state.highest_close == 100 and result.state.active_stop == state.active_stop
        state = result.state
    complete = evaluate(trial, state, [110] * 4 + [100], DAY + timedelta(days=5))
    assert complete.state.valid_sessions == 1 and complete.reason == "HOLD"
    stopped = evaluate(trial, complete.state, [90], DAY + timedelta(days=6), benchmark_bars=())
    assert stopped.can_execute and stopped.state.target_weight == 0


def test_missing_etf_management_evidence_is_explicit_without_losing_protection():
    trial, state = initial("etf_dual_momentum:60:1:WEEKLY")
    for prices, atr in (([90] * 60 + [100], None), ([100], D(2))):
        result = evaluate(trial, state, prices, atr=atr)
        assert result.reason == "MANAGEMENT_INPUTS_UNAVAILABLE"
        assert result.state.active_stop == state.active_stop and result.state.valid_sessions == 0
    trial, state = initial("etf_defensive_allocation:20:0.04:60")
    for prices, atr, weight in (([90] * 59 + [100], D(2), None),
                                 ([100], D(2), D("0.1")),
                                 ([90] * 59 + [100], None, D("0.1"))):
        assert evaluate(trial, state, prices, atr=atr,
                        defensive_target_weight=weight).reason == "MANAGEMENT_INPUTS_UNAVAILABLE"


@pytest.mark.parametrize("trial_id", ["stock_medium_momentum:60:10:60", "etf_dual_momentum:60:1:WEEKLY"])
def test_observed_peak_survives_missing_other_inputs_and_protects_next_day(trial_id):
    trial, state = initial(trial_id)
    peak = evaluate(trial, state, [120], benchmark_bars=(), atr=None)
    assert peak.state.highest_close == 120 and peak.state.valid_sessions == 0
    result = evaluate(trial, peak.state, [105], DAY + timedelta(days=1), atr=D(2))
    assert result.can_execute and result.state.target_weight == 0
    assert result.state.active_stop >= 108
