"""market.sector_daily 板块日线 DAO（热度现场计算的数据底座，决策 13）。"""

import pandas as pd

from db.instrument.dao._common import _clean_frame, _drop_close_nan, _bulk_upsert

TABLE = "market.sector_daily"
PK = ["source", "sector_code", "trade_date"]

SECTOR_DAILY_COLS = [
    "source", "sector_code", "trade_date",
    "open", "high", "low", "close", "pre_close", "change", "pct_chg",
    "vol", "amount", "turnover_rate", "updated_at",
]


def bulk_upsert_sector_daily(conn, df: pd.DataFrame, update: bool = False) -> int:
    """板块日线批量写入（后续阶段采集用；DO NOTHING 断点续跑）。"""
    if df is None or df.empty:
        return 0
    df = _drop_close_nan(df)
    if df.empty:
        return 0
    df = _clean_frame(df, SECTOR_DAILY_COLS)
    if df.empty:
        return 0
    return _bulk_upsert(conn, TABLE, SECTOR_DAILY_COLS, PK, df, update)


def query_by_window(conn, source: str, start_date: str, end_date: str) -> pd.DataFrame:
    """按来源 + 日期窗口全板块一次拉取（热度现场计算：source='dc'、近 20 交易日）。"""
    rows = conn.execute(
        "SELECT source, sector_code, trade_date, close, pct_chg, vol, amount, "
        "turnover_rate, updated_at FROM market.sector_daily "
        "WHERE source = %s AND trade_date BETWEEN %s AND %s ORDER BY sector_code, trade_date",
        (source, start_date, end_date),
    ).fetchall()
    return pd.DataFrame(rows, columns=[
        "source", "sector_code", "trade_date", "close", "pct_chg", "vol",
        "amount", "turnover_rate", "updated_at",
    ])


def query_bars(conn, source: str, sector_code: str, start_date: str,
               end_date: str) -> pd.DataFrame:
    """单板块区间日线（板块概念Treemap方案 3.3：概念 K 线读模型，升序）。"""
    rows = conn.execute(
        "SELECT trade_date, open, high, low, close, vol FROM market.sector_daily "
        "WHERE source = %s AND sector_code = %s "
        "AND trade_date BETWEEN %s AND %s ORDER BY trade_date",
        (source, sector_code, start_date, end_date),
    ).fetchall()
    return pd.DataFrame(rows, columns=["trade_date", "open", "high", "low",
                                       "close", "vol"])
