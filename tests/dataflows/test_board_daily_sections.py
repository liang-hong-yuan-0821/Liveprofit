"""
单元测试：行业/概念接口的「近 10 个交易日逐日涨跌幅」章节（Tushare）

覆盖：
- daily_matrix_utils 纯函数口径（compute_gap_dates 周末排除 / master_dates 兜底）
- 行业逐日章节：格式、列头 MM-DD、累计列 == 聚合 pct、days=20 固定 10 列、
  盘中标注、短序列「—」、全市场缺工作日 → 脚注、全失败门控契约
- 概念热度逐日章节：行序 = 热度 TOP30、单元格来自逐日快照 pct_change、
  today 仅调 1 次、预热共享缓存、新概念「—」、短天数降级 + 窗口过滤脚注
"""

from datetime import datetime as _real_dt

from unittest.mock import MagicMock

import pandas as pd
import pytest

from AI.dataflows.providers.cn import daily_matrix_utils
from AI.dataflows.providers.cn.tushare import TushareProvider


# ==================== fixtures / helpers ====================

def _new_tushare_provider(api=None):
    """绕过 __init__ 的连接逻辑，直接构造 mock provider"""
    prov = TushareProvider.__new__(TushareProvider)
    prov.name = "Tushare"
    prov.connected = True
    prov.api = api or MagicMock()
    return prov


class _FakeDateTime:
    """替换 cn.tushare.datetime：now() 返回固定时刻（测试可控）"""
    _now = _real_dt(2026, 8, 19, 16, 0, 0)   # 默认盘后

    @classmethod
    def now(cls):
        return cls._now


def _patch_now(monkeypatch, dt: _real_dt):
    """固定 provider 内 datetime.now() 到指定时刻"""
    import AI.dataflows.providers.cn.tushare as ts_prov
    _FakeDateTime._now = dt
    monkeypatch.setattr(ts_prov, "datetime", _FakeDateTime)


@pytest.fixture(autouse=True)
def clear_daily_cache():
    """按日缓存是类级 dict，测试间必须清空防止交叉污染"""
    TushareProvider._DC_CONCEPT_DAILY_CACHE.clear()
    TushareProvider._DC_INDUSTRY_FLOW_DAILY_CACHE.clear()
    yield
    TushareProvider._DC_CONCEPT_DAILY_CACHE.clear()
    TushareProvider._DC_INDUSTRY_FLOW_DAILY_CACHE.clear()


# 2026-08-19（周三）为"今天"；TRADING_11 = 最近 11 个交易日（升序）
DAY_TODAY = "20260819"
TRADING_11 = ["20260805", "20260806", "20260807", "20260810", "20260811",
              "20260812", "20260813", "20260814", "20260817", "20260818",
              "20260819"]
TRADING_10 = TRADING_11[1:]   # 08-06 ~ 08-19，10 个交易日


def _geo_closes(base: float, rate: float, n: int) -> list:
    """等比收盘序列（每日涨跌幅恒为 rate）"""
    return [base * (1 + rate) ** i for i in range(n)]


def _sw_df(dates, closes):
    return pd.DataFrame({"trade_date": dates, "close": closes})


def _industry_api(sw_map, classify_names=(("801780.SI", "银行"), ("801080.SI", "电子"))):
    """按入参分派 index_classify / sw_daily 的 _api_call mock"""
    classify_df = pd.DataFrame(
        [{"index_code": c, "industry_name": n} for c, n in classify_names])

    def fake(fn, **kwargs):
        if "src" in kwargs:                      # index_classify
            return classify_df
        if "ts_code" in kwargs:                  # sw_daily
            return sw_map.get(kwargs["ts_code"], pd.DataFrame())
        return pd.DataFrame()
    return MagicMock(side_effect=fake)


def _dc_snap(pcts):
    """构造 dc_index 概念快照 DataFrame：[(name, pct_change), ...]"""
    return pd.DataFrame([
        {"ts_code": f"BK{i}.DC", "name": name, "pct_change": pct,
         "turnover_rate": 1.0, "up_num": 1, "down_num": 0}
        for i, (name, pct) in enumerate(pcts)
    ])


def _dc_daily_df(dates, closes, vols=None):
    return pd.DataFrame({
        "ts_code": "X", "trade_date": dates, "close": closes,
        "vol": vols if vols is not None else [1000.0] * len(closes),
    })


