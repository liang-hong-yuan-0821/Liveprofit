"""Read-only, calendar-anchored historical market inventory.

Run ``python -m db.instrument.audit_history --start 2010 --end 2026 --output PATH``.
The report distinguishes absent bars from trusted suspensions and never treats
the broad fund directory as a verified ETF universe.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from AI.dataflows.providers.cn.tushare import TushareProvider
from db.instrument.db import get_connection

_STOCK_SQL = """
WITH days(day) AS (SELECT unnest(%s::date[])),
expected AS (
 SELECT i.ts_code, d.day, b.ts_code AS bar_code,
        b.open, b.high, b.low, b.close,
        a.adj_factor, f.ma_qfq_5, f.ma_qfq_20, f.ma_qfq_60,
        f.boll_mid_qfq, f.boll_upper_qfq, f.boll_lower_qfq,
        f.macd_dif_qfq, f.macd_dea_qfq, f.macd_qfq, f.rsi_qfq_6,
        f.atr_qfq, f.ts_code AS factor_code,
        s.ts_code AS status_code, s.is_suspended, s.suspension_scope, s.is_st,
        s.market_board, s.source AS status_source,
        EXISTS (SELECT 1 FROM market.suspension_evidence e
                WHERE e.ts_code=i.ts_code AND e.trade_date=d.day
                  AND e.scope='full_day') AS official_full_day,
        EXISTS (SELECT 1 FROM market.suspension_source_daily v
                WHERE v.ts_code=i.ts_code AND v.trade_date=d.day
                  AND v.scope='full_day' AND v.source='tushare_suspend_d')
                  AS verified_source_full_day
 FROM days d JOIN market.instrument i
   ON i.instrument_type='stock' AND i.list_date<=d.day
  AND (i.delist_date IS NULL OR d.day<i.delist_date)
  AND (i.ts_code NOT LIKE '%%.BJ' OR d.day>=DATE '2021-11-15')
 LEFT JOIN market.instrument_daily b ON b.ts_code=i.ts_code AND b.trade_date=d.day
 LEFT JOIN market.adj_factor a ON a.ts_code=i.ts_code AND a.trade_date=d.day
 LEFT JOIN market.factor_daily f ON f.ts_code=i.ts_code AND f.trade_date=d.day
 LEFT JOIN market.trade_status_effective s ON s.ts_code=i.ts_code AND s.trade_date=d.day
)
SELECT count(*) AS expected,
 count(*) FILTER (WHERE bar_code IS NULL AND
                  (official_full_day OR verified_source_full_day OR (is_suspended IS TRUE
                   AND suspension_scope='full_day'
                   AND status_source IN ('tushare','tushare+baostock')))) AS trusted_suspension,
 count(*) FILTER (WHERE bar_code IS NULL AND (
                  official_full_day OR verified_source_full_day OR (is_suspended IS TRUE
                  AND suspension_scope='full_day'
                  AND status_source IN ('tushare','tushare+baostock'))) IS NOT TRUE) AS unexplained_bar,
 count(*) FILTER (WHERE bar_code IS NOT NULL AND (
                  open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL
                  OR open<=0 OR high<=0 OR low<=0 OR close<=0
                  OR high<GREATEST(open,low,close)
                  OR low>LEAST(open,high,close))) AS invalid_bar,
 count(*) FILTER (WHERE bar_code IS NOT NULL AND (
                  adj_factor IS NULL OR adj_factor<=0
                  OR adj_factor='NaN'::float8 OR adj_factor='Infinity'::float8)) AS adj_gap,
 count(*) FILTER (WHERE bar_code IS NOT NULL AND (
                  ma_qfq_5 IS NULL OR ma_qfq_20 IS NULL OR ma_qfq_60 IS NULL
                  OR boll_mid_qfq IS NULL OR boll_upper_qfq IS NULL
                  OR boll_lower_qfq IS NULL OR macd_dif_qfq IS NULL
                  OR macd_dea_qfq IS NULL OR macd_qfq IS NULL
                  OR rsi_qfq_6 IS NULL
                  OR atr_qfq IS NULL OR atr_qfq<=0)) AS factor_gap_including_warmup,
 count(*) FILTER (WHERE status_code IS NULL
                  OR status_source NOT IN ('tushare','tushare+baostock')
                  OR is_suspended IS NULL OR is_st IS NULL
                  OR (is_suspended IS TRUE AND (suspension_scope IS NULL
                      OR suspension_scope='unknown'))
                  OR market_board IS NULL OR btrim(market_board)='') AS status_gap,
 count(*) FILTER (WHERE bar_code IS NOT NULL AND factor_code IS NULL)
                  AS factor_row_absent,
 count(*) FILTER (WHERE bar_code IS NOT NULL AND factor_code IS NOT NULL
                  AND (atr_qfq IS NULL OR atr_qfq<=0)) AS factor_atr_missing,
 count(*) FILTER (WHERE status_code IS NULL) AS status_row_absent,
 count(*) FILTER (WHERE status_code IS NULL AND bar_code IS NOT NULL)
                  AS status_row_absent_with_bar,
 count(*) FILTER (WHERE status_code IS NULL AND bar_code IS NULL
                  AND (official_full_day OR verified_source_full_day))
                  AS status_row_absent_trusted_pause,
 count(*) FILTER (WHERE status_code IS NULL AND bar_code IS NULL
                  AND NOT (official_full_day OR verified_source_full_day))
                  AS status_row_absent_unexplained_no_bar
