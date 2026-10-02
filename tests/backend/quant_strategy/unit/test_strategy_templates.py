# test-catalog-begin
# {
#   "purpose": "量化策略 / strategy_templates",
#   "keywords": [
#     "量化策略",
#     "指数",
#     "持仓生命周期",
#     "止损",
#     "模板",
#     "strategy_templates",
#     "index",
#     "lifecycle",
#     "stop",
#     "templates"
#   ],
#   "covers": [
#     "AI/strategy_sandbox/protocol.py",
#     "AI/strategy_sandbox/runner.py",
#     "AI/strategy_sandbox/validator.py",
#     "backend/modules/analysis/infrastructure/quant_execution_market_data.py",
#     "backend/modules/quant_strategy/domain/templates.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from __future__ import annotations

import pytest

from AI.strategy_sandbox.protocol import build_context
from AI.strategy_sandbox.runner import run_strategy
from AI.strategy_sandbox.validator import validate_strategy_source
from backend.modules.analysis.infrastructure.quant_execution_market_data import (
    benchmark_above_ma120,
)
from backend.modules.quant_strategy.domain.templates import (
    TEMPLATES,
    TemplateValidationError,
    freeze_template_contract,
    required_host_scalars,
    validate_frozen_template_context,
    validate_template_context,
)


def _context(size=60):
    def values(value):
        return [value for _ in range(size)]

    return {
        "meta": {"symbol": "000001.SZ", "bars_count": size},
        "ohlcv": {
            "trade_date": [f"d{i}" for i in range(size)],
            "open": values(10.0), "high": values(10.5), "low": values(9.5),
            "close": values(10.0), "volume": values(100.0), "amount": values(1000.0),
        },
        "indicators": {
            "ma_qfq_5": values(10.0), "ma_qfq_20": values(10.0), "ma_qfq_60": values(9.0),
            "boll_mid_qfq": values(10.0), "boll_upper_qfq": values(11.0),
            "boll_lower_qfq": values(9.0), "macd_dif_qfq": values(-0.1),
            "macd_dea_qfq": values(-0.05), "macd_qfq": values(0.1),
            "rsi_qfq_6": values(55.0),
        },
        "position": {"shares": 0, "average_cost": None, "market_value": 0},
    }


@pytest.mark.parametrize("template_id", list(TEMPLATES))
def test_default_template_passes_real_validator_and_runner(template_id):
    definition = TEMPLATES[template_id]
    source, params = definition.render()
    assert validate_strategy_source(source) == []
    assert set(params) == set(definition.parameters)
    result = run_strategy(source, _context(), timeout=2)
    assert result.ok, (template_id, result.error_code, result.error_message)
    assert set(result.output) == {
        "action", "score", "entry_price", "stop_loss", "take_profit", "sell_ratio", "reason",
    }


def test_first_wave_buy_formulas_run_in_real_sandbox():
    ma = _context()
    ma["ohlcv"]["close"][-1] = 10.3
    ma["indicators"]["ma_qfq_5"][-2:] = [10.0, 10.2]
    ma["indicators"]["ma_qfq_20"][-2:] = [10.05, 10.1]
    ma["indicators"]["ma_qfq_60"][-1] = 9.8
    assert run_strategy(TEMPLATES["ma_trend_cross_v1"].render()[0], ma, timeout=2).output["action"] == "BUY"

    pullback = _context()
    pullback["ohlcv"]["close"][-2:] = [9.0, 9.6]
    pullback["indicators"]["boll_lower_qfq"][-2:] = [9.1, 9.2]
    pullback["indicators"]["ma_qfq_20"][-1] = 9.4
    pullback["indicators"]["rsi_qfq_6"][-2:] = [38.0, 44.0]
    pullback["indicators"]["macd_qfq"][-2:] = [-0.1, 0.0]
    assert run_strategy(TEMPLATES["trend_pullback_v1"].render()[0], pullback, timeout=2).output["action"] == "BUY"

    volume = _context()
    volume["ohlcv"]["close"][-2:] = [10.0, 10.2]
    volume["ohlcv"]["volume"][-1] = 220.0
    volume["indicators"]["ma_qfq_20"][-1] = 9.8
    assert run_strategy(TEMPLATES["volume_surge_confirm_v1"].render()[0], volume, timeout=2).output["action"] == "BUY"