def _heat_api(snap_map, daily_map):
    """按入参分派 dc_index（trade_date） / dc_daily（ts_code）的 _api_call mock"""
    def fake(fn, **kwargs):
        if "trade_date" in kwargs:               # dc_index
            return snap_map.get(kwargs["trade_date"], pd.DataFrame())
        if "ts_code" in kwargs:                  # dc_daily
            return daily_map.get(kwargs["ts_code"], pd.DataFrame())
        return pd.DataFrame()
    return MagicMock(side_effect=fake)


# ==================== daily_matrix_utils 纯函数 ====================

def test_compute_gap_dates_excludes_weekend_includes_holiday():
    """窗口内缺数据的周末不入口径清单，缺数据的节假日（工作日）入清单"""
    gaps = daily_matrix_utils.compute_gap_dates(
        "20260805", "20260819", set(TRADING_11) - {"20260813"})
    assert gaps == ["20260813"]   # 20260815/16 为周末，排除


def test_master_dates_fallback_by_coverage():
    """最长序列口径不满足 min_coverage 时改用覆盖占比口径"""
    series = {
        "A": {d: 1.0 for d in TRADING_10},
        "B": {d: 1.0 for d in TRADING_10[-2:]},   # 只覆盖最后 2 天
        "C": {d: 1.0 for d in TRADING_10[-2:]},
    }
    # 最长口径：A 的最近 10 天全覆盖占比 1/3 < 0.5 → 兜底取覆盖 ≥50% 的最近日期
    master = daily_matrix_utils.master_dates_from_series(series, n=3, min_coverage=0.5)
    assert master == TRADING_10[-2:]


# ==================== 行业逐日章节 ====================

def test_industry_daily_section_format(monkeypatch):
    """章节格式：列头 MM-DD、单元格、累计列 == 聚合 pct、行序 = 排名序"""
    prov = _new_tushare_provider()
    prov._api_call = _industry_api({
        "801780.SI": _sw_df(TRADING_11, _geo_closes(100, 0.02, 11)),   # 银行 +2%/日
        "801080.SI": _sw_df(TRADING_11, _geo_closes(100, -0.02, 11)),  # 电子 -2%/日
    })
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    out = prov.get_industry_sector_performance(days=10)

    # 标题下方展示聚合区间 YYYYMMDD - YYYYMMDD
    assert "# 全行业板块涨跌排名（近 10 日）\n（20260805 - 20260819）" in out
    assert "## 近10个交易日逐日涨跌幅（行业×日期，列头 MM-DD）" in out
    assert "| 行业 | 08-06 | 08-07 | 08-10 | 08-11 | 08-12 | 08-13 | 08-14 | 08-17 | 08-18 | 08-19 | 近10日累计 |" in out
    # 银行：每日 +2.0%，累计 (1.02^10-1) = +21.90%，与聚合排名 pct 一致
    assert "| 银行 | +2.0% | +2.0% | +2.0% | +2.0% | +2.0% | +2.0% | +2.0% | +2.0% | +2.0% | +2.0% | +21.90% |" in out
    # 电子：每日 -2.0%，累计 (0.98^10-1) = -18.29%
    assert "| 电子 | -2.0% | -2.0% | -2.0% | -2.0% | -2.0% | -2.0% | -2.0% | -2.0% | -2.0% | -2.0% | -18.29% |" in out
    # 行序 = 聚合排名序（银行第一）——逐日章节内比行序
    daily = out.split("## 近10个交易日逐日涨跌幅", 1)[1]
    assert daily.index("| 银行 |") < daily.index("| 电子 |")


def test_industry_daily_section_fixed_10_even_days_20(monkeypatch):
    """days=20：聚合表 20 日 pct，逐日表仍固定 10 列"""
    dates = ["20260722", "20260723", "20260724", "20260727", "20260728",
             "20260729", "20260730", "20260731", "20260803", "20260804"] \
            + TRADING_11   # 10 天 + 11 天 = 21 行，聚合窗口 20 个日变化
    prov = _new_tushare_provider()
    prov._api_call = _industry_api({
        "801780.SI": _sw_df(dates, _geo_closes(100, 0.02, len(dates))),
        "801080.SI": _sw_df(dates, _geo_closes(100, -0.02, len(dates))),
    })
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    out = prov.get_industry_sector_performance(days=20)

    assert "## 近10个交易日逐日涨跌幅（行业×日期，列头 MM-DD）" in out
    # 聚合 20 日 pct = (1.02^20-1) = +48.59%；逐日表累计列仍为近 10 日 +21.90%
    assert "| 1 | 银行 | +48.59% |" in out
    assert "| 银行 | +2.0% | +2.0% | +2.0% | +2.0% | +2.0% | +2.0% | +2.0% | +2.0% | +2.0% | +2.0% | +21.90% |" in out


