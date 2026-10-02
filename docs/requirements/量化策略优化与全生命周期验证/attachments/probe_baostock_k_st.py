"""Read-only annual BaoStock isST/tradestatus extraction and cross-source audit.

Raw observations are written to an ignored local parquet file for later review.
This command never changes PostgreSQL market facts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import time
from datetime import date, datetime, timezone
from pathlib import Path

import baostock as bs
import exchange_calendars as xc
import pandas as pd
from dotenv import load_dotenv

from db.instrument.db import get_connection

_FIELDS = "date,code,close,tradestatus,isST"


def _fetch(code: str, year: int) -> list[tuple] | None:
    ticker, exchange = code.split(".")
    vendor_code = f"{exchange.lower()}.{ticker}"
    for attempt in range(3):
        response = bs.query_history_k_data_plus(
            vendor_code, _FIELDS, start_date=f"{year}-01-01",
            end_date=f"{year}-12-31", frequency="d", adjustflag="3",
        )
        if response.error_code == "0" and response.fields == _FIELDS.split(","):
            rows = []
            while response.next():
                day, returned_code, close, trading, is_st = response.get_row_data()
                if returned_code != vendor_code or trading not in ("0", "1") or is_st not in ("0", "1"):
                    return None
                try:
                    observed_day = date.fromisoformat(day)
                except ValueError:
                    return None
                rows.append((code, observed_day, trading == "1", is_st == "1", close))
            if len({item[1] for item in rows}) != len(rows):
                return None
            return rows
        if attempt < 2:
            time.sleep(0.2 * (attempt + 1))
    return None


def _load_verified_resume(year: int, output: Path, raw_output: Path) -> dict[str, list[tuple]]:
    """Reuse only complete per-code observations from an unchanged prior extraction."""
    if not output.exists() or not raw_output.exists():
        return {}
    prior = json.loads(output.read_text(encoding="utf-8"))
    if prior.get("year") != year or hashlib.sha256(raw_output.read_bytes()).hexdigest() != prior.get(
        "raw_parquet_sha256"
    ):
        raise ValueError("resume report/raw mismatch")
    frame = pd.read_parquet(raw_output)
    if list(frame.columns) != ["ts_code", "trade_date", "trading", "is_st", "close"]:
        raise ValueError("resume raw columns mismatch")
    if len(frame) != prior.get("observed_stock_days") or frame.duplicated(
        ["ts_code", "trade_date"]
    ).any():
        raise ValueError("resume raw count or keys mismatch")
    dates = pd.to_datetime(frame["trade_date"], errors="coerce")
    if dates.isna().any() or not dates.dt.year.eq(year).all():
        raise ValueError("resume raw dates mismatch")
    if not pd.api.types.is_bool_dtype(frame["trading"]) or not pd.api.types.is_bool_dtype(frame["is_st"]):
        raise ValueError("resume raw flags mismatch")
    result = {}
    for code, group in frame.groupby("ts_code", sort=False):
        result[str(code)] = list(zip(
            group["ts_code"], pd.to_datetime(group["trade_date"]).dt.date,
            group["trading"].astype(bool), group["is_st"].astype(bool), group["close"],
        ))
    return result


def run(year: int, output: Path, raw_output: Path, *, resume: bool = False,
        scope_codes: list[str] | None = None) -> dict:
    calendar = {session.date() for session in xc.get_calendar(
        "XSHG", start=f"{year}-01-01", end=f"{year}-12-31",
    ).sessions}
    scope_sql = " AND ts_code=ANY(%s)" if scope_codes is not None else ""
    scope_params = (scope_codes,) if scope_codes is not None else ()
    with get_connection() as conn:
        instruments = conn.execute(
            "SELECT ts_code,list_date,delist_date FROM market.instrument "
            "WHERE instrument_type='stock' AND length(ts_code)=9 "
            "AND right(ts_code,3) IN ('.SH','.SZ') AND list_date<=%s "
            "AND (delist_date IS NULL OR delist_date>%s)" + scope_sql + " ORDER BY ts_code",
            (date(year, 12, 31), date(year, 1, 1)) + scope_params,
        ).fetchall()
        if scope_codes is not None and (not scope_codes or scope_codes != sorted(set(scope_codes))
                                       or set(scope_codes) != {row[0] for row in instruments}):
            raise ValueError("invalid explicit source scope")
        known = conn.execute(
            "SELECT ts_code,trade_date,is_st FROM market.trade_status_daily "
            "WHERE trade_date BETWEEN %s AND %s AND is_st IS NOT NULL" + scope_sql,
            (date(year, 1, 1), date(year, 12, 31)) + scope_params,
        ).fetchall()
        bar_rows = conn.execute(
            "SELECT ts_code,trade_date FROM market.instrument_daily "
            "WHERE trade_date BETWEEN %s AND %s" + scope_sql,
            (date(year, 1, 1), date(year, 12, 31)) + scope_params,
        ).fetchall()
    known_st = {(code, day): bool(value) for code, day, value in known}
    bar_keys = set(bar_rows)
    expected = 0
    rows = []
    failed_codes = []
    missing_examples = []
    extra_examples = []
    st_conflicts = []
    bar_conflicts = []
    overlap = 0
    missing_count = extra_count = st_conflict_count = bar_conflict_count = 0
    prior_rows = _load_verified_resume(year, output, raw_output) if resume else {}
    if set(prior_rows) - {code for code, _, _ in instruments}:
        raise ValueError("resume raw contains unknown code")
    reused_codes = 0
    for offset, (code, listed, delisted) in enumerate(instruments, 1):
        expected_dates = {day for day in calendar if listed <= day and (
            delisted is None or day < delisted
        )}
        expected += len(expected_dates)
        observed = prior_rows.get(code)
        if observed is not None and {row[1] for row in observed} == expected_dates:
            reused_codes += 1
        else:
            observed = _fetch(code, year)
        if observed is None:
            failed_codes.append(code)
            continue
        found_dates = {row[1] for row in observed}
        missing_count += len(expected_dates - found_dates)
        extra_count += len(found_dates - expected_dates)
        missing_examples.extend((code, day.isoformat()) for day in sorted(expected_dates - found_dates)[:3]
                                if len(missing_examples) < 40)
        extra_examples.extend((code, day.isoformat()) for day in sorted(found_dates - expected_dates)[:3]
                              if len(extra_examples) < 40)
        for row in observed:
            symbol, day, trading, is_st, _close = row
            if day not in expected_dates:
                continue
            rows.append(row)
            key = (symbol, day)
            if key in known_st:
                overlap += 1
                if known_st[key] != is_st:
                    st_conflict_count += 1
                    if len(st_conflicts) < 100:
                        st_conflicts.append((symbol, day.isoformat(), known_st[key], is_st))
            if (key in bar_keys) != trading:
                bar_conflict_count += 1
                if len(bar_conflicts) < 100:
                    bar_conflicts.append((symbol, day.isoformat(), key in bar_keys, trading))
        if offset % 500 == 0:
            print(f"{offset}/{len(instruments)} codes, {len(rows)} observations", flush=True)

    frame = pd.DataFrame(rows, columns=["ts_code", "trade_date", "trading", "is_st", "close"])
    frame.sort_values(["ts_code", "trade_date"], inplace=True)
    raw_output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(raw_output, index=False)
    digest = hashlib.sha256(raw_output.read_bytes()).hexdigest()
    report = {
        "year": year, "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "baostock.query_history_k_data_plus.daily.unadjusted",
        "scope_codes": scope_codes,
        "calendar_days": len(calendar), "instrument_codes": len(instruments),
        "expected_stock_days": expected, "observed_stock_days": len(frame),
        "reused_codes": reused_codes,
        "failed_codes": failed_codes, "missing_count": missing_count,
        "missing_examples": missing_examples, "extra_count": extra_count,
        "extra_examples": extra_examples, "known_st_overlap": overlap,
        "st_conflict_count": st_conflict_count, "st_conflict_examples": st_conflicts,
        "bar_presence_conflict_count": bar_conflict_count,
        "bar_presence_conflict_examples": bar_conflicts,
        "raw_parquet": str(raw_output), "raw_parquet_sha256": digest,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true", help="reuse hash-checked complete codes")
    parser.add_argument("--codes", help="Explicit SH/SZ codes; omitted means the full annual universe")
    args = parser.parse_args()
    load_dotenv(".env")
    socket.setdefaulttimeout(10)
    login = bs.login()
    if login.error_code != "0":
        raise SystemExit(f"BaoStock login failed: {login.error_code} {login.error_msg}")
    try:
        report = run(args.year, args.output, args.raw_output, resume=args.resume,
                     scope_codes=sorted(set(args.codes.split(","))) if args.codes else None)
    finally:
        bs.logout()
    print({key: report[key] for key in (
        "expected_stock_days", "observed_stock_days", "known_st_overlap"
    )}, "failed_codes", len(report["failed_codes"]),
          "st_conflicts_shown", len(report["st_conflict_examples"]),
          "bar_conflicts_shown", len(report["bar_presence_conflict_examples"]))


if __name__ == "__main__":
    main()
