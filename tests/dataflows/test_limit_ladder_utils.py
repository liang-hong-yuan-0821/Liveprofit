"""
单元测试：连板梯队共享纯函数模块（limit_ladder_utils）+ limit_list_d 侧归一化/缓存

纯函数部分（合成记录，不联网）：
- 晋级率精确 ts_code 匹配 / 首日无昨日数据 / 缺日（长间隔）
- 炸板率口径（含分母 0 → "--"）
- 格式化：6板+ 合并列宽、首板计数、盘中标注、数据缺口脚注

provider 部分（mock _api_call）：
- limit_list_d 三分桶归一化（U/D/Z、limit_times NaN/0 → 首板、open_times → 开板回封）
- 全部失败返回串；盘中/盘后标注（mock 当前时间）
- 按日缓存：仅非当日写入、精确命中不重复拉取
"""

from datetime import datetime as _real_dt

from unittest.mock import MagicMock

import pandas as pd
import pytest

from AI.dataflows.providers.cn.tushare import TushareProvider
from AI.dataflows.providers.cn.limit_ladder_utils import (
    calc_break_rate, calc_promotion_rates, format_ladder_matrix,
)


# ==================== helpers ====================

def _record(date, boards, dt_total=0, zb_total=0, open_broken=0, intraday=False):
    """boards: {ts_code: 连板数}（含首板 1）→ 构造归一化 day record"""
    ladder = {}
    for b in boards.values():
        if b >= 2:
            ladder[b] = ladder.get(b, 0) + 1
    return {
        "date": date,
        "zt_total": len(boards),
        "dt_total": dt_total,
        "ladder": ladder,
        "open_broken": open_broken,
        "zb_total": zb_total,
        "stock_boards": boards,
        "intraday": intraday,
    }


def _new_tushare_provider(api=None):
    prov = TushareProvider.__new__(TushareProvider)
    prov.name = "Tushare"
    prov.connected = True
    prov.api = api or MagicMock()
    return prov


class _FakeDateTime:
    _now = _real_dt(2026, 8, 19, 16, 0, 0)   # 默认盘后

    @classmethod
    def now(cls):
        return cls._now


def _patch_now(monkeypatch, dt: _real_dt):
    import AI.dataflows.providers.cn.tushare as ts_prov
    _FakeDateTime._now = dt
    monkeypatch.setattr(ts_prov, "datetime", _FakeDateTime)


@pytest.fixture(autouse=True)
def clear_ladder_cache():
    TushareProvider._LIMIT_LIST_D_DAILY_CACHE.clear()
    yield
    TushareProvider._LIMIT_LIST_D_DAILY_CACHE.clear()


# ==================== 晋级率纯函数 ====================

def test_promotion_exact_match():
    """今日 L+1 板且昨日 L 板（ts_code 精确匹配）/ 昨日 L 板家数"""
    day1 = _record("20260818", {"A": 1, "B": 1, "C": 2, "D": 3})
    day2 = _record("20260819", {"A": 2, "B": 1, "C": 3, "D": 1})

    promo = calc_promotion_rates([day1, day2])

    assert promo[0]["1→2"] == "--"       # 首日无昨日数据恒为 "--"
    assert promo[1]["1→2"] == "1/2"      # 今日2板: A（昨日首板）；昨日首板: A、B
    assert promo[1]["2→3"] == "1/1"      # 今日3板: C（昨日2板）
    assert promo[1]["3→4"] == "0/1"      # 今日4板: 无；昨日3板: D
    assert promo[1]["4→5"] == "--"       # 昨日无 4 板 → 分母 0
    assert promo[1]["5→6+"] == "--"      # 昨日无 ≥5 板 → 分母 0


def test_promotion_merged_6plus():
    """'5→6+' 合并口径：分子今日≥6板且昨日≥5板，分母昨日≥5板"""
    day1 = _record("20260818", {"E": 5, "F": 4})
    day2 = _record("20260819", {"E": 6, "F": 6})

    promo = calc_promotion_rates([day1, day2])

    assert promo[1]["5→6+"] == "1/1"     # E：今日6板且昨日5板；F 昨日仅 4 板不计
    assert promo[1]["4→5"] == "0/1"      # 今日5板: 无；昨日4板: F


def test_promotion_gap_day_all_dash():
    """与上一采集日间隔过长（长假期/缺数据日）→ 该行全部 "--" """
    day1 = _record("20260810", {"A": 1})
    day2 = _record("20260819", {"A": 2})

    promo = calc_promotion_rates([day1, day2])

    for col in ("1→2", "2→3", "3→4", "4→5", "5→6+"):
        assert promo[1][col] == "--"


def test_promotion_weekend_gap_is_consecutive():
    """周五→周一（3 自然日间隔）视为连续，正常计算"""
    day1 = _record("20260814", {"A": 1})   # 周五
    day2 = _record("20260817", {"A": 2})   # 周一

    promo = calc_promotion_rates([day1, day2])

    assert promo[1]["1→2"] == "1/1"


