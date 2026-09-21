from __future__ import annotations

import pytest

from AI.strategy_sandbox.runner import run_strategy
from AI.strategy_sandbox.validator import validate_strategy_source
from backend.modules.quant_strategy.domain.templates import (
    TEMPLATES,
    TemplateValidationError,
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