def test_industry_daily_section_intraday_annotation(monkeypatch):
    """盘中运行且最新日期 == 今日 → 列头加（盘中）；盘后不加"""
    prov = _new_tushare_provider()
    prov._api_call = _industry_api({
        "801780.SI": _sw_df(TRADING_11, _geo_closes(100, 0.02, 11)),
    })
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 10, 0, 0))   # 盘中

    out = prov.get_industry_sector_performance(days=10)
    assert "| 08-19（盘中） |" in out

    prov = _new_tushare_provider()
    prov._api_call = _industry_api({
        "801780.SI": _sw_df(TRADING_11, _geo_closes(100, 0.02, 11)),
    })
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))   # 盘后

    out = prov.get_industry_sector_performance(days=10)
    assert "（盘中）" not in out


def test_industry_daily_section_short_series(monkeypatch):
    """个别行业序列不足 → 缺失单元格「—」"""
    prov = _new_tushare_provider()
    prov._api_call = _industry_api({
        "801780.SI": _sw_df(TRADING_11, _geo_closes(100, 0.02, 11)),
        "801080.SI": _sw_df(TRADING_11[-5:], _geo_closes(100, -0.02, 5)),  # 仅 5 行
    })
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    out = prov.get_industry_sector_performance(days=10)

    daily = out.split("## 近10个交易日逐日涨跌幅", 1)[1]
    electronic_row = daily.split("| 电子 |", 1)[1].split("\n", 1)[0]
    assert electronic_row.count("—") >= 5   # 前 5 列缺数据
    assert "| 银行 | +2.0%" in daily         # 银行完整


def test_industry_gap_footnote(monkeypatch):
    """全市场缺某工作日（含节假日）→ 脚注列出该日；周末不入清单"""
    dates = [d for d in TRADING_11 if d != "20260813"]   # 全行业缺 08-13
    prov = _new_tushare_provider()
    prov._api_call = _industry_api({
        "801780.SI": _sw_df(dates, _geo_closes(100, 0.02, len(dates))),
        "801080.SI": _sw_df(dates, _geo_closes(100, -0.02, len(dates))),
    })
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    out = prov.get_industry_sector_performance(days=10)

    assert "> 数据缺口：以下日期无数据已跳过：" in out
    assert "08-13" in out.split("> 数据缺口", 1)[1]
    assert "08-15" not in out.split("> 数据缺口", 1)[1]   # 周末不入清单


def test_industry_all_failed_keeps_placeholder(monkeypatch):
    """全失败路径返回串不以 # 开头（门控契约回归）"""
    prov = _new_tushare_provider()
    prov._api_call = _industry_api({})
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    out = prov.get_industry_sector_performance(days=10)

    assert out == "未获取到行业板块表现数据。"
    assert not out.startswith("#")


def test_industry_descending_order_returns_latest(monkeypatch):
    """端点返回降序（新→旧）时，tail 必须取到最新 10 个交易日
    （2026-08-23 踩坑回归：降序数据直接 tail 会取到最旧 17 天前的数据）"""
    desc = _sw_df(TRADING_11, _geo_closes(100, 0.02, 11)).iloc[::-1].reset_index(drop=True)
    prov = _new_tushare_provider()
    prov._api_call = _industry_api({
        "801780.SI": desc,
    })
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    out = prov.get_industry_sector_performance(days=10)

    assert "| 行业 | 08-06 | 08-07 | 08-10 | 08-11 | 08-12 | 08-13 | 08-14 | 08-17 | 08-18 | 08-19 | 近10日累计 |" in out
    assert "07-24" not in out.split("## 近10个交易日逐日涨跌幅", 1)[1]   # 最旧日期不得入列


