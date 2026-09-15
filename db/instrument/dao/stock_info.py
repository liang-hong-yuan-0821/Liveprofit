"""market.stock_info 股票信息表 DAO（stock_basic 差异列）。

exchange/market/area 三列原值直迁（列名沿用 tushare 原名，决策 10）；
industry 列丢弃不迁（行业归属走申万体系）。
"""

import pandas as pd

from db.instrument.dao._common import _clean_frame, _bulk_upsert

TABLE = "market.stock_info"
PK = ["ts_code"]

STOCK_INFO_COLS = ["ts_code", "exchange", "market", "area"]


def upsert_stock_info(conn, df: pd.DataFrame, update: bool = True) -> int:
    """stock_info 表 ON CONFLICT (ts_code) upsert（exchange/market/area 原文）。"""
    if df is None or df.empty:
        return 0
    df = df[pd.notna(df["ts_code"])].copy()
    if "updated_at" not in df.columns:
        df["updated_at"] = pd.Timestamp.now()
    df = _clean_frame(df, STOCK_INFO_COLS + ["updated_at"])
    if df.empty:
        return 0
    return _bulk_upsert(conn, TABLE, STOCK_INFO_COLS + ["updated_at"], PK, df, update)
