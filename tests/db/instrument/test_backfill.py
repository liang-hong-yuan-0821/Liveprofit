"""
单元测试：历史回填（db.instrument.ingest.backfill，迁移自 tests/dataflows/store/test_backfill）

- 截断触发降级：mock 单日返回 6000 行 → 按每批 100 代码分批补拉路径被调用
- 单日失败（接口异常与写入异常同一路径）→ 失败清单 JSON 写入与 --retry-missing 重跑
- 板块体系整体失败不阻断日线回填
- 断点续跑：按库内已入库交易日集合跳过（禁用 max 截断）
- fetch_day_frames：两市场日线均为空 → StoreFetchError（整日失败）
- 指数全历史内置分项（backfill_index_history）：自举前置 + DO UPDATE + 断点跳过
"""

import json
from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from db.instrument.ingest import backfill as bf
from db.instrument.ingest.frames import StoreFetchError


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
    """构造 mock TushareProvider（provider_factory 注入）。"""
    prov = MagicMock()
    prov.connected = True
    prov.name = "Tushare"
    if daily_frames is not None:
        prov.get_full_market_daily_df = MagicMock(side_effect=list(daily_frames))
    if factor_frames is not None:
        prov.get_full_market_factor_df = MagicMock(side_effect=list(factor_frames))
    prov.get_stock_basic_df = MagicMock(return_value=pd.DataFrame(
        {"ts_code": ["000001.SZ", "600000.SH"]}))
    prov.get_fund_basic_df = MagicMock(return_value=pd.DataFrame(
        {"ts_code": ["510300.SH"]}))
    prov.get_trade_cal = MagicMock(return_value=pd.DataFrame({
        "trade_date": pd.to_datetime(cal_days or ["2026-08-28"]),
        "is_open": 1,
    }))
    prov.get_index_data_df = MagicMock(return_value=None)
    prov.get_index_factor_df = MagicMock(return_value=None)
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
    """monkeypatch run_backfill 的环境：DAO/板块采集/指数分项（隔离测试）。"""
    conn = MagicMock()
    _cursor = MagicMock()
    _cursor.fetchall.return_value = []
    conn.execute.return_value = _cursor
    monkeypatch.setattr(bf, "bulk_upsert_daily", MagicMock(return_value=2))
    monkeypatch.setattr(bf, "bulk_upsert_factor", MagicMock(return_value=2))
    monkeypatch.setattr(bf, "collect_sectors", MagicMock())
    # 指数分项隔离（单独测试 backfill_index_history）
    monkeypatch.setattr(bf, "backfill_index_history",
                        MagicMock(return_value={"bars": 0, "factors": 0}))
    return conn


# ==================== fetch_day_frames：截断降级 ====================

def test_truncation_falls_back_to_batched_pull():
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

    summary = bf.run_backfill(conn, "2026-08-26", "2026-08-28",
                              provider_factory=lambda: provider)
    assert summary["days_done"] == 2
    assert summary["failed_days"] == ["20260827"]
    assert json.loads(tmp_failure_path.read_text(encoding="utf-8")) == ["20260827"]
    # 提交 = 板块采集 1 + 成功日 2（独立提交，逐日失败回滚不丢前置采集）
    assert conn.commit.call_count == 3

    # ---- --retry-missing：该日恢复成功 → 从清单移除 ----
    def fetch_day_frames_ok(p, d, stock_codes=None, fund_codes=None):
        return frames_ok

    monkeypatch.setattr(bf, "fetch_day_frames", fetch_day_frames_ok)
    summary2 = bf.run_backfill(conn, "2026-08-26", "2026-08-28", retry_missing=True,
                               provider_factory=lambda: provider)
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

    summary = bf.run_backfill(conn, "2026-08-26", "2026-08-26",
                              provider_factory=lambda: provider)
    assert summary["days_done"] == 0
    assert summary["failed_days"] == ["20260826"]
    assert json.loads(tmp_failure_path.read_text(encoding="utf-8")) == ["20260826"]
    conn.rollback.assert_called()


def test_sectors_committed_before_day_loop(monkeypatch, tmp_failure_path):
    """回归：全部交易日失败时，板块采集仍独立提交，不被逐日失败 rollback 静默丢弃。"""
    provider = _mock_provider(cal_days=["2026-08-26"])
    conn = _run_env(monkeypatch, provider, tmp_failure_path)

    def always_fail(p, d, stock_codes=None, fund_codes=None):
        raise StoreFetchError("mock 接口失败")

    monkeypatch.setattr(bf, "fetch_day_frames", always_fail)
    summary = bf.run_backfill(conn, "2026-08-26", "2026-08-26",
                              provider_factory=lambda: provider)
    assert summary["days_done"] == 0
    assert summary["failed_days"] == ["20260826"]
    # 板块采集 1 次提交（逐日失败只 rollback 当日写入）
    assert conn.commit.call_count == 1