def test_industry_stale_data_hint(monkeypatch):
    """最新交易日距今 > 3 天 → caption 提示数据可能滞后"""
    dates = ["20260804", "20260805", "20260806"]
    prov = _new_tushare_provider()
    prov._api_call = _industry_api({
        "801780.SI": _sw_df(dates, [100.0, 102.0, 104.04]),
    })
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    out = prov.get_industry_sector_performance(days=10)

    assert "最新交易日 08-06（13 天前），数据可能滞后" in out


def test_industry_nan_close_skipped(monkeypatch):
    """当日收盘 NaN → 跳过该日与次日（单元格「—」而非 +nan%）"""
    closes = _geo_closes(100, 0.02, 11)
    closes[2] = float("nan")
    prov = _new_tushare_provider()
    prov._api_call = _industry_api({
        "801780.SI": _sw_df(TRADING_11, closes),
        "801080.SI": _sw_df(TRADING_11, _geo_closes(100, -0.02, 11)),
    })
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    out = prov.get_industry_sector_performance(days=10)

    assert "+nan%" not in out
    daily = out.split("## 近10个交易日逐日涨跌幅", 1)[1]
    bank_row = daily.split("| 银行 |", 1)[1].split("\n", 1)[0]
    assert bank_row.count("—") >= 2   # NaN 当日与其后一日（前值 NaN）均跳过


# ==================== 概念热度逐日章节 ====================

def test_heat_daily_section_top30_order(monkeypatch):
    """逐日行序 = 热度排序（B>A>C），单元格来自逐日快照 pct_change（非 dc_daily）"""
    snaps = {d: _dc_snap([("A概念", 2.0), ("B概念", 1.0), ("C概念", -1.0)])
             for d in TRADING_10}
    daily = {
        "BK0.DC": _dc_daily_df(TRADING_11, _geo_closes(100, 0.02, 11)),   # A: pct 21.90
        "BK1.DC": _dc_daily_df(TRADING_11, _geo_closes(100, 0.03, 11)),   # B: pct 34.39
        "BK2.DC": _dc_daily_df(TRADING_11, _geo_closes(100, 0.01, 11)),   # C: pct 10.46
    }
    prov = _new_tushare_provider()
    prov._api_call = _heat_api(snaps, daily)
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    out = prov.get_concept_board_heat_rank(days=10)

    # 热度 = pct*0.6（vol 恒定无量变）：B(34.39) > A(21.90) > C(10.46)
    assert out.index("| B概念 | +1.0%") < out.index("| A概念 | +2.0%") < out.index("| C概念 | -1.0%")
    # 单元格来自快照 pct_change（B 快照 +1.0%/日 → 复利累计 +10.46%），而非 dc_daily 的 3%
    assert "| B概念 | +1.0% | +1.0% | +1.0% | +1.0% | +1.0% | +1.0% | +1.0% | +1.0% | +1.0% | +1.0% | +10.46% |" in out
    assert "累计列为逐日快照涨跌幅复利计算" in out
    assert "## 近10个交易日逐日涨跌幅（概念×日期，列头 MM-DD）" in out


def test_heat_daily_section_today_fetched_once(monkeypatch):
    """today 快照注入采集器后不重复调 API：trade_date=today 的 dc_index 恰 1 次"""
    snaps = {d: _dc_snap([("A概念", 2.0)]) for d in TRADING_10}
    daily = {"BK0.DC": _dc_daily_df(TRADING_11, _geo_closes(100, 0.02, 11))}

    calls = []
    def fake(fn, **kwargs):
        calls.append(kwargs)
        if "trade_date" in kwargs:
            return snaps.get(kwargs["trade_date"], pd.DataFrame())
        if "ts_code" in kwargs:
            return daily.get(kwargs["ts_code"], pd.DataFrame())
        return pd.DataFrame()

    prov = _new_tushare_provider()
    prov._api_call = MagicMock(side_effect=fake)
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    prov.get_concept_board_heat_rank(days=10)

    today_calls = [k for k in calls
                   if "trade_date" in k and k["trade_date"] == DAY_TODAY]
    assert len(today_calls) == 1   # 仅 Step 1 探测一次


