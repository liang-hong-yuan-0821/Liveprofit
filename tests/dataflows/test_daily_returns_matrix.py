"""
单元测试：行业/概念「近 10 个交易日逐日涨跌幅」结构化矩阵（热力图数据源）

覆盖（Tushare + AKShare 两 provider）：
- 行业矩阵：降序输入回归（sw_daily 降序 → dates 升序断言）、None 缺失格、
  行序与 names 一致性、复利累计口径与文本逐日章节同源
- 概念矩阵：行集合 = 热度排序（与概念热度 TOP N 章节同口径）、
  单元格来自 dc_index 逐日快照（Tushare）/ 已取日线 close（AKShare）
- 基类默认返回 None（结构化接口约定）
"""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from AI.dataflows.providers.base_provider import BaseStockDataProvider
from AI.dataflows.providers.tushare_provider import TushareProvider
from AI.dataflows.providers import akshare_provider

DAY_TODAY = "20260819"
TRADING_11 = ["20260805", "20260806", "20260807", "20260810", "20260811",
              "20260812", "20260813", "20260814", "20260817", "20260818",
              "20260819"]
TRADING_10 = TRADING_11[1:]   # 08-06 ~ 08-19，10 个交易日


def _geo_closes(base: float, rate: float, n: int) -> list:
    """等比收盘序列（每日涨跌幅恒为 rate）"""
    return [base * (1 + rate) ** i for i in range(n)]


# ==================== Tushare ====================

def _new_tushare_provider(api=None):
    """绕过 __init__ 的连接逻辑，直接构造 mock provider"""
    prov = TushareProvider.__new__(TushareProvider)
    prov.name = "Tushare"
    prov.connected = True
    prov.api = api or MagicMock()
    return prov


def _industry_api(sw_map):
    """index_classify / sw_daily 按入参分派的 _api_call mock"""
    classify_df = pd.DataFrame([
        {"index_code": "801780.SI", "industry_name": "银行"},
        {"index_code": "801080.SI", "industry_name": "电子"},
    ])
    api = MagicMock()
    api.index_classify = MagicMock(return_value=classify_df)
    api.sw_daily = MagicMock(side_effect=lambda **kw: sw_map[kw["ts_code"]])
    return api


@pytest.fixture(autouse=True)
def clear_daily_cache():
    """按日缓存是类级 dict，测试间必须清空防止交叉污染"""
    TushareProvider._DC_CONCEPT_DAILY_CACHE.clear()
    yield
    TushareProvider._DC_CONCEPT_DAILY_CACHE.clear()


def test_tushare_industry_matrix_descending_input_ascending_dates():
    """降序输入回归（sw_daily 按 trade_date 降序返回）：dates 必须升序"""
    desc_dates = TRADING_11[::-1]
    sw_map = {
        "801780.SI": pd.DataFrame({"trade_date": desc_dates,
                                   "close": _geo_closes(100, 0.02, 11)[::-1]}),
        "801080.SI": pd.DataFrame({"trade_date": desc_dates,
                                   "close": _geo_closes(100, -0.01, 11)[::-1]}),
    }
    prov = _new_tushare_provider(_industry_api(sw_map))

    data = prov.get_industry_daily_returns_matrix(days=10)

    assert data["source"] == "tushare"
    assert data["dates"] == TRADING_10          # 升序；11 行收盘 → 10 个 pct
    assert data["names"] == ["银行", "电子"]     # provider 获取序（未排序）
    assert len(data["pct_matrix"]) == 2
    assert all(len(r) == 10 for r in data["pct_matrix"])
    assert abs(data["pct_matrix"][0][0] - 2.0) < 1e-9
    assert abs(data["pct_matrix"][1][0] + 1.0) < 1e-9


def test_tushare_industry_matrix_missing_day_none():
    """某行业缺最新一日 → 该格为 None，其余正常"""
    sw_map = {
        # 银行缺 08-19（10 行 → 9 个 pct）
        "801780.SI": pd.DataFrame({"trade_date": TRADING_11[:-1],
                                   "close": _geo_closes(100, 0.02, 10)}),
        "801080.SI": pd.DataFrame({"trade_date": TRADING_11,
                                   "close": _geo_closes(100, -0.01, 11)}),
    }
    prov = _new_tushare_provider(_industry_api(sw_map))

    data = prov.get_industry_daily_returns_matrix(days=10)

    assert data["dates"] == TRADING_10          # 覆盖 ≥50% 口径仍取满 10 天
    assert data["pct_matrix"][0][-1] is None    # 银行缺 08-19
    assert all(v is not None for v in data["pct_matrix"][1])


def test_tushare_industry_matrix_not_connected():
    prov = _new_tushare_provider()
    prov.connected = False
    assert prov.get_industry_daily_returns_matrix(days=10) is None


