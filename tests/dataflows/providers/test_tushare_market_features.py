"""
单元测试：T5 市场特征层 Provider 侧（8 个结构化接口）

零网络：api 用 MagicMock，`_api_call` 直通（绕过线程池），交易日历/区间拉取按需打桩。
覆盖：
- 基类契约（规则 8）：默认返回 None（绝不返回 `_not_supported()` 字符串）
- 能力探测 `interface.market_dataset_support()`（覆写检测，零 API 调用）
- Tushare 覆写：指数序列形状、两融单日聚合与“新交易日 0 行”缺日语义、
  估值分位序列（按 curr_date 截断）、流动性（缺失写入 missing）、
  规则交割日历/长假窗口
- 纯工具：`_trim_series` / `_module_frame_entry` / `_paged_range_fetch` 去重排序
"""

from unittest.mock import MagicMock

import pandas as pd
import pytest

from AI.dataflows import interface as iface
from AI.dataflows import market_features as mf
from AI.dataflows.providers.base_provider import BaseStockDataProvider
from AI.dataflows.providers.cn.tushare import TushareProvider

CURR = "2026-09-11"


def _new_provider(api=None):
    prov = TushareProvider.__new__(TushareProvider)
    prov.name = "Tushare"
    prov.connected = True
    prov.api = api or MagicMock()
    prov._api_call = lambda fn, *a, **kw: fn(*a, **kw)  # 直通（不经过线程池）
    return prov


# ==================== 基类契约 ====================

@pytest.mark.parametrize("method,args", [
    ("get_market_index_features", (CURR,)),
    ("get_market_breadth_history", (CURR,)),
    ("get_market_fund_flow_history", (CURR,)),
    ("get_margin_trading_history", (CURR,)),
    ("get_market_valuation", (CURR,)),
    ("get_cn_liquidity_indicators", (CURR,)),
    ("get_cn_event_calendar", (CURR,)),
    ("get_global_risk_indicators", (CURR,)),
])
def test_base_provider_returns_none(method, args):
    """规则 8：基类默认 None（结构化消费方按缺失处理，不解析字符串）。"""

    class _Dummy:
        name = "Dummy"

    result = getattr(BaseStockDataProvider, method)(_Dummy(), *args)
    assert result is None
    assert not isinstance(result, str)


def test_market_dataset_support_covers_all_datasets(monkeypatch):
    support = iface.market_dataset_support()
    assert set(support) == set(mf.MARKET_DATASETS)
    assert all(isinstance(v, bool) for v in support.values())


def test_market_dataset_support_tushare_all_true(monkeypatch):
    monkeypatch.setattr(iface, "_get_data_source", lambda: "tushare")
    support = iface.market_dataset_support()
    assert all(support.values()), support


def test_market_dataset_support_akshare_none(monkeypatch):
    """AKShare 未覆写 8 接口 → 全部 False（能力探测不臆造）。"""
    monkeypatch.setattr(iface, "_get_data_source", lambda: "akshare")
    support = iface.market_dataset_support()
    assert not any(support.values()), support


def test_interface_dispatch_uses_provider_method(monkeypatch):
    """interface 层 hasattr 动态派发：有实现则透传，无实现返回 None。"""
    monkeypatch.setattr(iface, "_correct_trade_date",
                        lambda curr, caller=None: curr)

    class _With:
        def get_margin_trading_history(self, curr_date, days=20):
            return {"as_of_date": curr_date, "days": days, "series": [],
                    "missing": {}, "notes": ["stub"]}

    class _Without:
        pass

    monkeypatch.setattr(iface, "_get_provider", lambda: _With())
    assert iface.get_margin_trading_history(CURR)["notes"] == ["stub"]
    monkeypatch.setattr(iface, "_get_provider", lambda: _Without())
    assert iface.get_margin_trading_history(CURR) is None


# ==================== 漂移守卫（provider 常量 ↔ 特征层常量） ====================

def test_index_universe_matches_feature_layer():
    assert set(TushareProvider._MARKET_INDEX_CODES) == set(mf.INDEX_UNIVERSE)
    style_codes = {code for pair in mf.STYLE_PAIRS.values() for code in pair[:2]}
    assert style_codes <= set(TushareProvider._MARKET_INDEX_CODES)


