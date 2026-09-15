"""
单元测试：TushareProvider store 结构化接口（方案 3.6）

- get_full_market_daily_df：daily / fund_daily 不同列序归一（含乱序/降序列、缺失列置 NaN）、
  仅传 trade_date 单日参数、未连接/异常 → None
- get_full_market_factor_df：标准列 [ts_code, trade_date, adj_factor]
- get_stock_basic_df / get_fund_basic_df：参数与透传
- get_concept_list_df：ths / dc / 未知 source
- get_concept_members_df：ths 必须 ts_code= 参数、dc 组合过滤 + trade_date 必传、
  6 位代码补交易所后缀
"""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from AI.dataflows.providers.cn.tushare import TushareProvider


def _new_provider(api=None):
    prov = TushareProvider.__new__(TushareProvider)
    prov.name = "Tushare"
    prov.connected = True
    prov.api = api or MagicMock()
    prov._api_call = lambda fn, *a, **kw: fn(*a, **kw)  # 直通（不经过线程池）
    return prov


STANDARD_DAILY_COLS = [
    "ts_code", "trade_date", "open", "high", "low", "close",
    "pre_close", "change", "pct_chg", "vol", "amount",
]


# ==================== get_full_market_daily_df ====================

def test_daily_normalizes_fund_daily_column_order():
    # 实测 fund_daily 列序：pre_close 在 open 前（方案 3.2.1）
    api = MagicMock()
    api.fund_daily.return_value = pd.DataFrame({
        "ts_code": ["159919.SZ"], "trade_date": ["20260828"],
        "pre_close": [1.9], "open": [1.95], "high": [2.0], "low": [1.93],
        "close": [1.99], "change": [0.09], "pct_chg": [4.7],
        "vol": [100.0], "amount": [200.0],
    })
    prov = _new_provider(api)
    df = prov.get_full_market_daily_df("2026-08-28", market="fund")
    assert list(df.columns) == STANDARD_DAILY_COLS
    api.fund_daily.assert_called_once_with(trade_date="20260828")
    # 只传 trade_date，无区间参数（禁止区间查询）
    _, kwargs = api.fund_daily.call_args
    assert set(kwargs) == {"trade_date"}


def test_daily_normalizes_shuffled_order_and_missing_cols():
    # 乱序列 + 缺失列（pct_chg 缺）→ 缺失列置 NaN
    api = MagicMock()
    api.daily.return_value = pd.DataFrame({
        "trade_date": ["20260828"], "close": [11.4], "ts_code": ["000001.SZ"],
        "vol": [100.0], "high": [11.5], "amount": [1000.0],
        "open": [11.0], "low": [11.0],
    })
    prov = _new_provider(api)
    df = prov.get_full_market_daily_df("2026-08-28", market="stock")
    assert list(df.columns) == STANDARD_DAILY_COLS
    assert df["ts_code"].iloc[0] == "000001.SZ"
    assert df["close"].iloc[0] == 11.4
    assert pd.isna(df["pct_chg"].iloc[0])


def test_daily_unknown_market_and_not_connected_return_none():
    prov = _new_provider()
    assert prov.get_full_market_daily_df("20260828", market="hk") is None
    prov.connected = False
    assert prov.get_full_market_daily_df("20260828") is None


def test_daily_exception_returns_none():
    api = MagicMock()
    api.daily.side_effect = RuntimeError("boom")
    assert _new_provider(api).get_full_market_daily_df("20260828") is None


def test_daily_none_result_propagates():
    api = MagicMock()
    api.daily.return_value = None
    assert _new_provider(api).get_full_market_daily_df("20260828") is None


# ==================== get_full_market_factor_df ====================

def test_factor_normalizes_columns():
    api = MagicMock()
    api.fund_adj.return_value = pd.DataFrame({
        "adj_factor": [1.05], "trade_date": ["20260828"], "ts_code": ["159919.SZ"],
    })
    prov = _new_provider(api)
    df = prov.get_full_market_factor_df("2026-08-28", market="fund")
    assert list(df.columns) == ["ts_code", "trade_date", "adj_factor"]
    assert df["adj_factor"].iloc[0] == 1.05
    _, kwargs = api.fund_adj.call_args
    assert set(kwargs) == {"trade_date"}


def test_factor_unknown_market_returns_none():
    assert _new_provider().get_full_market_factor_df("20260828", market="xx") is None


# ==================== get_stock_basic_df / get_fund_basic_df ====================