# ==================== 炸板率纯函数 ====================

def test_break_rate_formula():
    """炸板率 = zb/(zt+zb)；开板回封率 = open_broken/zt（示例口径 36/12/6）"""
    records = _example_records()

    rates = calc_break_rate(records)

    assert rates[0]["zb_rate"] == "25.0%"          # 12/(36+12)
    assert rates[0]["open_back_rate"] == "16.7%"   # 6/36


def test_break_rate_zero_denominator():
    """分母为 0 → 对应格 '--'"""
    records = [_record("20260819", {})]

    rates = calc_break_rate(records)

    assert rates[0]["zb_rate"] == "--"
    assert rates[0]["open_back_rate"] == "--"


# ==================== 格式化 ====================

def _example_records():
    """按方案 2.1 示例构造：zt 36 / dt 118 / ladder {2:8, 3:4, 5:1} / open_broken 6 / zb 12"""
    boards = {}
    for i in range(23):
        boards[f"1{i:05d}.SH"] = 1
    for i in range(8):
        boards[f"2{i:05d}.SH"] = 2
    for i in range(4):
        boards[f"3{i:05d}.SH"] = 3
    boards["500000.SH"] = 5
    return [_record("20260819", boards, dt_total=118, zb_total=12, open_broken=6)]


def test_format_matrix_matches_spec_example():
    """梯队表行与方案示例一致；标题内嵌实际采集日数"""
    records = _example_records()
    promo = calc_promotion_rates(records)
    rates = calc_break_rate(records)

    out = format_ladder_matrix(records, promo, rates)

    assert out.startswith("# 近 1 日连板梯队与情绪数据")
    # 示例行：36 | 118 | 23 | 8 | 4 | 0 | 1 | 0 | 5板(1) | 6(16.7%) | 12 | 25.0%
    assert "| 20260819 | 36 | 118 | 23 | 8 | 4 | 0 | 1 | 0 | 5板(1) | 6(16.7%) | 12 | 25.0% |" in out
    assert "## 晋级率矩阵（精确个股匹配）" in out
    assert "> 口径脚注：" in out


def test_format_6plus_merged_column():
    """≥6 板合并为'6板+'单列（防表格过宽）"""
    boards = {"A": 2, "B": 3, "C": 6, "D": 7, "E": 9, "F": 1}
    records = [_record("20260819", boards)]
    promo = calc_promotion_rates(records)
    rates = calc_break_rate(records)

    out = format_ladder_matrix(records, promo, rates)

    assert "| 20260819 | 6 | 0 | 1 | 1 | 1 | 0 | 0 | 3 | 9板(1) |" in out
    # 晋级率矩阵列宽固定为 1→2 … 5→6+，不出现 7→8 等宽列
    assert "7→8" not in out


def test_format_intraday_and_skipped():
    """盘中标注落在日期单元格；数据缺口脚注列出被跳过日期"""
    records = [_record("20260819", {"A": 1}, intraday=True)]
    promo = calc_promotion_rates(records)
    rates = calc_break_rate(records)

    out = format_ladder_matrix(records, promo, rates, skipped_dates=["20260818"])

    assert "| 20260819（盘中） |" in out
    assert "> 数据缺口：以下日期无数据已跳过：20260818" in out


def test_format_all_first_board_day():
    """无连板股时：首板 = 涨停总数，最高板为 1板"""
    records = [_record("20260819", {"A": 1, "B": 1, "C": 1})]
    promo = calc_promotion_rates(records)
    rates = calc_break_rate(records)

    out = format_ladder_matrix(records, promo, rates)

    assert "| 20260819 | 3 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | 1板(3) |" in out


def test_format_zero_limit_up_day_highest_dash():
    """极端日涨停家数为 0：最高板输出 '--' 而非 '1板(0)'"""
    records = [_record("20260819", {})]
    promo = calc_promotion_rates(records)
    rates = calc_break_rate(records)

    out = format_ladder_matrix(records, promo, rates)

    assert "| 20260819 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | -- |" in out


# ==================== provider：limit_list_d 归一化 ====================

def _limit_list_df():
    """构造 limit_list_d 全表：U 涨停（含 NaN/0 limit_times、开板回封）、D 跌停、Z 炸板"""
    rows = [
        {"ts_code": "600000.SH", "limit": "U", "limit_times": 2.0, "open_times": 0.0},
        {"ts_code": "300001.SZ", "limit": "U", "limit_times": None, "open_times": None},  # NaN → 首板
        {"ts_code": "000001.SZ", "limit": "U", "limit_times": 0.0, "open_times": 0.0},   # 0 → 首板
        {"ts_code": "600001.SH", "limit": "U", "limit_times": 3.0, "open_times": 2.0},   # 开板回封
        {"ts_code": "", "limit": "U", "limit_times": 1.0, "open_times": 0.0},            # 脏数据剔除
        {"ts_code": "000002.SZ", "limit": "D", "limit_times": None, "open_times": None},
        {"ts_code": "000003.SZ", "limit": "D", "limit_times": None, "open_times": None},
        {"ts_code": "000004.SZ", "limit": "Z", "limit_times": None, "open_times": None},
    ]
    return pd.DataFrame(rows)


