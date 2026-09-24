"""market.trade_status_daily DAO。"""

import pandas as pd
from pandas.api.types import is_bool

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


def upsert_observed_trade_status(conn, df: pd.DataFrame) -> int:
    """Persist verified facts without clearing previously observed price limits."""
    if df is None or df.empty:
        return 0
    required = ["ts_code", "trade_date", "is_suspended", "is_st",
                "market_board", "source", "updated_at"]
    if any(c not in df.columns for c in required):
        raise ValueError("trade status observation is missing required columns")
    if df[required].isna().any().any() or df.duplicated(PK).any():
        raise ValueError("trade status observation contains unknown or duplicate facts")
    if not all(df[c].map(is_bool).all() for c in ("is_suspended", "is_st")):
        raise ValueError("trade status booleans must be observed, not inferred")
    frame = _clean_frame(df, COLS)
    if len(frame) != len(df):
        raise ValueError("trade status observation contains invalid keys")
    return _bulk_upsert(conn, TABLE, COLS, PK, frame, True,
                        preserve_null=("up_limit", "down_limit"))