FROM expected
"""

_BJ_EXCLUDED_SQL = """
WITH days(day) AS (SELECT unnest(%s::date[]))
SELECT count(*)
FROM days d JOIN market.instrument i ON i.instrument_type='stock'
 AND i.ts_code LIKE '%%.BJ' AND i.list_date<=d.day
 AND (i.delist_date IS NULL OR d.day<i.delist_date)
 AND d.day<DATE '2021-11-15'
"""

_SAMPLE_SQL = """
WITH days(day) AS (SELECT unnest(%s::date[]))
SELECT d.day, i.ts_code
FROM days d JOIN market.instrument i ON i.instrument_type='stock'
 AND i.list_date<=d.day AND (i.delist_date IS NULL OR d.day<i.delist_date)
 AND (i.ts_code NOT LIKE '%%.BJ' OR d.day>=DATE '2021-11-15')
LEFT JOIN market.instrument_daily b ON b.ts_code=i.ts_code AND b.trade_date=d.day
LEFT JOIN market.trade_status_effective s ON s.ts_code=i.ts_code AND s.trade_date=d.day
WHERE b.ts_code IS NULL AND (
 EXISTS (SELECT 1 FROM market.suspension_evidence e
         WHERE e.ts_code=i.ts_code AND e.trade_date=d.day AND e.scope='full_day')
 OR EXISTS (SELECT 1 FROM market.suspension_source_daily v
            WHERE v.ts_code=i.ts_code AND v.trade_date=d.day
              AND v.scope='full_day' AND v.source='tushare_suspend_d')
 OR (s.is_suspended IS TRUE AND s.suspension_scope='full_day'
     AND s.source IN ('tushare','tushare+baostock'))) IS NOT TRUE
ORDER BY d.day,i.ts_code LIMIT %s
"""

_SUSPENSION_BAR_SPIKE_SQL = """
SELECT s.trade_date, count(*)
FROM market.trade_status_effective s
JOIN market.instrument_daily b
  ON b.ts_code=s.ts_code AND b.trade_date=s.trade_date
WHERE s.trade_date=ANY(%s::date[]) AND s.is_suspended
  AND s.source IN ('tushare','tushare+baostock')
GROUP BY s.trade_date
HAVING count(*) > 50
ORDER BY s.trade_date
"""

_SUSPENSION_SCOPE_CONFLICT_SQL = """
SELECT s.trade_date, s.ts_code, s.suspension_scope,
       b.ts_code IS NOT NULL AS has_bar
FROM market.trade_status_effective s
LEFT JOIN market.instrument_daily b
  ON b.ts_code=s.ts_code AND b.trade_date=s.trade_date
WHERE s.trade_date=ANY(%s::date[])
  AND s.source IN ('tushare','tushare+baostock')
  AND s.is_suspended
  AND ((s.suspension_scope='full_day' AND b.ts_code IS NOT NULL)
    OR (s.suspension_scope='intraday' AND b.ts_code IS NULL)
    OR (s.suspension_scope IS NULL AND b.ts_code IS NOT NULL)
    OR (s.suspension_scope='unknown' AND b.ts_code IS NOT NULL))
