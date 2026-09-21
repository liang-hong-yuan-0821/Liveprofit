"""market.trade_status_daily DAO。"""

import pandas as pd

from db.instrument.dao._common import _bulk_upsert, _clean_frame

TABLE = "market.trade_status_daily"
PK = ["ts_code", "trade_date"]
COLS = [
    "ts_code", "trade_date", "is_suspended", "is_st", "market_board",
    "up_limit", "down_limit", "source", "updated_at",
]


def bulk_upsert_trade_status(conn, df: pd.DataFrame, update: bool = True) -> int:
    if df is None or df.empty:
        return 0
    frame = _clean_frame(df, COLS)
    if frame.empty:
        return 0
    return _bulk_upsert(conn, TABLE, COLS, PK, frame, update)
