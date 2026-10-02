# test-catalog-begin
# {
#   "purpose": "量化研究 / trial_executor",
#   "keywords": [
#     "量化研究",
#     "现金",
#     "每日",
#     "重复请求",
#     "执行",
#     "市场分析",
#     "投资组合",
#     "选择范围",
#     "个股分析",
#     "trial_executor",
#     "cash",
#     "daily",
#     "duplicate",
#     "execution",
#     "market",
#     "portfolio",
#     "selection",
#     "stock"
#   ],
#   "covers": [
#     "backend/modules/quant_research/application/trial_executor.py",
#     "backend/modules/quant_research/domain/trial_registry.py",
#     "backend/modules/quant_strategy/domain/etf_dual_momentum.py",
#     "backend/modules/quant_strategy/domain/stock_inputs.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal as D
from uuid import UUID
import json

import pytest

from backend.modules.quant_research.application.trial_executor import (
    PortfolioTrialInput, PortfolioTrialResult, prepare_trial, execute_single_trial, execute_portfolio_trial,
)
from backend.modules.quant_research.domain.trial_registry import preregistered_trials
from backend.modules.quant_strategy.domain.etf_dual_momentum import FundCandidate
from backend.modules.quant_strategy.domain.stock_inputs import StockCandidate

DAY = date(2026, 9, 24)
TRIALS = preregistered_trials()


def bars(prices):
    return tuple((DAY - timedelta(days=len(prices) - 1 - i), D(str(price))) for i, price in enumerate(prices))


def inputs():
    prices = bars([100] * 225 + [150] * 24 + [120])
    funds = tuple(FundCandidate(code, category, category, DAY, 300, D(30000000),
                                bars([100 + i / 10 for i in range(250)]), D("0.5"))
                  for code, category in (("510300.SH", "EQUITY_300"), ("511010.SH", "BOND_MEDIUM"),
                                         ("518880.SH", "GOLD")))
    return PortfolioTrialInput(
        DAY, DAY + timedelta(days=5), DAY, UUID(int=1),
        is_week_end=True, is_month_end=True,
        stocks=(StockCandidate("000001.SZ", prices, 300, D(30000000), False, False),),
        funds=funds, benchmark_bars=bars([100] * 249 + [101]),
        monthly_representatives=frozenset(item.ts_code for item in funds),
        defensive_symbols=tuple((item.category, item.ts_code) for item in funds),
    )


def context():
    values = lambda v: [v] * 250
    return {"meta": {"symbol": "000001.SZ", "bars_count": 250,
                     "arc_neckline_40": 11, "volume_median_20": 100,
                     "boll_low_quartile_10_120": True, "benchmark_above_ma120": True},
            "ohlcv": {"trade_date": [day.isoformat() for day, _ in bars([10] * 250)],
                      "open": values(10), "high": values(11), "low": values(9),
                      "close": values(10), "volume": values(100), "amount": values(1000)},
            "indicators": {name: values(value) for name, value in {
                "ma_qfq_5": 10, "ma_qfq_20": 10, "ma_qfq_60": 9, "boll_mid_qfq": 10,
                "boll_upper_qfq": 11, "boll_lower_qfq": 9, "macd_dif_qfq": -.1,
                "macd_dea_qfq": -.05, "macd_qfq": .1, "rsi_qfq_6": 55}.items()},
            "position": {"shares": 0, "average_cost": None, "market_value": 0}}


@pytest.mark.parametrize("spec", TRIALS, ids=lambda spec: spec.trial_id)
def test_all_76_registered_candidates_have_executable_entrypoints(spec):
    prepared = prepare_trial(spec.trial_id)
    if prepared.source_code is not None:
        result = execute_single_trial(prepared, context(), {"atr_qfq": .5}, timeout=2)
        assert result.signal.ok, result.signal
    else:
        result = execute_portfolio_trial(prepared, inputs())
        assert result.intent is not None
        assert result.disposition == "TARGET"
        assert result.intent.family_id == spec.family
        assert result.intent.policy_id == spec.management_policy
        assert result.intent.trial_id == spec.trial_id
        assert result.intent.definition_hash == prepared.definition_hash
    assert result.trial_id == spec.trial_id and result.definition_hash == prepared.definition_hash


def test_definition_hash_distinguishes_all_cells_and_rejects_tampering():
    prepared = [prepare_trial(item.trial_id) for item in TRIALS]
    assert len({item.definition_hash for item in prepared}) == 76
    with pytest.raises(ValueError):
        prepare_trial("unregistered")
    with pytest.raises(ValueError):
        execute_single_trial(replace(prepared[0], source_code="def strategy(c): return {}"), context(), {})
    short = next(item for item in prepared if item.spec.family == "stock_short_reversion")
    assert json.loads(short.management_config_json)["parameters"]["exit_days"] in (3, 5)