def test_sectors_failure_does_not_block_daily_backfill(monkeypatch,
                                                       tmp_failure_path):
    provider = _mock_provider(cal_days=["2026-08-26"])
    frames_ok = {"daily": _small_daily("000001.SZ"), "factor": _small_factor("000001.SZ")}
    monkeypatch.setattr(bf, "fetch_day_frames",
                        lambda p, d, stock_codes=None, fund_codes=None: frames_ok)
    monkeypatch.setattr(bf, "collect_sectors",
                        MagicMock(side_effect=RuntimeError("板块体系整体失败")))
    conn = _run_env(monkeypatch, provider, tmp_failure_path)

    summary = bf.run_backfill(conn, "2026-08-26", "2026-08-26",
                              provider_factory=lambda: provider)
    assert summary["days_done"] == 1          # 日线循环继续、当日正常入库
    assert summary["failed_days"] == []
    assert bf.bulk_upsert_daily.call_count == 1


def test_resume_ignores_index_rows_for_daily_domain(monkeypatch, tmp_failure_path):
    """CR BLOCKER 1 回归：库内已有指数交易日行（指数回填先写）时，
    个股基金回填的断点判定按数据域过滤——指数行不抬高个股基金断点。"""
    provider = _mock_provider(cal_days=["2026-08-26", "2026-08-27", "2026-08-28"])
    frames_ok = {"daily": _small_daily("000001.SZ"), "factor": _small_factor("000001.SZ")}
    fetched = []
    conn = _run_env(monkeypatch, provider, tmp_failure_path)
    # 断点查询按 SQL 子串分发：指数行查询（含 JOIN market.instrument）返回空、
    # 个股基金域查询返回空 → 三日全部待回填
    conn.execute.side_effect = None
    monkeypatch.setattr(bf, "fetch_day_frames",
                        lambda p, d, stock_codes=None, fund_codes=None:
                        (fetched.append(d) or frames_ok))

    summary = bf.run_backfill(conn, "2026-08-26", "2026-08-28",
                              provider_factory=lambda: provider)
    # 三日均被纳入待回填（指数行不影响个股基金域断点）
    assert fetched == ["20260826", "20260827", "20260828"]
    assert summary["days_done"] == 3
    # 断点查询带域过滤参数
    domain_calls = [c for c in conn.execute.call_args_list
                    if "instrument_type = ANY" in str(c.args[0])]
    assert domain_calls, "断点查询应含 instrument_type 过滤"


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

    summary = bf.run_backfill(conn, "2026-08-26", "2026-08-28",
                              provider_factory=lambda: provider)
    assert fetched == ["20260826", "20260828"]
    assert summary["days_done"] == 2


def test_skip_concepts_flag(monkeypatch, tmp_failure_path):
    """--skip-concepts：跳过板块体系采集（数据已新鲜时省 ~35 分钟）。"""
    provider = _mock_provider(cal_days=["2026-08-26"])
    frames_ok = {"daily": _small_daily("000001.SZ"), "factor": _small_factor("000001.SZ")}
    conn = _run_env(monkeypatch, provider, tmp_failure_path)
    monkeypatch.setattr(bf, "fetch_day_frames",
                        lambda p, d, stock_codes=None, fund_codes=None: frames_ok)

    summary = bf.run_backfill(conn, "2026-08-26", "2026-08-26", skip_concepts=True,
                              provider_factory=lambda: provider)
    bf.collect_sectors.assert_not_called()
    assert summary["days_done"] == 1
    assert bf.bulk_upsert_daily.call_count == 1
    # 提交 = 当日 1（板块跳过无提交）
    assert conn.commit.call_count == 1


def test_basics_failure_does_not_block(monkeypatch, tmp_failure_path):
    provider = _mock_provider(cal_days=["2026-08-26"])
    provider.get_stock_basic_df = MagicMock(side_effect=RuntimeError("basic 失败"))
    frames_ok = {"daily": _small_daily("000001.SZ"), "factor": _small_factor("000001.SZ")}
    monkeypatch.setattr(bf, "fetch_day_frames",
                        lambda p, d, stock_codes=None, fund_codes=None: frames_ok)
    conn = _run_env(monkeypatch, provider, tmp_failure_path)

    summary = bf.run_backfill(conn, "2026-08-26", "2026-08-26",
                              provider_factory=lambda: provider)
    assert summary["days_done"] == 1