def test_valuation_and_global_universes_sane():
    assert "000300.SH" in TushareProvider._VALUATION_INDEX_CODES
    assert set(TushareProvider._VALUATION_INDEX_CODES) <= set(mf.INDEX_UNIVERSE) | {
        "000300.SH", "000905.SH"}
    assert TushareProvider._GLOBAL_INDEX_CODES and TushareProvider._COMMODITY_CODES
    # 全球风险核心项所需代码（`global_risk_assessment` 证据链）
    assert {"SPX", "IXIC"} & set(TushareProvider._GLOBAL_INDEX_CODES)
    assert {"SC.INE", "AU.SHF", "CU.SHF"} == set(TushareProvider._COMMODITY_CODES)


# ==================== M9：代理端点静默截断（≥6000 行） ====================

def _all_market_frame(rows):
    """全市场单日样本帧（行数可控，用于触发截断阈值）。"""
    return pd.DataFrame({
        "ts_code": [f"{600000 + i}.SH" for i in range(rows)],
        "pct_chg": [1.0] * rows,
        "amount": [1.0e5] * rows,
    })


def test_truncation_threshold_matches_store_constant():
    """provider 常量与 db.instrument.ingest.frames 同值（常量为重复定义，防漂移；评审 M9）。"""
    from db.instrument.ingest.frames import TRUNCATION_ROWS
    assert TushareProvider._TRUNCATION_ROWS == TRUNCATION_ROWS == 6000


def test_truncated_rows_threshold_boundary():
    """`_truncated_rows` 边界：<6000 → 0；=6000 → 行数；None/空 → 0（评审 M9）。"""
    assert TushareProvider._truncated_rows(None) == 0
    assert TushareProvider._truncated_rows(pd.DataFrame()) == 0
    assert TushareProvider._truncated_rows(_all_market_frame(5999)) == 0
    assert TushareProvider._truncated_rows(_all_market_frame(6000)) == 6000


def test_market_breadth_truncated_day_dropped_and_recorded(monkeypatch):
    """单日全市场日线 ≥6000 行 = 代理端点静默截断 → 丢弃该日宽度/溢价统计（评审 M9）。

    按残缺样本算出的上涨占比/成交额看似正常实则偏误，必须降级为缺失并
    把截断事实写入 missing/notes（→ 接口 data_quality）。
    """
    api = MagicMock()
    api.daily.side_effect = lambda trade_date=None: _all_market_frame(6000)
    api.limit_list_d.side_effect = lambda trade_date=None: None
    prov = _new_provider(api)
    monkeypatch.setattr(prov, "_recent_trade_dates",
                        lambda days, end_date: ["2026-09-10"])

    data = prov.get_market_breadth_history(CURR, days=1)
    row = data["series"][0]
    assert row["up"] is None and row["up_ratio"] is None
    assert row["market_amount"] is None
    assert "截断" in data["missing"]["daily"]
    assert any("截断降级" in note for note in data["notes"])


def test_market_fund_flow_truncated_day_dropped_and_recorded(monkeypatch):
    """moneyflow 单日 ≥6000 行 → 该日主力净额降级为缺失并记录截断（评审 M9）。"""
    api = MagicMock()
    api.moneyflow.side_effect = lambda trade_date=None: _all_market_frame(6000)
    api.moneyflow_hsgt.side_effect = lambda trade_date=None: None
    prov = _new_provider(api)
    monkeypatch.setattr(prov, "_recent_trade_dates",
                        lambda days, end_date: ["2026-09-10"])

    data = prov.get_market_fund_flow_history(CURR, days=1)
    assert data["series"][0]["main_net_amount"] is None
    assert "截断" in data["missing"]["moneyflow"]
    assert any("截断降级" in note for note in data["notes"])


def test_market_valuation_truncated_snapshot_day_skipped(monkeypatch):
    """daily_basic 单日 ≥6000 行 → 该日中位数不可用，回退更早交易日（评审 M9）。"""
    api = MagicMock()
    api.index_dailybasic.side_effect = lambda ts_code=None, start_date=None, end_date=None: (
        pd.DataFrame({"trade_date": ["2026-09-09"], "pe_ttm": [12.0], "pb": [1.2]}))

    def daily_basic(trade_date=None, fields=None):
        if trade_date == "20260910":
            return _all_market_frame(6001)      # 截断日：不得用于中位数
        return pd.DataFrame({"ts_code": ["a", "b"], "pe_ttm": [10.0, 20.0],
                             "pb": [1.0, 3.0]})

    api.daily_basic.side_effect = daily_basic
    prov = _new_provider(api)
    monkeypatch.setattr(prov, "_VALUATION_INDEX_CODES", ("000300.SH",))
    monkeypatch.setattr(prov, "_recent_trade_dates",
                        lambda days, end_date: ["2026-09-09", "2026-09-10"])

    data = prov.get_market_valuation(CURR)
    assert data["all_a_snapshot"]["trade_date"] == "2026-09-09"   # 回退更早交易日
    assert data["all_a_snapshot"]["pe_ttm_median"] == pytest.approx(15.0)
    assert "all_a_snapshot_truncated" in data["missing"]
    assert "截断" in data["missing"]["all_a_snapshot_truncated"]


