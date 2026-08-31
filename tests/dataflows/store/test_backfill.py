"""
单元测试：历史回填（backfill，方案 3.6）

- 截断触发降级：mock 单日返回 6000 行 → 按每批 100 代码分批补拉路径被调用
- 单日失败（接口异常与写入异常同一路径）→ 失败清单 JSON 写入与 --retry-missing 重跑
- 概念体系整体失败不阻断日线回填（mock collect_concepts 抛异常 → 日线循环继续）
- 断点续跑：库内 max(trade_date) 之前的交易日跳过
- fetch_day_frames：两市场日线均为空 → StoreFetchError（整日失败）
"""

import json
from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from AI.dataflows.store import backfill as bf
from AI.dataflows.store.backfill import StoreFetchError


# ==================== helpers ====================

def _small_daily(ts_code, trade_date="20260828"):
    return pd.DataFrame({
        "ts_code": [ts_code], "trade_date": [trade_date],
        "open": [1.0], "high": [1.1], "low": [0.9], "close": [1.05],
        "pre_close": [1.0], "change": [0.05], "pct_chg": [5.0],
        "vol": [100.0], "amount": [100.0],
    })


def _small_factor(ts_code, trade_date="20260828"):
    return pd.DataFrame({"ts_code": [ts_code], "trade_date": [trade_date],
                         "adj_factor": [1.0]})


def _mock_provider(daily_frames=None, factor_frames=None, cal_days=None,
                   api_daily_fn=None, api_factor_fn=None):
    """构造 mock TushareProvider。daily_frames/factor_frames 为 (stock_df, fund_df)
    元组（get_full_market_*_df 按调用序返回）；api_daily_fn/api_factor_fn 模拟
    api.daily / api.adj_factor（分批补拉路径用）。"""
    prov = MagicMock()
    prov.connected = True
    prov.name = "Tushare"
    if daily_frames is not None:
        prov.get_full_market_daily_df = MagicMock(side_effect=list(daily_frames))
    if factor_frames is not None:
        prov.get_full_market_factor_df = MagicMock(side_effect=list(factor_frames))
    prov.get_stock_basic_df = MagicMock(return_value=pd.DataFrame(
        {"ts_code": ["000001.SZ", "600000.SH"],
         "list_date": ["19910403", "19991110"],
         "delist_date": [None, None]}))
    prov.get_fund_basic_df = MagicMock(return_value=pd.DataFrame(
        {"ts_code": ["510300.SH"]}))
    prov.get_trade_cal = MagicMock(return_value=pd.DataFrame({
        "trade_date": pd.to_datetime(cal_days or ["2026-08-28"]),
        "is_open": 1,
    }))
    prov.api = MagicMock()
    if api_daily_fn is not None:
        prov.api.daily = api_daily_fn
    if api_factor_fn is not None:
        prov.api.adj_factor = api_factor_fn
    prov._api_call = lambda fn, *a, **kw: fn(*a, **kw)
    return prov


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(bf.time, "sleep", lambda s: None)


@pytest.fixture
def tmp_failure_path(tmp_path, monkeypatch):
    p = tmp_path / "failures.json"
    monkeypatch.setattr(bf, "FAILURE_LIST_PATH", p)
    return p


def _patch_run_env(monkeypatch, provider):
    """monkeypatch run_backfill 的环境：连接/schema/DAO/概念采集。"""
    conn = MagicMock()
    # 断点查询（SELECT DISTINCT trade_date）默认返回空集合
    _cursor = MagicMock()
    _cursor.fetchall.return_value = []
    conn.execute.return_value = _cursor
    monkeypatch.setattr(bf, "get_connection", lambda: conn)
    monkeypatch.setattr(bf, "init_store_schema", lambda c=None: True)
    monkeypatch.setattr(bf, "TushareProvider", lambda: provider)
    monkeypatch.setattr(bf, "upsert_stock_basic", MagicMock(return_value=1))
    monkeypatch.setattr(bf, "upsert_fund_basic", MagicMock(return_value=1))
    monkeypatch.setattr(bf, "bulk_upsert_daily", MagicMock(return_value=2))
    monkeypatch.setattr(bf, "bulk_upsert_factor", MagicMock(return_value=2))
    monkeypatch.setattr(bf.concepts, "collect_concepts", MagicMock())
    return conn


# ==================== fetch_day_frames：截断降级 ====================