def test_heat_daily_section_warms_shared_cache(monkeypatch):
    """heat 预热后 top_gains 的历史交易日 dc_index 调用为 0（今日 1 次除外）"""
    snaps = {d: _dc_snap([("A概念", 2.0), ("B概念", 1.0)]) for d in TRADING_10}
    daily = {
        "BK0.DC": _dc_daily_df(TRADING_11, _geo_closes(100, 0.02, 11)),
        "BK1.DC": _dc_daily_df(TRADING_11, _geo_closes(100, 0.01, 11)),
    }
    prov = _new_tushare_provider()
    prov._api_call = _heat_api(snaps, daily)
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    prov.get_concept_board_heat_rank(days=10)

    cache = TushareProvider._DC_CONCEPT_DAILY_CACHE
    assert DAY_TODAY not in cache               # 今日快照注入不写缓存
    assert "20260818" in cache                  # 非当日已写入

    # top_gains 复用缓存：历史日零 API，今日 1 次
    calls = []
    def fake2(fn, **kwargs):
        calls.append(kwargs)
        if "trade_date" in kwargs:
            return snaps.get(kwargs["trade_date"], pd.DataFrame())
        return pd.DataFrame()
    prov2 = _new_tushare_provider()
    prov2._api_call = MagicMock(side_effect=fake2)

    out = prov2.get_concept_daily_top_gains(days=2, top_n=3)
    assert out.startswith("#")                  # 门控契约
    history_calls = [k for k in calls
                     if "trade_date" in k and k["trade_date"] != DAY_TODAY
                     and k["trade_date"] in TRADING_10]
    assert history_calls == []                  # 历史日全部缓存命中


def test_heat_nan_pct_change_skipped(monkeypatch):
    """快照 pct_change 为 NaN → 该概念当日单元格「—」而非 +nan%"""
    snaps = {d: _dc_snap([("A概念", 2.0), ("B概念", float("nan"))])
             for d in TRADING_10}
    daily = {
        "BK0.DC": _dc_daily_df(TRADING_11, _geo_closes(100, 0.02, 11)),
        "BK1.DC": _dc_daily_df(TRADING_11, _geo_closes(100, 0.01, 11)),
    }
    prov = _new_tushare_provider()
    prov._api_call = _heat_api(snaps, daily)
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    out = prov.get_concept_board_heat_rank(days=10)

    assert "+nan%" not in out
    daily = out.split("## 近10个交易日逐日涨跌幅", 1)[1]
    b_row = daily.split("| B概念 |", 1)[1].split("\n", 1)[0]
    assert b_row.count("—") >= 9   # 全部日期 pct_change 为 NaN 均跳过


def test_heat_daily_section_new_concept_missing(monkeypatch):
    """历史日不存在的新概念 → 逐日行单元格「—」"""
    snaps = {DAY_TODAY: _dc_snap([("X概念", 5.0), ("A概念", 2.0)])}
    for d in TRADING_10[:-1]:
        snaps[d] = _dc_snap([("A概念", 2.0)])   # X 仅今日存在（TRADING_10[:-1] 不含今日）
    daily = {
        "BK0.DC": _dc_daily_df(TRADING_11, _geo_closes(100, 0.05, 11)),   # X
        "BK1.DC": _dc_daily_df(TRADING_11, _geo_closes(100, 0.02, 11)),   # A
    }
    prov = _new_tushare_provider()
    prov._api_call = _heat_api(snaps, daily)
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    out = prov.get_concept_board_heat_rank(days=10)

    daily = out.split("## 近10个交易日逐日涨跌幅", 1)[1]
    x_row = daily.split("| X概念 |", 1)[1].split("\n", 1)[0]
    assert x_row.count("—") >= 8               # 历史日全部缺数据


def test_heat_daily_section_short_days(monkeypatch):
    """仅 3 个交易日：caption 注仅覆盖 + 窗口内缺口脚注（窗口外探测日期被过滤）"""
    snaps = {
        "20260814": _dc_snap([("A概念", 2.0)]),
        "20260818": _dc_snap([("A概念", 2.0)]),
        DAY_TODAY: _dc_snap([("A概念", 2.0)]),
    }
    daily = {"BK0.DC": _dc_daily_df(["20260814", "20260818", "20260819"],
                                    [100.0, 102.0, 104.04])}
    prov = _new_tushare_provider()
    prov._api_call = _heat_api(snaps, daily)
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    out = prov.get_concept_board_heat_rank(days=10)

    assert "仅覆盖 3 个交易日" in out
    assert "## 近3个交易日逐日涨跌幅（概念×日期，列头 MM-DD）" in out
    # 窗口内缺的 08-17（周一）入脚注；窗口外的探测空表日期与周末被过滤
    footnote = out.split("> 数据缺口", 1)[1]
    assert "08-17" in footnote
    assert "08-15" not in footnote and "08-16" not in footnote
    assert "08-13" not in footnote   # 窗口外