def test_macd_bonus_requires_dif_improvement_not_merely_golden_cross():
    source = TEMPLATES["macd_rsi_reversal_v1"].render()[0]
    falling_dif = _context()
    falling_dif["indicators"]["macd_dif_qfq"][-2:] = [-0.1, -0.15]
    falling_dif["indicators"]["macd_dea_qfq"][-2:] = [-0.08, -0.2]
    falling_dif["indicators"]["macd_qfq"][-2:] = [-0.04, 0.1]
    falling_dif["indicators"]["rsi_qfq_6"][-2:] = [30., 40.]
    falling_dif["indicators"]["ma_qfq_20"][-1] = 9.5
    base = run_strategy(source, falling_dif, timeout=2)
    assert base.ok and base.output["action"] == "BUY"

    rising_dif = _context()
    rising_dif["indicators"]["macd_dif_qfq"][-2:] = [-0.2, -0.15]
    rising_dif["indicators"]["macd_dea_qfq"][-2:] = [-0.18, -0.2]
    rising_dif["indicators"]["macd_qfq"][-2:] = [-0.04, 0.1]
    rising_dif["indicators"]["rsi_qfq_6"][-2:] = [30., 40.]
    rising_dif["indicators"]["ma_qfq_20"][-1] = 9.5
    improved = run_strategy(source, rising_dif, timeout=2)
    assert improved.ok and improved.output["action"] == "BUY"
    assert improved.output["score"] - base.output["score"] == 8


def test_macd_five_day_oversold_variant_uses_prior_days_only():
    context = _context()
    context["indicators"]["macd_dif_qfq"][-2:] = [-0.2, -0.1]
    context["indicators"]["macd_dea_qfq"][-2:] = [-0.15, -0.15]
    context["indicators"]["macd_qfq"][-2:] = [-0.1, 0.1]
    context["indicators"]["rsi_qfq_6"][-6:] = [55., 30., 40., 40., 40., 42.]
    context["indicators"]["ma_qfq_20"][-1] = 9.5
    template = TEMPLATES["macd_rsi_reversal_v1"]
    baseline = run_strategy(template.render()[0], context, timeout=2)
    variant = run_strategy(template.render({"five_day_oversold": 1})[0], context, timeout=2)
    assert baseline.ok and baseline.output["action"] == "HOLD"
    assert variant.ok and variant.output["action"] == "BUY"


def test_arc_entry_variant_and_held_exit_use_frozen_lifecycle_neckline():
    context = _context()
    close = context["ohlcv"]["close"]
    close[-41], close[-31], close[-21], close[-11], close[-5], close[-1] = (
        10., 9.8, 9., 10., 11., 10.5,
    )
    context["ohlcv"]["volume"][-1] = 200.
    context["meta"]["arc_neckline_40"] = 11.
    context["indicators"]["ma_qfq_20"][-2:] = [9.5, 10.]
    baseline = TEMPLATES["arc_bottom_75a_v1"].render()[0]
    rolling = TEMPLATES["arc_bottom_75a_v1"].render({"rolling_neckline": 1})[0]
    assert validate_strategy_source(rolling) == []
    assert run_strategy(baseline, context, timeout=2).output["action"] == "BUY"
    assert run_strategy(rolling, context, timeout=2).output["action"] == "HOLD"
    context["position"]["shares"] = 100
    # The trusted lifecycle uses the stored entry neckline (10), so a new
    # rolling neckline of 11 must not force an unrelated exit at 10.5.
    assert run_strategy(rolling, context, timeout=2).output["action"] == "HOLD"


def test_arc_rolling_neckline_uses_only_prior_40_closes():
    seed = _context()
    seed["ohlcv"]["close"][-2] = 11.
    seed["ohlcv"]["close"][-1] = 99.
    context = build_context(
        symbol="000001.SZ", effective_trade_date="2020-01-02",
        ohlcv=seed["ohlcv"], indicators=seed["indicators"],
        position=seed["position"],
    )
    assert context["meta"]["arc_neckline_40"] == 11.
    seed["ohlcv"]["close"][-2] = None
    unavailable = build_context(
        symbol="000001.SZ", effective_trade_date="2020-01-02",
        ohlcv=seed["ohlcv"], indicators=seed["indicators"],
        position=seed["position"],
    )
    assert unavailable["meta"]["arc_neckline_40"] is None


