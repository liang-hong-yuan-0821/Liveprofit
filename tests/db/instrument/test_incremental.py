"""
单元测试：统一每日增量（db.instrument.ingest.incremental，
迁移自 tests/dataflows/store/test_incremental）

- 最近 3 交易日窗口（trade_cal 末 3 位）
- DO UPDATE 路径（bulk_upsert_daily / bulk_upsert_factor 以 update=True 调用）
- 基本信息刷新拆两路（instrument + stock_info / fund_info）
- 指数采集：自举前置 + 双源兜底 + source 写实际成功源 + 缺段拒绝
- 库内最新日期已覆盖目标窗口 → 跳过（不重复拉取）
- 板块周刷：refresh_sectors 三种取值（None=按周一自动 / True / False）
- 单日拉取失败仅跳过该日，不阻断其他日
- 兜底源拉取异常只跳过该码 bars（与 backfill 对称修复回归）
- 非 CN 目标（US 3 + KS11）不请求因子（idx_factor_pro 为 CN 端点）
"""

from datetime import date, datetime
from unittest.mock import MagicMock

import pandas as pd
import pytest

from db.instrument.ingest import incremental as inc
from db.instrument.ingest.frames import StoreFetchError


class _FakeDateTime(datetime):
    """固定 now() 的 datetime 替身（板块周刷"周一"判定用）。"""
    _now = datetime(2026, 8, 31, 8, 0)   # 2026-08-31 = 周一

    @classmethod
    def now(cls, tz=None):
        return cls._now


def _mock_provider(days=None):
    prov = MagicMock()
    prov.connected = True
    prov.get_trade_cal.return_value = pd.DataFrame({
        "trade_date": pd.to_datetime(
            days or ["2026-08-26", "2026-08-27", "2026-08-28", "2026-08-31"]),
        "is_open": 1,
    })
    prov.get_stock_basic_df.return_value = pd.DataFrame({
        "ts_code": ["000001.SZ"], "name": ["平安银行"],
        "list_status": ["L"], "list_date": ["19910403"],
        "delist_date": [None], "exchange": ["SZSE"],
        "market": ["主板"], "area": ["深圳"],
    })
    prov.get_fund_basic_df.return_value = pd.DataFrame({
        "ts_code": ["510300.SH"], "name": ["沪深300ETF"],
        "list_date": ["20120528"], "delist_date": [None],
    })
    # 指数接口默认无数据（指数采集单独测试）
    prov.get_index_data_df = MagicMock(return_value=None)
    prov.get_index_factor_df = MagicMock(return_value=None)
    return prov


_FRAMES = {"daily": pd.DataFrame({"ts_code": ["000001.SZ"]}),
           "factor": pd.DataFrame({"ts_code": ["000001.SZ"]})}


@pytest.fixture
def env(monkeypatch):
    """monkeypatch collect_incremental 的环境，返回 (conn, provider, 记录 dict)。"""
    conn = MagicMock()
    prov = _mock_provider()
    monkeypatch.setattr(inc, "latest_trade_date", lambda c, instrument_types=None: None)
    monkeypatch.setattr(inc, "bulk_upsert_daily", MagicMock(return_value=2))
    monkeypatch.setattr(inc, "bulk_upsert_factor", MagicMock(return_value=2))
    monkeypatch.setattr(inc, "bulk_upsert_factor_daily", MagicMock(return_value=2))
    monkeypatch.setattr(inc, "_bootstrap_instruments", MagicMock())
    monkeypatch.setattr(inc.instrument_dao, "upsert_instrument", MagicMock(return_value=1))
    monkeypatch.setattr(inc.stock_info, "upsert_stock_info", MagicMock(return_value=1))
    monkeypatch.setattr(inc.fund_info, "upsert_fund_info", MagicMock(return_value=1))
    monkeypatch.setattr(inc, "fetch_day_frames", MagicMock(return_value=_FRAMES))
    monkeypatch.setattr(inc, "collect_sectors", MagicMock())
    monkeypatch.setattr(inc, "datetime", _FakeDateTime)
    return conn, prov


def _run(conn, prov, **kw):
    return inc.collect_incremental(conn, lambda: prov, **kw)