# ==================== 1) 指数特征 ====================

def _index_frame(rows=30, end="2026-09-10"):
    dates = pd.bdate_range(end=end, periods=rows).strftime("%Y-%m-%d")
    step = range(1, rows + 1)
    return pd.DataFrame({
        "trade_date": dates,
        "open": [100.0 + i for i in step],
        "high": [101.0 + i for i in step],
        "low": [99.0 + i for i in step],
        "close": [100.5 + i for i in step],
        "vol": [1.0e6] * rows,
        "amount": [2.0e6] * rows,
    })


def test_get_market_index_features_shape(monkeypatch):
    prov = _new_provider()
    monkeypatch.setattr(prov, "get_index_data_df",
                        lambda code, start, end: _index_frame() if code == "000001.SH"
                        else None)
    data = prov.get_market_index_features(CURR, lookbacks=(5, 20))
    assert data["as_of_date"] == "2026-09-10"
    assert data["requested_date"] == CURR
    assert data["source"] == "tushare:index_daily"
    assert data["lookbacks"] == [5, 20]
    assert list(data["indices"]) == ["000001.SH"]
    entry = data["indices"]["000001.SH"]
    assert entry["rows"] == 25  # 保留 max(lookbacks)+5 个样本
    assert entry["trade_dates"] == sorted(entry["trade_dates"])
    assert entry["close"][-1] == pytest.approx(130.5)
    assert entry["first_date"] < entry["last_date"]
    assert len(data["missing"]) == len(TushareProvider._MARKET_INDEX_CODES) - 1


def test_get_market_index_features_tail_and_future_guard(monkeypatch):
    """序列截断到 max(lookbacks)+5，且不得包含晚于 curr_date 的行。"""
    prov = _new_provider()
    df = _index_frame(rows=30, end="2026-09-18")  # 含晚于 CURR 的行
    monkeypatch.setattr(prov, "get_index_data_df", lambda code, start, end: df)
    monkeypatch.setattr(prov, "_MARKET_INDEX_CODES", ("000001.SH",))
    data = prov.get_market_index_features(CURR, lookbacks=(5, 20))
    entry = data["indices"]["000001.SH"]
    assert data["as_of_date"] == CURR
    assert entry["rows"] == 25  # max(20)+5
    assert max(entry["trade_dates"]) == CURR


def test_get_market_index_features_all_missing_returns_none(monkeypatch):
    prov = _new_provider()
    monkeypatch.setattr(prov, "get_index_data_df", lambda code, start, end: None)
    assert prov.get_market_index_features(CURR) is None


def test_get_market_index_features_not_connected():
    prov = _new_provider()
    prov.connected = False
    assert prov.get_market_index_features(CURR) is None


# ==================== 2) 两融历史 ====================

def test_get_margin_trading_history_aggregates_daily(monkeypatch):
    api = MagicMock()
    frames = {}
    for d, rzye in (("20260908", 1.50e12), ("20260909", 1.52e12),
                    ("20260910", 1.55e12)):
        frames[d] = pd.DataFrame({"rzye": [rzye / 2, rzye / 2, 0.0],
                                  "rqye": [1.0e10, 1.0e10, 0.0]})
    api.margin.side_effect = lambda trade_date=None: frames.get(trade_date)
    prov = _new_provider(api)
    monkeypatch.setattr(prov, "_recent_trade_dates",
                        lambda days, end_date: ["2026-09-08", "2026-09-09", "2026-09-10"])

    data = prov.get_margin_trading_history(CURR, days=3)
    assert data["as_of_date"] == "2026-09-10"
    assert data["days"] == 3
    assert [r["trade_date"] for r in data["series"]] == [
        "2026-09-08", "2026-09-09", "2026-09-10"]
    assert data["series"][-1]["rzye"] == pytest.approx(1.55e12)
    assert data["series"][-1]["rows"] == 3
    assert not data["missing"]