def test_weekly_and_monthly_dual_momentum_are_not_the_same_execution():
    weekly = prepare_trial("etf_dual_momentum:60:1:WEEKLY")
    monthly = prepare_trial("etf_dual_momentum:60:1:MONTHLY")
    day = replace(inputs(), is_month_end=False)
    assert execute_portfolio_trial(weekly, day).intent is not None
    monthly_result = execute_portfolio_trial(monthly, day)
    weekly_result = execute_portfolio_trial(weekly, replace(day, is_week_end=False))
    assert monthly_result.intent is None and monthly_result.disposition == "NOT_SCHEDULED"
    assert weekly_result.intent is None and weekly_result.disposition == "NOT_SCHEDULED"


def test_missing_membership_is_not_an_all_cash_target():
    prepared = prepare_trial("etf_dual_momentum:60:1:WEEKLY")
    missing = execute_portfolio_trial(prepared, replace(inputs(), monthly_representatives=None))
    empty = execute_portfolio_trial(prepared, replace(inputs(), monthly_representatives=frozenset()))
    assert missing.intent is None and missing.disposition == "NO_TARGET_UNCLASSIFIED"
    assert empty.intent.cash_only and empty.disposition == "TARGET"


def test_stock_no_selection_is_unclassified_and_not_a_cash_target():
    medium = prepare_trial("stock_medium_momentum:60:10:60")
    short = prepare_trial("stock_short_reversion:2:1.5:3")
    no_stocks = replace(inputs(), stocks=())
    for prepared in (medium, short):
        result = execute_portfolio_trial(prepared, no_stocks)
        assert result.intent is None and result.disposition == "NO_TARGET_UNCLASSIFIED"
    off_day = execute_portfolio_trial(medium, replace(no_stocks, is_month_end=False))
    assert off_day.intent is None and off_day.disposition == "NOT_SCHEDULED"
    with pytest.raises(ValueError, match="disposition and intent disagree"):
        PortfolioTrialResult(medium.spec.trial_id, medium.definition_hash, None, "TARGET")
    valid = execute_portfolio_trial(medium, inputs())
    assert valid.intent is not None
    with pytest.raises(ValueError, match="identity mismatch"):
        PortfolioTrialResult(valid.trial_id, valid.definition_hash,
                             replace(valid.intent, trial_id="different"), "TARGET")
    target = execute_portfolio_trial(medium, inputs()).intent
    with pytest.raises(ValueError, match="disposition and intent disagree"):
        PortfolioTrialResult(medium.spec.trial_id, medium.definition_hash, target, "NOT_SCHEDULED")
    with pytest.raises(ValueError, match="invalid portfolio trial disposition"):
        PortfolioTrialResult(medium.spec.trial_id, medium.definition_hash, None, "UNKNOWN")


def test_defensive_missing_membership_and_daily_prior_weights_are_distinct():
    prepared = prepare_trial("etf_defensive_allocation:20:0.04:60")
    missing = execute_portfolio_trial(prepared, replace(inputs(), defensive_symbols=None))
    assert missing.intent is None and missing.disposition == "NO_TARGET_UNCLASSIFIED"
    with pytest.raises(ValueError, match="previous target weights"):
        execute_portfolio_trial(prepared, replace(inputs(), is_week_end=False,
                                                  previous_weights=None))


def test_defensive_daily_adapter_only_reduces_existing_weights():
    prepared = prepare_trial("etf_defensive_allocation:20:0.04:60")
    day = replace(inputs(), is_week_end=False, previous_weights=(("510300.SH", D("0.1")),))
    target = execute_portfolio_trial(prepared, day).intent
    assert [(leg.ts_code, leg.family_weight) for leg in target.legs] == [("510300.SH", D("0.1"))]


def test_invalid_dates_and_missing_variant_host_feature_fail_closed():
    with pytest.raises(ValueError):
        replace(inputs(), evaluation_as_of=DAY + timedelta(days=1))
    prepared = prepare_trial("ma_trend_cross_v1:VARIANT:FAMILY_POLICY")
    data = context()
    del data["meta"]["benchmark_above_ma120"]
    result = execute_single_trial(prepared, data, {"atr_qfq": .5})
    assert not result.signal.ok and result.signal.error_code == "INDICATOR_UNAVAILABLE"