def test_volume_median_variant_excludes_today_and_differs_from_five_day_max():
    seed = _context()
    volume = seed["ohlcv"]["volume"]
    volume[-21:-6] = [50.] * 15
    volume[-6:-1] = [100.] * 5
    volume[-1] = 110.
    seed["ohlcv"]["close"][-2:] = [10., 10.2]
    seed["indicators"]["ma_qfq_20"][-1] = 9.8
    context = build_context(
        symbol="000001.SZ", effective_trade_date="2020-01-02",
        ohlcv=seed["ohlcv"], indicators=seed["indicators"],
        position=seed["position"],
    )
    assert context["meta"]["volume_median_20"] == 50.
    definition = TEMPLATES["volume_surge_confirm_v1"]
    baseline = run_strategy(definition.render()[0], context, timeout=2)
    variant = run_strategy(definition.render({"median_volume_baseline": 1})[0], context, timeout=2)
    assert baseline.ok and baseline.output["action"] == "HOLD"
    assert variant.ok and variant.output["action"] == "BUY"


def test_pullback_variant_freezes_prior_five_low_as_initial_stop():
    context = _context()
    context["ohlcv"]["close"][-2:] = [9.0, 9.6]
    context["ohlcv"]["low"][-6:-1] = [9.0, 9.1, 9.2, 9.3, 9.4]
    context["indicators"]["boll_lower_qfq"][-2:] = [9.1, 9.2]
    context["indicators"]["ma_qfq_20"][-1] = 9.4
    context["indicators"]["rsi_qfq_6"][-2:] = [38., 44.]
    context["indicators"]["macd_qfq"][-2:] = [-0.1, 0.0]
    definition = TEMPLATES["trend_pullback_v1"]
    baseline = run_strategy(definition.render()[0], context, timeout=2)
    variant = run_strategy(definition.render({"prior_five_low_stop": 1})[0], context, timeout=2)
    assert baseline.ok and baseline.output["action"] == "BUY"
    assert variant.ok and variant.output["action"] == "BUY"
    assert baseline.output["stop_loss"] == pytest.approx(9.12)
    assert variant.output["stop_loss"] == 9.0


def test_pre_cross_variant_waits_for_actual_ma_cross():
    context = _context()
    context["ohlcv"]["close"][-1] = 10.3
    context["indicators"]["ma_qfq_5"][-2:] = [9.9, 10.2]
    context["indicators"]["ma_qfq_20"][-2:] = [10., 10.1]
    definition = TEMPLATES["ma5_pre_cross_v1"]
    baseline = run_strategy(definition.render()[0], context, timeout=2)
    variant = run_strategy(definition.render({"confirmed_cross": 1})[0], context, timeout=2)
    assert baseline.ok and baseline.output["action"] == "HOLD"
    assert variant.ok and variant.output["action"] == "BUY"
    assert variant.output["reason"] == "MA5_CONFIRMED_CROSS"


def test_boll_bandwidth_variant_uses_prior_windows_and_rejects_wide_band():
    seed = _context(150)
    upper = seed["indicators"]["boll_upper_qfq"]
    lower = seed["indicators"]["boll_lower_qfq"]
    upper[-11:-1] = [11.5] * 10
    lower[-11:-1] = [8.5] * 10
    upper[-1], lower[-1] = 10.5, 9.5
    seed["ohlcv"]["close"][-2:] = [10., 11.]
    seed["ohlcv"]["volume"][-1] = 200.
    context = build_context(
        symbol="000001.SZ", effective_trade_date="2020-01-02",
        ohlcv=seed["ohlcv"], indicators=seed["indicators"],
        position=seed["position"],
    )
    assert context["meta"]["boll_low_quartile_10_120"] is False
    definition = TEMPLATES["boll_volume_breakout_v1"]
    baseline = run_strategy(definition.render()[0], context, timeout=2)
    variant = run_strategy(definition.render({"low_bandwidth_quartile": 1})[0], context, timeout=2)
    assert baseline.ok and baseline.output["action"] == "BUY"
    assert variant.ok and variant.output["action"] == "HOLD"
    upper[-11:-1] = [10.25] * 10
    lower[-11:-1] = [9.75] * 10
    narrow = build_context(
        symbol="000001.SZ", effective_trade_date="2020-01-02",
        ohlcv=seed["ohlcv"], indicators=seed["indicators"],
        position=seed["position"],
    )
    assert narrow["meta"]["boll_low_quartile_10_120"] is True


