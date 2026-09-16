"""技术指标纯函数（板块概念Treemap方案 m7：概念 K 线自算指标）。

背景（用户拍板 2026-09-16）：板块指数（dc 源）无任何上游因子源——tushare 无
板块指数因子端点，"技术指标不自算"决策（2026-09-12）在此路径无因子可取，
要画与指数 K 线同款指标只能自算。口径沿用已批纯函数设计（归档
K线指标叠加方案 3.1 / MACD指标副图方案 3.1——彼时因不自算决策未实施）：
- MA：滚动均值，前 period-1 根 None（窗口不足即 None，不伪造）
- BOLL(20,2)：mid = MA20；σ 用总体标准差（ddof=0，与主流行情软件口径一致）
- MACD(12,26,9)：EMA 前 period 根 SMA 作种子；DIF = EMA(fast) − EMA(slow)
  （第 slow−1 根起有值）；DEA = EMA(DIF, signal)（再滞后 signal−1 根）；
  hist = 2×(DIF−DEA)（国内惯例）

纯 Python 零三方依赖；本模块只负责计算，补窗口/切片对齐由服务层负责。
指数/个股主路径仍取 idx_factor_pro/stk_factor_pro 入库值（不自算），不经过本模块。
"""

from __future__ import annotations

MA_PERIODS = (5, 10, 20, 60)
BOLL_PERIOD = 20
BOLL_K = 2.0
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9


def ma(values: list[float], period: int) -> list[float | None]:
    """滚动均值：前 period-1 个位置为 None（窗口不足即 None，不伪造）。"""
    out: list[float | None] = []
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= period - 1:
            if i >= period:
                total -= values[i - period]
            out.append(total / period)
        else:
            out.append(None)
    return out


def boll(
    values: list[float],
    period: int = BOLL_PERIOD,
    k: float = BOLL_K,
) -> dict[str, list[float | None]]:
    """布林带三线：mid = ma(period)，上下轨 = mid ± k·σ。

    σ 用总体标准差（ddof=0，与主流行情软件口径一致）；窗口不足为 None。
    """
    mid = ma(values, period)
    upper: list[float | None] = []
    lower: list[float | None] = []
    for i, m in enumerate(mid):
        if m is None:
            upper.append(None)
            lower.append(None)
            continue
        window = values[i - period + 1 : i + 1]
        mean = sum(window) / period
        variance = sum((x - mean) ** 2 for x in window) / period  # ddof=0 总体方差
        sd = variance ** 0.5
        upper.append(m + k * sd)
        lower.append(m - k * sd)
    return {"mid": mid, "upper": upper, "lower": lower}


def ema(values: list[float | None], period: int) -> list[float | None]:
    """指数移动平均：跳过前导 None，从首个非 null 起前 period 根用 SMA 作种子
    （index = 第 period-1 个有效位）；此后 EMA_t = α·v_t + (1−α)·EMA_{t−1}，
    α = 2/(period+1)；输出与原序列等长（前导 None 保留）。

    有效值不足 period 根 → 全 None（窗口不足即 None，不伪造）。
    """
    alpha = 2.0 / (period + 1)
    out: list[float | None] = [None] * len(values)
    valid = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(valid) < period:
        return out
    prev = sum(v for _, v in valid[:period]) / period
    out[valid[period - 1][0]] = prev
    for i, v in valid[period:]:
        prev = alpha * v + (1 - alpha) * prev
        out[i] = prev
    return out


def macd(
    closes: list[float],
    fast: int = MACD_FAST,
    slow: int = MACD_SLOW,
    signal: int = MACD_SIGNAL,
) -> dict[str, list[float | None]]:
    """返回 {"dif", "dea", "hist"}：DIF = EMA(fast) − EMA(slow)（第 slow 根起有值）；
    DEA = EMA(DIF, signal)——DIF 是含前导 None 的序列，ema 内部跳过 None 滚动，
    滞后 signal−1 根；hist = 2×(DIF−DEA)。"""
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    dif: list[float | None] = [
        None if a is None or b is None else a - b
        for a, b in zip(ema_fast, ema_slow)
    ]
    dea = ema(dif, signal)
    hist: list[float | None] = [
        None if d is None or e is None else 2.0 * (d - e)
        for d, e in zip(dif, dea)
    ]
    return {"dif": dif, "dea": dea, "hist": hist}


def compute_indicators(closes: list[float]) -> dict:
    """组装契约形状（与 BarsData.indicators 同构）：
    {"ma": [{"period", "values"}, ...], "boll": {"period", "k", "mid", "upper", "lower"},
     "macd": {"fast", "slow", "signal", "dif", "dea", "hist"}}。

    各数组与 closes 等长、按 index 对齐；空序列各数组为空（调用方保证 bars 非空）。
    """
    return {
        "ma": [{"period": p, "values": ma(closes, p)} for p in MA_PERIODS],
        "boll": {
            "period": BOLL_PERIOD,
            "k": BOLL_K,
            **boll(closes),
        },
        "macd": {
            "fast": MACD_FAST,
            "slow": MACD_SLOW,
            "signal": MACD_SIGNAL,
            **macd(closes),
        },
    }
