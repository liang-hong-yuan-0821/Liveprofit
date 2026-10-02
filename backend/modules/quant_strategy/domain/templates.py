"""七套参考策略的注册、参数校验、源码渲染与输入门控。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from math import isfinite

from AI.strategy_sandbox.strategy_contract import INDEXED_PATHS

RENDERER_VERSION = "template_renderer_v1"


class TemplateValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ParameterSpec:
    default: str | int
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    integer: bool = False
    editable: bool = True

    def normalize(self, value) -> str | int:
        if self.integer:
            if isinstance(value, bool):
                raise TemplateValidationError("参数必须是整数")
            try:
                number = Decimal(str(value))
            except (InvalidOperation, ValueError):
                raise TemplateValidationError("参数必须是整数") from None
            if number != number.to_integral_value():
                raise TemplateValidationError("参数必须是整数")
            result: str | int = int(number)
        else:
            try:
                number = Decimal(str(value))
            except (InvalidOperation, ValueError):
                raise TemplateValidationError("参数必须是有限数值") from None
            if not number.is_finite():
                raise TemplateValidationError("参数必须是有限数值")
            result = format(number.normalize(), "f")
        numeric = Decimal(str(result))
        if self.minimum is not None and numeric < self.minimum:
            raise TemplateValidationError(f"参数不得小于 {self.minimum}")
        if self.maximum is not None and numeric > self.maximum:
            raise TemplateValidationError(f"参数不得大于 {self.maximum}")
        if not self.editable and str(result) != str(self.default):
            raise TemplateValidationError("固定参数不可修改")
        return result


@dataclass(frozen=True)
class FieldRequirement:
    path: tuple[str, str]
    indices: tuple[int, ...]
    must_be_positive: bool = False


@dataclass(frozen=True)
class StrategyTemplateDefinition:
    template_id: str
    display_name: str
    description: str
    required_bars: int
    parameters: dict[str, ParameterSpec]
    required_fields: tuple[FieldRequirement, ...]
    renderer: Callable[[dict[str, str | int]], str]
    wave: int

    def normalize_params(self, params: dict | None = None) -> dict[str, str | int]:
        raw = params or {}
        unknown = sorted(set(raw) - set(self.parameters))
        if unknown:
            raise TemplateValidationError(f"未知参数: {','.join(unknown)}")
        normalized = {
            key: spec.normalize(raw.get(key, spec.default)) for key, spec in self.parameters.items()
        }
        self._validate_cross_constraints(normalized)
        return normalized

    def _validate_cross_constraints(self, values: dict[str, str | int]) -> None:
        if "rsi_buy_low" in values and Decimal(str(values["rsi_buy_low"])) >= Decimal(str(values["rsi_buy_high"])):
            raise TemplateValidationError("rsi_buy_low 必须小于 rsi_buy_high")
        if "rsi_oversold" in values and Decimal(str(values["rsi_oversold"])) >= Decimal(str(values["rsi_entry_ceiling"])):
            raise TemplateValidationError("rsi_oversold 必须小于 rsi_entry_ceiling")

    def render(self, params: dict | None = None) -> tuple[str, dict[str, str | int]]:
        normalized = self.normalize_params(params)
        return self.renderer(normalized), normalized


def _p(path: tuple[str, str], *indices: int, positive: bool = False) -> FieldRequirement:
    if path not in INDEXED_PATHS:
        raise RuntimeError(f"模板字段未登记到 Sandbox 合同: {path}")
    return FieldRequirement(path, tuple(indices), positive)


def freeze_template_contract(definition: StrategyTemplateDefinition) -> dict:
    """生成任务快照可持久化的最小字段门禁；执行期不得再依赖可变注册表。"""
    return {
        "template_id": definition.template_id,
        "display_name": definition.display_name,
        "renderer_version": RENDERER_VERSION,
        "required_bars": definition.required_bars,
        "required_fields": [
            {
                "section": requirement.path[0],
                "field": requirement.path[1],
                "indices": list(requirement.indices),
                "must_be_positive": requirement.must_be_positive,
            }
            for requirement in definition.required_fields
        ],
    }


def validate_frozen_template_context(contract: dict, context: dict) -> str | None:
    """按已冻结 JSON 合同校验；合同损坏一律 fail-closed。"""
    try:
        required_bars = int(contract["required_bars"])
        fields = contract["required_fields"]
        if int(context.get("meta", {}).get("bars_count") or 0) < required_bars:
            return "WARMUP_INCOMPLETE"
        for item in fields:
            values = context[item["section"]][item["field"]]
            for index in item["indices"]:
                value = values[int(index)]
                if value is None or not isfinite(float(value)):
                    return "INDICATOR_UNAVAILABLE"
                if bool(item.get("must_be_positive")) and float(value) <= 0:
                    return "INDICATOR_UNAVAILABLE"
    except (KeyError, IndexError, TypeError, ValueError, OverflowError):
        return "INDICATOR_UNAVAILABLE"
    return None


def _return(action: str, score: str, reason: str, *, buy: bool = False) -> str:
    if buy:
        return (
            f'{{"action": "{action}", "score": {score}, "entry_price": entry, '
            '"stop_loss": stop, "take_profit": take, "sell_ratio": None, '
            f'"reason": "{reason}"}}'
        )
    return (
        f'{{"action": "{action}", "score": {score}, "entry_price": None, '
        '"stop_loss": None, "take_profit": None, "sell_ratio": None, '
        f'"reason": "{reason}"}}'
    )


def _ma_trend(p):
    benchmark_gate = (
        'context["meta"]["benchmark_above_ma120"] == True'
        if p["benchmark_filter"] else "True"
    )
    return f'''def strategy(context):
    shares = context["position"]["shares"]
    close = context["ohlcv"]["close"]
    ma5 = context["indicators"]["ma_qfq_5"]
    ma20 = context["indicators"]["ma_qfq_20"]
    ma60 = context["indicators"]["ma_qfq_60"]
    rsi = context["indicators"]["rsi_qfq_6"]
    macd = context["indicators"]["macd_qfq"]
    if shares > 0 and ma5[-1] < ma20[-1] and ma5[-2] >= ma20[-2]:
        return {_return("SELL_ALL", "0", "MA_DEATH_CROSS")}
    if shares == 0 and {benchmark_gate} and ma5[-1] > ma20[-1] and ma5[-2] <= ma20[-2] and ma20[-1] > ma60[-1] and close[-1] > ma20[-1] and rsi[-1] >= 45 and rsi[-1] <= 70 and macd[-1] > 0:
        entry = close[-1]
        stop = entry * (1 - {p['stop_pct']})
        take = entry + {p['reward_multiple']} * (entry - stop)
        score = 70
        if rsi[-1] >= 50 and rsi[-1] <= 65:
            score += 5
        if macd[-1] > macd[-2]:
            score += 5
        if close[-1] > ma5[-1]:
            score += 5
        return {_return("BUY", "score", "MA_TREND_CROSS", buy=True)}
    return {_return("HOLD", "0", "NO_SIGNAL")}
'''


def _pullback(p):
    stop_expr = (
        "min(low[-6], low[-5], low[-4], low[-3], low[-2])"
        if p["prior_five_low_stop"] else f"entry * (1 - {p['stop_pct']})"
    )
    return f'''def strategy(context):
    shares = context["position"]["shares"]
    close = context["ohlcv"]["close"]
    low = context["ohlcv"]["low"]
    ma20 = context["indicators"]["ma_qfq_20"]
    ma60 = context["indicators"]["ma_qfq_60"]
    lower = context["indicators"]["boll_lower_qfq"]
    rsi = context["indicators"]["rsi_qfq_6"]
    macd = context["indicators"]["macd_qfq"]
    if shares > 0 and close[-1] < lower[-1] and rsi[-1] < rsi[-2]:
        return {_return("SELL_ALL", "0", "PULLBACK_INVALIDATION")}
    if shares == 0 and ma20[-1] > ma60[-1] and close[-2] <= lower[-2] and close[-1] > lower[-1] and rsi[-1] >= {p['rsi_buy_low']} and rsi[-1] <= {p['rsi_buy_high']} and rsi[-1] > rsi[-2] and macd[-1] >= macd[-2]:
        entry = close[-1]
        stop = {stop_expr}
        take = entry + {p['reward_multiple']} * (entry - stop)
        score = 68
        if close[-1] > ma20[-1]:
            score += 8
        if rsi[-1] >= 40 and rsi[-1] <= 50:
            score += 7
        if macd[-2] < 0 and macd[-1] >= 0:
            score += 7
        if stop < entry:
            return {_return("BUY", "score", "TREND_PULLBACK", buy=True)}
    return {_return("HOLD", "0", "NO_SIGNAL")}
'''


def _boll_breakout(p):
    squeeze_gate = (
        'context["meta"]["boll_low_quartile_10_120"] == True'
        if p["low_bandwidth_quartile"] else "True"
    )
    return f'''def strategy(context):
    shares = context["position"]["shares"]
    close = context["ohlcv"]["close"]
    volume = context["ohlcv"]["volume"]
    ma20 = context["indicators"]["ma_qfq_20"]
    ma60 = context["indicators"]["ma_qfq_60"]
    mid = context["indicators"]["boll_mid_qfq"]
    upper = context["indicators"]["boll_upper_qfq"]
    rsi = context["indicators"]["rsi_qfq_6"]
    macd = context["indicators"]["macd_qfq"]
    base = max(volume[-6], volume[-5], volume[-4], volume[-3], volume[-2])
    if shares > 0 and close[-1] < mid[-1] and macd[-1] < 0:
        return {_return("SELL_ALL", "0", "BREAKOUT_INVALIDATION")}
    if shares == 0 and {squeeze_gate} and ma20[-1] > ma60[-1] and close[-2] <= upper[-2] and close[-1] > upper[-1] and base > 0 and volume[-1] > 0 and volume[-1] >= {p['volume_multiple']} * base and rsi[-1] >= 50 and rsi[-1] <= {p['rsi_ceiling']}:
        entry = close[-1]
        stop = max(mid[-1], entry * (1 - {p['floor_stop_pct']}))
        take = entry + {p['reward_multiple']} * (entry - stop)
        if stop < entry:
            score = 72
            if volume[-1] >= 2 * base:
                score += 8
            if rsi[-1] >= 55 and rsi[-1] <= 70:
                score += 5
            if close[-1] > 1.03 * ma20[-1]:
                score += 5
            return {_return("BUY", "score", "BOLL_VOLUME_BREAKOUT", buy=True)}
    return {_return("HOLD", "0", "NO_SIGNAL")}
'''


def _macd_reversal(p):
    oversold = (
        "min(rsi[-6], rsi[-5], rsi[-4], rsi[-3], rsi[-2])"
        if p["five_day_oversold"] else "rsi[-2]"
    )
    return f'''def strategy(context):
    shares = context["position"]["shares"]
    close = context["ohlcv"]["close"]
    ma20 = context["indicators"]["ma_qfq_20"]
    dif = context["indicators"]["macd_dif_qfq"]
    dea = context["indicators"]["macd_dea_qfq"]
    macd = context["indicators"]["macd_qfq"]
    rsi = context["indicators"]["rsi_qfq_6"]
    if shares > 0 and dif[-1] < dea[-1] and macd[-1] < 0:
        return {_return("SELL_ALL", "0", "MACD_REVERSAL_FAILURE")}
    if shares == 0 and dif[-2] <= dea[-2] and dif[-1] > dea[-1] and dif[-1] < 0 and macd[-1] > macd[-2] and {oversold} < {p['rsi_oversold']} and rsi[-1] >= {p['rsi_oversold']} and rsi[-1] <= {p['rsi_entry_ceiling']} and close[-1] >= 0.97 * ma20[-1]:
        entry = close[-1]
        stop = entry * (1 - {p['stop_pct']})
        take = entry + {p['reward_multiple']} * (entry - stop)
        score = 65
        if dif[-1] > dif[-2]:
            score += 8
        if rsi[-1] >= 40:
            score += 7
        if close[-1] > ma20[-1]:
            score += 5
        return {_return("BUY", "score", "MACD_RSI_REVERSAL", buy=True)}
    return {_return("HOLD", "0", "NO_SIGNAL")}
'''


def _pre_cross(p):
    confirmed = bool(p["confirmed_cross"])
    cross_line = "" if confirmed else "    cross = (20 * (ma20[-1] - ma5[-1]) + 4 * close[-5] - close[-20]) / 3\n"
    entry_condition = (
        "ma5[-2] <= ma20[-2] and ma5[-1] > ma20[-1]"
        if confirmed else
        f"ma5[-1] <= ma20[-1] and ma5[-1] > ma5[-2] and cross > close[-1] and cross - close[-1] <= {p['max_projected_cross_pct']} * close[-1]"
    )
    bonus_condition = "ma5[-1] <= 1.01 * ma20[-1]" if confirmed else "cross - close[-1] <= 0.01 * close[-1]"
    reason = "MA5_CONFIRMED_CROSS" if confirmed else "MA5_PRE_CROSS"
    return f'''def strategy(context):
    shares = context["position"]["shares"]
    close = context["ohlcv"]["close"]
    ma5 = context["indicators"]["ma_qfq_5"]
    ma20 = context["indicators"]["ma_qfq_20"]
    ma60 = context["indicators"]["ma_qfq_60"]
    rsi = context["indicators"]["rsi_qfq_6"]
    macd = context["indicators"]["macd_qfq"]
    if shares > 0 and ma5[-1] < ma20[-1] and ma5[-2] >= ma20[-2]:
        return {_return("SELL_ALL", "0", "MA_DEATH_CROSS")}
{cross_line}    if shares == 0 and {entry_condition} and ma20[-1] > ma60[-1] and close[-1] > ma5[-1] and rsi[-1] >= 45 and rsi[-1] <= 70 and macd[-1] >= 0:
        entry = close[-1]
        stop = entry * (1 - {p['stop_pct']})
        take = entry + {p['reward_multiple']} * (entry - stop)
        score = 68
        if {bonus_condition}:
            score += 8
        if rsi[-1] >= 50 and rsi[-1] <= 65:
            score += 5
        if macd[-1] > macd[-2]:
            score += 5
        return {_return("BUY", "score", reason, buy=True)}
    return {_return("HOLD", "0", "NO_SIGNAL")}
'''


def _volume_surge(p):
    volume_baseline = (
        'context["meta"]["volume_median_20"]'
        if p["median_volume_baseline"] else
        "max(volume[-6], volume[-5], volume[-4], volume[-3], volume[-2])"
    )
    return f'''def strategy(context):
    shares = context["position"]["shares"]
    close = context["ohlcv"]["close"]
    volume = context["ohlcv"]["volume"]
    ma20 = context["indicators"]["ma_qfq_20"]
    ma60 = context["indicators"]["ma_qfq_60"]
    rsi = context["indicators"]["rsi_qfq_6"]
    macd = context["indicators"]["macd_qfq"]
    base = {volume_baseline}
    if shares > 0 and close[-1] < ma20[-1] and macd[-1] < 0:
        return {_return("SELL_ALL", "0", "VOLUME_SURGE_FAILURE")}
    if shares == 0 and base != None and base > 0 and volume[-1] >= {p['volume_multiple']} * base and close[-1] >= close[-2] * (1 + {p['min_price_gain_pct']}) and ma20[-1] > ma60[-1] and close[-1] > ma20[-1] and rsi[-1] >= 50 and rsi[-1] <= 75 and macd[-1] > 0:
        entry = close[-1]
        stop = entry * (1 - {p['stop_pct']})
        take = entry + {p['reward_multiple']} * (entry - stop)
        score = 70
        if volume[-1] >= 3 * base:
            score += 10
        if close[-1] >= 1.03 * close[-2]:
            score += 5
        if rsi[-1] >= 55 and rsi[-1] <= 68:
            score += 5
        return {_return("BUY", "score", "VOLUME_SURGE_CONFIRM", buy=True)}
    return {_return("HOLD", "0", "NO_SIGNAL")}
'''


def _arc_bottom(p):
    neckline_expr = (
        'context["meta"]["arc_neckline_40"]'
        if p["rolling_neckline"] else "max(close[-41], close[-11])"
    )
    return f'''def strategy(context):
    shares = context["position"]["shares"]
    close = context["ohlcv"]["close"]
    volume = context["ohlcv"]["volume"]
    ma20 = context["indicators"]["ma_qfq_20"]
    ma60 = context["indicators"]["ma_qfq_60"]
    macd = context["indicators"]["macd_qfq"]
    rsi = context["indicators"]["rsi_qfq_6"]
    neckline = {neckline_expr}
    base = max(volume[-6], volume[-5], volume[-4], volume[-3], volume[-2])
    if shares > 0 and ma20[-1] < ma60[-1] and macd[-1] < 0:
        return {_return("SELL_ALL", "0", "ARC_BOTTOM_INVALIDATION")}
    if shares == 0 and neckline != None and close[-21] <= close[-41] * (1 - {p['min_left_decline_pct']}) and close[-21] <= close[-31] and close[-21] <= close[-11] and close[-11] >= close[-21] * (1 + {p['min_recovery_pct']}) and close[-1] >= neckline * (1 + {p['neckline_breakout_pct']}) and base > 0 and volume[-1] >= {p['volume_multiple']} * base and ma20[-1] > ma20[-2] and ma20[-1] > ma60[-1] and rsi[-1] >= 50 and rsi[-1] <= 75 and macd[-1] > 0 and macd[-1] >= macd[-2]:
        entry = close[-1]
        stop = max(neckline, entry * (1 - {p['stop_pct']}))
        take = entry + {p['reward_multiple']} * (entry - stop)
        if stop < entry:
            score = 70
            if volume[-1] >= 2 * base:
                score += 8
            if rsi[-1] >= 55 and rsi[-1] <= 68:
                score += 5
            if close[-1] >= 1.03 * neckline:
                score += 5
            return {_return("BUY", "score", "ARC_BOTTOM_75A", buy=True)}
    return {_return("HOLD", "0", "NO_SIGNAL")}
'''


def _d(value: str) -> Decimal:
    return Decimal(value)


COMMON_RISK = {
    "stop_pct": ParameterSpec("0.06", _d("0.03"), _d("0.10")),
    "reward_multiple": ParameterSpec("2.50", _d("2.00"), _d("3.00")),
}


TEMPLATES: dict[str, StrategyTemplateDefinition] = {
    "ma_trend_cross_v1": StrategyTemplateDefinition(
        "ma_trend_cross_v1", "均线趋势交叉", "中期趋势中的 MA5/MA20 首次金叉", 2,
        {**COMMON_RISK, "benchmark_filter": ParameterSpec(0, _d("0"), _d("1"), True)},
        (_p(("ohlcv", "close"), -1), _p(("indicators", "ma_qfq_5"), -2, -1),
         _p(("indicators", "ma_qfq_20"), -2, -1), _p(("indicators", "ma_qfq_60"), -1),
         _p(("indicators", "rsi_qfq_6"), -1), _p(("indicators", "macd_qfq"), -2, -1)),
        _ma_trend, 1,
    ),
    "trend_pullback_v1": StrategyTemplateDefinition(
        "trend_pullback_v1", "强势回撤反弹", "上行趋势中的布林下轨回收", 6,
        {"rsi_buy_low": ParameterSpec(35, _d("20"), _d("45"), True),
         "rsi_buy_high": ParameterSpec(55, _d("45"), _d("70"), True),
         "prior_five_low_stop": ParameterSpec(0, _d("0"), _d("1"), True),
         "stop_pct": ParameterSpec("0.05", _d("0.03"), _d("0.10")),
         "reward_multiple": ParameterSpec("2.20", _d("2.00"), _d("3.00"))},
        (_p(("ohlcv", "close"), -2, -1), _p(("ohlcv", "low"), -6, -5, -4, -3, -2, positive=True),
         _p(("indicators", "ma_qfq_20"), -1),
         _p(("indicators", "ma_qfq_60"), -1), _p(("indicators", "boll_lower_qfq"), -2, -1),
         _p(("indicators", "rsi_qfq_6"), -2, -1), _p(("indicators", "macd_qfq"), -2, -1)),
        _pullback, 1,
    ),
    "boll_volume_breakout_v1": StrategyTemplateDefinition(
        "boll_volume_breakout_v1", "布林放量突破", "上升趋势中的布林上轨放量突破", 140,
        {"volume_multiple": ParameterSpec("1.50", _d("1.20"), _d("3.00")),
         "low_bandwidth_quartile": ParameterSpec(0, _d("0"), _d("1"), True),
         "rsi_ceiling": ParameterSpec(75, _d("60"), _d("85"), True),
         "floor_stop_pct": ParameterSpec("0.06", _d("0.03"), _d("0.10")),
         "reward_multiple": ParameterSpec("2.00", _d("2.00"), _d("3.00"))},
        (_p(("ohlcv", "close"), -2, -1), _p(("ohlcv", "volume"), -6, -5, -4, -3, -2, -1, positive=True),
         _p(("indicators", "ma_qfq_20"), -1), _p(("indicators", "ma_qfq_60"), -1),
         _p(("indicators", "boll_mid_qfq"), -1), _p(("indicators", "boll_upper_qfq"), -2, -1),
         _p(("indicators", "rsi_qfq_6"), -1), _p(("indicators", "macd_qfq"), -1)),
        _boll_breakout, 2,
    ),
    "macd_rsi_reversal_v1": StrategyTemplateDefinition(
        "macd_rsi_reversal_v1", "MACD-RSI 动量反转", "超卖恢复与零轴下 MACD 金叉", 6,
        {"rsi_oversold": ParameterSpec(35, _d("20"), _d("40"), True),
         "five_day_oversold": ParameterSpec(0, _d("0"), _d("1"), True),
         "rsi_entry_ceiling": ParameterSpec(55, _d("40"), _d("65"), True),
         "stop_pct": ParameterSpec("0.05", _d("0.03"), _d("0.10")),
         "reward_multiple": ParameterSpec("2.00", _d("2.00"), _d("3.00"))},
        (_p(("ohlcv", "close"), -1), _p(("indicators", "ma_qfq_20"), -1),
         _p(("indicators", "macd_dif_qfq"), -2, -1), _p(("indicators", "macd_dea_qfq"), -2, -1),
         _p(("indicators", "macd_qfq"), -2, -1),
         _p(("indicators", "rsi_qfq_6"), -6, -5, -4, -3, -2, -1)),
        _macd_reversal, 2,
    ),
    "ma5_pre_cross_v1": StrategyTemplateDefinition(
        "ma5_pre_cross_v1", "MA5 预上穿 MA20", "以固定窗口临界价提前布局", 20,
        {"max_projected_cross_pct": ParameterSpec("0.02", _d("0.005"), _d("0.05")),
         "confirmed_cross": ParameterSpec(0, _d("0"), _d("1"), True),
         "confirmation_window_trading_days": ParameterSpec(3, _d("3"), _d("3"), True, False),
         "stop_pct": ParameterSpec("0.05", _d("0.03"), _d("0.10")),
         "reward_multiple": ParameterSpec("2.20", _d("2.00"), _d("3.00"))},
        (_p(("ohlcv", "close"), -20, -5, -1), _p(("indicators", "ma_qfq_5"), -2, -1),
         _p(("indicators", "ma_qfq_20"), -2, -1), _p(("indicators", "ma_qfq_60"), -1),
         _p(("indicators", "rsi_qfq_6"), -1), _p(("indicators", "macd_qfq"), -2, -1)),
        _pre_cross, 2,
    ),
    "volume_surge_confirm_v1": StrategyTemplateDefinition(
        "volume_surge_confirm_v1", "成交量骤增确认", "前五日量能基线上的趋势确认", 21,
        {"volume_multiple": ParameterSpec("2.00", _d("1.20"), _d("5.00")),
         "median_volume_baseline": ParameterSpec(0, _d("0"), _d("1"), True),
         "min_price_gain_pct": ParameterSpec("0.015", _d("0.005"), _d("0.08")),
         "stop_pct": ParameterSpec("0.06", _d("0.03"), _d("0.10")),
         "reward_multiple": ParameterSpec("2.00", _d("2.00"), _d("3.00"))},
        (_p(("ohlcv", "close"), -2, -1),
         _p(("ohlcv", "volume"), *range(-21, 0), positive=True),
         _p(("indicators", "ma_qfq_20"), -1), _p(("indicators", "ma_qfq_60"), -1),
         _p(("indicators", "rsi_qfq_6"), -1), _p(("indicators", "macd_qfq"), -1)),
        _volume_surge, 1,
    ),
    "arc_bottom_75a_v1": StrategyTemplateDefinition(
        "arc_bottom_75a_v1", "圆弧底 75A", "固定 41 根锚点的右侧突破实验模板", 41,
        {"min_left_decline_pct": ParameterSpec("0.08", _d("0.05"), _d("0.20")),
         "rolling_neckline": ParameterSpec(0, _d("0"), _d("1"), True),
         "min_recovery_pct": ParameterSpec("0.06", _d("0.03"), _d("0.15")),
         "neckline_breakout_pct": ParameterSpec("0.01", _d("0.005"), _d("0.05")),
         "volume_multiple": ParameterSpec("1.50", _d("1.20"), _d("5.00")),
         "stop_pct": ParameterSpec("0.06", _d("0.03"), _d("0.10")),
         "reward_multiple": ParameterSpec("2.20", _d("2.00"), _d("3.00"))},
        (_p(("ohlcv", "close"), -41, -31, -21, -11, -1, positive=True),
         _p(("ohlcv", "volume"), -6, -5, -4, -3, -2, -1, positive=True),
         _p(("indicators", "ma_qfq_20"), -2, -1), _p(("indicators", "ma_qfq_60"), -1),
         _p(("indicators", "macd_qfq"), -2, -1), _p(("indicators", "rsi_qfq_6"), -1)),
        _arc_bottom, 3,
    ),
}


HOST_SCALAR_VARIANTS = {
    ("arc_bottom_75a_v1", "rolling_neckline"): "arc_neckline_40",
    ("volume_surge_confirm_v1", "median_volume_baseline"): "volume_median_20",
    ("boll_volume_breakout_v1", "low_bandwidth_quartile"): "boll_low_quartile_10_120",
    ("ma_trend_cross_v1", "benchmark_filter"): "benchmark_above_ma120",
}


def required_host_scalars(template_id: str | None, params: dict | None) -> tuple[str, ...]:
    frozen_params = params or {}
    return tuple(field for (family, parameter), field in HOST_SCALAR_VARIANTS.items()
                 if family == template_id and frozen_params.get(parameter) == 1)


def get_template(template_id: str) -> StrategyTemplateDefinition:
    try:
        return TEMPLATES[template_id]
    except KeyError:
        raise TemplateValidationError(f"未知模板: {template_id}") from None


def list_templates() -> list[StrategyTemplateDefinition]:
    return list(TEMPLATES.values())


def validate_template_context(definition: StrategyTemplateDefinition, context: dict) -> str | None:
    try:
        if int(context.get("meta", {}).get("bars_count") or 0) < definition.required_bars:
            return "WARMUP_INCOMPLETE"
    except (TypeError, ValueError):
        return "WARMUP_INCOMPLETE"
    for requirement in definition.required_fields:
        section, field = requirement.path
        values = context.get(section, {}).get(field)
        if not isinstance(values, list):
            return "INDICATOR_UNAVAILABLE"
        for index in requirement.indices:
            if len(values) < abs(index):
                return "WARMUP_INCOMPLETE"
            value = values[index]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
                return "INDICATOR_UNAVAILABLE"
            if requirement.must_be_positive and value <= 0:
                return "INDICATOR_UNAVAILABLE"
    return None
