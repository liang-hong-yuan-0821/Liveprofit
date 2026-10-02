"""Append-only observations of etf_basic; observation time is not list date."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from db.instrument.dao._common import _bulk_upsert, _clean_frame

TABLE = "market.etf_catalog_observation"
COLS = ["ts_code", "observed_date", "available_at", "index_code", "exchange",
        "etf_type", "list_date", "list_status", "source"]


def insert_observation(conn, frame: pd.DataFrame) -> int:
    """Do not rewrite an earlier observation when the upstream catalog changes."""
    if frame.empty:
        return 0
    cleaned = _clean_frame(frame, COLS)
    return _bulk_upsert(conn, TABLE, COLS, ["ts_code", "observed_date"], cleaned, False)


def query_as_of(conn, cutoff: datetime) -> pd.DataFrame:
    """Latest observation actually available by the historical cutoff."""
    if cutoff.tzinfo is None:
        raise ValueError("ETF catalog cutoff must include a timezone")
    rows = conn.execute(
        "SELECT DISTINCT ON (ts_code) ts_code, index_code, exchange, etf_type, "
        "list_date, list_status, available_at FROM market.etf_catalog_observation "
        "WHERE available_at <= %s ORDER BY ts_code, available_at DESC",
        (cutoff,),
    ).fetchall()
    return pd.DataFrame(rows, columns=["ts_code", "index_code", "exchange", "etf_type",
                                       "list_date", "list_status", "available_at"])
