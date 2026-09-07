"""
单元测试：get_concept_daily_top_gains（东财概念逐日涨幅 TOP 矩阵）

mock provider 内部 API 调用，验证：
- dropna 后排序、TOP{top_n} 选取（NaN 不入榜）
- 跨日上榜统计"≥2 次"阈值
- 缺数据日跳过 + 输出标注；全部失败返回串
- 盘中/盘后标注（mock 当前时间）
- 按日缓存：仅非当日写入、精确命中不重复拉取、未命中向后 probe、上限裁剪
"""

from datetime import datetime as _real_dt

from unittest.mock import MagicMock

import pandas as pd
import pytest

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


def _by_date_api_call(mapping: dict):
    """构造按 trade_date 返回 DataFrame 的 _api_call mock（未命中返回空表）"""
    def fake(fn, **kwargs):
        return mapping.get(kwargs.get("trade_date"), pd.DataFrame())
    return MagicMock(side_effect=fake)


def _dc_df(pcts):
    """构造 dc_index 快照 DataFrame：[(name, pct_change, turnover_rate), ...]"""
    return pd.DataFrame([
        {"ts_code": f"BK{i}.DC", "name": name, "pct_change": pct, "turnover_rate": tr}
        for i, (name, pct, tr) in enumerate(pcts)
    ])


# 测试默认"今天"：2026-08-19（周三，交易日）
DAY_TODAY = "20260819"
DAY_1 = "20260818"
DAY_2 = "20260817"

DC_FIELDS = "ts_code,name,pct_change,turnover_rate,up_num,down_num"


@pytest.fixture(autouse=True)
def clear_daily_cache():
    """按日缓存是类级 dict，测试间必须清空防止交叉污染"""
    TushareProvider._DC_CONCEPT_DAILY_CACHE.clear()
    yield
    TushareProvider._DC_CONCEPT_DAILY_CACHE.clear()


# ==================== 矩阵主体 ====================

def test_basic_matrix_topn_dropna(monkeypatch):
    """按 pct_change 降序取 TOP N；NaN 与降序外板块不入榜；换手率格式化"""
    prov = _new_tushare_provider()
    prov._api_call = _by_date_api_call({
        DAY_TODAY: _dc_df([
            ("AI眼镜", 10.0, 8.2), ("机器人", 5.0, 5.1), ("算力", 3.0, 1.2),
            ("白酒", 1.0, 0.5), ("芯片", 0.5, 0.3), ("脏数据", None, None),
        ]),
        DAY_1: _dc_df([("AI眼镜", 8.0, 7.0), ("机器人", 4.0, 4.0)]),
    })
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    out = prov.get_concept_daily_top_gains(days=2, top_n=3)

    assert out.startswith("# 近 2 日东财概念板块逐日涨幅 TOP3 矩阵")
    # TOP3 按降序、条目带 涨幅%/换手%
    assert f"| {DAY_TODAY} | AI眼镜(+10.0%/8.2%)、机器人(+5.0%/5.1%)、算力(+3.0%/1.2%) |" in out
    # NaN 涨幅与降序外板块不入榜
    assert "脏数据" not in out
    assert "白酒" not in out
    # 请求参数必须带 idx_type 与 fields
    kwargs = prov._api_call.call_args.kwargs
    assert kwargs["idx_type"] == "概念板块"
    assert kwargs["fields"] == DC_FIELDS


def test_cross_day_stats_threshold(monkeypatch):
    """跨日上榜统计仅列上榜 ≥2 次的板块"""
    prov = _new_tushare_provider()
    prov._api_call = _by_date_api_call({
        DAY_TODAY: _dc_df([("A概念", 10.0, 1.0), ("B概念", 5.0, 2.0), ("C概念", 3.0, 3.0)]),
        DAY_1: _dc_df([("D概念", 9.0, 1.0), ("B概念", 4.0, 2.0), ("C概念", 2.0, 3.0)]),
    })
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    out = prov.get_concept_daily_top_gains(days=2, top_n=3)

    assert "## 跨日上榜统计" in out
    assert "| B概念 | 2/2 | 20260819 | +5.0% |" in out
    assert "| C概念 | 2/2 | 20260819 | +3.0% |" in out
    # 上榜仅 1 次的板块不进入统计表
    assert "A概念 | 1/2" not in out
    assert "D概念 | 1/2" not in out


