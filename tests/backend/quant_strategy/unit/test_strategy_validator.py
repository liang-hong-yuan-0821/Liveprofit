# test-catalog-begin
# {
#   "purpose": "AST 校验器单测（plan 4.1.1/4.1.3）：白名单放行、其余拒绝、错误含稳定 code 与位置。",
#   "keywords": [
#     "量化策略",
#     "仓位管理",
#     "来源证据",
#     "strategy_validator",
#     "position",
#     "source"
#   ],
#   "covers": [
#     "AI/strategy_sandbox/validator.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""AST 校验器单测（plan 4.1.1/4.1.3）：白名单放行、其余拒绝、错误含稳定 code 与位置。"""

from __future__ import annotations

import pytest

from AI.strategy_sandbox.validator import validate_strategy_source

# 合法均线策略：数组整体读取 + 局部常量下标 + 四 action 分支 + 七键返回
LEGAL_MA = '''
def strategy(context):
    close = context["ohlcv"]["close"]
    ma5 = context["indicators"]["ma_bfq_5"][-1]
    ma20 = context["indicators"]["ma_bfq_20"][-1]
    rsi6 = context["indicators"]["rsi_bfq_6"][-1]
    price = close[-1]
    shares = context["position"]["shares"]

    if shares > 0 and price < ma20:
        return {"action": "SELL_ALL", "score": 0, "entry_price": None,
                "stop_loss": None, "take_profit": None,
                "sell_ratio": None, "reason": "跌破 MA20 止损线"}

    if shares == 0 and ma5 > ma20 and rsi6 < 35:
        return {"action": "BUY", "score": 90,
                "entry_price": price, "stop_loss": round(price * 0.965, 2),
                "take_profit": round(price * 1.10, 2),
                "sell_ratio": None, "reason": "MA5 上穿 MA20 且 RSI6 超卖"}

    return {"action": "HOLD", "score": 50, "entry_price": None,
            "stop_loss": None, "take_profit": None,
            "sell_ratio": None, "reason": "不满足条件"}
'''


def _codes(source: str) -> set[str]:
    return {i.code for i in validate_strategy_source(source)}


def test_legal_ma_strategy_passes():
    assert validate_strategy_source(LEGAL_MA) == []


def test_empty_source():
    codes = _codes("")
    assert "EMPTY_SOURCE" in codes


def test_syntax_error():
    codes = _codes("def strategy(context):\n    return {")
    assert "SYNTAX_ERROR" in codes


