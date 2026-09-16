"""沙箱协议（plan 4.1.1）：context 构造与七键输出校验，宿主与子进程共用。

- JSON context 固定为 meta/ohlcv/indicators/position 四段；
  数组严格按交易日升序，OHLCV 与指标数组同长度，因子缺失用 null 对齐，绝不前填。
- 输出必须是七键字典：action∈{BUY,SELL_ALL,SELL_PARTIAL,HOLD}、score∈[0,100]、
  reason ≤240 字符且无控制字符；BUY 必须 0<stop<entry<take 且 sell_ratio=None；
  SELL_ALL 必须有持仓、价格与 sell_ratio 全 None；SELL_PARTIAL 必须有持仓、
  价格全 None 且 0<ratio<1；HOLD 四交易字段全 None。非有限数值/超长/控制字符均无效。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

ACTIONS = frozenset({"BUY", "SELL_ALL", "SELL_PARTIAL", "HOLD"})
TRADE_KEYS = ("entry_price", "stop_loss", "take_profit", "sell_ratio")
REQUIRED_KEYS = (
    "action",
    "score",
    "entry_price",
    "stop_loss",
    "take_profit",
    "sell_ratio",
    "reason",
)
MAX_REASON_CHARS = 240
MAX_STDOUT_BYTES = 4 * 1024


def build_context(
    *,
    symbol: str,
    effective_trade_date: str,
    ohlcv: dict[str, list],
    indicators: dict[str, list],
    position: dict,
    bars_count: int | None = None,
    price_basis: str = "raw",
) -> dict:
    """构造 strategy(context) 的 JSON 上下文（canonical，无 None 前填）。"""
    if bars_count is None:
        bars_count = len(ohlcv.get("trade_date", []))
    return {
        "meta": {
            "symbol": symbol,
            "effective_trade_date": effective_trade_date,
            "bars_count": bars_count,
            "price_basis": price_basis,
        },
        "ohlcv": ohlcv,
        "indicators": indicators,
        "position": position,
    }


@dataclass(frozen=True)
class OutputValidation:
    ok: bool
    code: str | None = None
    message: str | None = None


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _isfinite(v) -> bool:
    return not isinstance(v, float) or math.isfinite(v)


def validate_strategy_output(output, *, has_position: bool) -> OutputValidation:
    """校验策略返回的七键字典（plan 4.1.1 校验矩阵），返回稳定 code/message。"""
    if not isinstance(output, dict):
        return OutputValidation(False, "INVALID_OUTPUT", "输出必须是 JSON 对象")
    missing = [k for k in REQUIRED_KEYS if k not in output]
    if missing:
        return OutputValidation(False, "INVALID_OUTPUT", f"输出缺少键: {','.join(missing)}")
    extra = [k for k in output if k not in REQUIRED_KEYS]
    if extra:
        return OutputValidation(False, "INVALID_OUTPUT", f"输出出现未允许的键: {','.join(extra)}")

    action = output["action"]
    if not isinstance(action, str) or action not in ACTIONS:
        return OutputValidation(False, "INVALID_OUTPUT", f"action 非法: {action!r}")
    score = output["score"]
    if not _is_number(score) or not _isfinite(score) or not 0 <= score <= 100:
        return OutputValidation(False, "INVALID_OUTPUT", "score 必须是 [0,100] 的有限数值")
    reason = output["reason"]
    if not isinstance(reason, str) or len(reason) > MAX_REASON_CHARS:
        return OutputValidation(False, "INVALID_OUTPUT", f"reason 必须是 ≤{MAX_REASON_CHARS} 字符的纯文本")
    if any(ord(ch) < 32 or ord(ch) == 127 or 128 <= ord(ch) <= 159 for ch in reason):
        return OutputValidation(False, "INVALID_OUTPUT", "reason 含控制字符")

    entry = output["entry_price"]
    stop = output["stop_loss"]
    take = output["take_profit"]
    ratio = output["sell_ratio"]
    for name, v in (("entry_price", entry), ("stop_loss", stop), ("take_profit", take), ("sell_ratio", ratio)):
        if v is not None and (not _is_number(v) or not _isfinite(v)):
            return OutputValidation(False, "INVALID_OUTPUT", f"{name} 必须是非有限数值除外的数值或 null")

    if action == "BUY":
        if ratio is not None:
            return OutputValidation(False, "INVALID_OUTPUT", "BUY 的 sell_ratio 必须为 null")
        if entry is None or stop is None or take is None:
            return OutputValidation(False, "INVALID_OUTPUT", "BUY 必须给出 entry/stop/take 三价")
        if not (0 < stop < entry < take):
            return OutputValidation(False, "INVALID_OUTPUT", "BUY 必须满足 0 < stop_loss < entry_price < take_profit")
    elif action == "SELL_ALL":
        if not has_position:
            return OutputValidation(False, "INVALID_OUTPUT", "SELL_ALL 必须有持仓")
        if entry is not None or stop is not None or take is not None or ratio is not None:
            return OutputValidation(False, "INVALID_OUTPUT", "SELL_ALL 的价格字段与 sell_ratio 必须为 null")
    elif action == "SELL_PARTIAL":
        if not has_position:
            return OutputValidation(False, "INVALID_OUTPUT", "SELL_PARTIAL 必须有持仓")
        if entry is not None or stop is not None or take is not None:
            return OutputValidation(False, "INVALID_OUTPUT", "SELL_PARTIAL 的价格字段必须为 null")
        if ratio is None or not _is_number(ratio) or not _isfinite(ratio) or not 0 < ratio < 1:
            return OutputValidation(False, "INVALID_OUTPUT", "SELL_PARTIAL 的 sell_ratio 必须满足 0 < ratio < 1")
    else:  # HOLD
        if entry is not None or stop is not None or take is not None or ratio is not None:
            return OutputValidation(False, "INVALID_OUTPUT", "HOLD 的四个交易字段必须为 null")
    return OutputValidation(ok=True)


@dataclass(frozen=True)
class StrategyRunResult:
    """一次沙箱执行的结果：成功输出或稳定错误码（fail-closed）。"""

    ok: bool
    output: dict | None = None
    error_code: str | None = None
    error_message: str | None = None
    resource_limit_degraded: bool = False
