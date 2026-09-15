"""板块日线每日增量采集测试（板块概念Treemap方案 3.1，落点 tests/db/instrument/）。

- 列映射：dc_daily 实测列 → SECTOR_DAILY_COLS（pre_close 恒 None、pct_change→pct_chg、
  swing/category/ts_code 丢弃、source/updated_at 采集层显式填）
- 窗口日期转换：YYYY-MM-DD 入口 → YYYYMMDD 调 provider（70 自然日默认）
- 事务：每板块独立 commit；写入失败 rollback 后下一板块继续
- 失败分层 + 熔断：单板块失败跳过；连续 5 板块失败终止、剩余板块记 failed
- drop_duplicates 双保险（store-daily 实测踩坑 4）
"""

from datetime import date, timedelta
from unittest.mock import MagicMock

import pandas as pd
import pytest

from db.instrument.ingest import sector_daily as sdc


def _dc_frame(trade_dates):
    """dc_daily 实测列集帧（降序输入，含全部 13 列）。"""
    n = len(trade_dates)
    return pd.DataFrame({
        "ts_code": ["BK1753.DC"] * n,
        "trade_date": trade_dates,
        "close": [100.0 + i for i in range(n)],
        "open": [99.0 + i for i in range(n)],
        "high": [101.0 + i for i in range(n)],
        "low": [98.0 + i for i in range(n)],
        "change": [1.0] * n,
        "pct_change": [1.5] * n,
        "vol": [1000.0] * n,
        "amount": [2.16e10] * n,
        "swing": [3.37] * n,
        "turnover_rate": [2.84] * n,
        "category": ["概念板块"] * n,
    })


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(sdc.time, "sleep", lambda s: None)


@pytest.fixture
def _fake_conn(monkeypatch):
    """伪连接 + 记录 DAO 入参；get_sectors/bulk_upsert 按 monkeypatch 注入。"""
    conn = MagicMock()
    recorded = {}

    def _fake_get_sectors(c, source):
        recorded["boards_source"] = source
        return pd.DataFrame({
            "source": ["dc"] * 3,
            "sector_code": ["BK1753.DC", "BK1754.DC", "BK1755.DC"],
            "name": ["光刻胶", "半导体", "芯片"],
        })

    def _fake_upsert(c, df, update):
        recorded.setdefault("frames", []).append(df)
        recorded.setdefault("updates", []).append(update)
        return len(df)

    monkeypatch.setattr(sdc, "get_sectors", _fake_get_sectors)
    monkeypatch.setattr(sdc, "bulk_upsert_sector_daily", _fake_upsert)
    return conn, recorded


def test_column_mapping_and_source_updated_at_fill(_fake_conn):
    conn, recorded = _fake_conn
    prov = MagicMock()
    prov.get_sector_daily_df.side_effect = (
        lambda source, code, start, end: _dc_frame(["20260911", "20260910"]))
    result = sdc.collect_sector_daily_incremental(conn, prov)

    assert result["boards"] == 3 and result["rows"] == 6 and result["failed"] == []
    frame = recorded["frames"][0]
    # 列映射：pre_close 恒 None、pct_chg 取自 pct_change、amount 有值、
    # swing/category/ts_code 不落入写集
    assert set(frame.columns) == {
        "trade_date", "open", "high", "low", "close", "pre_close", "change",
        "pct_chg", "vol", "amount", "turnover_rate", "source",
        "sector_code", "updated_at"}
    assert frame["pre_close"].isna().all()
    assert (frame["pct_chg"] == 1.5).all()
    assert (frame["amount"] == 2.16e10).all()
    # source 恒 'dc'、updated_at 采集时刻非空（漏填则 DO UPDATE 把 NULL 覆盖回已有行）
    assert (frame["source"] == "dc").all()
    assert (frame["sector_code"] == "BK1753.DC").all()
    assert frame["updated_at"].notna().all()
    assert recorded["updates"] == [True, True, True]   # DO UPDATE
    assert conn.commit.call_count == 3                 # 每板块独立 commit


def test_window_dates_converted_to_yyyymmdd(_fake_conn):
    conn, _ = _fake_conn
    prov = MagicMock()
    prov.get_sector_daily_df.return_value = _dc_frame(["20260911"])
    sdc.collect_sector_daily_incremental(conn, prov)

    calls = prov.get_sector_daily_df.call_args_list
    today = date.today()
    expect_start = (today - timedelta(days=70)).strftime("%Y%m%d")
    expect_end = today.strftime("%Y%m%d")
    assert calls[0].args == ("dc", "BK1753.DC", expect_start, expect_end)


def test_single_failure_skips_and_continues(_fake_conn):
    conn, recorded = _fake_conn
    prov = MagicMock()
    prov.get_sector_daily_df.side_effect = [
        None,                                              # BK1753 失败
        _dc_frame(["20260911"]),                           # BK1754 成功
        _dc_frame(["20260911"]),
    ]
    result = sdc.collect_sector_daily_incremental(conn, prov)

    assert result["failed"] == ["BK1753.DC"]
    assert result["boards"] == 2 and result["rows"] == 2
    assert prov.get_sector_daily_df.call_count == 3        # 失败后继续下一板块


