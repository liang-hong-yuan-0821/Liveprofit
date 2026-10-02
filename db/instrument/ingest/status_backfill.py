"""Bounded, audited backfill of historical stock status observations."""

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
from db.instrument.audit_history import _year_days
from db.instrument.db import get_connection
from db.instrument.ingest.guard import FATAL_INGEST_ERRORS, IngestBusy
from db.instrument.ingest.stock_factors import collect_stock_status_day

logger = logging.getLogger(__name__)

_MISSING_STATUS_SQL = """
WITH days(day) AS (SELECT unnest(%s::date[]))
SELECT d.day, count(s.ts_code) AS observed_status_rows
FROM days d JOIN market.instrument i ON i.instrument_type='stock'
 AND i.list_date<=d.day AND (i.delist_date IS NULL OR d.day<i.delist_date)
 AND (i.ts_code NOT LIKE '%%.BJ' OR d.day>=DATE '2021-11-15')
LEFT JOIN market.trade_status_effective s ON s.ts_code=i.ts_code AND s.trade_date=d.day
GROUP BY d.day
HAVING count(*) FILTER (WHERE s.ts_code IS NULL
 OR s.source NOT IN ('tushare','tushare+baostock')
 OR s.is_suspended IS NULL OR s.is_st IS NULL OR s.market_board IS NULL
 OR (s.is_suspended IS TRUE AND (s.suspension_scope IS NULL
     OR s.suspension_scope='unknown'))
 OR btrim(s.market_board)='') > 0
-- A partially covered day remains pending, but must not starve untouched days.
ORDER BY observed_status_rows ASC, d.day DESC
"""


def missing_status_days(conn, days: list[date]) -> list[date]:
    if not days:
        return []
    return [row[0] for row in conn.execute(_MISSING_STATUS_SQL, (days,)).fetchall()]


def missing_status_codes(conn, day: date) -> list[str]:
    """Refresh only deficient symbols, preserving already verified observations."""
    rows = conn.execute(
        "SELECT i.ts_code FROM market.instrument i "
        "LEFT JOIN market.trade_status_effective s "
        "ON s.ts_code=i.ts_code AND s.trade_date=%s "
        "WHERE i.instrument_type='stock' AND i.list_date<=%s "
        "AND (i.delist_date IS NULL OR %s<i.delist_date) "
        "AND (i.ts_code NOT LIKE '%%.BJ' OR %s>=DATE '2021-11-15') "
        "AND (s.ts_code IS NULL OR s.source NOT IN ('tushare','tushare+baostock') "
        "OR s.is_suspended IS NULL OR s.is_st IS NULL OR s.market_board IS NULL "
        "OR (s.is_suspended IS TRUE AND (s.suspension_scope IS NULL "
        "OR s.suspension_scope='unknown')) OR btrim(s.market_board)='') "
        "ORDER BY i.ts_code", (day, day, day, day),
    ).fetchall()
    return [row[0] for row in rows]


def verified_st_observations(conn, day: date) -> pd.DataFrame | None:
    """Use complete immutable SH/SZ facts; BSE membership remains a provider responsibility."""
    batch = conn.execute(
        "SELECT id FROM market.stock_st_source_batch "
        "WHERE source='baostock_kline' AND source_year=%s AND status='VERIFIED' "
        "AND scope_codes IS NULL "
        "ORDER BY observed_at DESC LIMIT 1", (day.year,),
    ).fetchone()
    if batch is None:
        return None
    conflicts = {row[0] for row in conn.execute(
        "SELECT ts_code FROM market.stock_st_source_conflict "
        "WHERE batch_id=%s AND trade_date=%s", (batch[0], day),
    ).fetchall()}
    expected = {row[0] for row in conn.execute(
        "SELECT ts_code FROM market.instrument WHERE instrument_type='stock' "
        "AND list_date<=%s AND (delist_date IS NULL OR delist_date>%s) "
        "AND ts_code NOT LIKE '%%.BJ'",
        (day, day),
    ).fetchall()}
    rows = conn.execute(
        "SELECT ts_code,is_st,reported_trading FROM market.stock_st_source_observation "
        "WHERE batch_id=%s AND trade_date=%s", (batch[0], day),
    ).fetchall()
    if not rows or len(rows) != len(expected) or {row[0] for row in rows} != expected:
        return None
    return pd.DataFrame({"ts_code": [row[0] for row in rows],
                         "trade_date": [day.isoformat()] * len(rows),
                         "is_st": [bool(row[1]) for row in rows],
                         "reported_trading": [bool(row[2]) for row in rows],
                         "st_conflict": [row[0] in conflicts for row in rows]})