def test_skipped_day_annotated(monkeypatch):
    """单日无数据 → 跳过该日继续 probe，并在输出标注数据缺口"""
    prov = _new_tushare_provider()
    prov._api_call = _by_date_api_call({
        DAY_1: _dc_df([("A概念", 10.0, 1.0)]),
        DAY_2: _dc_df([("B概念", 5.0, 1.0)]),
    })   # DAY_TODAY 未命中 → 空表 → 跳过
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    out = prov.get_concept_daily_top_gains(days=2, top_n=3)

    assert out.startswith("# 近 2 日东财概念板块逐日涨幅 TOP3 矩阵")
    assert "> 数据缺口：以下日期无数据已跳过：" + DAY_TODAY in out


def test_all_failed_returns_placeholder(monkeypatch):
    prov = _new_tushare_provider()
    prov._api_call = MagicMock(return_value=pd.DataFrame())
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    out = prov.get_concept_daily_top_gains(days=2, top_n=3)

    assert out == "近 2 个交易日无概念板块快照数据。"


def test_not_connected():
    prov = _new_tushare_provider()
    prov.connected = False

    out = prov.get_concept_daily_top_gains(days=2)

    assert out == "Tushare 未连接。"


# ==================== 盘中/盘后标注 ====================

def test_intraday_annotation(monkeypatch):
    """交易时段内运行 → 当日行标注"盘中"；盘后运行不标注"""
    mapping = {DAY_TODAY: _dc_df([("A概念", 10.0, 1.0)])}

    prov = _new_tushare_provider()
    prov._api_call = _by_date_api_call(mapping)
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 10, 0, 0))   # 盘中

    out = prov.get_concept_daily_top_gains(days=1, top_n=3)
    assert f"| {DAY_TODAY}（盘中） |" in out

    prov = _new_tushare_provider()
    prov._api_call = _by_date_api_call(mapping)
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))   # 盘后

    out = prov.get_concept_daily_top_gains(days=1, top_n=3)
    assert f"| {DAY_TODAY}（盘中） |" not in out
    assert f"| {DAY_TODAY} |" in out


def test_weekend_no_intraday_annotation(monkeypatch):
    """周末运行（即使 10:00）不标注盘中——测点落在 weekday()>=5 分支上"""
    prov = _new_tushare_provider()
    prov._api_call = _by_date_api_call({"20260822": _dc_df([("A概念", 10.0, 1.0)])})
    _patch_now(monkeypatch, _real_dt(2026, 8, 22, 10, 0, 0))   # 周六

    out = prov.get_concept_daily_top_gains(days=1, top_n=3)

    assert "（盘中）" not in out
    assert "| 20260822 |" in out


# ==================== 按日缓存 ====================

def test_daily_cache_only_non_today_written(monkeypatch):
    """仅非当日日期写入按日缓存（当日快照盘中/收盘前未定稿）"""
    prov = _new_tushare_provider()
    prov._api_call = _by_date_api_call({
        DAY_TODAY: _dc_df([("A概念", 10.0, 1.0)]),
        DAY_1: _dc_df([("B概念", 5.0, 1.0)]),
    })
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    prov.get_concept_daily_top_gains(days=2, top_n=3)

    assert DAY_1 in TushareProvider._DC_CONCEPT_DAILY_CACHE
    assert DAY_TODAY not in TushareProvider._DC_CONCEPT_DAILY_CACHE


