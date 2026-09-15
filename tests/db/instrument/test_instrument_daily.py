"""
单元测试：market.instrument_daily 统一日线 DAO（迁移自 test_stock_daily_dao）

- 批量写入：close NaN 行 drop（计日志）、NaN→None、COPY 临时表 + ON CONFLICT
  语句形态（market. 前缀限定名）、DO NOTHING / DO UPDATE 两态、临时表用完即删
- get_daily / query_range / get_cross_section（instrument_type 显式列过滤——
  前缀函数 is_fund_ts_code 已退役）
- get_qfq_daily 前复权公式（构造 2 因子序列手算对照、因子缺失日）
"""

from datetime import date

import pandas as pd

from db.instrument.dao import instrument_daily as dao
from tests.db.instrument._helpers import _FakeConn


def _daily_df(rows):
    return pd.DataFrame(rows, columns=dao.DAILY_COLS)


def _row(ts_code, close, source="tushare", trade_date="20260828",
         open_=11.0, high=11.5, low=11.0, pre_close=11.3,
         change=0.1, pct_chg=0.88, vol=100.0, amount=1000.0,
         updated_at="2026-08-28 16:30:00+08"):
    return (ts_code, trade_date, open_, high, low, close, pre_close,
            change, pct_chg, vol, amount, source, updated_at)


def test_bulk_upsert_daily_drops_close_nan_and_nan_to_none(caplog):
    conn = _FakeConn()
    df = _daily_df([
        _row("000001.SZ", 11.4),
        _row("600000.SH", float("nan"), pre_close=None, change=None,
             pct_chg=None, vol=None, amount=None),
        ("300001.SZ", "20260828", None, None, None, 5.0, float("nan"), None,
         None, float("nan"), None, "tushare", None),
    ])
    n = dao.bulk_upsert_daily(conn, df)
    assert n == 2  # close NaN 行已 drop
    assert "close 为 NaN 已 drop" in caplog.text

    written = conn.cursor_obj.copier.rows
    assert len(written) == 2
    first = written[0]
    assert first[0] == "000001.SZ" and first[1] == "2026-08-28"
    # 其余 NaN → None：第三行 open/pre_close/vol 原为 NaN/None
    second = written[1]
    assert second[2] is None      # open
    assert second[6] is None      # pre_close
    assert second[9] is None      # vol


def test_bulk_upsert_daily_sql_shape_do_nothing():
    conn = _FakeConn()
    df = _daily_df([_row("000001.SZ", 11.4)])
    dao.bulk_upsert_daily(conn, df, update=False)
    sqls = " ||| ".join(conn.cursor_obj.executed)
    assert 'CREATE TEMP TABLE "tmp_market_instrument_daily_' in sqls
    # LIKE 必须带 INCLUDING DEFAULTS：否则 updated_at（DEFAULT now()）COPY 时 NULL 违例
    assert 'INCLUDING DEFAULTS' in sqls
    assert 'COPY "tmp_market_instrument_daily_' in sqls
    # market. 前缀限定名：不整体引号包裹（包裹会把限定名当单个标识符）
    assert 'INSERT INTO market.instrument_daily' in sqls
    assert 'ON CONFLICT ("ts_code", "trade_date") DO NOTHING' in sqls
    assert 'DROP TABLE IF EXISTS "tmp_market_instrument_daily_' in sqls


def test_bulk_upsert_daily_sql_shape_do_update():
    conn = _FakeConn()
    df = _daily_df([_row("000001.SZ", 11.4)])
    dao.bulk_upsert_daily(conn, df, update=True)
    sqls = " ||| ".join(conn.cursor_obj.executed)
    assert "ON CONFLICT (\"ts_code\", \"trade_date\") DO UPDATE SET" in sqls
    # 全列（不含 PK）均更新——含 source/updated_at
    for col in ("open", "high", "low", "close", "pre_close", "change",
                "pct_chg", "vol", "amount", "source", "updated_at"):
        assert f'"{col}" = EXCLUDED."{col}"' in sqls
    assert 'ts_code" = EXCLUDED."ts_code"' not in sqls
    assert 'trade_date" = EXCLUDED."trade_date"' not in sqls


def test_get_daily_orders_by_trade_date():
    # 伪连接不执行 ORDER BY，行序即"SQL 已排序"的返回（DAO 依赖 SQL 排序）
    rows = [
        ("000001.SZ", date(2026, 8, 26), 9.9, 10.1, 9.8, 10.1, 10.0,
         0.1, 1.0, 90.0, 900.0, "tushare"),
        ("000001.SZ", date(2026, 8, 27), 10.0, 10.5, 9.8, 10.2, 10.1,
         0.1, 0.99, 100.0, 1000.0, "tushare"),
    ]
    conn = _FakeConn({"FROM market.instrument_daily": rows})
    df = dao.get_daily(conn, "000001.SZ", "2026-08-01", "2026-08-31")
    assert df["trade_date"].tolist() == [date(2026, 8, 26), date(2026, 8, 27)]
    sql = conn.cursor_obj.executed[-1]
    assert "ORDER BY trade_date" in sql