def verified_no_trade_observations(conn, day: date) -> pd.DataFrame | None:
    """A scoped extraction can prove individual no-trade facts, never full ST coverage."""
    rows = conn.execute(
        "SELECT o.ts_code FROM market.stock_st_source_observation o "
        "JOIN market.stock_st_source_batch b ON b.id=o.batch_id "
        "WHERE b.status='VERIFIED' AND b.source='baostock_kline' AND o.trade_date=%s "
        "GROUP BY o.ts_code HAVING bool_and(o.reported_trading IS FALSE)", (day,),
    ).fetchall()
    if not rows:
        return None
    return pd.DataFrame({"ts_code": [row[0] for row in rows],
                         "trade_date": [day.isoformat()] * len(rows),
                         "reported_trading": [False] * len(rows)})


def backfill_status(conn, provider: TushareProvider, *, start: date, end: date,
                    max_days: int = 20) -> dict:
    """Newest deficient dates first; never infer an empty ST response as all-clear."""
    if start > end or max_days < 1:
        raise ValueError("invalid status backfill range")
    calendar = []
    for year in range(start.year, end.year + 1):
        calendar.extend(day for day in _year_days(provider, year, end)
                        if start <= day <= end)
    if not calendar:
        raise RuntimeError("verified calendar has no trading days")
    pending = missing_status_days(conn, calendar)
    attempted = []
    consecutive_unavailable = 0
    for day in pending[:max_days]:
        try:
            codes = missing_status_codes(conn, day)
            if not codes:
                continue
            st_observations = verified_st_observations(conn, day)
            if st_observations is None:
                no_trade = verified_no_trade_observations(conn, day)
                outcome = collect_stock_status_day(
                    conn, provider, day.strftime("%Y%m%d"), active_codes=codes,
                    **({"no_trade_observations": no_trade} if no_trade is not None else {}),
                )
            else:
                outcome = collect_stock_status_day(
                    conn, provider, day.strftime("%Y%m%d"), active_codes=codes,
                    st_observations=st_observations,
                )
            attempted.append({"trade_date": day.isoformat(), **outcome})
        except (*FATAL_INGEST_ERRORS, IngestBusy, ProviderNetworkAccessDenied):
            raise
        except Exception as exc:
            conn.rollback()
            attempted.append({"trade_date": day.isoformat(), "status": "FAILED",
                              "error_type": type(exc).__name__})
        if attempted[-1]["status"] in ("FAILED", "UNAVAILABLE"):
            consecutive_unavailable += 1
        else:
            consecutive_unavailable = 0
        if len(attempted) % 10 == 0:
            logger.info("status days %d/%d; written %d", len(attempted), min(len(pending), max_days),
                        sum(item.get("rows", 0) for item in attempted))
        if consecutive_unavailable >= 5:
            logger.warning("five consecutive unavailable status days; stopping this batch")
            break
    remaining = missing_status_days(conn, calendar)
    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "calendar_days": len(calendar), "pending_before": len(pending),
        "attempted": attempted, "pending_after": len(remaining),
        "next_dates": [day.isoformat() for day in remaining[:max_days]],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--max-days", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv(".env")
    provider = TushareProvider()
    if not provider.connected:
        raise RuntimeError("historical status provider unavailable")
    with get_connection() as conn:
        report = backfill_status(conn, provider, start=args.start, end=args.end,
                                 max_days=args.max_days)
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "attempted"}, ensure_ascii=False))
    if report["pending_after"] or any(item["status"] != "SUCCESS" for item in report["attempted"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