@pytest.mark.parametrize(
    "source,expected",
    [
        # 结构违规
        ("def strategy(context):\n    import os\n    return None", "FORBIDDEN_IMPORT"),
        ("def strategy(context):\n    while True:\n        pass\n    return None", "FORBIDDEN_LOOP"),
        ("def strategy(context):\n    for i in range(3):\n        pass\n    return None", "FORBIDDEN_LOOP"),
        ("def strategy(context):\n    try:\n        pass\n    except:\n        pass\n    return None", "FORBIDDEN_CONTROL"),
        ("def strategy(context):\n    raise ValueError()\n", "FORBIDDEN_CONTROL"),
        ("def strategy(context):\n    with open('x'):\n        pass\n    return None", "FORBIDDEN_CONTROL"),
        ("def strategy(context):\n    return lambda x: x", "FORBIDDEN_LAMBDA"),
        ("def strategy(context):\n    x = [i for i in range(3)]\n    return None", "FORBIDDEN_COMPREHENSION"),
        ("def strategy(context):\n    def inner():\n        pass\n    return None", "NESTED_DEFINITION"),
        ("def strategy(context):\n    assert True\n    return None", "FORBIDDEN_STATEMENT"),
        # 属性/调用
        ("def strategy(context):\n    return context.__class__", "FORBIDDEN_ATTRIBUTE"),
        ("def strategy(context):\n    x = len([1])\n    return None", "FORBIDDEN_CALL"),
        ("def strategy(context):\n    x = max([1], key=lambda v: v)\n    return None", "FORBIDDEN_CALL_KWARGS"),
        # 未知名/坏标识符
        ("def strategy(context):\n    x = undefined_name\n    return None", "UNKNOWN_NAME"),
        ("def strategy(context):\n    _x = 1\n    return None", "BAD_IDENTIFIER"),
        ("def strategy(context):\n    x__y = 1\n    return None", "BAD_IDENTIFIER"),
        # context 路径
        ('def strategy(context):\n    x = context["foo"]\n    return None', "BAD_SUBSCRIPT_PATH"),
        ('def strategy(context):\n    x = context["ohlcv"]["close"][-1:]\n    return None', "BAD_INDEX"),
        ("def strategy(context):\n    i = 1\n    x = context[\"ohlcv\"][\"close\"][i]\n    return None", "BAD_INDEX"),
        ('def strategy(context):\n    x = context["meta"]["symbol"][0]\n    return None', "BAD_SUBSCRIPT_PATH"),
        ('def strategy(context):\n    x = context["ohlcv"][-1]["close"]\n    return None', "BAD_SUBSCRIPT_PATH"),
        # 返回字典合同
        ('def strategy(context):\n    return {"action": "HOLD"}', "MISSING_RETURN_KEY"),
        (
            'def strategy(context):\n    return {"action": "HOLD", "score": 1, "entry_price": None, "stop_loss": None, "take_profit": None, "sell_ratio": None, "reason": "", "extra": 1}',
            "EXTRA_RETURN_KEY",
        ),
        (
            'def strategy(context):\n    key = "action"\n    return {key: "HOLD", "score": 1, "entry_price": None, "stop_loss": None, "take_profit": None, "sell_ratio": None, "reason": ""}',
            "DYNAMIC_DICT_KEY",
        ),
        (
            'def strategy(context):\n    return {"action": "HOLD", "action": "BUY", "score": 1, "entry_price": None, "stop_loss": None, "take_profit": None, "sell_ratio": None, "reason": ""}',
            "DUPLICATE_DICT_KEY",
        ),
        (
            'def strategy(context):\n    d = {"action": "HOLD"}\n    return {**d, "score": 1, "entry_price": None, "stop_loss": None, "take_profit": None, "sell_ratio": None, "reason": ""}',
            "STARRED_DICT",
        ),
        (
            'def strategy(context):\n    return {"action": ["HOLD"], "score": 1, "entry_price": None, "stop_loss": None, "take_profit": None, "sell_ratio": None, "reason": ""}',
            "NESTED_CONTAINER",
        ),
        ("def strategy(context):\n    return None", "NO_RETURN_DICT"),
        ("def strategy(context):\n    x = 1\n", "NO_RETURN_DICT"),
        # 函数签名
        ("def strategy():\n    return None", "BAD_PARAMETERS"),
        ("def strategy(context, extra):\n    return None", "BAD_PARAMETERS"),
        ("def strategy(context: dict):\n    return None", "BAD_PARAMETERS"),
        ("def other(context):\n    return None", "BAD_FUNCTION_NAME"),
        ("def strategy(context):\n    return None\ndef strategy2(context):\n    return None", "BAD_FUNCTION_COUNT"),
        ("x = 1\ndef strategy(context):\n    return None", "FORBIDDEN_TOP_LEVEL"),
    ],
)
def test_reject_matrix(source: str, expected: str):
    assert expected in _codes(source), f"期望 {expected}，实际 {sorted(_codes(source))}"


def test_issue_has_stable_position():
    issues = validate_strategy_source("def strategy(context):\n    import os\n")
    import_issues = [i for i in issues if i.code == "FORBIDDEN_IMPORT"]
    assert len(import_issues) == 1
    assert import_issues[0].line == 2
    assert import_issues[0].column >= 1


def test_limits():
    # 语句数上限 64：65 条赋值
    src = "def strategy(context):\n" + "".join(f"    x{i} = {i}\n" for i in range(65))
    src += '    return {"action": "HOLD", "score": 1, "entry_price": None, "stop_loss": None, "take_profit": None, "sell_ratio": None, "reason": ""}\n'
    assert "TOO_MANY_STATEMENTS" in _codes(src)
    # if 深度上限 6：7 层（return 作为最内层 body，缩进 32 空格）
    src = "def strategy(context):\n" + "".join("    " * (i + 1) + f"if x{i} == 0:\n" for i in range(7))
    src += " " * 32 + 'return {"action": "HOLD", "score": 1, "entry_price": None, "stop_loss": None, "take_profit": None, "sell_ratio": None, "reason": ""}\n'
    assert "IF_TOO_DEEP" in _codes(src)
    # 源码大小上限 12KiB
    big = "def strategy(context):\n    x = " + repr("y" * 20000) + "\n    return None\n"
    assert "SOURCE_TOO_LARGE" in _codes(big)
