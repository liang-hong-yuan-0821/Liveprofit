"""
单元测试：market.instrument / market.stock_info DAO
（迁移自 test_stock_daily_dao 的 upsert_stock_basic 用例——stock_basic 拆两路写入）
"""

import pandas as pd

from db.instrument.dao import instrument as inst_dao
from db.instrument.dao import stock_info as stock_dao
from db.instrument.dao import adj_factor as adj_dao
from tests.db.instrument._helpers import _FakeConn


def test_upsert_instrument_sql_shape_and_dates():
    conn = _FakeConn()
    df = pd.DataFrame([("000001.SZ", "平安银行", "stock", "L", "19910403", None, "tushare")],
                      columns=inst_dao.INSTRUMENT_COLS)
    n = inst_dao.upsert_instrument(conn, df)
    assert n == 1
    sqls = " ".join(conn.cursor_obj.executed)
    assert 'INSERT INTO market.instrument' in sqls
    assert 'ON CONFLICT ("ts_code") DO UPDATE SET' in sqls
    written = conn.cursor_obj.copier.rows
    assert written[0][4] == "1991-04-03"   # list_date 归一 ISO
    assert written[0][5] is None           # delist_date


def test_upsert_instrument_do_nothing_for_migration():
    # 迁移步骤 7 多源先后写：ON CONFLICT DO NOTHING（先到者胜）
    conn = _FakeConn()
    df = pd.DataFrame([("000001.SH", "上证综指", "index", None, None, None, "tushare")],
                      columns=inst_dao.INSTRUMENT_COLS)
    inst_dao.upsert_instrument(conn, df, update=False)
    sqls = " ".join(conn.cursor_obj.executed)
    assert 'ON CONFLICT ("ts_code") DO NOTHING' in sqls


def test_upsert_stock_info_original_cols():
    conn = _FakeConn()
    df = pd.DataFrame([("000001.SZ", "SZSE", "主板", "深圳")],
                      columns=stock_dao.STOCK_INFO_COLS)
    n = stock_dao.upsert_stock_info(conn, df)
    assert n == 1
    sqls = " ".join(conn.cursor_obj.executed)
    assert 'INSERT INTO market.stock_info' in sqls
    assert 'ON CONFLICT ("ts_code") DO UPDATE SET' in sqls


def test_bulk_upsert_factor_nan_to_none():
    conn = _FakeConn()
    df = pd.DataFrame([
        ("000001.SZ", "20260828", 1.0),
        ("600000.SH", "20260828", float("nan")),
    ], columns=adj_dao.FACTOR_COLS)
    n = adj_dao.bulk_upsert_factor(conn, df)
    assert n == 2
    written = conn.cursor_obj.copier.rows
    assert written[0][2] == 1.0
    assert written[1][2] is None
    assert 'ON CONFLICT ("ts_code", "trade_date") DO NOTHING' in " ".join(
        conn.cursor_obj.executed)