def test_tushare_concept_matrix_same_rows_as_heat(monkeypatch):
    """概念矩阵：行集合 = 热度排序（与概念热度 TOP N 章节同口径），单元格来自逐日快照"""
    api = MagicMock()
    snapshot = pd.DataFrame([
        {"ts_code": "BK1", "name": "A概念", "pct_change": 5.0},
        {"ts_code": "BK2", "name": "B概念", "pct_change": 3.0},
    ])
    api.dc_index = MagicMock(return_value=snapshot)
    api.dc_daily = MagicMock(side_effect=lambda **kw: pd.DataFrame({
        "trade_date": TRADING_11,
        "close": _geo_closes(100, 0.02 if kw["ts_code"] == "BK1" else 0.01, 11),
        "vol": [100.0] * 11,
    }))
    prov = _new_tushare_provider(api)

    def fake_collect(days, today_df, today_date):
        def day_df(pct_a, pct_b):
            return pd.DataFrame([{"name": "A概念", "pct_change": pct_a},
                                 {"name": "B概念", "pct_change": pct_b}])
        return ({"20260818": day_df(1.0, -1.0), "20260819": day_df(2.0, 0.5)}, [])
    monkeypatch.setattr(prov, "_collect_concept_daily_snapshots", fake_collect)

    data = prov.get_concept_daily_returns_matrix(days=10, top_n=30)

    assert data["source"] == "tushare"
    assert data["dates"] == ["20260818", "20260819"]
    assert data["names"] == ["A概念", "B概念"]     # 热度排序（A 涨幅 2% > B 1%）
    assert data["pct_matrix"] == [[1.0, 2.0], [-1.0, 0.5]]


def test_tushare_concept_matrix_insufficient_days_none(monkeypatch):
    """快照不足 2 天 → 返回 None"""
    api = MagicMock()
    api.dc_index = MagicMock(return_value=pd.DataFrame(
        [{"ts_code": "BK1", "name": "A概念", "pct_change": 5.0}]))
    api.dc_daily = MagicMock(return_value=pd.DataFrame({
        "trade_date": TRADING_11, "close": _geo_closes(100, 0.02, 11),
        "vol": [100.0] * 11}))
    prov = _new_tushare_provider(api)
    monkeypatch.setattr(prov, "_collect_concept_daily_snapshots",
                        lambda days, today_df, today_date: ({"20260819": pd.DataFrame(
                            [{"name": "A概念", "pct_change": 1.0}])}, []))

    assert prov.get_concept_daily_returns_matrix(days=10, top_n=30) is None


# ==================== AKShare ====================

def _em_hist(dates, closes, date_as_str=False, vol=None):
    """构造 EM 风格日线 DataFrame（列名 日期/收盘/成交量）"""
    if date_as_str:
        date_vals = [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates]
    else:
        date_vals = pd.to_datetime(dates, format="%Y%m%d")
    cols = {"日期": date_vals, "收盘": closes}
    if vol is not None:
        cols["成交量"] = vol
    return pd.DataFrame(cols)


@pytest.fixture
def fake_ak(monkeypatch):
    """模块级 ak 替换：行业/概念名单 + 按 symbol 返回日线的 hist_em"""
    import types
    ak = types.SimpleNamespace()
    ak.stock_board_industry_name_em = MagicMock(
        return_value=pd.DataFrame({"板块名称": ["银行", "电子"]}))
    ak.stock_board_industry_hist_em = MagicMock()
    ak.stock_board_concept_name_em = MagicMock(
        return_value=pd.DataFrame({"概念名称": ["A概念", "B概念"]}))
    ak.stock_board_concept_hist_em = MagicMock()
    monkeypatch.setattr(akshare_provider, "ak", ak)
    monkeypatch.setattr(akshare_provider, "AKSHARE_AVAILABLE", True)
    return ak


def _new_akshare_provider():
    return akshare_provider.AKShareProvider.__new__(akshare_provider.AKShareProvider)


def test_ak_industry_matrix(fake_ak):
    fake_ak.stock_board_industry_hist_em.side_effect = (
        lambda symbol=None, **kw: {
            "银行": _em_hist(TRADING_11, _geo_closes(100, 0.02, 11)),
            "电子": _em_hist(TRADING_11, _geo_closes(100, -0.01, 11), True),
        }.get(symbol, pd.DataFrame()))

    data = _new_akshare_provider().get_industry_daily_returns_matrix(days=10)

    assert data["source"] == "akshare"
    assert data["dates"] == TRADING_10
    assert data["names"] == ["银行", "电子"]
    assert abs(data["pct_matrix"][0][0] - 2.0) < 1e-9


def test_ak_concept_matrix(fake_ak):
    fake_ak.stock_board_concept_hist_em.side_effect = (
        lambda symbol=None, **kw: {
            "A概念": _em_hist(TRADING_11, _geo_closes(100, 0.02, 11), vol=[100.0] * 11),
            "B概念": _em_hist(TRADING_11, _geo_closes(100, 0.01, 11), vol=[100.0] * 11),
        }.get(symbol, pd.DataFrame()))

    data = _new_akshare_provider().get_concept_daily_returns_matrix(days=10, top_n=30)

    assert data["source"] == "akshare"
    assert data["dates"] == TRADING_10
    assert data["names"] == ["A概念", "B概念"]     # 热度排序（A 2% > B 1%）
    assert abs(data["pct_matrix"][0][0] - 2.0) < 1e-9


# ==================== 基类契约 ====================

def test_base_provider_default_none():
    """结构化接口默认返回 None（不支持时 None/{} 约定）"""
    class _Concrete(BaseStockDataProvider):
        def get_stock_data(self, code, start_date, end_date):
            return ""

        def get_stock_info(self, code):
            return {}

    prov = _Concrete("test")
    assert prov.get_industry_daily_returns_matrix() is None
    assert prov.get_concept_daily_returns_matrix() is None