def test_ma_cross_benchmark_variant_fails_closed_on_missing_trend():
    context = _context()
    context["ohlcv"]["close"][-1] = 10.3
    context["indicators"]["ma_qfq_5"][-2:] = [10., 10.2]
    context["indicators"]["ma_qfq_20"][-2:] = [10.05, 10.1]
    context["indicators"]["ma_qfq_60"][-1] = 9.8
    definition = TEMPLATES["ma_trend_cross_v1"]
    source = definition.render({"benchmark_filter": 1})[0]
    context["meta"]["benchmark_above_ma120"] = None
    assert run_strategy(source, context, timeout=2).output["action"] == "HOLD"
    assert required_host_scalars("ma_trend_cross_v1", {"benchmark_filter": 1}) == (
        "benchmark_above_ma120",
    )
    context["meta"]["benchmark_above_ma120"] = True
    assert run_strategy(source, context, timeout=2).output["action"] == "BUY"
    context["meta"]["benchmark_above_ma120"] = False
    assert run_strategy(source, context, timeout=2).output["action"] == "HOLD"


def test_benchmark_trend_requires_current_day_and_full_120_sessions():
    from datetime import date, timedelta
    from types import SimpleNamespace

    today = date(2020, 6, 30)
    rows = [(today - timedelta(days=index), 101. if index == 0 else 100.)
            for index in range(120)]
    calendar = SimpleNamespace(schedule=lambda _market: SimpleNamespace(
        available=True,
        sessions=[SimpleNamespace(trade_date=row[0]) for row in reversed(rows)],
    ))

    class Conn:
        def __init__(self, values):
            self.values = values

        def execute(self, sql, params):
            assert "000300.SH" in sql and "LIMIT 120" in sql
            assert params == (today.isoformat(),)
            return self

        def fetchall(self):
            return self.values

    assert benchmark_above_ma120(Conn(rows), today, calendar=calendar) is True
    assert benchmark_above_ma120(Conn(rows[1:]), today, calendar=calendar) is None
    assert benchmark_above_ma120(
        Conn([(today - timedelta(days=1), 101.), *rows[1:]]), today, calendar=calendar,
    ) is None
    assert benchmark_above_ma120(Conn([(today, float("nan")), *rows[1:]]), today, calendar=calendar) is None
    assert benchmark_above_ma120(Conn([rows[0], *rows[2:], rows[-1]]), today, calendar=calendar) is None


def test_parameter_boundaries_and_cross_constraints():
    definition = TEMPLATES["trend_pullback_v1"]
    definition.render({"rsi_buy_low": 20, "rsi_buy_high": 70, "stop_pct": "0.03", "reward_multiple": "3"})
    with pytest.raises(TemplateValidationError):
        definition.render({"rsi_buy_low": 45, "rsi_buy_high": 45})
    with pytest.raises(TemplateValidationError):
        definition.render({"unknown": 1})
    with pytest.raises(TemplateValidationError):
        TEMPLATES["ma5_pre_cross_v1"].render({"confirmation_window_trading_days": 4})


@pytest.mark.parametrize("template_id", list(TEMPLATES))
def test_template_input_guard_uses_actual_minimum_window(template_id):
    definition = TEMPLATES[template_id]
    context = _context(definition.required_bars)
    assert validate_template_context(definition, context) is None
    assert validate_template_context(definition, _context(max(1, definition.required_bars - 1))) == "WARMUP_INCOMPLETE"


def test_validator_rejects_out_of_contract_index():
    source = '''def strategy(context):
    price = context["ohlcv"]["close"][0]
    return {"action": "HOLD", "score": 0, "entry_price": None,
            "stop_loss": None, "take_profit": None, "sell_ratio": None, "reason": "NO_SIGNAL"}
'''
    assert "BAD_INDEX" in {issue.code for issue in validate_strategy_source(source)}


def test_frozen_template_contract_is_independent_from_registry_object():
    contract = freeze_template_contract(TEMPLATES["ma_trend_cross_v1"])
    assert validate_frozen_template_context(contract, _context(2)) is None
    broken = {**contract, "required_bars": 3}
    assert validate_frozen_template_context(broken, _context(2)) == "WARMUP_INCOMPLETE"
    malformed = {**contract, "required_fields": [{"section": "indicators", "field": "removed", "indices": [-1]}]}
    assert validate_frozen_template_context(malformed, _context(2)) == "INDICATOR_UNAVAILABLE"