ORDER BY s.trade_date,s.ts_code
"""

_OFFICIAL_EVIDENCE_CONFLICT_SQL = """
SELECT e.trade_date,e.ts_code,e.scope,b.ts_code IS NOT NULL AS has_bar
FROM market.suspension_evidence e
LEFT JOIN market.instrument_daily b
  ON b.ts_code=e.ts_code AND b.trade_date=e.trade_date
WHERE e.trade_date=ANY(%s::date[])
  AND ((e.scope='full_day' AND b.ts_code IS NOT NULL)
    OR (e.scope='intraday' AND b.ts_code IS NULL))
ORDER BY e.trade_date,e.ts_code
"""

_SOURCE_SUSPENSION_CONFLICT_SQL = """
SELECT v.trade_date,v.ts_code
FROM market.suspension_source_daily v
JOIN market.instrument_daily b
  ON b.ts_code=v.ts_code AND b.trade_date=v.trade_date
WHERE v.trade_date=ANY(%s::date[]) AND v.scope='full_day'
  AND v.source='tushare_suspend_d'
ORDER BY v.trade_date,v.ts_code
"""

_FUND_SQL = """
WITH days(day) AS (SELECT unnest(%s::date[]))
SELECT count(*) AS listed_fund_days,
 count(*) FILTER (WHERE b.ts_code IS NULL) AS daily_gap,
 count(*) FILTER (WHERE b.ts_code IS NOT NULL AND
                  (a.adj_factor IS NULL OR a.adj_factor<=0
                   OR a.adj_factor='NaN'::float8 OR a.adj_factor='Infinity'::float8)) AS adj_gap,
 count(*) FILTER (WHERE b.ts_code IS NOT NULL AND
                  (f.ma_bfq_5 IS NULL OR f.ma_bfq_20 IS NULL OR f.ma_bfq_60 IS NULL
                   OR f.ma_bfq_250 IS NULL OR f.atr_bfq IS NULL OR f.atr_bfq<=0))
                  AS factor_gap_including_warmup
FROM days d JOIN market.instrument i ON i.instrument_type='fund'
 AND i.list_date<=d.day AND (i.delist_date IS NULL OR d.day<i.delist_date)