def test_provider_not_connected_returns_early(monkeypatch, tmp_failure_path):
    provider = _mock_provider(cal_days=["2026-08-26"])
    provider.connected = False
    conn = _run_env(monkeypatch, provider, tmp_failure_path)
    summary = bf.run_backfill(conn, "2026-08-26", "2026-08-26",
                              provider_factory=lambda: provider)
    assert summary["days_done"] == 0


# ==================== 指数全历史内置分项（3.5.1） ====================

def _index_bars_df(code, days):
    return pd.DataFrame({
        "trade_date": pd.to_datetime(days),
        "open": [1.0] * len(days), "high": [1.1] * len(days),
        "low": [0.9] * len(days), "close": [1.05] * len(days),
        "pre_close": [1.0] * len(days), "change": [0.05] * len(days),
        "pct_chg": [5.0] * len(days), "vol": [100.0] * len(days),
        "amount": [100.0] * len(days),
    })


def test_backfill_index_history_do_update_and_bootstrap(monkeypatch):
    conn = MagicMock()
    _cursor = MagicMock()
    _cursor.fetchall.return_value = []
    conn.execute.return_value = _cursor
    upsert = MagicMock(return_value=2)
    monkeypatch.setattr(bf, "_bootstrap_instruments", MagicMock())
    monkeypatch.setattr(bf, "bulk_upsert_daily", upsert)
    monkeypatch.setattr(bf, "bulk_upsert_factor_daily", MagicMock(return_value=1))

    provider = MagicMock()
    provider.get_index_data_df = MagicMock(
        return_value=_index_bars_df("000001.SH", ["2026-08-26", "2026-08-27"]))
    provider.get_index_factor_df = MagicMock(return_value=pd.DataFrame(
        {"trade_date": ["2026-08-26", "2026-08-27"]}))

    result = bf.backfill_index_history(conn, provider, "2026-08-26", "2026-08-27")
    assert result["bars"] > 0
    # 自举前置
    bf._bootstrap_instruments.assert_called_once()
    # 指数行 DO UPDATE（补齐迁移行缺失三列的显式例外）
    assert upsert.call_args_list[0].kwargs == {"update": True}
    # 帧列集：扩列三列 + source/updated_at（首帧 = 首个目标 000001.SH）
    written = upsert.call_args_list[0].args[1]
    for col in ("pre_close", "change", "pct_chg", "source", "updated_at"):
        assert col in written.columns
    assert written["ts_code"].iloc[0] == "000001.SH"


def test_backfill_index_history_skips_existing_days(monkeypatch):
    conn = MagicMock()
    _cursor = MagicMock()
    _cursor.fetchall.return_value = [(pd.Timestamp("2026-08-26"),),
                                     (pd.Timestamp("2026-08-27"),)]
    # 缺列行检查：fetchone 返回 None = 无缺列行（跳过条件满足）
    _cursor.fetchone.return_value = None
    conn.execute.return_value = _cursor
    monkeypatch.setattr(bf, "_bootstrap_instruments", MagicMock())
    monkeypatch.setattr(bf, "bulk_upsert_daily", MagicMock())

    provider = MagicMock()
    provider.get_index_data_df = MagicMock(
        return_value=_index_bars_df("000001.SH", ["2026-08-26", "2026-08-27"]))

    result = bf.backfill_index_history(conn, provider, "2026-08-26", "2026-08-27")
    # 两日已全部入库且无缺列行 → 段跳过，无写入
    assert result["bars"] == 0
    bf.bulk_upsert_daily.assert_not_called()


def test_backfill_index_history_reruns_missing_column_rows(monkeypatch):
    """缺列行存在（迁移迁入行 pre_close NULL）→ 日期已入库仍重跑 DO UPDATE 补齐。"""
    conn = MagicMock()
    _cursor = MagicMock()
    _cursor.fetchall.return_value = [(pd.Timestamp("2026-08-26"),),
                                     (pd.Timestamp("2026-08-27"),)]
    _cursor.fetchone.return_value = (1,)   # 存在缺列行
    conn.execute.return_value = _cursor
    monkeypatch.setattr(bf, "_bootstrap_instruments", MagicMock())
    upsert = MagicMock(return_value=2)
    monkeypatch.setattr(bf, "bulk_upsert_daily", upsert)
    monkeypatch.setattr(bf, "bulk_upsert_factor_daily", MagicMock(return_value=1))

    provider = MagicMock()
    provider.get_index_data_df = MagicMock(
        return_value=_index_bars_df("000001.SH", ["2026-08-26", "2026-08-27"]))
    provider.get_index_factor_df = MagicMock(return_value=None)

    result = bf.backfill_index_history(conn, provider, "2026-08-26", "2026-08-27")
    # 缺列行存在 → 不跳过，DO UPDATE 补齐
    assert result["bars"] > 0
    assert upsert.call_args.kwargs == {"update": True}


