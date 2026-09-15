"""market.adj_factor 复权因子 DAO（股票 adj_factor + 基金 fund_adj 接口共用）。"""

import pandas as pd

from db.instrument.dao._common import _clean_frame, _bulk_upsert

TABLE = "market.adj_factor"
PK = ["ts_code", "trade_date"]

FACTOR_COLS = ["ts_code", "trade_date", "adj_factor"]


def bulk_upsert_factor(conn, df: pd.DataFrame, update: bool = False) -> int:
    """复权因子批量写入（无 NOT NULL 列，仅 NaN→None）。"""
    if df is None or df.empty:
        return 0
    df = _clean_frame(df, FACTOR_COLS)
    if df.empty:
        return 0
    return _bulk_upsert(conn, TABLE, FACTOR_COLS, PK, df, update)