LEFT JOIN market.instrument_daily b ON b.ts_code=i.ts_code AND b.trade_date=d.day
LEFT JOIN market.adj_factor a ON a.ts_code=i.ts_code AND a.trade_date=d.day
LEFT JOIN market.factor_daily f ON f.ts_code=i.ts_code AND f.trade_date=d.day
"""


def _year_days(provider: TushareProvider, year: int, through: date,
               *, crosscheck_calendar: bool = True) -> list[date]:
    last = min(date(year, 12, 31), through)
    if last < date(year, 1, 1):
        return []
    frame = provider.get_trade_cal(f"{year}0101", last.strftime("%Y%m%d"))
    if frame is None or frame.empty or not {"trade_date", "is_open"} <= set(frame):
        raise RuntimeError(f"calendar unavailable: {year}")
    dates = [pd.date() for pd in frame["trade_date"]]
    if len(dates) != len(set(dates)) or dates != sorted(dates):
        raise RuntimeError(f"calendar duplicate/unsorted: {year}")
    if any(day.year != year or day > through for day in dates):
        raise RuntimeError(f"calendar date out of range: {year}")
    if not frame["is_open"].eq(1).all():
        raise RuntimeError(f"calendar contains closed days: {year}")
    if year < through.year and year >= 1991 and len(dates) < 180:
        raise RuntimeError(f"calendar suspiciously short: {year}")
    if crosscheck_calendar and year >= 1991:
        import exchange_calendars as xc

        exchange = xc.get_calendar("XSHG", start=f"{year}-01-01", end=f"{year}-12-31")
        independent = [session.date() for session in exchange.sessions
                       if session.year == year and session.date() <= through]
        if dates != independent:
            raise RuntimeError(f"Tushare and XSHG calendars disagree: {year}")
    return dates


def run_audit(conn, provider: TushareProvider, *, start: int, end: int,
              through: date, sample_limit: int = 20,
              crosscheck_calendar: bool = True) -> dict:
    """Stream yearly SQL aggregates; source connection is read-only."""
    if not 1990 <= start <= end <= through.year or sample_limit < 0:
        raise ValueError("invalid audit range")
    conn.execute("SET TRANSACTION READ ONLY")
    inventory = conn.execute(
        "SELECT instrument_type,count(*),count(*) FILTER (WHERE list_date IS NULL),"
        " count(*) FILTER (WHERE delist_date IS NOT NULL) FROM market.instrument "
        "WHERE instrument_type IN ('stock','fund') GROUP BY instrument_type"
    ).fetchall()
    catalog = conn.execute("SELECT count(*),min(observed_date),max(observed_date) "
                           "FROM market.etf_catalog_observation").fetchone()
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "through": through.isoformat(),
        "calendar_source": "Tushare trade_cal(exchange=SSE,is_open=1), yearly request",
        "scope": "listed stock and broad fund symbol-days; factor gaps include warmup; fund is not ETF classification",
        "inventory": {
            kind: {"count": count, "unknown_list_date": unknown,
                   "has_delist_date": delisted}
            for kind, count, unknown, delisted in inventory
        },
        "etf_catalog": {"observations": catalog[0],
                        "first": str(catalog[1]) if catalog[1] else None,
                        "last": str(catalog[2]) if catalog[2] else None},
        "years": [],
    }
    columns = ("expected", "trusted_suspension", "unexplained_bar", "invalid_bar",
               "adj_gap", "factor_gap_including_warmup", "status_gap",
               "factor_row_absent", "factor_atr_missing", "status_row_absent",
               "status_row_absent_with_bar", "status_row_absent_trusted_pause",
               "status_row_absent_unexplained_no_bar")
    fund_columns = ("listed_fund_days", "daily_gap", "adj_gap", "factor_gap_including_warmup")
    for year in range(start, end + 1):
        days = _year_days(provider, year, through, crosscheck_calendar=crosscheck_calendar)
        if not days:
            continue
        counts = dict(zip(columns, conn.execute(_STOCK_SQL, (days,)).fetchone()))
        if sum(counts[key] for key in (
            "status_row_absent_with_bar", "status_row_absent_trusted_pause",
            "status_row_absent_unexplained_no_bar",
        )) != counts["status_row_absent"]:
            raise RuntimeError("status row absence partition is inconsistent")
        counts["excluded_bj_before_open"] = conn.execute(
            _BJ_EXCLUDED_SQL, (days,),
        ).fetchone()[0]
        fund_counts = dict(zip(fund_columns, conn.execute(_FUND_SQL, (days,)).fetchone()))
        examples = [
            {"trade_date": str(day), "ts_code": code}
            for day, code in conn.execute(_SAMPLE_SQL, (days, sample_limit)).fetchall()
        ]
        # A handful of intraday pauses legitimately coexist with a daily bar.
        # Hundreds on one date indicate a source/ingest classification failure.
        spikes = [
            {"trade_date": str(day), "suspended_with_bar": count}
            for day, count in conn.execute(_SUSPENSION_BAR_SPIKE_SQL, (days,)).fetchall()
        ]
        scope_conflicts = [
            {"trade_date": str(day), "ts_code": code, "scope": scope,
             "has_bar": has_bar}
            for day, code, scope, has_bar in conn.execute(
                _SUSPENSION_SCOPE_CONFLICT_SQL, (days,),
            ).fetchall()
        ]
        evidence_conflicts = [
            {"trade_date": str(day), "ts_code": code, "scope": scope,
             "has_bar": has_bar}
            for day, code, scope, has_bar in conn.execute(
                _OFFICIAL_EVIDENCE_CONFLICT_SQL, (days,),
            ).fetchall()
        ]
        source_conflicts = [
            {"trade_date": str(day), "ts_code": code}
            for day, code in conn.execute(
                _SOURCE_SUSPENSION_CONFLICT_SQL, (days,),
            ).fetchall()
        ]
        result["years"].append({
            "year": year, "calendar_days": len(days),
            "calendar_sha256": hashlib.sha256(
                "\n".join(day.isoformat() for day in days).encode()
            ).hexdigest(),
            **counts, "unexplained_bar_examples": examples,
            "suspension_bar_spikes": spikes,
            "suspension_scope_conflicts": scope_conflicts,
            "official_evidence_conflicts": evidence_conflicts,
            "source_suspension_conflicts": source_conflicts,
            "broad_fund": fund_counts,
        })
    return result


def compare_audits(current: dict, baseline: dict) -> list[str]:
    """Flag newly degraded coverage on the same frozen calendar and catalog."""
    if current.get("through") != baseline.get("through"):
        raise ValueError("audit comparison requires the same through date")
    old_years = {row["year"]: row for row in baseline["years"]}
    new_years = {row["year"]: row for row in current["years"]}
    findings = []
    for kind in ("stock", "fund"):
        old_inventory = baseline.get("inventory", {}).get(kind)
        new_inventory = current.get("inventory", {}).get(kind)
        if old_inventory is not None and new_inventory is not None:
            for field in ("count", "unknown_list_date", "has_delist_date"):
                if old_inventory[field] != new_inventory[field]:
                    findings.append(f"{kind} catalog {field} changed")
    for year in sorted(old_years.keys() - new_years.keys()):
        findings.append(f"{year}: current year absent")
    stock_metrics = ("unexplained_bar", "invalid_bar", "adj_gap",
                     "factor_gap_including_warmup", "status_gap")
    fund_metrics = ("daily_gap", "adj_gap", "factor_gap_including_warmup")
    for row in current["years"]:
        year = row["year"]
        old = old_years.get(year)
        if old is None:
            findings.append(f"{year}: baseline year absent")
            continue
        if row["calendar_sha256"] != old["calendar_sha256"]:
            findings.append(f"{year}: calendar changed")
        for field in ("expected", "broad_fund"):
            if field == "expected" and row[field] != old[field]:
                findings.append(f"{year}: stock expected population changed")
            elif field == "broad_fund" and row[field]["listed_fund_days"] != old[field]["listed_fund_days"]:
                findings.append(f"{year}: fund expected population changed")
        for metric in stock_metrics:
            if row[metric] > old[metric]:
                findings.append(f"{year}: stock {metric} increased {old[metric]} -> {row[metric]}")
        for metric in fund_metrics:
            new_value = row["broad_fund"][metric]
            old_value = old["broad_fund"][metric]
            if new_value > old_value:
                findings.append(f"{year}: fund {metric} increased {old_value} -> {new_value}")
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=int, default=1990)
    today = datetime.now(timezone.utc).date()
    parser.add_argument("--end", type=int, default=today.year)
    parser.add_argument("--through", type=date.fromisoformat, default=today)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path,
                        help="Compare with a prior report for the same through date; exit nonzero on regression")
    args = parser.parse_args()
    load_dotenv(".env")
    provider = TushareProvider()
    if not provider.connected:
        raise RuntimeError("verified market calendar provider unavailable")
    with get_connection() as conn:
        report = run_audit(conn, provider, start=args.start, end=args.end,
                           through=args.through)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    spikes = [(row["year"], item) for row in report["years"]
              for item in row["suspension_bar_spikes"]]
    for year, item in spikes:
        print("AUDIT_SUSPENSION_BAR_SPIKE", year, item, flush=True)
    if spikes:
        raise SystemExit(1)
    conflicts = [(row["year"], item) for row in report["years"]
                 for item in row["suspension_scope_conflicts"]]
    for year, item in conflicts:
        print("AUDIT_SUSPENSION_SCOPE_CONFLICT", year, item, flush=True)
    if conflicts:
        raise SystemExit(1)
    evidence_conflicts = [(row["year"], item) for row in report["years"]
                          for item in row["official_evidence_conflicts"]]
    for year, item in evidence_conflicts:
        print("AUDIT_OFFICIAL_EVIDENCE_CONFLICT", year, item, flush=True)
    if evidence_conflicts:
        raise SystemExit(1)
    source_conflicts = [(row["year"], item) for row in report["years"]
                        for item in row["source_suspension_conflicts"]]
    for year, item in source_conflicts:
        print("AUDIT_SOURCE_SUSPENSION_CONFLICT", year, item, flush=True)
    if source_conflicts:
        raise SystemExit(1)
    if args.baseline is not None:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        findings = compare_audits(report, baseline)
        for finding in findings:
            print("AUDIT_REGRESSION", finding, flush=True)
        if findings:
            raise SystemExit(1)
    for row in report["years"]:
        print(row["year"], row["expected"], row["unexplained_bar"],
              row["adj_gap"], row["factor_gap_including_warmup"], row["status_gap"],
              flush=True)


if __name__ == "__main__":
    main()
