"""market.industry / market.industry_member 申万行业体系 DAO。

列名对齐 tushare index_classify：industry_code（去 .SI 后缀 6 位码）/
name（名称列统一裸 name，决策 15）/ source；成分表写入并入本模块（成分表豁免）。
"""

import pandas as pd

from db.instrument.dao._common import _clean_frame, _bulk_upsert

TABLE = "market.industry"
MEMBER_TABLE = "market.industry_member"

INDUSTRY_COLS = ["source", "industry_code", "name", "count"]
MEMBER_COLS = ["source", "industry_code", "ts_code"]


def upsert_industry(conn, df: pd.DataFrame, update: bool = True) -> int:
    """industry 表 ON CONFLICT (source, industry_code) upsert（字典行）。"""
    if df is None or df.empty:
        return 0
    df = _clean_frame(df, INDUSTRY_COLS)
    if df.empty:
        return 0
    return _bulk_upsert(conn, TABLE, INDUSTRY_COLS,
                        ["source", "industry_code"], df, update)


def upsert_industry_members(conn, df: pd.DataFrame) -> int:
    """industry_member 表 ON CONFLICT (source, industry_code, ts_code) DO UPDATE。

    全列均属 PK，DO UPDATE 退化为空更新（幂等）；全量替换式刷新的差集删除
    由采集层在同一事务内完成（方案 3.7.1 ③）。
    """
    if df is None or df.empty:
        return 0
    df = df.drop_duplicates(subset=MEMBER_COLS)
    df = _clean_frame(df, MEMBER_COLS)
    if df.empty:
        return 0
    return _bulk_upsert(conn, MEMBER_TABLE, MEMBER_COLS, MEMBER_COLS, df, update=True)


def get_industries(conn, source: str = None) -> pd.DataFrame:
    """行业字典列表（source=None 全来源），ORDER BY source, industry_code。"""
    sql = ("SELECT source, industry_code, name, count FROM market.industry")
    params = ()
    if source is not None:
        sql += " WHERE source = %s"
        params = (source,)
    sql += " ORDER BY source, industry_code"
    rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame(rows, columns=INDUSTRY_COLS)


def get_industry_members(conn, industry_code: str,
                         source: str = None) -> pd.DataFrame:
    """按行业查成分（source=None 全来源）。"""
    sql = ("SELECT source, industry_code, ts_code FROM market.industry_member "
           "WHERE industry_code = %s")
    params = [industry_code]
    if source is not None:
        sql += " AND source = %s"
        params.append(source)
    sql += " ORDER BY source, ts_code"
    rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame(rows, columns=MEMBER_COLS)


def get_stock_industry(conn, ts_code: str, source: str = None) -> pd.DataFrame:
    """按股票查所属行业（申万互斥完备——正常至多一行），走 (ts_code) 反查索引。"""
    sql = ("SELECT source, industry_code, ts_code FROM market.industry_member "
           "WHERE ts_code = %s")
    params = [ts_code]
    if source is not None:
        sql += " AND source = %s"
        params.append(source)
    sql += " ORDER BY source, industry_code"
    rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame(rows, columns=MEMBER_COLS)
