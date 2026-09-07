"""技术指标纯函数（K线指标叠加方案 §3.1：MA5/10/20/60 + BOLL(20,2)）。

- 零三方依赖（不依赖 pandas/stockstats），输入为升序 close 序列；
- 窗口不足处为 None（数据缺失是事实，不伪造）；
- BOLL σ 用总体标准差（ddof=0），与主流行情软件口径一致。
"""

from __future__ import annotations

MA_PERIODS = (5, 10, 20, 60)
BOLL_PERIOD = 20
BOLL_K = 2.0


def ma(values: list[float], period: int) -> list[float | None]:
    """滚动均值：前 period-1 个位置为 None（窗口不足即 None）。"""
    if period <= 0:
        raise ValueError(f"period 必须为正整数：{period}")
    result: list[float | None] = []
    window_sum = 0.0
    for i, value in enumerate(values):
        window_sum += value
        if i >= period:
            window_sum -= values[i - period]
        result.append(window_sum / period if i >= period - 1 else None)
    return result


def boll(values: list[float], period: int = BOLL_PERIOD, k: float = BOLL_K) -> dict[str, list[float | None]]:
    """布林带：返回 {"mid", "upper", "lower"}；mid = ma(period)，上下轨 = mid ± k·σ。

    σ 用总体标准差（ddof=0）；窗口不足处为 None。
    """
    if period <= 0:
        raise ValueError(f"period 必须为正整数：{period}")
    mid = ma(values, period)
    upper: list[float | None] = []
    lower: list[float | None] = []
    for i in range(len(values)):
        if mid[i] is None:
            upper.append(None)
            lower.append(None)
            continue
        window = values[i - period + 1 : i + 1]
        mean = mid[i]  # 上方 continue 已保证非 None
        variance = sum((v - mean) ** 2 for v in window) / period
        sigma = variance**0.5
        upper.append(mean + k * sigma)
        lower.append(mean - k * sigma)
    return {"mid": mid, "upper": upper, "lower": lower}


def compute_indicators(closes: list[float]) -> dict:
    """组装契约为 {"ma": [{"period": p, "values": [...]}, ...], "boll": {...}}。

    各数组与输入 closes 等长、按 index 对齐；空序列返回各数组为空列表。
    """
    bands = boll(closes)
    return {
        "ma": [{"period": p, "values": ma(closes, p)} for p in MA_PERIODS],
        "boll": {
            "period": BOLL_PERIOD,
            "k": BOLL_K,
            "mid": bands["mid"],
            "upper": bands["upper"],
            "lower": bands["lower"],
        },
    }
