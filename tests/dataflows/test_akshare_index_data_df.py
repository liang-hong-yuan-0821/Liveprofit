"""
单元测试：AKShareProvider 非 CN 指数日线（新浪兜底分支，US/KR 上线方案 3.2）

mock 模块级 ak 对象（SimpleNamespace + MagicMock），验证：
- US 分支（.INX/.DJI/.IXIC）：index_us_stock_sina → 10 列标准帧；
  核心回归 = **区间首行 pre_close 等于区间外前一行的 close**（全历史帧先算
  pre_close/change/pct_chg 再按区间过滤，分块回填下每块首行 pre_close 仍有值）
- KS11 分支：index_global_hist_sina(symbol='首尔综合指数')（akshare 内部 map
  以中文名为 key，传 'KS11' 会 KeyError）
- 入参边界兼容 YYYYMMDD 与 YYYY-MM-DD；异常/空/区间外 → None
- CN 分支不受影响（stock_zh_index_daily 路径，index_us_stock_sina 未被调）
- AI 面 get_global_index("KOSPI") 走新浪源（东财 KS11 本机空表修复回归）
"""

import types
from unittest.mock import MagicMock

import pandas as pd
import pytest

from AI.dataflows.providers.cn import akshare
from AI.dataflows.providers.cn.akshare import AKShareProvider

# 全历史 8 行（2026-01-01 ~ 2026-01-08 连续日，无周末语义）；
# 查询区间 [2026-01-05, 2026-01-08] → 结果 4 行，首行 pre_close = 01-04 的 close
DATES = [f"2026-01-0{d}" for d in range(1, 9)]
CLOSES = [100.0, 101.0, 99.5, 102.0, 103.0, 106.0, 104.5, 105.0]


@pytest.fixture
def fake_ak(monkeypatch):
    """模块级 ak 替换：非 CN 新浪源 + CN stock_zh_index_daily 全 mock"""
    ak = types.SimpleNamespace()
    ak.stock_zh_index_daily = MagicMock(return_value=pd.DataFrame())
    ak.index_us_stock_sina = MagicMock()
    ak.index_global_hist_sina = MagicMock()
    ak.stock_zh_index_daily_em = MagicMock(return_value=pd.DataFrame())
    monkeypatch.setattr(akshare, "ak", ak)
    monkeypatch.setattr(akshare, "AKSHARE_AVAILABLE", True)
    return ak


def _us_frame(dates, closes):
    """新浪 US 形态：date/open/high/low/close/volume/amount（amount 恒 0）。"""
    return pd.DataFrame({
        "date": dates,
        "open": [c - 0.5 for c in closes],
        "high": [c + 1.0 for c in closes],
        "low": [c - 1.0 for c in closes],
        "close": closes,
        "volume": [1_000_000.0] * len(closes),
        "amount": [0.0] * len(closes),
    })


def _kr_frame(dates, closes):
    """新浪 KS11 形态：date/open/high/low/close/volume（无 amount 列）。"""
    return pd.DataFrame({
        "date": dates,
        "open": [c - 0.5 for c in closes],
        "high": [c + 1.0 for c in closes],
        "low": [c - 1.0 for c in closes],
        "close": closes,
        "volume": [500_000.0] * len(closes),
    })


def _provider():
    return AKShareProvider.__new__(AKShareProvider)


# ==================== US 分支 ====================

def test_us_branch_returns_standard_frame(fake_ak):
    fake_ak.index_us_stock_sina.return_value = _us_frame(DATES, CLOSES)
    df = _provider().get_index_data_df(".INX", "2026-01-05", "2026-01-08")
    assert list(df.columns) == [
        "trade_date", "open", "high", "low", "close",
        "pre_close", "change", "pct_chg", "vol", "amount",
    ]
    assert df["trade_date"].tolist() == ["2026-01-05", "2026-01-06",
                                         "2026-01-07", "2026-01-08"]
    # 核心回归：区间首行 pre_close = 区间外前一行（2026-01-04）的 close=102.0
    assert df["pre_close"].iloc[0] == 102.0
    assert df["close"].iloc[0] == 103.0
    assert df["change"].iloc[0] == pytest.approx(1.0)
    assert df["pct_chg"].iloc[0] == pytest.approx(1.0 / 102.0 * 100.0)
    # 上游 amount 恒 0 无信息量 → 统一 None
    assert df["amount"].isna().all()
    assert df["vol"].iloc[0] == 1_000_000.0
    fake_ak.index_us_stock_sina.assert_called_once_with(symbol=".INX")


