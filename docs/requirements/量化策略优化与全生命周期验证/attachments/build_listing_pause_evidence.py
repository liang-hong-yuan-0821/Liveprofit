"""Build annual official pause evidence from a reviewed, exact-shape manifest."""

import argparse
import json
from datetime import date
from pathlib import Path

import exchange_calendars as xc
from dotenv import load_dotenv

from db.instrument.db import get_connection

ROOT = Path(__file__).parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    args = parser.parse_args()
    if not 2016 <= args.year <= 2026:
        raise ValueError("unsupported year")
    load_dotenv()
    manifest_path = ROOT / f"listing_pause_sources_{args.year}.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, list) or not manifest:
        raise ValueError("empty pause manifest")
    codes = [item["ts_code"] for item in manifest]
    if len(codes) != len(set(codes)):
        raise ValueError("duplicate pause code")
    sessions = [stamp.date() for stamp in xc.get_calendar(
        "XSHG", start=f"{args.year}-01-01", end=f"{args.year}-12-31",
    ).sessions]
    evidence = []
    with get_connection() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        for item in manifest:
            code = item["ts_code"]
            first = date.fromisoformat(item["first"])
            last = date.fromisoformat(item["last"])
            if first.year != args.year or last.year != args.year or first > last:
                raise ValueError(f"invalid year/range: {code}")
            rows = conn.execute(
                "SELECT d.day FROM unnest(%s::date[]) AS d(day) "
                "JOIN market.instrument i ON i.ts_code=%s AND i.instrument_type='stock' "
                "AND i.list_date<=d.day AND (i.delist_date IS NULL OR d.day<i.delist_date) "
                "LEFT JOIN market.instrument_daily b ON b.ts_code=i.ts_code AND b.trade_date=d.day "
                "LEFT JOIN market.trade_status_daily s ON s.ts_code=i.ts_code AND s.trade_date=d.day "
                "WHERE b.ts_code IS NULL AND d.day BETWEEN %s AND %s "
                "AND NOT EXISTS (SELECT 1 FROM market.suspension_source_daily v "
                "WHERE v.ts_code=i.ts_code AND v.trade_date=d.day AND v.scope='full_day' "
                "AND v.source='tushare_suspend_d') "
                "AND NOT (s.is_suspended IS TRUE AND s.suspension_scope='full_day' "
                "AND s.source='tushare') ORDER BY d.day",
                (sessions, code, first, last),
            ).fetchall()
            days = [row[0] for row in rows]
            if (len(days) != item["count"] or not days
                    or days[0] != first or days[-1] != last):
                raise RuntimeError(f"unexpected pause gap shape: {code} {len(days)}")
            for day in days:
                evidence.append({
                    "ts_code": code, "trade_date": day.isoformat(),
                    "scope": "full_day", "source_url": item["source_url"],
                    "published_on": item["published_on"],
                    "evidence_note": item["evidence_note"],
                })
    if len(evidence) != sum(item["count"] for item in manifest):
        raise RuntimeError("pause evidence total mismatch")
    path = ROOT / f"suspension_evidence_{args.year}_listing_pauses.json"
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print({"year": args.year, "codes": len(codes), "rows": len(evidence), "path": str(path)})


if __name__ == "__main__":
    main()
