"""Read-only comparison of unresolved MA250 cases with upstream fund daily history."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from AI.dataflows.providers.cn.tushare import TushareProvider
from db.instrument.db import get_connection


def main() -> None:
    load_dotenv(".env")
    root = Path(__file__).parent
    cases = {}
    for name in ("fund_factor_day_2026_pilot.json", "fund_factor_day_2026_full.json"):
        report = json.loads((root / name).read_text(encoding="utf-8"))
        for result in report["results"]:
            for code in result.get("incomplete_indicator_codes", []):
                cases.setdefault(code, []).append(result["trade_date"])
    provider = TushareProvider()
    results = []
    for code, targets in sorted(cases.items()):
        end = max(targets)
        with get_connection() as conn:
            listed = conn.execute("SELECT list_date FROM market.instrument WHERE ts_code=%s", (code,)).fetchone()[0]
            local = conn.execute(
                "SELECT trade_date,close FROM market.instrument_daily "
                "WHERE ts_code=%s AND trade_date BETWEEN %s AND %s ORDER BY trade_date",
                (code, listed, end),
            ).fetchall()
        if listed is None:
            results.append({"code": code, "status": "UNKNOWN_LISTING"})
            continue
        source = provider._api_call(
            provider.api.fund_daily, ts_code=code,
            start_date=listed.strftime("%Y%m%d"), end_date=end.replace("-", ""),
            fields="ts_code,trade_date,close",
        )
        if source is None or source.empty or len(source) >= 8000:
            results.append({"code": code, "status": "SOURCE_UNAVAILABLE_OR_TRUNCATED"})
            continue
        source = source.copy()
        days = pd.to_datetime(source["trade_date"].astype(str), errors="coerce")
        if (not source["ts_code"].eq(code).all() or days.isna().any()
                or days.duplicated().any()
                or not days.dt.date.between(listed, pd.Timestamp(end).date()).all()):
            results.append({"code": code, "status": "SOURCE_IDENTITY_INVALID"})
            continue
        source["trade_date"] = days.dt.strftime("%Y-%m-%d")
        source["close"] = pd.to_numeric(source["close"], errors="coerce")
        local_by_day = {day.isoformat(): float(close) for day, close in local}
        source_by_day = dict(zip(source["trade_date"], source["close"]))
        common = local_by_day.keys() & source_by_day.keys()
        mismatches = sum(not np.isclose(local_by_day[day], source_by_day[day], rtol=0, atol=1e-4)
                         for day in common)
        item = {
            "code": code, "listed": listed.isoformat(), "end": end,
            "local_rows": len(local), "source_rows": len(source),
            "missing_local_dates": sorted(source_by_day.keys() - local_by_day.keys()),
            "extra_local_dates": sorted(local_by_day.keys() - source_by_day.keys()),
            "price_mismatches": mismatches,
            "targets": [{"day": day, "upstream_observed_bars": sum(d <= day for d in source_by_day)}
                        for day in sorted(targets)],
        }
        item["status"] = ("MATCHED" if not item["missing_local_dates"]
                          and not item["extra_local_dates"] and mismatches == 0 else "MISMATCH")
        results.append(item)
        print(code, item["status"], len(source), flush=True)
    output = {"generated_at": datetime.now(timezone.utc).isoformat(), "results": results}
    (root / "fund_factor_2026_warmup_source_verification.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8",
    )


if __name__ == "__main__":
    main()