def test_get_margin_trading_history_skips_unloaded_day(monkeypatch):
    """新交易日入库前 0 行 → 跳过该日并记 missing（不视为故障）。"""
    api = MagicMock()
    ok = pd.DataFrame({"rzye": [1.0e12], "rqye": [1.0e10]})
    api.margin.side_effect = lambda trade_date=None: (
        ok if trade_date == "20260909" else pd.DataFrame())
    prov = _new_provider(api)
    monkeypatch.setattr(prov, "_recent_trade_dates",
                        lambda days, end_date: ["2026-09-08", "2026-09-09"])

    data = prov.get_margin_trading_history(CURR, days=2)
    assert [r["trade_date"] for r in data["series"]] == ["2026-09-09"]
    assert "margin" in data["missing"]
    assert data["as_of_date"] == "2026-09-09"


def test_get_margin_trading_history_all_empty_returns_none(monkeypatch):
    api = MagicMock()
    api.margin.side_effect = lambda trade_date=None: None
    prov = _new_provider(api)
    monkeypatch.setattr(prov, "_recent_trade_dates", lambda days, end_date: ["2026-09-09"])
    assert prov.get_margin_trading_history(CURR, days=1) is None


# ==================== 3) 估值 ====================

def test_get_market_valuation_truncates_and_stores_series(monkeypatch):
    api = MagicMock()
    api.index_dailybasic.side_effect = lambda ts_code=None, start_date=None, end_date=None: (
        pd.DataFrame({
            "trade_date": ["2026-09-09", "2026-09-18"],  # 第二条晚于 curr
            "pe_ttm": [12.0, 99.0],
            "pb": [1.2, 9.9],
        }) if ts_code == "000300.SH" else None)
    api.daily_basic.side_effect = lambda trade_date=None, fields=None: pd.DataFrame(
        {"ts_code": ["a", "b"], "pe_ttm": [10.0, 20.0], "pb": [1.0, 3.0]})
    prov = _new_provider(api)
    monkeypatch.setattr(prov, "_VALUATION_INDEX_CODES", ("000300.SH",))
    monkeypatch.setattr(prov, "_recent_trade_dates", lambda days, end_date: ["2026-09-10"])

    data = prov.get_market_valuation(CURR)
    entry = data["index_valuation"]["000300.SH"]
    assert entry["trade_dates"] == ["2026-09-09"]
    assert entry["pe_ttm"] == [12.0]
    assert entry["last_date"] == "2026-09-09"
    assert data["all_a_snapshot"]["trade_date"] == "2026-09-10"
    assert data["all_a_snapshot"]["pe_ttm_median"] == pytest.approx(15.0)
    assert data["all_a_snapshot"]["rows"] == 2
    assert data["as_of_date"] == "2026-09-10"


def test_get_market_valuation_missing_all_a_still_returns_index(monkeypatch):
    api = MagicMock()
    api.index_dailybasic.side_effect = lambda ts_code=None, start_date=None, end_date=None: (
        pd.DataFrame({"trade_date": ["2026-09-09"], "pe_ttm": [12.0], "pb": [1.2]}))
    api.daily_basic.side_effect = lambda trade_date=None, fields=None: pd.DataFrame()
    prov = _new_provider(api)
    monkeypatch.setattr(prov, "_VALUATION_INDEX_CODES", ("000300.SH",))
    monkeypatch.setattr(prov, "_recent_trade_dates", lambda days, end_date: ["2026-09-10"])
    data = prov.get_market_valuation(CURR)
    assert data["all_a_snapshot"] is None
    assert "all_a_snapshot" in data["missing"]


def test_get_market_valuation_all_missing_returns_none(monkeypatch):
    api = MagicMock()
    api.index_dailybasic.side_effect = lambda ts_code=None, start_date=None, end_date=None: None
    api.daily_basic.side_effect = lambda trade_date=None, fields=None: None
    prov = _new_provider(api)
    monkeypatch.setattr(prov, "_VALUATION_INDEX_CODES", ("000300.SH",))
    monkeypatch.setattr(prov, "_recent_trade_dates", lambda days, end_date: ["2026-09-10"])
    assert prov.get_market_valuation(CURR) is None


# ==================== 4) 流动性 ====================

