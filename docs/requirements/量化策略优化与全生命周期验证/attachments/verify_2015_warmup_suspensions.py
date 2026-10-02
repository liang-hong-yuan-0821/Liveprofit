"""Verify 2015 warmup no-bar days against independent full-day suspension feed."""

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from AI.dataflows.providers.base_provider import raise_if_network_access_denied
from AI.dataflows.providers.cn.tushare import TushareProvider
from db.instrument.db import get_connection
from db.instrument.ingest.guard import IngestGuard

ROOT = Path(__file__).parent
SOURCE = "tushare_suspend_d"


def main():
    load_dotenv(".env")
    context = json.loads((ROOT / "warmup_context_2015_backfill.json").read_text(encoding="utf-8"))
    expected = {(item["ts_code"], date.fromisoformat(day))
                for item in context["codes"] for day in item["factor_only_dates"]}
    codes = sorted({code for code, _ in expected})
    if len(expected) != 199 or codes != ["300456.SZ", "300466.SZ"]:
        raise RuntimeError("2015 factor-only set changed")
    provider = TushareProvider()
    if not provider.connected:
        raise RuntimeError("Tushare provider unavailable")
    observed = set()
    details = []
    for code in codes:
        frame = provider._api_call(provider.api.suspend_d, ts_code=code,
                                   start_date="20150101", end_date="20151231")
        raise_if_network_access_denied(provider)
        if (frame is None or not {"ts_code", "trade_date", "suspend_type",
                                   "suspend_timing"} <= set(frame.columns)):
            raise RuntimeError(f"suspend_d unavailable: {code}")
        frame = frame.copy()
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise").dt.date
        if (frame["ts_code"].ne(code).any() or frame["trade_date"].duplicated().any()
                or frame["trade_date"].lt(date(2015, 1, 1)).any()
                or frame["trade_date"].gt(date(2015, 12, 31)).any()):
            raise RuntimeError(f"suspend_d identity/date mismatch: {code}")
        eligible = frame.loc[(frame["suspend_type"] == "S")
                             & frame["suspend_timing"].isna()]
        observed.update((code, day) for day in eligible["trade_date"])
        details.append({"ts_code": code, "source_rows": len(frame),
                        "full_day_rows": len(eligible)})
    if not expected <= observed:
        raise RuntimeError(f"199 missing sessions not source verified: {len(expected - observed)}")
    with get_connection() as conn, IngestGuard(conn) as guard:
        db = guard.connection
        for code, day in sorted(expected):
            row = db.execute(
                "SELECT i.list_date, EXISTS(SELECT 1 FROM market.instrument_daily b "
                "WHERE b.ts_code=i.ts_code AND b.trade_date=%s) "
                "FROM market.instrument i WHERE i.ts_code=%s AND i.instrument_type='stock'",
                (day, code),
            ).fetchone()
            if row is None or row[0] > day or row[1]:
                raise RuntimeError(f"catalog/bar conflict: {code} {day}")
            proof = hashlib.sha256(
                f"{SOURCE}|{day.isoformat()}|{code}|S|no_daily".encode(),
            ).hexdigest()
            db.execute(
                "INSERT INTO market.suspension_source_daily "
                "(ts_code,trade_date,scope,source,evidence_hash) "
                "VALUES (%s,%s,'full_day',%s,%s) ON CONFLICT DO NOTHING",
                (code, day, SOURCE, proof),
            )
        db.commit()
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "source": SOURCE, "details": details,
              "verified_target_rows": len(expected),
              "target": "2015 factor-only no-bar dates for 2016 warmup candidates"}
    path = ROOT / "warmup_suspensions_2015_verification.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
