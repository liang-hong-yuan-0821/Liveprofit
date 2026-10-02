# test-catalog-begin
# {
#   "purpose": "技术指标纯函数单测（板块概念Treemap方案 m7：概念 K 线自算指标）。",
#   "keywords": [
#     "行情服务",
#     "行情数据",
#     "indicators"
#   ],
#   "covers": [
#     "backend/modules/market_data/application/indicators.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""技术指标纯函数单测（板块概念Treemap方案 m7：概念 K 线自算指标）。

口径沿用归档 K线指标叠加方案 3.1 / MACD指标副图方案 3.1 已批设计——
已知序列手算数值、窗口不足 null 边界、空/短序列；同一声明只落一份，
服务层补窗口切片由契约测试承载。
"""

import pytest

from backend.modules.market_data.application.indicators import (
    boll,
    compute_indicators,
    ema,
    ma,
    macd,
)


class TestMa:
    def test_window_insufficient_leading_nones(self):
        # period=3：前 2 个位置 None，第 3 根起滚动均值
        assert ma([1.0, 2.0, 3.0, 4.0], 3) == [None, None, 2.0, 3.0]

    def test_single_element_window(self):
        assert ma([5.0, 6.0], 1) == [5.0, 6.0]

    def test_short_sequence_all_none(self):
        assert ma([1.0, 2.0], 5) == [None, None]

    def test_empty(self):
        assert ma([], 5) == []


class TestBoll:
    def test_hand_computed(self):
        # period=2、k=2、ddof=0：窗口 [1,3] 均值 2、总体 σ=1 → 上下轨 4/0
        result = boll([1.0, 3.0], period=2, k=2.0)
        assert result["mid"] == [None, 2.0]
        assert result["upper"] == [None, 4.0]
        assert result["lower"] == [None, 0.0]

    def test_constant_series_band_collapses(self):
        # 常数序列 σ=0 → 上下轨 = mid
        result = boll([2.0] * 3, period=2, k=2.0)
        assert result["mid"] == [None, 2.0, 2.0]
        assert result["upper"] == [None, 2.0, 2.0]
        assert result["lower"] == [None, 2.0, 2.0]

    def test_short_sequence_all_none(self):
        result = boll([1.0], period=5)
        assert result["mid"] == [None]
        assert result["upper"] == [None]
        assert result["lower"] == [None]


class TestEma:
    def test_hand_computed(self):
        # [1..6]、period=2（α=2/3）：种子=首 2 根 SMA=1.5；
        # ema[2]=2/3·3+1/3·1.5=2.5、ema[3]=3.5、ema[4]=4.5、ema[5]=5.5
        assert ema([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], 2) == pytest.approx(
            [None, 1.5, 2.5, 3.5, 4.5, 5.5]
        )

    def test_leading_none_skipped(self):
        # 前导 None 跳过滚动：有效序列 [1,2,3]、period=2 → 种子在 index 2（值 1.5）
        assert ema([None, 1.0, 2.0, 3.0], 2) == pytest.approx([None, None, 1.5, 2.5])

    def test_short_valid_subsequence_all_none(self):
        assert ema([None, 1.0], 5) == [None, None]

    def test_empty(self):
        assert ema([], 5) == []


class TestMacd:
    def test_hand_computed(self):
        # [1..6]、fast=2、slow=3、signal=2：
        # ema2=[None,1.5,2.5,3.5,4.5,5.5]、ema3=[None,None,2,3,4,5]
        # dif=[None,None,0.5,0.5,0.5,0.5]（第 slow−1=2 根起有值）
        # dea=ema(dif,2)：种子=首 2 有效根均值 0.5（index 3）→ 其后恒 0.5
        # hist=2×(dif−dea)（国内惯例 2×）
        result = macd([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], fast=2, slow=3, signal=2)
        assert result["dif"] == pytest.approx([None, None, 0.5, 0.5, 0.5, 0.5])
        assert result["dea"] == pytest.approx([None, None, None, 0.5, 0.5, 0.5])
        assert result["hist"] == pytest.approx([None, None, None, 0.0, 0.0, 0.0])

    def test_constant_series_zero_macd(self):
        # 常数序列 DIF=0 → DEA=0、hist=0（全窗口非 None）
        result = macd([2.0] * 5, fast=2, slow=3, signal=2)
        assert result["dif"] == pytest.approx([None, None, 0.0, 0.0, 0.0])
        assert result["dea"] == pytest.approx([None, None, None, 0.0, 0.0])
        assert result["hist"] == pytest.approx([None, None, None, 0.0, 0.0])

    def test_short_sequence_all_none(self):
        result = macd([1.0, 2.0], fast=2, slow=3, signal=2)
        assert result["dif"] == [None, None]
        assert result["dea"] == [None, None]
        assert result["hist"] == [None, None]


class TestComputeIndicators:
    def test_contract_shape(self):
        # 40 根线性序列：验证组装形状与关键对齐（数值口径由上面单测承载）
        closes = [100.0 + i for i in range(40)]
        result = compute_indicators(closes)
        assert [line["period"] for line in result["ma"]] == [5, 10, 20, 60]
        for line in result["ma"]:
            assert len(line["values"]) == 40
        assert (result["boll"]["period"], result["boll"]["k"]) == (20, 2.0)
        for key in ("mid", "upper", "lower"):
            assert len(result["boll"][key]) == 40
        assert (result["macd"]["fast"], result["macd"]["slow"], result["macd"]["signal"]) == (12, 26, 9)
        for key in ("dif", "dea", "hist"):
            assert len(result["macd"][key]) == 40
        # MA5 首值 = mean(100..104) = 102；MA60 全 None（40 < 60）
        assert result["ma"][0]["values"][4] == pytest.approx(102.0)
        assert result["ma"][0]["values"][3] is None
        assert result["ma"][3]["values"] == [None] * 40
        # DIF 第 slow−1=25 根起有值；DEA 再滞后 signal−1=8 根（index 33 起）
        assert result["macd"]["dif"][24] is None
        assert result["macd"]["dif"][25] is not None
        assert result["macd"]["dea"][32] is None
        assert result["macd"]["dea"][33] is not None
        # hist = 2×(dif−dea)（末根一致性）
        i = 39
        assert result["macd"]["hist"][i] == pytest.approx(
            2.0 * (result["macd"]["dif"][i] - result["macd"]["dea"][i])
        )

    def test_empty_closes(self):
        # 空序列：结构仍在（ma 四条线各空数组），各数组为空（调用方保证 bars 非空）
        result = compute_indicators([])
        assert [line["period"] for line in result["ma"]] == [5, 10, 20, 60]
        assert all(line["values"] == [] for line in result["ma"])
        assert result["boll"]["mid"] == []
        assert result["macd"]["dif"] == []