def test_stock_basic_passes_fields_and_normalizes():
    api = MagicMock()
    api.stock_basic.return_value = pd.DataFrame({
        "ts_code": ["000001.SZ"], "name": ["平安银行"], "market": ["主板"],
        "exchange": ["SZSE"], "industry": ["银行"], "area": ["深圳"],
        "list_status": ["L"], "list_date": ["19910403"], "delist_date": [None],
    })
    prov = _new_provider(api)
    df = prov.get_stock_basic_df()
    assert list(df.columns) == [
        "ts_code", "name", "market", "exchange", "industry", "area",
        "list_status", "list_date", "delist_date",
    ]
    # 全量拉取：不传 list_status（含退市 D/暂停 P）
    _, kwargs = api.stock_basic.call_args
    assert "list_status" not in kwargs
    assert "fields" in kwargs


def test_fund_basic_passthrough_market_e():
    api = MagicMock()
    api.fund_basic.return_value = pd.DataFrame({"ts_code": ["158013.SZ"],
                                                "m_fee": [0.15]})
    prov = _new_provider(api)
    df = prov.get_fund_basic_df()
    assert df["ts_code"].iloc[0] == "158013.SZ"
    api.fund_basic.assert_called_once_with(market="E")


# ==================== get_concept_list_df ====================

def test_concept_list_ths():
    api = MagicMock()
    api.ths_index.return_value = pd.DataFrame({
        "ts_code": ["883300.TI"], "name": ["沪深300样本股"], "count": [300],
        "exchange": ["A"], "list_date": ["20100413"], "type": ["N"],
    })
    prov = _new_provider(api)
    df = prov.get_concept_list_df("ths")
    assert list(df.columns) == ["ts_code", "name", "count", "exchange",
                                "list_date", "type"]
    api.ths_index.assert_called_once_with(type="N")


def test_concept_list_dc_normalizes_missing_cols():
    api = MagicMock()
    api.dc_index.return_value = pd.DataFrame({
        "ts_code": ["BK1753"], "name": ["光刻胶"],
    })
    prov = _new_provider(api)
    df = prov.get_concept_list_df("dc")
    assert list(df.columns) == ["ts_code", "name", "count", "exchange",
                                "list_date", "type"]
    assert df["name"].iloc[0] == "光刻胶"
    assert df["count"].isna().all() and df["list_date"].isna().all()
    _, kwargs = api.dc_index.call_args
    assert kwargs["idx_type"] == "概念板块"
    assert "trade_date" in kwargs


def test_concept_list_unknown_source_returns_none():
    assert _new_provider().get_concept_list_df("xx") is None


# ==================== get_concept_members_df ====================

def test_members_ths_uses_ts_code_param_and_adds_suffix():
    api = MagicMock()
    api.ths_member.return_value = pd.DataFrame({
        "con_code": ["000001", "600000", "920992", "430047"],
        "con_name": ["平安银行", "浦发银行", "920992", "430047"],
    })
    prov = _new_provider(api)
    df = prov.get_concept_members_df("883300.TI", source="ths")
    assert list(df.columns) == ["sector_code", "ts_code"]
    # 必须用 ts_code= 参数（代理端点忽略 code= 参数，方案 3.2.1 实测）
    _, kwargs = api.ths_member.call_args
    assert kwargs == {"ts_code": "883300.TI"}
    # con_name 丢弃（决策 12）；6 位代码补交易所后缀；sector_code = 概念代码
    assert df["ts_code"].tolist() == ["000001.SZ", "600000.SH",
                                      "920992.BJ", "430047.BJ"]
    assert (df["sector_code"] == "883300.TI").all()


def test_members_dc_requires_trade_date_combo_filter():
    api = MagicMock()
    api.dc_member.return_value = pd.DataFrame({"con_code": ["301630"]})
    prov = _new_provider(api)
    df = prov.get_concept_members_df("BK1753", source="dc",
                                     trade_date="2026-08-28")
    assert df["ts_code"].tolist() == ["301630.SZ"]
    _, kwargs = api.dc_member.call_args
    assert kwargs == {"ts_code": "BK1753", "trade_date": "20260828"}


def test_members_dc_without_trade_date_returns_none():
    prov = _new_provider()
    assert prov.get_concept_members_df("BK1753", source="dc") is None


def test_members_unknown_source_and_not_connected_return_none():
    prov = _new_provider()
    assert prov.get_concept_members_df("BK1753", source="xx") is None
    prov.connected = False
    assert prov.get_concept_members_df("BK1753", source="ths") is None


