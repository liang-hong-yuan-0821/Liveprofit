"""
单元测试：market.sector / market.sector_member 板块体系 DAO
（迁移自 test_concepts.py 的 DAO 部分——concept→sector、concept_code→sector_code）
"""

from datetime import date

import pandas as pd
import pytest

from db.instrument.dao import sector as dao
from tests.db.instrument._helpers import _FakeConn


def _sector_df(codes, source_col=True):
    df = pd.DataFrame({
        "ts_code": codes,
        "name": [f"板块{c}" for c in codes],
        "count": [1.0] * len(codes),
        "exchange": ["A"] * len(codes),
        "list_date": ["20200101"] * len(codes),
        "type": ["N"] * len(codes),
    })
    if source_col:
        df["source"] = ["ths"] * len(codes)
    return df


def test_upsert_sectors_renames_and_source():
    conn = _FakeConn()
    # count 用 float（tushare 实测返回 300.0）且多行含 None——须转 Python int，
    # 否则 COPY 报 invalid input syntax for type integer（单行场景会被
    # Series.apply 的 dtype 推断骗过：单行推 int64、多行含 None 压回 float64）
    df = pd.DataFrame([
        ("883300.TI", "沪深300样本股", 300.0, "A", "20100413", "N"),
        ("883301.TI", "板块B", 50.0, "A", "20200101", "N"),
        ("883302.TI", "板块C", None, "A", "20200101", "N"),
    ], columns=["ts_code", "name", "count", "exchange", "list_date", "type"])
    df["source"] = "ths"
    n = dao.upsert_sectors(conn, df)
    assert n == 3
    sqls = " ".join(conn.cursor_obj.executed)
    assert 'INSERT INTO market.sector' in sqls
    assert 'ON CONFLICT ("source", "sector_code") DO UPDATE SET' in sqls
    written = conn.cursor_obj.copier.rows
    assert written[0][0] == "ths" and written[0][1] == "883300.TI"
    assert written[0][3] == 300 and isinstance(written[0][3], int)
    assert written[1][3] == 50 and isinstance(written[1][3], int)
    assert written[2][3] is None            # count NaN → NULL
    assert written[0][5] == "2010-04-13"


def test_upsert_sectors_missing_source_raises():
    df = pd.DataFrame([("883300.TI", "沪深300样本股", 300, "A", "20100413", "N")],
                      columns=["ts_code", "name", "count", "exchange",
                               "list_date", "type"])
    with pytest.raises(ValueError):
        dao.upsert_sectors(_FakeConn(), df)


def test_upsert_sector_members_all_pk_do_update():
    conn = _FakeConn()
    df = pd.DataFrame([("883300.TI", "000001.SZ")],
                      columns=["sector_code", "ts_code"])
    df["source"] = "ths"
    n = dao.upsert_sector_members(conn, df)
    assert n == 1
    sqls = " ".join(conn.cursor_obj.executed)
    assert 'ON CONFLICT ("source", "sector_code", "ts_code") DO UPDATE SET' in sqls


def test_upsert_sector_members_deduplicates_same_pk():
    # 双保险去重：同批次重复 PK 会使 INSERT ... ON CONFLICT DO UPDATE
    # 报 cannot affect row a second time
    conn = _FakeConn()
    df = pd.DataFrame([("883300.TI", "000001.SZ"),
                       ("883300.TI", "000001.SZ"),
                       ("883300.TI", "600000.SH")],
                      columns=["sector_code", "ts_code"])
    df["source"] = "ths"
    n = dao.upsert_sector_members(conn, df)
    assert n == 2
    written = conn.cursor_obj.copier.rows
    assert [r[2] for r in written] == ["000001.SZ", "600000.SH"]


def _query_conn(rows):
    from unittest.mock import MagicMock
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = rows
    return conn


def test_sector_queries_bidirectional():
    # 伪连接不执行 ORDER BY，行序即"SQL 已按 source 排序"的返回
    conn = _query_conn([
        ("dc", "BK1753", "光刻胶", None, None, None, None),
        ("ths", "883300.TI", "沪深300样本股", 300, "A", date(2010, 4, 13), "N"),
    ])
    df = dao.get_sectors(conn)    # source=None 全来源
    assert df["sector_code"].tolist() == ["BK1753", "883300.TI"]  # 按 source 排序

    conn2 = _query_conn([("ths", "883300.TI", "000001.SZ")])
    df2 = dao.get_sector_members(conn2, "883300.TI", source="ths")
    assert df2["ts_code"].tolist() == ["000001.SZ"]

    conn3 = _query_conn([("dc", "BK1753", "000001.SZ"),
                         ("ths", "883300.TI", "000001.SZ")])
    df3 = dao.get_stock_sectors(conn3, "000001.SZ")   # source=None 全来源
    assert df3["source"].tolist() == ["dc", "ths"]    # ORDER BY source


def test_get_sectors_with_source_filter():
    conn = _query_conn([])
    dao.get_sectors(conn, source="ths")
    sql = conn.execute.call_args[0][0]
    assert "WHERE source = %s" in sql
    assert conn.execute.call_args[0][1] == ("ths",)