def test_collects_last_3_days_with_do_update(env):
    conn, prov = env
    result = _run(conn, prov, refresh_sectors=False)

    fetched = [c.args[1] for c in inc.fetch_day_frames.call_args_list]
    assert fetched == ["20260827", "20260828", "20260831"]   # 最近 3 交易日
    assert set(result["daily"]) == {"20260827", "20260828", "20260831"}
    assert result["daily"]["20260827"] == {"daily": 2, "factor": 2}

    # DO UPDATE 入库（覆盖 tushare 日终修正）
    for call in inc.bulk_upsert_daily.call_args_list:
        assert call.kwargs == {"update": True}
    for call in inc.bulk_upsert_factor.call_args_list:
        assert call.kwargs == {"update": True}
    # 提交 = 基本信息 1 + 指数自举 1 + 指数步骤结束 1 + 3 日单日提交
    assert conn.commit.call_count == 6


def test_basics_refreshed_split_two_ways(env):
    conn, prov = env
    _run(conn, prov, refresh_sectors=False)
    prov.get_stock_basic_df.assert_called_once()
    prov.get_fund_basic_df.assert_called_once()
    # stock 拆两路：instrument 通用列（instrument_type='stock'）+ stock_info 三列
    inst_df = inc.instrument_dao.upsert_instrument.call_args_list[0].args[1]
    assert inst_df["instrument_type"].iloc[0] == "stock"
    assert inst_df["data_source"].iloc[0] == "tushare"
    inc.stock_info.upsert_stock_info.assert_called_once()
    inc.fund_info.upsert_fund_info.assert_called_once()
    # fund 行 list_status 恒 NULL（fund_basic 无此列）
    fund_df = [c.args[1] for c in inc.instrument_dao.upsert_instrument.call_args_list][1]
    assert fund_df["list_status"].iloc[0] is None


def test_index_rows_do_not_cover_daily_window(env, monkeypatch):
    """CR B1 回归：库内最新 = 指数行（步骤 3 先写入）但个股基金无数据 →
    门控按数据域判定（instrument_type 过滤），步骤 4 仍执行。"""
    conn, prov = env
    # latest_trade_date 按 instrument_type 过滤后返回 None（个股基金无行）
    latest_mock = MagicMock(return_value=None)
    monkeypatch.setattr(inc, "latest_trade_date", latest_mock)
    _run(conn, prov, refresh_sectors=False)
    # 步骤 4 执行：3 个交易日均拉取
    fetched = [c.args[1] for c in inc.fetch_day_frames.call_args_list]
    assert fetched == ["20260827", "20260828", "20260831"]
    # 门控调用带 instrument_types 过滤参数（B1 修复的落点）
    assert latest_mock.call_args.kwargs == {"instrument_types": ("stock", "fund")}


def test_index_fallback_exception_skips_bars_only(env):
    """兜底源拉取异常只跳过该码 bars、不中断其余目标（与 backfill 对称的
    修复回归：新浪网络异常时兜底源抛异常不应吞掉整个步骤 3）。"""
    conn, prov = env
    fallback = MagicMock()
    fallback.name = "AKShare"
    fallback.get_index_data_df = MagicMock(side_effect=RuntimeError("新浪网络异常"))
    result = inc.collect_incremental(
        conn, lambda: prov, lambda: fallback, refresh_sectors=False)
    assert result["index"] == {"bars": 0, "factors": 0}
    # 异常被隔离在单码：全部 13 目标都走完兜底（各抛一次），而非首个异常中断
    # 整个步骤 3（修复前会在首个码 000001.SH 就 abort，call_count 只有 1）
    assert fallback.get_index_data_df.call_count == len(inc.INDEX_TARGETS)
    fetched = [c.args[1] for c in inc.fetch_day_frames.call_args_list]
    assert fetched == ["20260827", "20260828", "20260831"]


def test_non_cn_codes_skip_factor_fetch(env):
    """非 CN 目标（US 3 + KS11）不请求因子（idx_factor_pro 为 tushare CN 端点）。"""
    conn, prov = env
    _run(conn, prov, refresh_sectors=False)
    codes = {c.args[0] for c in prov.get_index_factor_df.call_args_list}
    assert codes == {c for c in inc.INDEX_TARGETS if inc._is_cn_index_code(c)}
    assert ".INX" not in codes and "KS11" not in codes


def test_skips_when_window_covered(env, monkeypatch):
    conn, prov = env
    # 库内最新 = 目标窗口最后一天（如周末重复运行）→ 仅日线窗口跳过，
    # 基本信息仍刷新（覆盖更名/新上市）
    monkeypatch.setattr(inc, "latest_trade_date", lambda c, instrument_types=None: date(2026, 8, 31))
    result = _run(conn, prov, refresh_sectors=False)
    assert result["daily"] == {}
    inc.fetch_day_frames.assert_not_called()
    inc.instrument_dao.upsert_instrument.assert_called()   # basic 刷新不被早退短路