def test_backfill_index_factor_missing_chunks_rejected(monkeypatch):
    conn = MagicMock()
    _cursor = MagicMock()
    _cursor.fetchall.return_value = []
    conn.execute.return_value = _cursor
    upsert_factor = MagicMock()
    monkeypatch.setattr(bf, "_bootstrap_instruments", MagicMock())
    monkeypatch.setattr(bf, "bulk_upsert_daily", MagicMock(return_value=1))
    monkeypatch.setattr(bf, "bulk_upsert_factor_daily", upsert_factor)

    provider = MagicMock()
    provider.get_index_data_df = MagicMock(
        return_value=_index_bars_df("000001.SH", ["2026-08-26"]))
    factor_df = pd.DataFrame({"trade_date": ["2026-08-26"]})
    factor_df.attrs["missing_chunks"] = ["2025-01-01 ~ 2025-05-01"]
    provider.get_index_factor_df = MagicMock(return_value=factor_df)

    bf.backfill_index_history(conn, provider, "2026-08-26", "2026-08-26")
    # 缺段拒绝部分入库
    upsert_factor.assert_not_called()


def test_backfill_index_history_non_cn_fallback_source_tagged(monkeypatch):
    """非 CN 码主源（tushare 不支持海外）返回 None → 兜底源成功，帧 source
    写实际成功源 'akshare'（US/KR 上线双源兜底落点）。"""
    conn = MagicMock()
    _cursor = MagicMock()
    _cursor.fetchall.return_value = []
    _cursor.fetchone.return_value = None
    conn.execute.return_value = _cursor
    upsert = MagicMock(return_value=2)
    monkeypatch.setattr(bf, "_bootstrap_instruments", MagicMock())
    monkeypatch.setattr(bf, "bulk_upsert_daily", upsert)
    monkeypatch.setattr(bf, "bulk_upsert_factor_daily", MagicMock(return_value=1))

    provider = MagicMock()
    provider.name = "Tushare"
    provider.get_index_data_df = MagicMock(return_value=None)
    provider.get_index_factor_df = MagicMock(return_value=None)
    fallback = MagicMock()
    fallback.name = "AKShare"
    fallback.get_index_data_df = MagicMock(
        return_value=_index_bars_df("000001.SH", ["2026-08-26"]))

    bf.backfill_index_history(conn, provider, "2026-08-26", "2026-08-26",
                              fallback_provider=fallback)
    frames = [c.args[1] for c in upsert.call_args_list]
    us_frame = next(f for f in frames if f["ts_code"].iloc[0] == ".INX")
    assert us_frame["source"].iloc[0] == "akshare"
    cn_frame = next(f for f in frames if f["ts_code"].iloc[0] == "000001.SH")
    assert cn_frame["source"].iloc[0] == "akshare"  # 主源全 None → 全走兜底（既有语义）


def test_backfill_index_history_skips_factors_for_non_cn(monkeypatch):
    """非 CN 目标（US 3 + KS11）不请求因子（idx_factor_pro 为 tushare CN 端点）。"""
    conn = MagicMock()
    _cursor = MagicMock()
    _cursor.fetchall.return_value = []
    _cursor.fetchone.return_value = None
    conn.execute.return_value = _cursor
    monkeypatch.setattr(bf, "_bootstrap_instruments", MagicMock())
    monkeypatch.setattr(bf, "bulk_upsert_daily", MagicMock(return_value=1))
    monkeypatch.setattr(bf, "bulk_upsert_factor_daily", MagicMock(return_value=1))

    provider = MagicMock()
    provider.get_index_data_df = MagicMock(
        return_value=_index_bars_df("000001.SH", ["2026-08-26"]))
    provider.get_index_factor_df = MagicMock(return_value=None)

    bf.backfill_index_history(conn, provider, "2026-08-26", "2026-08-26")
    codes = {c.args[0] for c in provider.get_index_factor_df.call_args_list}
    assert codes == {c for c in bf.INDEX_TARGETS if bf._is_cn_index_code(c)}
    assert ".INX" not in codes and "KS11" not in codes
