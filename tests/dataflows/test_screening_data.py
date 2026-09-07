"""
单元测试：选股层数据接口（东财概念体系）

mock provider 内部 API 调用，验证：
- Tushare: dc_index 名单、dc_member 必传 trade_date、daily 涨幅计算、连续失败熔断
- AKShare: stock_board_concept_*_em 列名防御解析、未复权口径
"""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from AI.dataflows.providers.cn.tushare import TushareProvider
from AI.dataflows.providers.cn.akshare import AKShareProvider


# ==================== fixtures ====================

def _dc_index_df():
    return pd.DataFrame([
        {"ts_code": "BK1753.DC", "trade_date": "20260814", "name": "光刻胶",
         "idx_type": "概念板块", "pct_change": 1.82},
        {"ts_code": "BK0477.DC", "trade_date": "20260814", "name": "白酒",
         "idx_type": "概念板块", "pct_change": 0.5},
    ])


def _dc_member_df():
    return pd.DataFrame([
        {"trade_date": "20260814", "ts_code": "BK1753.DC", "con_code": "000422", "name": "湖北宜化"},
        {"trade_date": "20260814", "ts_code": "BK1753.DC", "con_code": "688550", "name": "瑞联新材"},
        {"trade_date": "20260814", "ts_code": "BK1753.DC", "con_code": "bad_code", "name": "脏数据"},
    ])


def _daily_df(n=11, close_base=100.0):
    """构造 n 行日线（收盘价线性递增），用于近 N 日涨幅计算。

    默认 11 行 = days(10) + 1，保证 tail(days+1) 取到完整窗口。
    """
    rows = []
    for i in range(n):
        rows.append({
            "trade_date": f"202608{str(i + 1).zfill(2)}",
            "close": close_base + i * 2.0,
            "amount": 1000000.0 + i * 10000.0,
        })
    return pd.DataFrame(rows)


def _new_tushare_provider(api=None):
    """绕过 __init__ 的连接逻辑，直接构造 mock provider"""
    prov = TushareProvider.__new__(TushareProvider)
    prov.name = "Tushare"
    prov.connected = True
    prov.api = api or MagicMock()
    return prov


def _new_akshare_provider():
    prov = AKShareProvider.__new__(AKShareProvider)
    prov.name = "AKShare"
    prov.connected = True
    return prov


@pytest.fixture(autouse=True)
def clear_dc_cache():
    """dc_index 快照是类级按日缓存，测试间必须清空防止交叉污染"""
    TushareProvider._DC_CONCEPT_INDEX_CACHE["date"] = None
    TushareProvider._DC_CONCEPT_INDEX_CACHE["df"] = None
    yield
    TushareProvider._DC_CONCEPT_INDEX_CACHE["date"] = None
    TushareProvider._DC_CONCEPT_INDEX_CACHE["df"] = None


# ==================== Tushare ====================

def test_tushare_get_concept_board_names():
    prov = _new_tushare_provider()
    prov._api_call = MagicMock(return_value=_dc_index_df())

    out = prov.get_concept_board_names()

    assert out == "光刻胶\n白酒"
    # 必须带 idx_type=概念板块 过滤参数
    kwargs = prov._api_call.call_args.kwargs
    assert kwargs["idx_type"] == "概念板块"
    assert "trade_date" in kwargs


def test_tushare_get_concept_board_names_retries_on_empty():
    """dc_index 首次返回空时向前重试"""
    prov = _new_tushare_provider()
    prov._api_call = MagicMock(side_effect=[pd.DataFrame(), _dc_index_df()])

    out = prov.get_concept_board_names()

    assert out == "光刻胶\n白酒"
    assert prov._api_call.call_count == 2


def test_tushare_get_sector_constituents_passes_trade_date():
    """dc_member 必须传 trade_date（不传返回历史累计成员）"""
    prov = _new_tushare_provider()
    prov._api_call = MagicMock(side_effect=[_dc_index_df(), _dc_member_df()])

    out = prov.get_sector_constituents("光刻胶")

    lines = out.split("\n")
    assert lines[0] == "000422|湖北宜化"
    assert lines[1] == "688550|瑞联新材"
    assert len(lines) == 2  # 非 6 位代码的脏数据被剔除
    # 第二次调用是 dc_member，必须带 trade_date
    mem_kwargs = prov._api_call.call_args_list[1].kwargs
    assert mem_kwargs["ts_code"] == "BK1753.DC"
    assert mem_kwargs["trade_date"] == "20260814"


def test_tushare_get_sector_constituents_not_found():
    prov = _new_tushare_provider()
    prov._api_call = MagicMock(return_value=_dc_index_df())

    out = prov.get_sector_constituents("不存在的板块")

    assert out == "未找到东财概念板块: 不存在的板块"