def test_get_cn_liquidity_indicators_shapes(monkeypatch):
    api = MagicMock()
    api.shibor.side_effect = lambda start_date=None, end_date=None: pd.DataFrame({
        "date": ["2026-09-08", "2026-09-09", "2026-09-18"],
        "on": [1.30, 1.31, 1.99], "1w": [1.45, 1.50, 1.99],
        "1m": [1.60, 1.61, 1.99], "3m": [1.70, 1.71, 1.99], "1y": [1.90, 1.91, 1.99],
    })
    api.shibor_lpr.side_effect = lambda start_date=None, end_date=None: pd.DataFrame({
        "date": ["2026-07-20"], "1y": [3.0], "5y": [3.5]})
    api.cn_m.side_effect = lambda start_date=None, end_date=None: pd.DataFrame({
        "month": ["202608"], "m1_yoy": [4.6], "m2_yoy": [6.2],
        "m1_mom": [0.1], "m2_mom": [0.2]})
    prov = _new_provider(api)

    data = prov.get_cn_liquidity_indicators(CURR, days=20)
    assert data["shibor"]["trade_dates"] == ["2026-09-08", "2026-09-09"]
    assert data["shibor"]["1w"] == [1.45, 1.50]
    assert data["as_of_date"] == "2026-09-09"
    assert data["lpr"]["1y"] == [3.0]
    assert data["money_supply"]["months"] == ["202608"]
    assert not data["missing"]
    # 无接口能力显式降级（非静默）
    assert any("10Y" in note for note in data["notes"])
    assert any("DR007" in note for note in data["notes"])


def test_get_cn_liquidity_indicators_missing_recorded(monkeypatch):
    api = MagicMock()
    api.shibor.side_effect = lambda start_date=None, end_date=None: None
    api.shibor_lpr.side_effect = lambda start_date=None, end_date=None: pd.DataFrame()
    api.cn_m.side_effect = lambda start_date=None, end_date=None: pd.DataFrame({
        "month": ["202608"], "m1_yoy": [4.6], "m2_yoy": [6.2],
        "m1_mom": [0.1], "m2_mom": [0.2]})
    prov = _new_provider(api)
    data = prov.get_cn_liquidity_indicators(CURR)
    assert set(data["missing"]) == {"shibor", "lpr"}
    assert data["shibor"] == {} and data["lpr"] == {}
    assert data["as_of_date"] == "202608"  # 回退到月度数据截止


# ==================== 4b) 资金日历（IPO/解禁/交割/长假） ====================

def test_get_cn_event_calendar_shapes(monkeypatch):
    api = MagicMock()
    api.new_share.side_effect = lambda start_date=None, end_date=None: pd.DataFrame({
        "ts_code": ["A.SH"], "name": ["甲"], "ipo_date": ["20260916"],
        "issue_date": ["20260920"], "price": [10.0], "amount": [1000.0],
        "market": ["主板"], "funds": [None], "market_amount": [None]})
    api.share_float.side_effect = lambda start_date=None, end_date=None: pd.DataFrame({
        "ts_code": ["B.SH"], "name": ["乙"], "float_date": ["20260925"],
        "float_share": [1000.0], "float_ratio": [0.15], "share_type": ["首发原股东"]})
    prov = _new_provider(api)
    cal = [d.strftime("%Y-%m-%d")
           for d in pd.bdate_range(start="2026-09-11", periods=80)]
    monkeypatch.setattr(prov, "_trade_dates_in_range", lambda start, end: cal)

    data = prov.get_cn_event_calendar(CURR, windows=(5, 20, 60))
    assert data["as_of_date"] == CURR
    assert data["windows"] == [5, 20, 60]
    assert data["window_end"] == cal[60]
    ipo = data["ipo"][0]
    assert ipo["subscribe_date"] == "2026-09-16"  # ipo_date 优先
    assert ipo["list_date"] == "2026-09-20"
    # price(元/股) × amount(万股) / 1e4 = 亿元
    assert ipo["market_amount"] == pytest.approx(10.0 * 1000.0 / 1e4)
    unlock = data["unlocks"][0]
    assert unlock["float_date"] == "2026-09-25"
    assert unlock["float_ratio"] == pytest.approx(0.15)
    assert data["expiry"] and all(e["date"] >= CURR for e in data["expiry"])
    assert data["macro_releases"] == []
    assert not data["missing"]
    assert any("规则推导" in note for note in data["notes"])


def test_get_cn_event_calendar_missing_sources(monkeypatch):
    api = MagicMock()
    api.new_share.side_effect = lambda start_date=None, end_date=None: pd.DataFrame()
    api.share_float.side_effect = lambda start_date=None, end_date=None: None
    prov = _new_provider(api)
    cal = [d.strftime("%Y-%m-%d")
           for d in pd.bdate_range(start="2026-09-11", periods=80)]
    monkeypatch.setattr(prov, "_trade_dates_in_range", lambda start, end: cal)
    data = prov.get_cn_event_calendar(CURR)
    assert data["ipo"] == [] and data["unlocks"] == []
    assert set(data["missing"]) == {"ipo", "unlock"}
    assert data["expiry"]  # 规则交割日历不依赖接口