# ==================== 行业资金流向排名（东财行业口径） ====================

FLOW_DAYS = ["20260817", "20260818", "20260819", "20260820", "20260821"]


def _flow_df(rows):
    """构造 moneyflow_ind_dc 快照：[(content_type, name, net_amount, pct_change), ...]"""
    return pd.DataFrame([
        {"content_type": ct, "ts_code": f"BK{i}.DC", "name": name,
         "net_amount": net, "net_amount_rate": 0.0, "pct_change": pct}
        for i, (ct, name, net, pct) in enumerate(rows)
    ])


def _flow_api(cal_days, flow_map):
    """按入参分派 trade_cal（exchange） / moneyflow_ind_dc（trade_date）的 mock"""
    def fake(fn, **kwargs):
        if "exchange" in kwargs:                 # trade_cal
            return pd.DataFrame({"cal_date": cal_days})
        if "trade_date" in kwargs:               # moneyflow_ind_dc
            return flow_map.get(kwargs["trade_date"], pd.DataFrame())
        return pd.DataFrame()
    return MagicMock(side_effect=fake)


def test_sector_fund_flow_rank_aggregates(monkeypatch):
    """行业口径过滤 + 近 N 日净流入聚合（亿元）+ 行序 = 净流入降序 + 区间行"""
    # 概念行必须被过滤；近 5 日聚合：有色 8+2*4=16 亿，电子 0.8-0.5*4=-1.2 亿
    day_latest = _flow_df([("行业板块", "有色金属", 8e9, 2.59),
                           ("行业板块", "电子", 8e8, 0.73),
                           ("概念板块", "算力", 1e9, 5.0)])
    day_other = _flow_df([("行业板块", "有色金属", 2e9, 1.0),
                          ("行业板块", "电子", -5e8, -0.5),
                          ("概念板块", "算力", 2e8, 3.0)])
    flow_map = {d: (day_latest if d == FLOW_DAYS[-1] else day_other)
                for d in FLOW_DAYS}
    prov = _new_tushare_provider()
    prov._api_call = _flow_api(list(reversed(FLOW_DAYS)), flow_map)   # 端点降序
    _patch_now(monkeypatch, _real_dt(2026, 8, 21, 16, 0, 0))

    out = prov.get_sector_fund_flow_rank(days=5)

    assert "# 行业资金流向排名（近 5 日，东财行业口径）" in out
    assert "（20260817 - 20260821）" in out
    assert "| 1 | 有色金属 | +160.00 | +80.00 | +2.59 |" in out
    assert "| 2 | 电子 | -12.00 | +8.00 | +0.73 |" in out
    assert "算力" not in out                     # 概念板块被过滤
    assert "## 净流入 TOP5" in out and "## 净流出 BOTTOM5" in out


def test_sector_fund_flow_rank_cache(monkeypatch):
    """仅非当日写入按日缓存；当日不写；命中缓存不再调 API"""
    day_latest = _flow_df([("行业板块", "有色金属", 8e9, 2.59)])
    day_other = _flow_df([("行业板块", "有色金属", 2e9, 1.0)])
    flow_map = {d: (day_latest if d == FLOW_DAYS[-1] else day_other)
                for d in FLOW_DAYS}
    prov = _new_tushare_provider()
    prov._api_call = _flow_api(list(reversed(FLOW_DAYS)), flow_map)
    _patch_now(monkeypatch, _real_dt(2026, 8, 21, 16, 0, 0))

    prov.get_sector_fund_flow_rank(days=5)

    cache = TushareProvider._DC_INDUSTRY_FLOW_DAILY_CACHE
    assert "20260820" in cache and "20260821" not in cache


def test_sector_fund_flow_rank_no_data(monkeypatch):
    """日历/快照全空 → 返回占位串（不以 # 开头，门控契约）"""
    prov = _new_tushare_provider()
    prov._api_call = _flow_api([], {})
    _patch_now(monkeypatch, _real_dt(2026, 8, 21, 16, 0, 0))

    out = prov.get_sector_fund_flow_rank(days=5)

    assert out == "暂无行业资金流向数据。"
    assert not out.startswith("#")