def test_truncation_falls_back_to_batched_pull(tmp_path):
    codes = [f"{i:06d}.SZ" for i in range(250)]
    provider = _mock_provider()
    # 直拉返回 6000 行 → 截断
    truncated = pd.DataFrame({"ts_code": range(6000)})
    provider.get_full_market_daily_df = MagicMock(
        side_effect=[truncated, _small_daily("510300.SH")])
    provider.get_full_market_factor_df = MagicMock(
        side_effect=[_small_factor("000001.SZ"), _small_factor("510300.SH")])

    def api_daily(**kwargs):
        batch = kwargs["ts_code"].split(",")
        return pd.DataFrame({"ts_code": batch, "trade_date": [kwargs["trade_date"]] * len(batch)})

    def api_factor(**kwargs):
        batch = kwargs["ts_code"].split(",")
        return pd.DataFrame({"ts_code": batch, "trade_date": [kwargs["trade_date"]] * len(batch),
                             "adj_factor": [1.0] * len(batch)})

    provider.api.daily = MagicMock(side_effect=api_daily)
    provider.api.adj_factor = MagicMock(side_effect=api_factor)

    frames = bf.fetch_day_frames(provider, "20260828", stock_codes=codes,
                                 fund_codes=["510300.SH"])
    # 250 个代码 → 3 批（100/100/50）
    assert provider.api.daily.call_count == 3
    sizes = [len(c.kwargs["ts_code"].split(","))
             for c in provider.api.daily.call_args_list]
    assert sizes == [100, 100, 50]
    # 每批都传 trade_date（单日查询，无区间参数）
    for c in provider.api.daily.call_args_list:
        assert set(c.kwargs) == {"ts_code", "trade_date"}
    # 补拉结果取代截断结果，与基金日线合并；因子未截断，直拉 2 行
    assert len(frames["daily"]) == 250 + 1
    assert len(frames["factor"]) == 2


def test_truncation_without_codes_fails_day():
    provider = _mock_provider()
    provider.get_full_market_daily_df = MagicMock(
        return_value=pd.DataFrame({"ts_code": range(6000)}))
    provider.get_full_market_factor_df = MagicMock(
        return_value=_small_factor("000001.SZ"))
    with pytest.raises(StoreFetchError):
        bf.fetch_day_frames(provider, "20260828", stock_codes=None,
                            fund_codes=None)


def test_fetch_day_frames_empty_both_markets_raises():
    provider = _mock_provider(
        daily_frames=[pd.DataFrame(), pd.DataFrame()],
        factor_frames=[pd.DataFrame(), pd.DataFrame()])
    with pytest.raises(StoreFetchError):
        bf.fetch_day_frames(provider, "20260828")


def test_fetch_day_frames_single_interface_failure_raises():
    provider = _mock_provider(
        daily_frames=[_small_daily("000001.SZ"), _small_daily("510300.SH")],
        factor_frames=[None, _small_factor("510300.SH")])
    with pytest.raises(StoreFetchError):
        bf.fetch_day_frames(provider, "20260828")


# ==================== run_backfill：失败清单与 retry-missing ====================

def _run_env(monkeypatch, provider, tmp_failure_path):
    conn = _patch_run_env(monkeypatch, provider)
    return conn


def test_day_failure_written_to_failure_list_and_retry_missing(monkeypatch,
                                                               tmp_failure_path):
    provider = _mock_provider(cal_days=["2026-08-26", "2026-08-27", "2026-08-28"])
    frames_ok = {"daily": _small_daily("000001.SZ"), "factor": _small_factor("000001.SZ")}

    def fetch_day_frames(p, d, stock_codes=None, fund_codes=None):
        if d == "20260827":
            raise StoreFetchError("mock 接口失败")
        return frames_ok

    monkeypatch.setattr(bf, "fetch_day_frames", fetch_day_frames)
    conn = _run_env(monkeypatch, provider, tmp_failure_path)

    summary = bf.run_backfill("2026-08-26", "2026-08-28")
    assert summary["days_done"] == 2
    assert summary["failed_days"] == ["20260827"]
    assert json.loads(tmp_failure_path.read_text(encoding="utf-8")) == ["20260827"]
    # 提交 = 概念采集 1 + 基本信息 1 + 成功日 2（独立提交，逐日失败回滚不丢前置采集）
    assert conn.commit.call_count == 4

    # ---- --retry-missing：该日恢复成功 → 从清单移除 ----
    def fetch_day_frames_ok(p, d, stock_codes=None, fund_codes=None):
        return frames_ok

    monkeypatch.setattr(bf, "fetch_day_frames", fetch_day_frames_ok)
    summary2 = bf.run_backfill("2026-08-26", "2026-08-28", retry_missing=True)
    assert summary2["retry_ok"] == 1
    assert summary2["failed_days"] == []
    assert json.loads(tmp_failure_path.read_text(encoding="utf-8")) == []


def test_write_exception_same_path_as_fetch_exception(monkeypatch,
                                                      tmp_failure_path):
    provider = _mock_provider(cal_days=["2026-08-26"])
    frames_ok = {"daily": _small_daily("000001.SZ"), "factor": _small_factor("000001.SZ")}
    conn = _run_env(monkeypatch, provider, tmp_failure_path)
    monkeypatch.setattr(bf, "fetch_day_frames",
                        lambda p, d, stock_codes=None, fund_codes=None: frames_ok)
    # 写入异常（连接抖动/约束违例）→ 与接口失败同一路径：重试后跳过 + 记失败清单
    monkeypatch.setattr(bf, "bulk_upsert_daily",
                        MagicMock(side_effect=RuntimeError("写入失败")))

    summary = bf.run_backfill("2026-08-26", "2026-08-26")
    assert summary["days_done"] == 0
    assert summary["failed_days"] == ["20260826"]
    assert json.loads(tmp_failure_path.read_text(encoding="utf-8")) == ["20260826"]
    conn.rollback.assert_called()


