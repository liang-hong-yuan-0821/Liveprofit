"""Persist independently verified full-day vendor suspension observations."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import exchange_calendars as xc
import pandas as pd
from dotenv import load_dotenv

from AI.dataflows.providers.base_provider import (
    ProviderNetworkAccessDenied,
    raise_if_network_access_denied,
)
from AI.dataflows.providers.cn.tushare import TushareProvider
from db.instrument.db import get_connection
from db.instrument.ingest.guard import (
    FATAL_INGEST_ERRORS,
    IngestGuard,
    locked_ingestion,
)
from db.instrument.ingest.notifications import market_changed_notifier_from_env

SOURCE = "tushare_suspend_d"
logger = logging.getLogger(__name__)


def collect_suspension_source_day(conn, provider, day: date) -> dict:
    """Replace one day's observation set only after both upstream feeds validate."""
    frame = provider.get_verified_full_day_suspensions_df(day.isoformat())
    raise_if_network_access_denied(provider)
    if frame is None or not {"ts_code", "trade_date"} <= set(frame):
        return {"day": day.isoformat(), "status": "UNAVAILABLE", "rows": 0}
    dates = pd.to_datetime(frame["trade_date"].astype(str), errors="coerce")
    if (dates.isna().any() or not dates.dt.date.eq(day).all()
            or frame["ts_code"].isna().any()
            or frame["ts_code"].duplicated().any()):
        raise ValueError("SUSPENSION_SOURCE_INVALID_KEYS")
    codes = sorted(set(frame["ts_code"].astype(str)))
    active = set()
    if codes:
        active = {row[0] for row in conn.execute(
            "SELECT ts_code FROM market.instrument WHERE ts_code=ANY(%s) "
            "AND instrument_type='stock' AND list_date<=%s "
            "AND (delist_date IS NULL OR %s<delist_date) "
            "AND (ts_code NOT LIKE '%%.BJ' OR %s>=DATE '2021-11-15')",
            (codes, day, day, day),
        ).fetchall()}
    eligible = sorted(active & set(codes))
    conflicting = set()
    if eligible:
        conflicting = {row[0] for row in conn.execute(
            "SELECT ts_code FROM market.instrument_daily "
            "WHERE trade_date=%s AND ts_code=ANY(%s)",
            (day, eligible),
        ).fetchall()}
    verified = sorted(set(eligible) - conflicting)
    conn.execute(
        "DELETE FROM market.suspension_source_daily "
        "WHERE trade_date=%s AND source=%s", (day, SOURCE),
    )
    if verified:
        params = []
        for code in verified:
            evidence_hash = hashlib.sha256(
                f"{SOURCE}|{day.isoformat()}|{code}|S|no_daily".encode(),
            ).hexdigest()
            params.extend((code, day, SOURCE, evidence_hash))
        conn.execute(
            "INSERT INTO market.suspension_source_daily "
            "(ts_code,trade_date,scope,source,evidence_hash) "
            "VALUES " + ",".join(["(%s,%s,'full_day',%s,%s)"] * len(verified)),
            params,
        )
    conn.commit()
    return {"day": day.isoformat(), "status": "PARTIAL" if conflicting else "SUCCESS",
            "rows": len(verified), "bar_conflicts": sorted(conflicting),
            "outside_stock_pool": sorted(set(codes) - active)}


def _run_suspension_source_backfill(conn, provider_factory, start: date,
                                    end: date, max_days: int | None = None) -> dict:
    if start > end or max_days is not None and max_days < 1:
        raise ValueError("invalid suspension source window")
    provider = provider_factory()
    if getattr(provider, "connected", True) is False:
        raise RuntimeError("suspension source provider unavailable")
    days = [session.date() for session in xc.get_calendar(
        "XSHG", start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
    ).sessions if session.date() <= end]
    results = []
    consecutive_source_failures = 0
    for day in days[:max_days]:
        try:
            result = collect_suspension_source_day(conn, provider, day)
            results.append(result)
            consecutive_source_failures = (consecutive_source_failures + 1
                                           if result["status"] == "UNAVAILABLE" else 0)
        except (*FATAL_INGEST_ERRORS, ProviderNetworkAccessDenied):
            raise
        except Exception as exc:  # noqa: BLE001 - preserve each day's failure without guessing facts
            conn.rollback()
            results.append({"day": day.isoformat(), "status": "FAILED", "rows": 0,
                            "error_type": type(exc).__name__})
            consecutive_source_failures += 1
        if len(results) % 20 == 0:
            logger.info("停牌源逐日复核 %d/%d，可信全天停牌 %d 项",
                        len(results), len(days), sum(item["rows"] for item in results))
        if consecutive_source_failures >= 5:
            logger.warning("连续五个交易日停牌源不可用，停止请求上游")
            break
    return {"start": start.isoformat(), "end": end.isoformat(),
            "calendar_days": len(days), "attempted": len(results),
            "verified_rows": sum(item["rows"] for item in results),
            "non_success_days": [item["day"] for item in results
                                 if item["status"] != "SUCCESS"],
            "results": results}


run_suspension_source_backfill = locked_ingestion("CN_STOCK_DAILY")(
    _run_suspension_source_backfill)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--max-days", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with get_connection() as conn, IngestGuard(
        conn, changed=market_changed_notifier_from_env(),
    ) as guard:
        report = run_suspension_source_backfill(
            conn, TushareProvider, args.start, args.end,
            max_days=args.max_days, guard=guard,
        )
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print({key: report[key] for key in ("calendar_days", "attempted", "verified_rows",
                                       "non_success_days")})
    if report["non_success_days"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