def test_get_cross_section_filters_by_instrument_type():
    rows = [
        ("000001.SZ", date(2026, 8, 28), 10.0, 10.5, 9.8, 10.2, 10.1,
         0.1, 0.99, 100.0, 1000.0, "tushare"),
        ("510300.SH", date(2026, 8, 28), 4.0, 4.1, 3.9, 4.05, 4.0,
         0.05, 1.2, 500.0, 5000.0, "tushare"),
        ("159919.SZ", date(2026, 8, 28), 2.0, 2.1, 1.9, 2.05, 2.0,
         0.05, 2.5, 300.0, 3000.0, "tushare"),
    ]
    conn = _FakeConn({"FROM market.instrument_daily": rows})
    # instrument_type 显式列过滤（前缀函数退役）——EXISTS 子查询形态：
    # 过滤在 SQL 层，伪连接不执行子查询，单测断言 SQL 形态与参数
    dao.get_cross_section(conn, "2026-08-28", instrument_type="stock")
    sql = conn.cursor_obj.executed[-1]
    assert "EXISTS" in sql
    assert "instrument_type = %s" in sql
    all_rows = dao.get_cross_section(conn, "2026-08-28")
    assert len(all_rows) == 3   # instrument_type=None 不带 EXISTS（伪连接全行返回）
    last_sql = conn.cursor_obj.executed[-1]
    assert "EXISTS" not in last_sql


def test_get_qfq_daily_formula_hand_computed():
    daily_rows = [
        ("000001.SZ", date(2026, 1, 5), 10.0, 10.5, 9.8, 10.0, 9.9,
         0.1, 1.0, 100.0, 1000.0, "tushare"),
        ("000001.SZ", date(2026, 1, 6), 20.0, 20.5, 19.8, 20.0, 19.9,
         0.1, 0.5, 200.0, 2000.0, "tushare"),
        ("000001.SZ", date(2026, 1, 7), 30.0, 30.5, 29.8, 30.0, 29.9,
         0.1, 0.3, 300.0, 3000.0, "tushare"),
    ]
    factor_rows = [
        (date(2026, 1, 5), 1.0),
        (date(2026, 1, 6), 2.0),
        # 1/7 因子缺失（停牌/新上市场景）
    ]
    conn = _FakeConn({
        "FROM market.instrument_daily": daily_rows,
        "FROM market.adj_factor": factor_rows,
    })
    df = dao.get_qfq_daily(conn, "000001.SZ", "2026-01-01", "2026-01-31")
    # factor_latest = 2.0（区间内最新因子）
    # qfq = x * factor_t / factor_latest
    import pytest
    assert df["qfq_close"].iloc[0] == pytest.approx(10.0 * 1.0 / 2.0)
    assert df["qfq_close"].iloc[1] == pytest.approx(20.0 * 2.0 / 2.0)
    assert pd.isna(df["qfq_close"].iloc[2])   # 因子缺失日 → NaN
    # 原始列保留、adj_factor 不输出
    assert "close" in df.columns
    assert "adj_factor" not in df.columns
    assert set(df.columns) == set(dao.QUERY_COLS +
                                  ["qfq_open", "qfq_high", "qfq_low", "qfq_close"])


def test_get_qfq_daily_no_factor_returns_none_qfq():
    daily_rows = [
        ("000001.SZ", date(2026, 1, 5), 10.0, 10.5, 9.8, 10.0, 9.9,
         0.1, 1.0, 100.0, 1000.0, "tushare"),
    ]
    conn = _FakeConn({
        "FROM market.instrument_daily": daily_rows,
        "FROM market.adj_factor": [],
    })
    df = dao.get_qfq_daily(conn, "000001.SZ", "2026-01-01", "2026-01-31")
    assert df["qfq_close"].isna().all()


def test_get_qfq_daily_empty_daily():
    conn = _FakeConn({
        "FROM market.instrument_daily": [],
        "FROM market.adj_factor": [],
    })
    df = dao.get_qfq_daily(conn, "000001.SZ", "2026-01-01", "2026-01-31")
    assert df.empty
    assert set(df.columns) == set(dao.QUERY_COLS +
                                  ["qfq_open", "qfq_high", "qfq_low", "qfq_close"])
