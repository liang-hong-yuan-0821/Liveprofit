"""
单元测试：每日增量（incremental，方案 3.6）

- 最近 3 交易日窗口（trade_cal 末 3 位）
- DO UPDATE 路径（bulk_upsert_daily / bulk_upsert_factor 以 update=True 调用）
- 基本信息刷新（get_stock_basic_df / get_fund_basic_df + upsert）
- 库内最新日期已覆盖目标窗口 → 跳过（不重复拉取）
- 概念周刷：refresh_concepts 三种取值（None=按周一自动 / True / False）
- 单日拉取失败仅跳过该日，不阻断其他日
"""

from datetime import date, datetime
from unittest.mock import MagicMock

import pandas as pd
import pytest

from AI.dataflows.store import incremental as inc
from AI.dataflows.store.backfill import StoreFetchError


class _FakeDateTime(datetime):
    """固定 now() 的 datetime 替身（概念周刷"周一"判定用）。"""
    _now = datetime(2026, 8, 31, 8, 0)   # 2026-08-31 = 周一

    @classmethod
    def now(cls, tz=None):
        return cls._now


def _mock_provider(days=None):
    prov = MagicMock()
    prov.get_trade_cal.return_value = pd.DataFrame({
        "trade_date": pd.to_datetime(
            days or ["2026-08-26", "2026-08-27", "2026-08-28", "2026-08-31"]),
        "is_open": 1,
    })
    prov.get_stock_basic_df.return_value = pd.DataFrame({"ts_code": ["000001.SZ"]})
    prov.get_fund_basic_df.return_value = pd.DataFrame({"ts_code": ["510300.SH"]})
    return prov


_FRAMES = {"daily": pd.DataFrame({"ts_code": ["000001.SZ"]}),
           "factor": pd.DataFrame({"ts_code": ["000001.SZ"]})}


@pytest.fixture
def env(monkeypatch):
    """monkeypatch collect_incremental 的环境，返回 (conn, provider, 记录 dict)。"""
    conn = MagicMock()
    prov = _mock_provider()
    monkeypatch.setattr(inc, "init_store_schema", lambda c=None: True)
    monkeypatch.setattr(inc, "get_provider", lambda: prov)
    monkeypatch.setattr(inc, "latest_trade_date", lambda c: None)
    monkeypatch.setattr(inc, "bulk_upsert_daily", MagicMock(return_value=2))
    monkeypatch.setattr(inc, "bulk_upsert_factor", MagicMock(return_value=2))
    monkeypatch.setattr(inc, "upsert_stock_basic", MagicMock(return_value=1))
    monkeypatch.setattr(inc, "upsert_fund_basic", MagicMock(return_value=1))
    monkeypatch.setattr(inc, "fetch_day_frames", MagicMock(return_value=_FRAMES))
    monkeypatch.setattr(inc.concepts, "collect_concepts", MagicMock())
    monkeypatch.setattr(inc, "datetime", _FakeDateTime)
    return conn, prov


def test_collects_last_3_days_with_do_update(env):
    conn, prov = env
    result = inc.collect_incremental(conn, refresh_concepts=False)

    fetched = [c.args[1] for c in inc.fetch_day_frames.call_args_list]
    assert fetched == ["20260827", "20260828", "20260831"]   # 最近 3 交易日
    assert set(result) == {"20260827", "20260828", "20260831"}
    assert result["20260827"] == {"daily": 2, "factor": 2}

    # DO UPDATE 入库（覆盖 tushare 日终修正）
    for call in inc.bulk_upsert_daily.call_args_list:
        assert call.kwargs == {"update": True}
    for call in inc.bulk_upsert_factor.call_args_list:
        assert call.kwargs == {"update": True}
    assert conn.commit.call_count == 4   # 3 日单日提交 + 基本信息独立提交


def test_basics_refreshed_every_day(env):
    conn, prov = env
    inc.collect_incremental(conn, refresh_concepts=False)
    prov.get_stock_basic_df.assert_called_once()
    prov.get_fund_basic_df.assert_called_once()
    inc.upsert_stock_basic.assert_called_once()
    inc.upsert_fund_basic.assert_called_once()


def test_skips_when_window_covered(env, monkeypatch):
    conn, prov = env
    # 库内最新 = 目标窗口最后一天（如周末重复运行）→ 仅日线窗口跳过，
    # 基本信息仍刷新（覆盖更名/新上市）
    monkeypatch.setattr(inc, "latest_trade_date", lambda c: date(2026, 8, 31))
    result = inc.collect_incremental(conn, refresh_concepts=False)
    assert result == {}
    inc.fetch_day_frames.assert_not_called()
    inc.upsert_stock_basic.assert_called_once()   # basic 刷新不被早退短路


def test_single_day_failure_does_not_block_others(env, monkeypatch):
    conn, _ = env

    def flaky(p, d, stock_codes=None, fund_codes=None):
        if d == "20260828":
            raise RuntimeError("接口失败")
        return _FRAMES

    monkeypatch.setattr(inc, "fetch_day_frames", flaky)
    result = inc.collect_incremental(conn, refresh_concepts=False)
    assert set(result) == {"20260827", "20260831"}
    assert conn.commit.call_count == 3   # 2 日单日提交 + 基本信息独立提交


def test_trade_cal_unavailable_skips(env):
    conn, prov = env
    prov.get_trade_cal.return_value = None
    assert inc.collect_incremental(conn, refresh_concepts=False) == {}
    inc.fetch_day_frames.assert_not_called()


def test_provider_unsupported_methods_return_none(env, monkeypatch):
    conn, _ = env
    # akshare 环境下结构化方法返回 None → fetch_day_frames 抛 StoreFetchError
    # → 逐日 warning 跳过，不阻断（结果为空 dict）

    def unsupported(p, d, stock_codes=None, fund_codes=None):
        raise StoreFetchError("数据源不支持 store 采集")

    monkeypatch.setattr(inc, "fetch_day_frames", unsupported)
    result = inc.collect_incremental(conn, refresh_concepts=False)
    assert result == {}


def test_concepts_weekly_none_on_monday(env, monkeypatch):
    conn, _ = env
    # 默认 _FakeDateTime._now = 2026-08-31（周一）→ None 自动周刷
    inc.collect_incremental(conn, refresh_concepts=None)
    inc.concepts.collect_concepts.assert_called_once()


def test_concepts_weekly_false_on_monday(env):
    conn, _ = env
    inc.collect_incremental(conn, refresh_concepts=False)
    inc.concepts.collect_concepts.assert_not_called()


def test_concepts_weekly_true_not_monday(env, monkeypatch):
    conn, _ = env
    friday = type("_Fri", (_FakeDateTime,), {})
    friday._now = datetime(2026, 8, 28, 8, 0)   # 周五
    monkeypatch.setattr(inc, "datetime", friday)
    inc.collect_incremental(conn, refresh_concepts=True)   # 显式 True 强制
    inc.concepts.collect_concepts.assert_called_once()


def test_concepts_weekly_none_not_monday(env, monkeypatch):
    conn, _ = env
    friday = type("_Fri", (_FakeDateTime,), {})
    friday._now = datetime(2026, 8, 28, 8, 0)   # 周五
    monkeypatch.setattr(inc, "datetime", friday)
    inc.collect_incremental(conn, refresh_concepts=None)   # 非周一自动不刷
    inc.concepts.collect_concepts.assert_not_called()
