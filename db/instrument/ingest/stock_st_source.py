"""Import a verified, immutable BaoStock historical ST extraction.

The import stores source facts independently. It does not infer suspension or
replace full trade status when another field is unavailable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from datetime import date, datetime
from pathlib import Path

import exchange_calendars as xc
import pandas as pd
from dotenv import load_dotenv
from pandas.api.types import is_bool_dtype

from db.instrument.dao._common import _bulk_upsert
from db.instrument.db import get_connection
from db.instrument.ingest.guard import IngestGuard, locked_ingestion
from db.instrument.ingest.notifications import market_changed_notifier_from_env

SOURCE = "baostock_kline"
COLS = ["batch_id", "ts_code", "trade_date", "is_st", "reported_trading",
        "reported_close"]


def _validated_frame(conn, report: dict, raw_path: Path) -> pd.DataFrame:
    digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    if digest != report.get("raw_parquet_sha256"):
        raise ValueError("ST_SOURCE_RAW_HASH_MISMATCH")
    year = report.get("year")
    if (type(year) is not int or report.get("source") !=
            "baostock.query_history_k_data_plus.daily.unadjusted"
            or report.get("failed_codes") or report.get("missing_count") != 0
            or report.get("observed_stock_days") != report.get("expected_stock_days")):
        raise ValueError("ST_SOURCE_REPORT_NOT_COMPLETE")
    conflict_count = report.get("st_conflict_count")
    examples = report.get("st_conflict_examples", [])
    if (type(conflict_count) is not int or conflict_count < 0
            or not isinstance(examples, list) or len(examples) != conflict_count
            or any(not isinstance(item, list) or len(item) != 4
                   or not isinstance(item[0], str) or not isinstance(item[1], str)
                   or type(item[2]) is not bool or type(item[3]) is not bool
                   or item[2] == item[3] for item in examples)):
        raise ValueError("ST_SOURCE_CONFLICT_REPORT_INCOMPLETE")
    try:
        if any(date.fromisoformat(item[1]).year != year for item in examples):
            raise ValueError("ST_SOURCE_CONFLICT_REPORT_INCOMPLETE")
    except ValueError as exc:
        raise ValueError("ST_SOURCE_CONFLICT_REPORT_INCOMPLETE") from exc
    frame = pd.read_parquet(raw_path)
    if (list(frame.columns) != ["ts_code", "trade_date", "trading", "is_st", "close"]
            or len(frame) != report["expected_stock_days"] or frame.empty
            or frame[["ts_code", "trade_date", "trading", "is_st"]].isna().any().any()
            or not is_bool_dtype(frame["trading"]) or not is_bool_dtype(frame["is_st"])):
        raise ValueError("ST_SOURCE_RAW_INVALID_SHAPE")
    parsed = pd.to_datetime(frame["trade_date"], errors="coerce")
    if parsed.isna().any() or not parsed.dt.year.eq(year).all():
        raise ValueError("ST_SOURCE_RAW_INVALID_DATES")
    frame["trade_date"] = parsed.dt.date
    if frame.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("ST_SOURCE_RAW_DUPLICATE")
    calendar = {session.date() for session in xc.get_calendar(
        "XSHG", start=f"{year}-01-01", end=f"{year}-12-31",
    ).sessions}
    instruments = conn.execute(
        "SELECT ts_code,list_date,delist_date FROM market.instrument "
        "WHERE instrument_type='stock' AND length(ts_code)=9 "
        "AND right(ts_code,3) IN ('.SH','.SZ') AND list_date<=%s "
        "AND (delist_date IS NULL OR delist_date>%s)",
        (date(year, 12, 31), date(year, 1, 1)),
    ).fetchall()
    scope_codes = report.get("scope_codes")
    if scope_codes is not None:
        if (not isinstance(scope_codes, list) or not scope_codes
                or any(not isinstance(code, str) for code in scope_codes)
                or scope_codes != sorted(set(scope_codes))
                or not set(scope_codes) <= {row[0] for row in instruments}):
            raise ValueError("ST_SOURCE_INVALID_SCOPE")
        instruments = [row for row in instruments if row[0] in scope_codes]
    expected = {(code, day) for code, listed, delisted in instruments
                for day in calendar if listed <= day and (delisted is None or day < delisted)}
    actual = set(zip(frame["ts_code"].astype(str), frame["trade_date"]))
    if actual != expected:
        raise ValueError(f"ST_SOURCE_COVERAGE_MISMATCH:{len(expected - actual)}:{len(actual - expected)}")
    return frame


def _import_unlocked(conn, report_path: Path, raw_path: Path) -> dict:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    frame = _validated_frame(conn, report, raw_path)
    raw_hash = report["raw_parquet_sha256"]
    batch_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{SOURCE}|{report['year']}|{raw_hash}")
    observed_at = datetime.fromisoformat(report["generated_at"])
    if observed_at.tzinfo is None:
        raise ValueError("ST_SOURCE_BATCH_TIME_MUST_BE_AWARE")
    existing = conn.execute(
        "SELECT id,status,expected_days,scope_codes FROM market.stock_st_source_batch WHERE raw_sha256=%s",
        (raw_hash,),
    ).fetchone()
    if existing and (existing[0] != batch_id or existing[2] != len(frame)
                     or existing[3] != report.get("scope_codes")):
        raise ValueError("ST_SOURCE_BATCH_IDENTITY_CONFLICT")
    if existing and existing[1] == "VERIFIED":
        return {"batch_id": str(batch_id), "status": "VERIFIED", "rows": len(frame),
                "already_present": True}
    if existing is None:
        conn.execute(
            "INSERT INTO market.stock_st_source_batch "
            "(id,source,source_year,observed_at,raw_sha256,expected_days,observed_days,status,scope_codes) "
            "VALUES (%s,%s,%s,%s,%s,%s,0,'LOADING',%s)",
            (batch_id, SOURCE, report["year"], observed_at, raw_hash, len(frame), report.get("scope_codes")),
        )
        conn.commit()
    frame = frame.rename(columns={"trading": "reported_trading", "close": "reported_close"})
    frame["batch_id"] = batch_id
    frame["reported_close"] = pd.to_numeric(frame["reported_close"], errors="coerce")
    frame["reported_close"] = frame["reported_close"].astype(object).where(
        frame["reported_close"].notna(), None,
    )
    for offset in range(0, len(frame), 10000):
        chunk = frame.iloc[offset:offset + 10000]
        _bulk_upsert(
            conn, "market.stock_st_source_observation", COLS,
            ["batch_id", "ts_code", "trade_date"], chunk, False,
        )
        conn.commit()
    conflicts = conn.execute(
        "SELECT o.ts_code,o.trade_date,s.is_st,o.is_st "
        "FROM market.stock_st_source_observation o "
        "JOIN market.trade_status_daily s "
        "ON s.ts_code=o.ts_code AND s.trade_date=o.trade_date "
        "WHERE o.batch_id=%s AND s.is_st IS DISTINCT FROM o.is_st "
        "ORDER BY o.ts_code,o.trade_date",
        (batch_id,),
    ).fetchall()
    reported_conflicts = sorted(
        (code, date.fromisoformat(day), old, vendor)
        for code, day, old, vendor in report.get("st_conflict_examples", [])
    )
    if conflicts != reported_conflicts:
        raise ValueError("ST_SOURCE_CONFLICT_REPORT_MISMATCH")
    persisted = conn.execute(
        "SELECT count(*) FROM market.stock_st_source_observation WHERE batch_id=%s",
        (batch_id,),
    ).fetchone()[0]
    prior_conflicts = conn.execute(
        "SELECT count(*) FROM market.stock_st_source_observation o "
        "JOIN market.stock_st_source_observation old "
        "ON old.ts_code=o.ts_code AND old.trade_date=o.trade_date "
        "JOIN market.stock_st_source_batch b ON b.id=old.batch_id "
        "WHERE o.batch_id=%s AND old.batch_id<>%s AND b.source_year=%s "
        "AND b.status='VERIFIED' AND (old.is_st IS DISTINCT FROM o.is_st "
        "OR old.reported_trading IS DISTINCT FROM o.reported_trading)",
        (batch_id, batch_id, report["year"]),
    ).fetchone()[0]
    if persisted != len(frame) or prior_conflicts:
        raise ValueError(
            f"ST_SOURCE_POST_WRITE_MISMATCH:{persisted}:{len(conflicts)}:{prior_conflicts}",
        )
    for code, day, old, vendor in conflicts:
        conn.execute(
            "INSERT INTO market.stock_st_source_conflict "
            "(batch_id,ts_code,trade_date,incumbent_is_st,vendor_is_st) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (batch_id, code, day, old, vendor),
        )
    saved_conflicts = conn.execute(
        "SELECT ts_code,trade_date,incumbent_is_st,vendor_is_st "
        "FROM market.stock_st_source_conflict WHERE batch_id=%s "
        "ORDER BY ts_code,trade_date", (batch_id,),
    ).fetchall()
    if saved_conflicts != conflicts:
        raise ValueError("ST_SOURCE_CONFLICT_STORAGE_MISMATCH")
    conn.execute(
        "UPDATE market.stock_st_source_batch SET observed_days=%s,status='VERIFIED' "
        "WHERE id=%s AND status='LOADING'",
        (persisted, batch_id),
    )
    conn.commit()
    return {"batch_id": str(batch_id), "status": "VERIFIED", "rows": persisted,
            "quarantined_conflicts": len(conflicts), "already_present": False}


import_stock_st_source = locked_ingestion("CN_STOCK_QUANT_INPUTS")(_import_unlocked)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(".env")
    with get_connection() as conn, IngestGuard(
        conn, changed=market_changed_notifier_from_env(),
    ) as guard:
        result = import_stock_st_source(conn, args.report, args.raw, guard=guard)
        guard.commit("CN_STOCK_QUANT_INPUTS")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