def test_normalize_ladder_day():
    prov = _new_tushare_provider()

    rec = prov._normalize_ladder_day("20260819", _limit_list_df())

    assert rec["zt_total"] == 4            # 脏数据 ts_code 剔除
    assert rec["dt_total"] == 2
    assert rec["zb_total"] == 1
    assert rec["open_broken"] == 1         # 600001.SH open_times=2 > 0
    assert rec["ladder"] == {2: 1, 3: 1}
    assert rec["stock_boards"] == {
        "600000.SH": 2, "300001.SZ": 1, "000001.SZ": 1, "600001.SH": 3,
    }


def test_normalize_ladder_day_missing_limit_column():
    prov = _new_tushare_provider()

    assert prov._normalize_ladder_day("20260819", pd.DataFrame({"ts_code": ["600000.SH"]})) is None


# ==================== provider：get_limit_up_ladder 全流程 ====================

DAY_TODAY = "20260819"
DAY_1 = "20260818"


def _by_date_api_call(mapping: dict):
    def fake(fn, **kwargs):
        return mapping.get(kwargs.get("trade_date"), pd.DataFrame())
    return MagicMock(side_effect=fake)


def test_ladder_all_failed_returns_placeholder(monkeypatch):
    prov = _new_tushare_provider()
    prov._api_call = MagicMock(return_value=pd.DataFrame())
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    out = prov.get_limit_up_ladder(days=2)

    assert out == "近 2 个交易日无连板梯队数据。"


def test_ladder_not_connected():
    prov = _new_tushare_provider()
    prov.connected = False

    assert prov.get_limit_up_ladder() == "Tushare 未连接。"


def test_ladder_intraday_annotation(monkeypatch):
    """交易时段内运行 → 当日行标注"盘中"；盘后运行不标注"""
    mapping = {DAY_TODAY: _limit_list_df()}

    prov = _new_tushare_provider()
    prov._api_call = _by_date_api_call(mapping)
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 10, 0, 0))   # 盘中

    out = prov.get_limit_up_ladder(days=1)
    assert f"| {DAY_TODAY}（盘中） |" in out

    prov = _new_tushare_provider()
    prov._api_call = _by_date_api_call(mapping)
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))   # 盘后

    out = prov.get_limit_up_ladder(days=1)
    assert f"| {DAY_TODAY}（盘中） |" not in out
    assert f"| {DAY_TODAY} |" in out


def test_ladder_daily_cache_only_non_today_written(monkeypatch):
    prov = _new_tushare_provider()
    prov._api_call = _by_date_api_call({
        DAY_TODAY: _limit_list_df(),
        DAY_1: _limit_list_df(),
    })
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    prov.get_limit_up_ladder(days=2)

    assert DAY_1 in TushareProvider._LIMIT_LIST_D_DAILY_CACHE
    assert DAY_TODAY not in TushareProvider._LIMIT_LIST_D_DAILY_CACHE


def test_ladder_daily_cache_hit_skips_api(monkeypatch):
    """probe 到已缓存日期 → 直接复用，不再打 API"""
    TushareProvider._LIMIT_LIST_D_DAILY_CACHE[DAY_1] = _limit_list_df()

    prov = _new_tushare_provider()
    calls = []
    def fake(fn, **kwargs):
        calls.append(kwargs.get("trade_date"))
        return _limit_list_df() if kwargs.get("trade_date") == DAY_TODAY else pd.DataFrame()
    prov._api_call = MagicMock(side_effect=fake)
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    out = prov.get_limit_up_ladder(days=2)

    assert out.startswith("# 近 2 日连板梯队与情绪数据")
    assert DAY_1 not in calls
    assert DAY_TODAY in calls


def test_ladder_skipped_day_annotated(monkeypatch):
    """单日无数据 → 跳过并在输出标注数据缺口"""
    prov = _new_tushare_provider()
    prov._api_call = _by_date_api_call({
        DAY_1: _limit_list_df(),
        "20260817": _limit_list_df(),
    })
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    out = prov.get_limit_up_ladder(days=2)

    assert "> 数据缺口：以下日期无数据已跳过：" + DAY_TODAY in out


def test_ladder_unexpected_result_returns_failure_string(monkeypatch):
    """意外形状的响应 → 外层兜底返回失败串，不抛异常（基类契约）"""
    prov = _new_tushare_provider()
    prov._api_call = MagicMock(return_value="garbage")
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    out = prov.get_limit_up_ladder(days=1)

    assert out.startswith("获取连板梯队失败:")
    assert not out.startswith("#")          # 门控契约：失败串不以 # 开头