def test_single_day_failure_does_not_block_others(env, monkeypatch):
    conn, prov = env

    def flaky(p, d, stock_codes=None, fund_codes=None):
        if d == "20260828":
            raise RuntimeError("接口失败")
        return _FRAMES

    monkeypatch.setattr(inc, "fetch_day_frames", flaky)
    result = _run(conn, prov, refresh_sectors=False)
    assert set(result["daily"]) == {"20260827", "20260831"}
    assert conn.commit.call_count == 5   # 2 日单日提交 + 基本信息 + 指数 2 次


def test_trade_cal_unavailable_skips(env):
    conn, prov = env
    prov.get_trade_cal.return_value = None
    assert _run(conn, prov, refresh_sectors=False) == {"error": "交易日历不可用"}
    inc.fetch_day_frames.assert_not_called()


def test_provider_unsupported_methods_return_none(env, monkeypatch):
    conn, prov = env
    # akshare 环境下结构化方法返回 None → fetch_day_frames 抛 StoreFetchError
    # → 逐日 warning 跳过，不阻断（结果为空 dict）

    def unsupported(p, d, stock_codes=None, fund_codes=None):
        raise StoreFetchError("数据源不支持采集")

    monkeypatch.setattr(inc, "fetch_day_frames", unsupported)
    result = _run(conn, prov, refresh_sectors=False)
    assert result["daily"] == {}


def test_sectors_weekly_none_on_monday(env):
    conn, prov = env
    # 默认 _FakeDateTime._now = 2026-08-31（周一）→ None 自动周刷
    _run(conn, prov, refresh_sectors=None)
    inc.collect_sectors.assert_called_once()


def test_sectors_weekly_false_on_monday(env):
    conn, prov = env
    _run(conn, prov, refresh_sectors=False)
    inc.collect_sectors.assert_not_called()


def test_sectors_weekly_true_not_monday(env, monkeypatch):
    conn, prov = env
    friday = type("_Fri", (_FakeDateTime,), {})
    friday._now = datetime(2026, 8, 28, 8, 0)   # 周五
    monkeypatch.setattr(inc, "datetime", friday)
    _run(conn, prov, refresh_sectors=True)   # 显式 True 强制
    inc.collect_sectors.assert_called_once()


def test_sectors_weekly_none_not_monday(env, monkeypatch):
    conn, prov = env
    friday = type("_Fri", (_FakeDateTime,), {})
    friday._now = datetime(2026, 8, 28, 8, 0)   # 周五
    monkeypatch.setattr(inc, "datetime", friday)
    _run(conn, prov, refresh_sectors=None)   # 非周一自动不刷
    inc.collect_sectors.assert_not_called()


# ==================== 指数采集：自举 + 兜底 + source 标签 ====================

def _index_df(code="000001.SH"):
    return pd.DataFrame({
        "trade_date": ["2026-08-31"],
        "open": [1.0], "high": [1.1], "low": [0.9], "close": [1.05],
        "pre_close": [1.0], "change": [0.05], "pct_chg": [5.0],
        "vol": [100.0], "amount": [100.0],
    })


def test_index_bars_fallback_writes_actual_source(env):
    conn, prov = env
    fallback = MagicMock()
    fallback.name = "AKShare"  # _provider_source 用 provider.name（CR M1）
    prov.name = "Tushare"
    prov.get_index_data_df = MagicMock(return_value=None)   # 主源无数据
    fallback.get_index_data_df = MagicMock(return_value=_index_df())
    prov.get_index_factor_df = MagicMock(return_value=pd.DataFrame(
        {"trade_date": ["2026-08-31"]}))

    _run2 = inc.collect_incremental(conn, lambda: prov, lambda: fallback,
                                    refresh_sectors=False)
    assert _run2 is not None
    written = inc.bulk_upsert_daily.call_args_list[0].args[1]
    # 兜底源成功 → source 写实际成功源标签（决策 3.4.1：不照搬 data_source）
    assert written["source"].iloc[0] == "akshare"
    inc._bootstrap_instruments.assert_called_once()


def test_index_factor_missing_chunks_rejected(env):
    conn, prov = env
    prov.get_index_data_df = MagicMock(return_value=_index_df())
    factor_df = pd.DataFrame({"trade_date": ["2026-08-31"]})
    factor_df.attrs["missing_chunks"] = ["2026-01-01 ~ 2026-02-01"]
    prov.get_index_factor_df = MagicMock(return_value=factor_df)

    inc.collect_incremental(conn, lambda: prov, refresh_sectors=False)
    inc.bulk_upsert_factor_daily.assert_not_called()   # 缺段拒绝部分入库