def test_us_branch_tolerates_yyyymmdd_bounds(fake_ak):
    fake_ak.index_us_stock_sina.return_value = _us_frame(DATES, CLOSES)
    df = _provider().get_index_data_df(".INX", "20260105", "20260108")
    assert df["trade_date"].tolist() == ["2026-01-05", "2026-01-06",
                                         "2026-01-07", "2026-01-08"]


def test_us_branch_other_us_symbols(fake_ak):
    fake_ak.index_us_stock_sina.return_value = _us_frame(DATES, CLOSES)
    for code in (".DJI", ".IXIC"):
        df = _provider().get_index_data_df(code, "2026-01-05", "2026-01-08")
        assert df is not None and len(df) == 4
    assert fake_ak.index_us_stock_sina.call_count == 2


# ==================== KS11 分支 ====================

def test_ks11_branch_uses_global_hist_sina(fake_ak):
    fake_ak.index_global_hist_sina.return_value = _kr_frame(DATES, CLOSES)
    df = _provider().get_index_data_df("KS11", "2026-01-05", "2026-01-08")
    assert df is not None and len(df) == 4
    fake_ak.index_global_hist_sina.assert_called_once_with(symbol="首尔综合指数")
    assert fake_ak.index_us_stock_sina.call_count == 0
    # 无 amount 列仍产出标准帧、amount 全 None
    assert df["amount"].isna().all()
    assert df["pre_close"].iloc[0] == 102.0


# ==================== 失败路径 ====================

def test_non_cn_upstream_exception_returns_none(fake_ak):
    fake_ak.index_us_stock_sina.side_effect = RuntimeError("新浪网络异常")
    assert _provider().get_index_data_df(".INX", "2026-01-05", "2026-01-08") is None


def test_non_cn_empty_or_out_of_range_returns_none(fake_ak):
    fake_ak.index_us_stock_sina.return_value = pd.DataFrame()
    assert _provider().get_index_data_df(".INX", "2026-01-05", "2026-01-08") is None
    # 帧全部早于区间 → 过滤后空 → None
    fake_ak.index_us_stock_sina.return_value = _us_frame(
        ["2025-12-01", "2025-12-02"], [50.0, 51.0])
    assert _provider().get_index_data_df(".INX", "2026-01-05", "2026-01-08") is None


# ==================== CN 分支不受影响 ====================

def test_cn_branch_unchanged(fake_ak):
    # 上游 date 列实测为 datetime.date 对象（2026-09-14 实测 stock_zh_index_daily）
    fake_ak.stock_zh_index_daily.return_value = pd.DataFrame({
        "date": [pd.Timestamp("2026-01-05").date(), pd.Timestamp("2026-01-06").date()],
        "open": [3000.0, 3010.0], "high": [3100.0, 3110.0],
        "low": [2900.0, 2910.0], "close": [3050.0, 3060.0],
        "volume": [100.0, 110.0],
    })
    df = _provider().get_index_data_df("000001.SH", "20260105", "20260106")
    assert df is not None and len(df) == 2
    assert df["trade_date"].tolist() == ["2026-01-05", "2026-01-06"]
    fake_ak.stock_zh_index_daily.assert_called_once_with(symbol="sh000001")
    fake_ak.index_us_stock_sina.assert_not_called()
    fake_ak.index_global_hist_sina.assert_not_called()


# ==================== AI 面 kospi 工具修复 ====================

def test_get_global_index_kospi_uses_sina(fake_ak):
    fake_ak.index_global_hist_sina.return_value = _kr_frame(DATES, CLOSES)
    prov = _provider()
    prov.connected = True
    text = prov.get_global_index("KOSPI", days=5)
    assert "最近 5 个交易日" in text
    assert "韩国综合指数" in text
    fake_ak.index_global_hist_sina.assert_called_once_with(symbol="首尔综合指数")
    fake_ak.stock_zh_index_daily_em.assert_not_called()
