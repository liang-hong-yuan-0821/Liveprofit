"""market.sector / market.sector_member 板块体系 DAO（迁移自 store 概念 DAO）。

concept → sector、concept_code → sector_code（决策 12）；成分表写入并入本模块
（成分表豁免：不单设 sector_member.py）。
"""

import pandas as pd

from db.instrument.dao._common import _normalize_date_col, _clean_frame, _bulk_upsert

TABLE = "market.sector"
MEMBER_TABLE = "market.sector_member"

SECTOR_COLS = ["source", "sector_code", "name", "count", "exchange",
               "list_date", "type"]
MEMBER_COLS = ["source", "sector_code", "ts_code"]


def upsert_sectors(conn, df: pd.DataFrame) -> int:
    """sector 表 ON CONFLICT (source, sector_code) DO UPDATE。

    df 标准列 [ts_code(板块代码), name, count, exchange, list_date, type] + source
    （source 由采集层附加）；落表时 ts_code 重命名为 sector_code。
    """
    if df is None or df.empty:
        return 0
    df = df.copy()
    if "source" not in df.columns:
        raise ValueError("upsert_sectors: df 缺少 source 列")
    df = df[pd.notna(df["ts_code"])]
    df = df.rename(columns={"ts_code": "sector_code"})
    df = _normalize_date_col(df, "list_date")
    if "count" in df.columns:
        # count 列为 INTEGER，tushare 实测返回 float（如 300.0）——
        # 不转 Python int 会 COPY 报 invalid input syntax for type integer。
        # 必须显式 dtype=object：Series.apply 的 dtype 推断在多行含 None 时
        # 会把 int 整体压回 float64（单行均匀时又推成 int64，单测骗过了）
        df["count"] = pd.Series(
            [None if pd.isna(v) else int(float(v)) for v in df["count"]],
            dtype=object, index=df.index)
    df = _clean_frame(df, SECTOR_COLS)
    if df.empty:
        return 0
    return _bulk_upsert(conn, TABLE, SECTOR_COLS,
                        ["source", "sector_code"], df, update=True)


def upsert_sector_members(conn, df: pd.DataFrame) -> int:
    """sector_member 表 ON CONFLICT (source, sector_code, ts_code) DO UPDATE。

    df 标准列 [sector_code, ts_code] + source（source 由采集层附加）。
    全列均属 PK，DO UPDATE 退化为空更新（幂等，等价 DO NOTHING）。
    """
    if df is None or df.empty:
        return 0
    df = df.copy()
    if "source" not in df.columns:
        raise ValueError("upsert_sector_members: df 缺少 source 列")
    df = _clean_frame(df, MEMBER_COLS)
    # 双保险去重：同一批次含相同 PK 行时 INSERT ... ON CONFLICT DO UPDATE
    # 报 cannot affect row a second time（PG 约束，实测踩坑）
    df = df.drop_duplicates(subset=MEMBER_COLS)
    if df.empty:
        return 0
    return _bulk_upsert(conn, MEMBER_TABLE, MEMBER_COLS, MEMBER_COLS, df, update=True)


def get_sectors(conn, source: str = None) -> pd.DataFrame:
    """板块列表（source=None 全来源），ORDER BY source, sector_code。"""
    sql = ("SELECT source, sector_code, name, count, exchange, list_date, type "
           "FROM market.sector")
    params = ()
    if source is not None:
        sql += " WHERE source = %s"
        params = (source,)
    sql += " ORDER BY source, sector_code"
    rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame(rows, columns=SECTOR_COLS)


def get_sector_members(conn, sector_code: str, source: str = None) -> pd.DataFrame:
    """按板块查成分（source=None 全来源）。"""
    sql = ("SELECT source, sector_code, ts_code FROM market.sector_member "
           "WHERE sector_code = %s")
    params = [sector_code]
    if source is not None:
        sql += " AND source = %s"
        params.append(source)
    sql += " ORDER BY source, ts_code"
    rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame(rows, columns=MEMBER_COLS)


def get_stock_sectors(conn, ts_code: str, source: str = None) -> pd.DataFrame:
    """按股票查所属板块（全来源，走 idx_sector_member_ts 反查索引）。"""
    sql = ("SELECT source, sector_code, ts_code FROM market.sector_member "
           "WHERE ts_code = %s")
    params = [ts_code]
    if source is not None:
        sql += " AND source = %s"
        params.append(source)
    sql += " ORDER BY source, sector_code"
    rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame(rows, columns=MEMBER_COLS)