def test_concepts_and_basics_committed_before_day_loop(monkeypatch,
                                                       tmp_failure_path):
    """M1 回归：全部交易日失败时，概念与基本信息仍独立提交，
    不被逐日失败 rollback 静默丢弃。"""
    provider = _mock_provider(cal_days=["2026-08-26"])
    conn = _run_env(monkeypatch, provider, tmp_failure_path)

    def always_fail(p, d, stock_codes=None, fund_codes=None):
        raise StoreFetchError("mock 接口失败")

    monkeypatch.setattr(bf, "fetch_day_frames", always_fail)
    summary = bf.run_backfill("2026-08-26", "2026-08-26")
    assert summary["days_done"] == 0
    assert summary["failed_days"] == ["20260826"]
    # 概念采集 1 次提交 + 基本信息 1 次提交（逐日失败只 rollback 当日写入）
    assert conn.commit.call_count == 2


def test_concepts_failure_does_not_block_daily_backfill(monkeypatch,
                                                        tmp_failure_path):
    provider = _mock_provider(cal_days=["2026-08-26"])
    frames_ok = {"daily": _small_daily("000001.SZ"), "factor": _small_factor("000001.SZ")}
    monkeypatch.setattr(bf, "fetch_day_frames",
                        lambda p, d, stock_codes=None, fund_codes=None: frames_ok)
    monkeypatch.setattr(bf.concepts, "collect_concepts",
                        MagicMock(side_effect=RuntimeError("概念体系整体失败")))
    conn = _run_env(monkeypatch, provider, tmp_failure_path)

    summary = bf.run_backfill("2026-08-26", "2026-08-26")
    assert summary["days_done"] == 1          # 日线循环继续、当日正常入库
    assert summary["failed_days"] == []
    assert bf.bulk_upsert_daily.call_count == 1


def test_resume_skips_existing_days(monkeypatch, tmp_failure_path):
    """断点续跑按存在性跳过：库内已有 0827（如小窗口验证先入库尾部），
    只补拉缺失的 0826/0828——不能用 max 截断（会把 0826 误判跳过）。"""
    provider = _mock_provider(cal_days=["2026-08-26", "2026-08-27", "2026-08-28"])
    frames_ok = {"daily": _small_daily("000001.SZ"), "factor": _small_factor("000001.SZ")}
    fetched = []
    conn = _run_env(monkeypatch, provider, tmp_failure_path)
    conn.execute.return_value.fetchall.return_value = [("2026-08-27",)]
    monkeypatch.setattr(bf, "fetch_day_frames",
                        lambda p, d, stock_codes=None, fund_codes=None:
                        (fetched.append(d) or frames_ok))

    summary = bf.run_backfill("2026-08-26", "2026-08-28")
    assert fetched == ["20260826", "20260828"]
    assert summary["days_done"] == 2


def test_skip_concepts_flag(monkeypatch, tmp_failure_path):
    """--skip-concepts：跳过概念体系采集（数据已新鲜时省 ~35 分钟）。"""
    provider = _mock_provider(cal_days=["2026-08-26"])
    frames_ok = {"daily": _small_daily("000001.SZ"), "factor": _small_factor("000001.SZ")}
    conn = _run_env(monkeypatch, provider, tmp_failure_path)
    monkeypatch.setattr(bf, "fetch_day_frames",
                        lambda p, d, stock_codes=None, fund_codes=None: frames_ok)

    summary = bf.run_backfill("2026-08-26", "2026-08-26", skip_concepts=True)
    bf.concepts.collect_concepts.assert_not_called()
    assert summary["days_done"] == 1
    assert bf.bulk_upsert_daily.call_count == 1
    # 提交 = 基本信息 1 + 当日 1（概念跳过无提交）
    assert conn.commit.call_count == 2


def test_basics_failure_does_not_block(monkeypatch, tmp_failure_path):
    provider = _mock_provider(cal_days=["2026-08-26"])
    provider.get_stock_basic_df = MagicMock(side_effect=RuntimeError("basic 失败"))
    frames_ok = {"daily": _small_daily("000001.SZ"), "factor": _small_factor("000001.SZ")}
    monkeypatch.setattr(bf, "fetch_day_frames",
                        lambda p, d, stock_codes=None, fund_codes=None: frames_ok)
    _run_env(monkeypatch, provider, tmp_failure_path)

    summary = bf.run_backfill("2026-08-26", "2026-08-26")
    assert summary["days_done"] == 1


def test_provider_not_connected_returns_early(monkeypatch, tmp_failure_path):
    provider = _mock_provider(cal_days=["2026-08-26"])
    provider.connected = False
    _run_env(monkeypatch, provider, tmp_failure_path)
    summary = bf.run_backfill("2026-08-26", "2026-08-26")
    assert summary["days_done"] == 0