def test_no_breaker_below_threshold(_fake_conn):
    conn, recorded = _fake_conn
    prov = MagicMock()
    prov.get_sector_daily_df.return_value = None           # 全部失败
    result = sdc.collect_sector_daily_incremental(conn, prov)

    # 3 个板块 < 5 阈值：不熔断，全量尝试（熔断用例见下一条）
    assert prov.get_sector_daily_df.call_count == 3
    assert result["failed"] == ["BK1753.DC", "BK1754.DC", "BK1755.DC"]
    assert result["boards"] == 0


def test_circuit_breaker_halts_requests_for_remaining(_fake_conn, monkeypatch):
    conn, recorded = _fake_conn
    # 10 个板块：前 5 失败触发熔断，剩余 5 不再发请求
    codes = [f"BK{i:04d}.DC" for i in range(10)]
    monkeypatch.setattr(
        sdc, "get_sectors",
        lambda c, source: pd.DataFrame({
            "source": ["dc"] * 10, "sector_code": codes, "name": codes}))
    prov = MagicMock()
    prov.get_sector_daily_df.return_value = None
    result = sdc.collect_sector_daily_incremental(conn, prov)

    assert prov.get_sector_daily_df.call_count == sdc.MAX_CONSECUTIVE_FAILURES
    assert result["failed"] == codes                       # 剩余板块直接记 failed
    assert result["boards"] == 0


def test_write_failure_rolls_back_and_continues(_fake_conn, monkeypatch):
    conn, recorded = _fake_conn
    prov = MagicMock()
    prov.get_sector_daily_df.return_value = _dc_frame(["20260911"])

    calls = {"n": 0}
    real_upsert = lambda c, df, update: len(df)

    def flaky_upsert(c, df, update):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("COPY 失败")
        return real_upsert(c, df, update)

    monkeypatch.setattr(sdc, "bulk_upsert_sector_daily", flaky_upsert)
    result = sdc.collect_sector_daily_incremental(conn, prov)

    assert conn.rollback.call_count == 1                  # 失败分支 rollback 恢复
    assert result["failed"] == ["BK1753.DC"]
    assert result["boards"] == 2 and result["rows"] == 2  # 后续板块继续写入
    assert conn.commit.call_count == 2


def test_duplicate_pk_rows_dropped_before_upsert(_fake_conn):
    conn, recorded = _fake_conn
    prov = MagicMock()
    frame = pd.concat([_dc_frame(["20260911", "20260910"]),
                       _dc_frame(["20260911", "20260910"])])  # 重复 PK 行
    prov.get_sector_daily_df.return_value = frame
    sdc.collect_sector_daily_incremental(conn, prov)

    assert len(recorded["frames"][0]) == 2                # 去重后每板块 2 行


def test_empty_board_list_skips_without_request(_fake_conn, monkeypatch):
    conn, recorded = _fake_conn
    monkeypatch.setattr(sdc, "get_sectors", lambda c, source: pd.DataFrame())
    prov = MagicMock()
    result = sdc.collect_sector_daily_incremental(conn, prov)

    assert prov.get_sector_daily_df.call_count == 0
    assert result == {"boards": 0, "rows": 0, "failed": []}


def test_real_pg_write_and_rerun_idempotent(pg_env, clean_market_state, monkeypatch):
    """真实 PG 写路径（CR minor 7）：COPY 列与 DDL 对齐、updated_at 落库非 NULL、
    DO UPDATE 幂等重跑（行数不变）、provider 抛异常分支跳过并继续。"""
    monkeypatch.setattr(sdc.time, "sleep", lambda s: None)
    from db.instrument.db import get_connection

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO market.sector (source, sector_code, name) VALUES "
                "('dc', 'BK1753.DC', '光刻胶'), ('dc', 'BK1754.DC', '半导体')")
        conn.commit()

    prov = MagicMock()

    def flaky_side(source, code, start, end):
        if code == "BK1753.DC":
            raise RuntimeError("端点超时")   # 抛异常分支（非返回 None）
        return _dc_frame(["20260911", "20260910"])
    prov.get_sector_daily_df.side_effect = flaky_side

    with get_connection() as conn:
        result = sdc.collect_sector_daily_incremental(conn, prov)
    assert result["failed"] == ["BK1753.DC"]
    assert result["boards"] == 1 and result["rows"] == 2

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*), count(updated_at) FROM market.sector_daily "
                "WHERE source='dc' AND sector_code='BK1754.DC'")
            count, updated = cur.fetchone()
    assert count == 2 and updated == 2   # updated_at 落库非 NULL（DAO 不自动补）

    # 重跑幂等：DO UPDATE 覆盖同窗口，行数不变（无重复插入）
    prov.get_sector_daily_df.side_effect = (
        lambda source, code, start, end: _dc_frame(["20260911", "20260910"]))
    with get_connection() as conn:
        result2 = sdc.collect_sector_daily_incremental(conn, prov)
    assert result2["boards"] == 2 and result2["rows"] == 4
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM market.sector_daily WHERE source='dc'")
            total = cur.fetchone()[0]
    assert total == 4   # 2 板块 × 2 行，无重复
