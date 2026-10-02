"""Freeze ETF classifications at the time they are actually observed."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from AI.dataflows.providers.base_provider import raise_if_network_access_denied
from db.instrument.dao.etf_catalog import insert_observation
from db.instrument.ingest.guard import FATAL_INGEST_ERRORS, locked_ingestion

REQUIRED = {"ts_code", "index_code", "exchange", "etf_type", "list_date", "list_status"}


def _collect_etf_catalog_unlocked(conn, provider, *, observed_at: datetime | None = None) -> dict:
    moment = observed_at or datetime.now(ZoneInfo("Asia/Shanghai"))
    if moment.tzinfo is None:
        raise ValueError("ETF_CATALOG_OBSERVATION_TIME_NAIVE")
    catalog = provider.get_etf_basic_df()
    raise_if_network_access_denied(provider)
    if catalog is None or catalog.empty or not REQUIRED <= set(catalog):
        raise ValueError("ETF_CATALOG_UNAVAILABLE")
    if (catalog["ts_code"].isna().any() or catalog["ts_code"].duplicated().any()
            or not catalog["list_status"].isin(("L", "D", "P")).all()):
        raise ValueError("ETF_CATALOG_INVALID_IDENTITY")
    frame = catalog.copy()
    frame["observed_date"] = moment.astimezone(ZoneInfo("Asia/Shanghai")).date()
    frame["available_at"] = pd.Timestamp(moment).tz_convert("UTC")
    frame["source"] = "tushare"
    try:
        rows = insert_observation(conn, frame)
        conn.commit()
    except FATAL_INGEST_ERRORS:
        raise
    except Exception:
        conn.rollback()
        raise
    return {"status": "SUCCESS", "observed_date": str(frame["observed_date"].iloc[0]),
            "rows": rows}


collect_etf_catalog = locked_ingestion("CN_STOCK_QUANT_INPUTS")(
    _collect_etf_catalog_unlocked)
