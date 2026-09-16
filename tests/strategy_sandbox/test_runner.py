"""沙箱执行器单测（plan 4.1.3）：超时/崩溃/超长输出/非法数字/污染隔离/协议校验。"""

from __future__ import annotations

import pytest

from AI.strategy_sandbox.protocol import build_context, validate_strategy_output
from AI.strategy_sandbox.runner import run_strategy

CTX = build_context(
    symbol="600519.SH",
    effective_trade_date="2026-09-15",
    ohlcv={
        "trade_date": ["2026-09-11", "2026-09-12", "2026-09-15"],
        "open": [1400.0, 1410.0, 1420.0],
        "high": [1420.0, 1430.0, 1440.0],
        "low": [1390.0, 1400.0, 1410.0],
        "close": [1405.0, 1415.0, 1430.0],
        "volume": [1000.0, 1100.0, 1200.0],
        "amount": [1405000.0, 1556500.0, 1716000.0],
    },
    indicators={
        "ma_bfq_5": [None, None, 1416.7],
        "ma_bfq_20": [None, None, 1400.0],
        "rsi_bfq_6": [None, None, 55.0],
    },
    position={"shares": 0, "average_cost": None, "market_value": 0},
)

LEGAL = '''
def strategy(context):
    close = context["ohlcv"]["close"]
    price = close[-1]
    ma5 = context["indicators"]["ma_bfq_5"][-1]
    if price > ma5:
        return {"action": "BUY", "score": 90, "entry_price": price,
                "stop_loss": round(price * 0.965, 2), "take_profit": round(price * 1.10, 2),
                "sell_ratio": None, "reason": "MA5 上穿"}
    return {"action": "HOLD", "score": 50, "entry_price": None,
            "stop_loss": None, "take_profit": None,
            "sell_ratio": None, "reason": "不满足"}
'''


def test_legal_strategy_returns_output():
    result = run_strategy(LEGAL, CTX)
    assert result.ok
    assert result.output["action"] in {"BUY", "HOLD"}


def test_timeout_killed():
    src = "def strategy(context):\n    while True:\n        pass\n"
    result = run_strategy(src, CTX, timeout=0.3)
    assert not result.ok
    assert result.error_code == "EXECUTION_TIMEOUT"


def test_crash_fails_closed():
    src = 'def strategy(context):\n    raise RuntimeError("boom")\n'
    result = run_strategy(src, CTX)
    assert not result.ok
    assert result.error_code == "INVALID_OUTPUT"


def test_import_fails_in_child():
    # AST 上游已拒，但纵深防御：空 builtins 下 import 也是 NameError
    src = "def strategy(context):\n    import os\n    return None\n"
    result = run_strategy(src, CTX)
    assert not result.ok


def test_print_is_unavailable():
    src = 'def strategy(context):\n    print("x")\n    return None\n'
    result = run_strategy(src, CTX)
    assert not result.ok


def test_oversized_output_rejected():
    src = (
        "def strategy(context):\n"
        '    return {"action": "HOLD", "score": 50, "entry_price": None, "stop_loss": None,'
        ' "take_profit": None, "sell_ratio": None, "reason": "y" * 6000}\n'
    )
    result = run_strategy(src, CTX)
    assert not result.ok
    assert result.error_code == "INVALID_OUTPUT"


def test_nan_output_rejected():
    src = (
        "def strategy(context):\n"
        '    x = context["ohlcv"]["close"][-1]\n'
        '    return {"action": "BUY", "score": 90, "entry_price": x / 0 - x / 0,'
        ' "stop_loss": 1.0, "take_profit": 2.0, "sell_ratio": None, "reason": "nan"}\n'
    )
    result = run_strategy(src, CTX)
    assert not result.ok
    assert result.error_code == "INVALID_OUTPUT"


def test_no_pollution_between_runs():
    first = run_strategy(LEGAL, CTX)
    second = run_strategy(LEGAL, CTX)
    assert first.ok and second.ok
    assert first.output == second.output


def test_buy_price_order_enforced():
    src = (
        "def strategy(context):\n"
        '    p = context["ohlcv"]["close"][-1]\n'
        '    return {"action": "BUY", "score": 90, "entry_price": p,'
        ' "stop_loss": p + 10, "take_profit": p + 20, "sell_ratio": None, "reason": "坏价"}\n'
    )
    result = run_strategy(src, CTX)
    assert not result.ok
    assert "stop_loss" in (result.error_message or "")


# ---- 协议纯函数校验矩阵 ----

def _out(**overrides):
    base = {
        "action": "BUY", "score": 90, "entry_price": 10.0, "stop_loss": 9.0,
        "take_profit": 13.0, "sell_ratio": None, "reason": "r",
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "output,has_position",
    [
        (_out(action="SELL_ALL"), False),  # 无持仓 SELL_ALL
        (_out(action="SELL_PARTIAL", entry_price=None, stop_loss=None, take_profit=None, sell_ratio=0.5), False),
        (_out(score=101), True),
        (_out(score="90"), True),
        (_out(reason="\x01控制字符"), True),
        (_out(reason="x" * 241), True),
        (_out(sell_ratio=0.5), True),  # BUY 带 ratio
        (_out(stop_loss=10.0, entry_price=9.0), True),  # stop > entry
        (_out(take_profit=None), True),  # BUY 缺价
        (_out(action="HOLD", entry_price=None, stop_loss=None, take_profit=None, sell_ratio=0.5), True),
        ({"action": "BUY"}, True),  # 缺键
    ],
)
def test_output_validation_matrix_rejects(output, has_position):
    assert not validate_strategy_output(output, has_position=has_position).ok


def test_output_validation_accepts():
    assert validate_strategy_output(_out(), has_position=True).ok
    assert validate_strategy_output(
        _out(action="HOLD", entry_price=None, stop_loss=None, take_profit=None, sell_ratio=None), has_position=False
    ).ok
    assert validate_strategy_output(
        _out(action="SELL_ALL", entry_price=None, stop_loss=None, take_profit=None, sell_ratio=None), has_position=True
    ).ok
    assert validate_strategy_output(
        _out(action="SELL_PARTIAL", entry_price=None, stop_loss=None, take_profit=None, sell_ratio=0.5), has_position=True
    ).ok