def test_members_empty_or_missing_con_code():
    api = MagicMock()
    api.ths_member.return_value = pd.DataFrame()   # 空 DataFrame 原样返回（采集层记 failed）
    assert _new_provider(api).get_concept_members_df("X", "ths").empty

    api2 = MagicMock()
    api2.ths_member.return_value = pd.DataFrame({"name": ["无 con_code 列"]})
    assert _new_provider(api2).get_concept_members_df("X", "ths") is None


def test_members_drops_nan_con_code():
    api = MagicMock()
    api.ths_member.return_value = pd.DataFrame(
        {"con_code": ["000001", None], "con_name": ["平安银行", None]})
    df = _new_provider(api).get_concept_members_df("883300.TI", "ths")
    assert df["ts_code"].tolist() == ["000001.SZ"]


def test_members_drops_non_a_share_codes():
    # 实测 ths 跨市场概念含境外标的（美股 .O/.N、港股 .HK）——V1 范围外 drop
    api = MagicMock()
    api.ths_member.return_value = pd.DataFrame({
        "con_code": ["000001", "AAPL.O", "0700.HK", "NVDA.O", "600000",
                     "BABA.N", "920992", "乱七八糟"],
        "con_name": ["平安银行", "苹果", "腾讯", "英伟达", "浦发银行",
                     "阿里", "920992", "乱码"],
    })
    df = _new_provider(api).get_concept_members_df("883300.TI", "ths")
    assert df["ts_code"].tolist() == ["000001.SZ", "600000.SH", "920992.BJ"]


def test_members_keeps_suffixed_a_share_codes():
    # 已带 A 股后缀的代码原样保留（含 B 股 900/200，存储无害，方案 2.1）
    api = MagicMock()
    api.dc_member.return_value = pd.DataFrame(
        {"con_code": ["301630.SZ", "900942.SH", "200553.SZ"]})
    df = _new_provider(api).get_concept_members_df(
        "BK1753", source="dc", trade_date="2026-08-28")
    assert df["ts_code"].tolist() == ["301630.SZ", "900942.SH", "200553.SZ"]


def test_members_deduplicates_repeated_con_code():
    # 实测 dc_member 响应含重复 con_code——同批次重复 PK 会报
    # ON CONFLICT DO UPDATE cannot affect row a second time，必须去重
    api = MagicMock()
    api.dc_member.return_value = pd.DataFrame(
        {"con_code": ["301630", "301630", "300750"],
         "name": ["同宇新材", "同宇新材", "宁德时代"]})
    df = _new_provider(api).get_concept_members_df(
        "BK1753", source="dc", trade_date="2026-08-28")
    assert df["ts_code"].tolist() == ["301630.SZ", "300750.SZ"]


# ==================== get_sector_daily_df ====================

def test_sector_daily_dc_calls_dc_daily_and_sorts_asc():
    api = MagicMock()
    api.dc_daily.return_value = pd.DataFrame({
        "ts_code": ["BK1753.DC"] * 2,
        "trade_date": ["20260911", "20260910"],   # 降序输入（端点实测行序）
        "close": [1041.38, 986.1],
        "open": [1047.52, 993.08],
        "high": [1049.0, 994.63],
        "low": [1013.3, 953.47],
        "change": [-18.45, -13.9],
        "pct_change": [-1.74, -1.39],
        "vol": [6888712.0, 13305932.0],
        "amount": [21604826788.0, 43232400230.0],
        "swing": [3.37, 4.12],
        "turnover_rate": [2.84, 5.93],
        "category": ["概念板块", "概念板块"],
    })
    prov = _new_provider(api)
    df = prov.get_sector_daily_df("dc", "BK1753.DC", "20260801", "20260913")

    api.dc_daily.assert_called_once_with(
        ts_code="BK1753.DC", start_date="20260801", end_date="20260913",
        idx_type="概念板块")
    assert list(df["trade_date"]) == ["20260910", "20260911"]   # 升序归一
    assert df.index.tolist() == [0, 1]                          # reset_index


def test_sector_daily_ths_deferred_and_unknown_source():
    prov = _new_provider(MagicMock())
    assert prov.get_sector_daily_df("ths", "883300.TI", "20100101", "20260913") is None
    assert prov.get_sector_daily_df("xx", "BK1753.DC", "20260801", "20260913") is None


def test_sector_daily_none_response():
    api = MagicMock()
    api.dc_daily.return_value = None
    prov = _new_provider(api)
    assert prov.get_sector_daily_df("dc", "BK1753.DC", "20260801", "20260913") is None