# ==================== 5) 规则交割日历 / 长假窗口 ====================

def test_third_friday_and_fourth_wednesday():
    assert TushareProvider._third_friday(2026, 9).strftime("%Y-%m-%d") == "2026-09-18"
    assert TushareProvider._fourth_wednesday(2026, 9).strftime("%Y-%m-%d") == "2026-09-23"


def test_rule_based_expiry_calendar_ascending_future_only(monkeypatch):
    prov = _new_provider()
    out = prov._rule_based_expiry_calendar("2026-09-11", months=3)
    dates = [item["date"] for item in out]
    assert dates == sorted(dates)
    assert all(d >= "2026-09-11" for d in dates)
    assert dates[:2] == ["2026-09-18", "2026-09-23"]  # 第三周五 / 第四周三
    assert any("股指期货" in item["kind"] for item in out)


def test_holiday_windows_from_calendar_gap(monkeypatch):
    prov = _new_provider()
    monkeypatch.setattr(prov, "_trade_dates_in_range",
                        lambda start, end: ["2026-09-30", "2026-10-09"])
    windows = prov._holiday_windows("20260930", "20261020")
    assert windows == [{"start": "2026-10-01", "end": "2026-10-08", "days": 8}]


# ==================== 6) 纯工具 ====================

def test_series_to_list_and_to_num_nan_handling():
    assert TushareProvider._series_to_list([1.0, float("nan"), "x", None]) == [
        1.0, None, None, None]
    assert TushareProvider._to_num("nan") is None
    assert TushareProvider._to_num(None) is None
    assert TushareProvider._to_num("1.5") == pytest.approx(1.5)
    assert TushareProvider._norm_date_value("20260910") == "2026-09-10"
    assert TushareProvider._norm_date_value(float("nan")) is None


def test_trim_series_truncates_to_end_and_need():
    series = {"trade_dates": ["2026-09-08", "2026-09-09", "2026-09-18"],
              "field": "y10", "y10": [1.0, 2.0, 3.0]}
    out = TushareProvider._trim_series(series, need=5, end_dash="2026-09-09")
    assert out["trade_dates"] == ["2026-09-08", "2026-09-09"]
    assert out["y10"] == [1.0, 2.0]
    assert TushareProvider._trim_series({}, 5, "2026-09-09") == {}


def test_module_frame_entry_field_and_units():
    prov = _new_provider()
    df = pd.DataFrame({"trade_date": ["2026-09-09", "2026-09-18"],
                       "close": [100.0, 200.0]})
    entry = prov._module_frame_entry(df, "SC.INE", "fut_daily", "2026-09-09", 20)
    assert entry["trade_dates"] == ["2026-09-09"]
    assert entry["close"] == [100.0]
    assert entry["unit"] == "点"
    assert entry["source"] == "fut_daily:SC.INE"
    assert entry["note"] == "国内价、非 WTI/伦金"
    # 字段缺失 → None（不是抛异常）
    assert prov._module_frame_entry(pd.DataFrame({"trade_date": ["2026-09-09"]}),
                                    "SC.INE", "fut_daily", "2026-09-09", 20) is None


def test_paged_range_fetch_dedupes_and_sorts(monkeypatch):
    prov = _new_provider()
    calls = []

    def _fake(ts_code=None, start_date=None, end_date=None):
        calls.append((start_date, end_date))
        # 两页各返回 2 行，且有重复日期 → 去重后 3 行升序
        if start_date == "20260901":
            return pd.DataFrame({"trade_date": ["20260902", "20260901"],
                                 "close": [2.0, 1.0]})
        return pd.DataFrame({"trade_date": ["20260903", "20260902"],
                             "close": [3.0, 2.0]})

    df = prov._paged_range_fetch(_fake, "SPX", "20260901", "20261031", chunk_days=30)
    assert len(calls) >= 2
    assert list(df["trade_date"]) == ["20260901", "20260902", "20260903"]


def test_paged_range_fetch_empty_returns_none(monkeypatch):
    prov = _new_provider()
    assert prov._paged_range_fetch(lambda **kw: pd.DataFrame(), "SPX",
                                   "20260901", "20260910") is None
    assert prov._paged_range_fetch(lambda **kw: None, "SPX",
                                   "20260901", "20260910") is None
