"""Read-only BaoStock daily-name/ST cross-check against observed Tushare rows."""

from __future__ import annotations

import argparse
import json
import re
import socket
from datetime import date, datetime, timezone
from pathlib import Path

import baostock as bs
from dotenv import load_dotenv

from db.instrument.db import get_connection

_ST_PREFIX = re.compile(r"^(?:S)?\*?ST", re.IGNORECASE)


def _baostock_rows(day: date) -> dict[str, dict] | None:
    response = bs.query_all_stock(day=day.isoformat())
    if response.error_code != "0" or response.fields != ["code", "tradeStatus", "code_name"]:
        return None
    result = {}
    while response.next():
        code, trading, name = response.get_row_data()
        match = re.fullmatch(r"(sh|sz)\.(\d{6})", code)
        if match is None:
            continue
        symbol = f"{match.group(2)}.{match.group(1).upper()}"
        if symbol in result or trading not in ("0", "1") or not name:
            return None
        result[symbol] = {"name": name, "trading": trading == "1",
                          "is_st_name": bool(_ST_PREFIX.match(name))}
    return result


def _compare(conn, day: date) -> dict:
    vendor = _baostock_rows(day)
    if vendor is None:
        return {"day": day.isoformat(), "result": "BAOSTOCK_UNAVAILABLE"}
    rows = conn.execute(
        "SELECT i.ts_code, s.is_st FROM market.instrument i "
        "LEFT JOIN market.trade_status_daily s ON s.ts_code=i.ts_code AND s.trade_date=%s "
        "WHERE i.instrument_type='stock' AND length(i.ts_code)=9 "
        "AND right(i.ts_code,3) IN ('.SH','.SZ') "
        "AND i.list_date<=%s AND (i.delist_date IS NULL OR %s<i.delist_date)",
        (day, day, day),
    ).fetchall()
    missing_vendor, mismatch, observed = [], [], 0
    for code, is_st in rows:
        record = vendor.get(code)
        if record is None:
            missing_vendor.append(code)
            continue
        if is_st is not None:
            observed += 1
            if bool(is_st) != record["is_st_name"]:
                mismatch.append({"code": code, "tushare_is_st": bool(is_st), **record})
    return {"day": day.isoformat(), "result": "CHECKED", "expected_codes": len(rows),
            "vendor_codes": len(vendor), "missing_vendor_count": len(missing_vendor),
            "missing_vendor_examples": missing_vendor[:30], "observed_overlap": observed,
            "mismatch_count": len(mismatch), "mismatch_examples": mismatch[:30],
            "vendor_st_count_in_pool": sum(vendor.get(code, {}).get("is_st_name", False)
                                           for code, _ in rows)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(".env")
    socket.setdefaulttimeout(10)
    login = bs.login()
    if login.error_code != "0":
        raise SystemExit(f"BaoStock login failed: {login.error_code} {login.error_msg}")
    try:
        with get_connection() as conn:
            reports = [_compare(conn, date.fromisoformat(day)) for day in args.days]
    finally:
        bs.logout()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(),
                                       "reports": reports}, ensure_ascii=False, indent=2), encoding="utf-8")
    print([(item["day"], item["missing_vendor_count"], item["observed_overlap"],
            item["mismatch_count"]) for item in reports if item["result"] == "CHECKED"])


if __name__ == "__main__":
    main()
