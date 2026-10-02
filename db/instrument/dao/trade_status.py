"""market.trade_status_daily DAO。"""

import pandas as pd
from pandas.api.types import is_bool

from db.instrument.dao._common import _bulk_upsert, _clean_frame

TABLE = "market.trade_status_daily"
PK = ["ts_code", "trade_date"]
COLS = [
    "ts_code", "trade_date", "is_suspended", "suspension_scope", "suspend_timing",
    "is_st", "market_board",
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
    if "suspension_scope" in df:
        scopes = df["suspension_scope"].dropna().astype(str)
        if not scopes.isin(("none", "full_day", "intraday", "unknown")).all():
            raise ValueError("invalid suspension scope")
        observed = df["suspension_scope"].notna()
        if ((observed & df["is_suspended"].eq(True)
             & df["suspension_scope"].eq("none"))
                | (observed & df["is_suspended"].eq(False)
                   & df["suspension_scope"].ne("none"))).any():
            raise ValueError("suspension scope conflicts with event flag")
        if "suspend_timing" in df:
            timed = df["suspension_scope"].eq("intraday")
            if df.loc[timed, "suspend_timing"].isna().any():
                raise ValueError("intraday suspension requires timing")
    frame = _clean_frame(df, COLS)
    if len(frame) != len(df):
        raise ValueError("trade status observation contains invalid keys")
    return _bulk_upsert(conn, TABLE, COLS, PK, frame, True,
                        preserve_null=("up_limit", "down_limit"))


def remove_ambiguous_tushare_status(conn, trade_date: str, codes: set[str]) -> int:
    """Invalidate stale vendor status when fresh S/R events have no order or timing."""
    if not codes:
        return 0
    result = conn.execute(
        "DELETE FROM market.trade_status_daily WHERE trade_date=%s "
        "AND ts_code=ANY(%s) AND source IN ('tushare','tushare+baostock') "
        "AND NOT EXISTS (SELECT 1 FROM market.stock_st_source_conflict c "
        "WHERE c.ts_code=trade_status_daily.ts_code "
        "AND c.trade_date=trade_status_daily.trade_date)",
        (trade_date, sorted(codes)),
    )
    return result.rowcount
