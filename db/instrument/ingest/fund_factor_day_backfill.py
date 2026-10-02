"""Backfill missing observed fund factor rows by local trading day."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from AI.dataflows.providers.base_provider import ProviderNetworkAccessDenied
from AI.dataflows.providers.cn.tushare import TushareProvider
from db.instrument.db import get_connection
from db.instrument.ingest.fund_factors import (
    REQUIRED_HISTORY_BARS,
    _indicator_gaps,
    collect_fund_factor_day,
)
from db.instrument.ingest.guard import FATAL_INGEST_ERRORS, IngestGuard
from db.instrument.ingest.notifications import market_changed_notifier_from_env

logger = logging.getLogger(__name__)


class _WarmupRequestBudget:
    """Share a bounded, memoized upstream history budget across planning and writes."""

    def __init__(self, provider, limit: int):
        self.provider = provider
        self.limit = limit
        self.requests = 0
        self.cache = {}

    def __getattr__(self, name):
        return getattr(self.provider, name)

    def get_fund_daily_df(self, code, start, end):
        key = (code, start, end)
        if key in self.cache:
            return self.cache[key]
        if self.requests >= self.limit:
            return None  # No evidence: leave the indicator pending.
        self.requests += 1
        frame = self.provider.get_fund_daily_df(code, start, end)
        self.cache[key] = frame
        return frame


def _pending_days(conn, start: date, end: date, provider=None) -> list[date]:
    columns = list(REQUIRED_HISTORY_BARS)
    rows = conn.execute(
        "SELECT d.trade_date,d.ts_code,f.ts_code IS NOT NULL," +
        ",".join("f." + column for column in columns) +
        " FROM market.instrument_daily d "
        "JOIN market.instrument i ON i.ts_code=d.ts_code AND i.instrument_type='fund' "
        "LEFT JOIN market.factor_daily f ON f.ts_code=d.ts_code AND f.trade_date=d.trade_date "
        "WHERE d.trade_date BETWEEN %s AND %s AND (f.ts_code IS NULL OR " +
        " OR ".join("f." + column + " IS NULL" for column in columns) + ") "
        "ORDER BY d.trade_date", (start, end),
    ).fetchall()
    if not rows:
        return []
    candidates = pd.DataFrame(rows, columns=["trade_date", "ts_code", "has_factor", *columns])
    pending = []
    for day, frame in candidates.groupby("trade_date", sort=True):
        if not frame["has_factor"].all() or _indicator_gaps(
            conn, frame, day.isoformat(), provider,
        )[0]:
            pending.append(day)
    return pending


def run(conn, provider, *, start: date, end: date, guard: IngestGuard,
        limit_days: int | None = None, verification_requests: int = 64) -> dict:
    if start > end or limit_days is not None and limit_days < 1 or verification_requests < 0:
        raise ValueError("invalid fund factor day range or limit")
    provider = _WarmupRequestBudget(provider, verification_requests)
    pending = _pending_days(conn, start, end, provider)
    conn.commit()  # Release the read transaction before upstream requests.
    selected = pending[:limit_days]
    results = []
    failures = []
    consecutive_failures = 0
    for day in selected:
        try:
            result = collect_fund_factor_day(conn, provider, day.isoformat(), guard=guard)
            results.append(result)
            consecutive_failures = 0
        except (ProviderNetworkAccessDenied, *FATAL_INGEST_ERRORS):
            raise
        except Exception as exc:  # noqa: BLE001 - a failed day must not erase earlier commits
            conn.rollback()
            failures.append(day.isoformat())
            consecutive_failures += 1
            results.append({"trade_date": day.isoformat(), "status": "FAILED",
                            "error_type": type(exc).__name__, "error": str(exc)})
            logger.warning("fund factor day %s failed: %s", day, exc)
        if len(results) % 10 == 0:
            logger.info("fund factor days %d/%d, failed %d", len(results), len(selected), len(failures))
        if consecutive_failures >= 5:
            logger.warning("five consecutive fund factor source failures; stopping")
            break
    remaining = _pending_days(conn, start, end, provider)
    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "pending_before": len(pending), "attempted": len(results),
        "written": sum(item.get("factor_rows", 0) for item in results),
        "failed_days": failures,
        "partial_days": [item["trade_date"] for item in results
                         if item.get("status") == "PARTIAL"],
        "pending_after": len(remaining),
        "next_dates": [day.isoformat() for day in remaining[:20]],
        "history_verification_requests": provider.requests,
        "history_verification_budget": provider.limit,
        "results": results,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--limit-days", type=int)
    parser.add_argument("--verification-requests", type=int, default=64,
                        help="Maximum extra history requests shared by planning, writes and recheck")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with get_connection() as conn, IngestGuard(
        conn, changed=market_changed_notifier_from_env(),
    ) as guard:
        provider = TushareProvider()
        if not provider.connected:
            raise RuntimeError("fund factor provider unavailable")
        report = run(conn, provider, start=args.start, end=args.end,
                     limit_days=args.limit_days, guard=guard,
                     verification_requests=args.verification_requests)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "pending_before", "attempted", "written", "pending_after", "failed_days", "partial_days",
    )}, ensure_ascii=False))
    if report["failed_days"] or report["partial_days"] or report["pending_after"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