def test_dc_index_cache_within_day():
    """当日第二次调用命中缓存，不再请求 dc_index"""
    prov = _new_tushare_provider()
    prov._api_call = MagicMock(side_effect=[_dc_index_df(), _dc_member_df()])

    prov.get_concept_board_names()          # 第 1 次 dc_index
    out = prov.get_sector_constituents("光刻胶")  # 命中缓存，仅 dc_member

    assert out.split("\n")[0] == "000422|湖北宜化"
    assert prov._api_call.call_count == 2  # 无第二次 dc_index


def test_tushare_ranking_calculates_pct_and_mean():
    prov = _new_tushare_provider()
    prov._api_call = MagicMock(side_effect=[_daily_df(close_base=100.0),
                                            _daily_df(close_base=200.0)])

    out = prov.get_stocks_performance_ranking(["000422", "688550"], days=10)

    lines = out.split("\n")
    assert len(lines) == 3
    code1, pct1, close1, amount1 = lines[0].split("|")
    code2, pct2, close2, amount2 = lines[1].split("|")
    # 11 行窗口：第 1 行 close=100 → 第 11 行 close=120，涨幅 20%
    assert code1 == "000422.SZ"
    assert float(pct1) == pytest.approx(20.0)
    assert float(close1) == pytest.approx(120.0)
    # 第二只：200 → 220，涨幅 10%
    assert float(pct2) == pytest.approx(10.0)
    # 末行板块均值 = (20 + 10) / 2
    assert lines[2].startswith("板块均值|")
    assert float(lines[2].split("|")[1]) == pytest.approx(15.0)


def test_tushare_ranking_circuit_breaker():
    """连续 3 只失败 → 熔断中止"""
    prov = _new_tushare_provider()
    prov._api_call = MagicMock(return_value=None)

    out = prov.get_stocks_performance_ranking(
        ["000001", "000002", "000003", "000004", "000005"], days=10
    )

    assert out == "未获取到任何个股涨幅数据。"
    # 3 次失败即中止，不再请求后续
    assert prov._api_call.call_count == 3


# ==================== AKShare ====================

def test_akshare_get_concept_board_names(monkeypatch):
    import AI.dataflows.providers.cn.akshare as ak_prov
    fake_ak = MagicMock()
    fake_ak.stock_board_concept_name_em.return_value = pd.DataFrame(
        {"板块名称": ["白酒", "人工智能"]}
    )
    monkeypatch.setattr(ak_prov, "ak", fake_ak)

    out = _new_akshare_provider().get_concept_board_names()

    assert out == "白酒\n人工智能"


def test_akshare_get_sector_constituents(monkeypatch):
    import AI.dataflows.providers.cn.akshare as ak_prov
    fake_ak = MagicMock()
    fake_ak.stock_board_concept_cons_em.return_value = pd.DataFrame([
        {"序号": 1, "代码": "600519", "名称": "贵州茅台", "最新价": 1501.0},
        {"序号": 2, "代码": "000858", "名称": "五粮液", "最新价": 130.0},
    ])
    monkeypatch.setattr(ak_prov, "ak", fake_ak)

    out = _new_akshare_provider().get_sector_constituents("白酒")

    assert out == "600519|贵州茅台\n000858|五粮液"


def test_akshare_ranking_unadjusted(monkeypatch):
    """AKShare 排名用未复权日线（last_close 为真实市场价）"""
    import AI.dataflows.providers.cn.akshare as ak_prov
    fake_ak = MagicMock()
    fake_ak.stock_zh_a_hist.return_value = pd.DataFrame({
        "日期": [f"2026-08-{str(i + 1).zfill(2)}" for i in range(11)],
        "收盘": [100.0 + i * 2.0 for i in range(11)],
        "成交额": [1e6] * 11,
    })
    monkeypatch.setattr(ak_prov, "ak", fake_ak)

    out = _new_akshare_provider().get_stocks_performance_ranking(["600519"], days=10)

    lines = out.split("\n")
    code, pct, close, amount = lines[0].split("|")
    assert code == "600519"
    assert float(pct) == pytest.approx(20.0)
    assert float(close) == pytest.approx(120.0)
    assert lines[1].startswith("板块均值|")
    # 必须使用未复权口径
    kwargs = fake_ak.stock_zh_a_hist.call_args.kwargs
    assert kwargs["adjust"] == ""


def test_akshare_ranking_missing_close_column_fails_soft(monkeypatch):
    """无收盘价列 → 该只失败计数，最终无数据时安全返回"""
    import AI.dataflows.providers.cn.akshare as ak_prov
    fake_ak = MagicMock()
    fake_ak.stock_zh_a_hist.return_value = pd.DataFrame({"日期": ["2026-08-01"], "成交量": [1]})
    monkeypatch.setattr(ak_prov, "ak", fake_ak)

    out = _new_akshare_provider().get_stocks_performance_ranking(
        ["600519", "000001", "000002"], days=10
    )

    assert out == "未获取到任何个股涨幅数据。"