def test_management_comparison_preserves_entry_and_changes_target_contract():
    legacy = prepare_trial("ma_trend_cross_v1:BASELINE:CORRECTED_LEGACY")
    trend = prepare_trial("ma_trend_cross_v1:BASELINE:FAMILY_POLICY")
    data = context()
    data["ohlcv"]["close"][-1] = 10.3
    data["indicators"]["ma_qfq_5"][-2:] = [10.0, 10.2]
    data["indicators"]["ma_qfq_20"][-2:] = [10.05, 10.1]
    data["indicators"]["ma_qfq_60"][-1] = 9.8
    old = execute_single_trial(legacy, data, {"atr_qfq": .5}, timeout=2).signal.output
    new = execute_single_trial(trend, data, {"atr_qfq": .5}, timeout=2).signal.output
    assert old["action"] == new["action"] == "BUY"
    assert old["entry_price"] == new["entry_price"] and old["stop_loss"] == new["stop_loss"]
    assert old["take_profit"] is not None and new["take_profit"] is None


def test_portfolio_boundary_rejects_mutable_and_duplicate_candidates():
    with pytest.raises(TypeError):
        replace(inputs(), funds=list(inputs().funds))
    with pytest.raises(ValueError):
        replace(inputs(), funds=(inputs().funds[0], inputs().funds[0]))


def test_previous_session_facts_drive_next_day_targets_without_future_data():
    def previous(bars):
        return tuple((day - timedelta(days=1), price) for day, price in bars)

    original = inputs()
    dated = replace(
        original, evaluation_as_of=DAY - timedelta(days=1),
        stocks=tuple(replace(stock, adjusted_bars=previous(stock.adjusted_bars),
                             cooldown_until=DAY - timedelta(days=1)) for stock in original.stocks),
        funds=tuple(replace(fund, adjusted_bars=previous(fund.adjusted_bars),
                            trade_date=DAY - timedelta(days=1)) for fund in original.funds),
        benchmark_bars=previous(original.benchmark_bars),
    )
    for family in ("stock_medium_momentum", "stock_short_reversion",
                   "etf_dual_momentum", "etf_defensive_allocation"):
        spec = next(item for item in TRIALS if item.family == family)
        result = execute_portfolio_trial(prepare_trial(spec.trial_id), dated)
        assert result.intent is not None, family
        assert result.intent.evaluation_as_of == DAY - timedelta(days=1)
        assert result.intent.decision_date == DAY


def test_next_day_target_rejects_any_future_market_fact():
    prepared = prepare_trial("stock_medium_momentum:60:10:60")
    original = inputs()
    previous = DAY - timedelta(days=1)
    shifted_stocks = tuple(replace(stock, adjusted_bars=tuple(
        (day - timedelta(days=1), price) for day, price in stock.adjusted_bars))
        for stock in original.stocks)
    shifted_funds = tuple(replace(fund, trade_date=previous, adjusted_bars=tuple(
        (day - timedelta(days=1), price) for day, price in fund.adjusted_bars))
        for fund in original.funds)
    shifted_benchmark = tuple((day - timedelta(days=1), price)
                              for day, price in original.benchmark_bars)
    with pytest.raises(ValueError, match="future market facts"):
        execute_portfolio_trial(prepared, replace(original, evaluation_as_of=previous,
            stocks=shifted_stocks, funds=shifted_funds))
    with pytest.raises(ValueError, match="future market facts"):
        execute_portfolio_trial(prepared, replace(original, evaluation_as_of=previous,
            benchmark_bars=shifted_benchmark, funds=shifted_funds))
    with pytest.raises(ValueError, match="future market facts"):
        execute_portfolio_trial(prepared, replace(original, evaluation_as_of=previous,
            benchmark_bars=shifted_benchmark, stocks=shifted_stocks))
    interior = list(shifted_funds[0].adjusted_bars)
    interior[0] = (DAY + timedelta(days=1), interior[0][1])
    with pytest.raises(ValueError, match="future market facts"):
        execute_portfolio_trial(prepared, replace(original, evaluation_as_of=previous,
            benchmark_bars=shifted_benchmark, stocks=shifted_stocks,
            funds=(replace(shifted_funds[0], adjusted_bars=tuple(interior)), *shifted_funds[1:])))


def test_entry_adapter_cannot_leak_legacy_sells_into_frozen_management():
    trial = prepare_trial("macd_rsi_reversal_v1:BASELINE:FAMILY_POLICY")
    with pytest.raises(ValueError, match="lifecycle replay"):
        execute_single_trial(trial, context(), {}, has_position=True)
    data = context()
    data["position"]["shares"] = 100
    with pytest.raises(ValueError, match="lifecycle replay"):
        execute_single_trial(trial, data, {})
