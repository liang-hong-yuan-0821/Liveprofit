"""indicators 纯函数单测（K线指标叠加方案 §3.1.3：数值、窗口 null、边界序列）。"""

from __future__ import annotations

import math

import pytest

from backend.modules.market_data.application.indicators import (
    BOLL_K,
    BOLL_PERIOD,
    MA_PERIODS,
    boll,
    compute_indicators,
    ma,
)


def test_ma_known_sequence():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert ma(values, 2) == [None, 1.5, 2.5, 3.5, 4.5]
    assert ma(values, 3) == [None, None, 2.0, 3.0, 4.0]


def test_ma_window_insufficient_none():
    assert ma([1.0, 2.0], 5) == [None, None]


def test_ma_empty_and_single():
    assert ma([], 5) == []
    assert ma([1.0], 1) == [1.0]


def test_ma_invalid_period():
    with pytest.raises(ValueError):
        ma([1.0, 2.0], 0)


def test_boll_constant_series_population_std():
    # 恒定序列：σ=0 → 上下轨 = 中轨 = 序列值；前 19 个位置窗口不足为 None
    values = [10.0] * 20
    bands = boll(values, period=20, k=2.0)
    assert bands["mid"][-1] == pytest.approx(10.0)
    assert bands["upper"][-1] == pytest.approx(10.0)
    assert bands["lower"][-1] == pytest.approx(10.0)
    assert all(v is None for v in bands["mid"][:19])
    assert all(v is None for v in bands["upper"][:19])
    assert all(v is None for v in bands["lower"][:19])


def test_boll_std_population_not_sample():
    # [1, 2, 3]：总体 σ = sqrt(2/3) ≈ 0.8165（样本 σ = 1 会给出上轨 3.0）
    values = [1.0, 2.0, 3.0]
    bands = boll(values, period=3, k=1.0)
    sigma = math.sqrt(2 / 3)
    assert bands["mid"][-1] == pytest.approx(2.0)
    assert bands["upper"][-1] == pytest.approx(2.0 + sigma)
    assert bands["lower"][-1] == pytest.approx(2.0 - sigma)


def test_boll_window_insufficient_none():
    bands = boll([1.0, 2.0], period=20)
    assert bands["mid"] == [None, None]
    assert bands["upper"] == [None, None]
    assert bands["lower"] == [None, None]


def test_boll_invalid_period():
    with pytest.raises(ValueError):
        boll([1.0, 2.0], period=0)


def test_compute_indicators_contract_shape():
    # 30 个值：MA60 全 None、MA5/10/20 部分有值
    values = [float(i) for i in range(1, 31)]
    result = compute_indicators(values)
    assert [line["period"] for line in result["ma"]] == list(MA_PERIODS)
    for line in result["ma"]:
        assert len(line["values"]) == len(values)
    assert result["boll"]["period"] == BOLL_PERIOD
    assert result["boll"]["k"] == BOLL_K
    for key in ("mid", "upper", "lower"):
        assert len(result["boll"][key]) == len(values)

    ma60 = result["ma"][MA_PERIODS.index(60)]
    assert all(v is None for v in ma60["values"])
    ma5 = result["ma"][MA_PERIODS.index(5)]
    assert all(v is None for v in ma5["values"][:4])
    assert ma5["values"][4] == pytest.approx(3.0)  # mean(1..5)


def test_compute_indicators_empty():
    result = compute_indicators([])
    for line in result["ma"]:
        assert line["values"] == []
    assert result["boll"]["mid"] == []
    assert result["boll"]["upper"] == []
    assert result["boll"]["lower"] == []