def test_daily_cache_hit_skips_api(monkeypatch):
    """probe 到已缓存日期 → 直接复用，不再打 API"""
    cached_df = _dc_df([("B概念", 5.0, 1.0)])
    TushareProvider._DC_CONCEPT_DAILY_CACHE[DAY_1] = cached_df

    prov = _new_tushare_provider()
    calls = []
    def fake(fn, **kwargs):
        calls.append(kwargs.get("trade_date"))
        return _dc_df([("A概念", 10.0, 1.0)]) if kwargs.get("trade_date") == DAY_TODAY \
            else pd.DataFrame()
    prov._api_call = MagicMock(side_effect=fake)
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    out = prov.get_concept_daily_top_gains(days=2, top_n=3)

    assert out.startswith("# 近 2 日东财概念板块逐日涨幅 TOP3 矩阵")
    assert DAY_1 not in calls          # 缓存命中，未打 API
    assert DAY_TODAY in calls          # 未命中日期照常向后 probe


def test_daily_cache_miss_probes_backward(monkeypatch):
    """缓存未命中 → 从今天向后逐日 probe 直到收满 days 个交易日"""
    prov = _new_tushare_provider()
    calls = []
    def fake(fn, **kwargs):
        calls.append(kwargs.get("trade_date"))
        d = kwargs.get("trade_date")
        return _dc_df([("A概念", 10.0, 1.0)]) if d in (DAY_TODAY, DAY_2) else pd.DataFrame()
    prov._api_call = MagicMock(side_effect=fake)
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    out = prov.get_concept_daily_top_gains(days=2, top_n=3)

    assert out.startswith("# 近 2 日东财概念板块逐日涨幅 TOP3 矩阵")
    assert calls == [DAY_TODAY, DAY_1, DAY_2]   # 中间空表日跳过并继续 probe


def test_daily_cache_cap_prunes_oldest(monkeypatch):
    """缓存超限（>30 条）删除最旧日期"""
    for i in range(30):
        TushareProvider._DC_CONCEPT_DAILY_CACHE[f"2026{str(i).zfill(4)}"] = _dc_df([("X", 1.0, 1.0)])
    prov = _new_tushare_provider()
    prov._api_call = _by_date_api_call({
        DAY_TODAY: _dc_df([("A概念", 10.0, 1.0)]),
        DAY_1: _dc_df([("B概念", 5.0, 1.0)]),
    })
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    prov.get_concept_daily_top_gains(days=2, top_n=3)

    cache = TushareProvider._DC_CONCEPT_DAILY_CACHE
    assert len(cache) <= 30
    assert "20260000" not in cache          # 最旧日期被裁剪
    assert DAY_1 in cache


def test_unexpected_df_shape_returns_failure_string(monkeypatch):
    """意外形状的快照（缺 pct_change 列）→ 外层兜底返回失败串，不抛异常"""
    prov = _new_tushare_provider()
    prov._api_call = _by_date_api_call({DAY_TODAY: pd.DataFrame({"name": ["A"]})})
    _patch_now(monkeypatch, _real_dt(2026, 8, 19, 16, 0, 0))

    out = prov.get_concept_daily_top_gains(days=1, top_n=3)

    assert out.startswith("获取逐日概念涨幅失败:")
    assert not out.startswith("#")          # 门控契约：失败串不以 # 开头


def test_weekend_days_not_listed_as_data_gap(monkeypatch):
    """周末无数据属正常休市，不进入数据缺口清单（仅工作日标注）"""
    prov = _new_tushare_provider()
    prov._api_call = _by_date_api_call({
        "20260817": _dc_df([("A概念", 10.0, 1.0)]),   # 周一
        "20260814": _dc_df([("B概念", 5.0, 1.0)]),    # 周五
    })
    _patch_now(monkeypatch, _real_dt(2026, 8, 17, 16, 0, 0))

    out = prov.get_concept_daily_top_gains(days=2, top_n=3)

    # probe 经过的周六(0815)/周日(0816)空表属正常休市，不产出数据缺口脚注
    assert "> 数据缺口" not in out
    assert out.startswith("# 近 2 日东财概念板块逐日涨幅 TOP3 矩阵")
