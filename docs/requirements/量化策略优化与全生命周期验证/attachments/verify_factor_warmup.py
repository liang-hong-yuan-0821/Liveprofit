"""Read-only annual factor-gap warmup check against bars and trusted pauses."""

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import exchange_calendars as xc
from dotenv import load_dotenv

from db.instrument.db import get_connection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2017)
    parser.add_argument("--through", type=date.fromisoformat)
    args = parser.parse_args()
    year = args.year
    if not 2016 <= year <= 2026:
        raise ValueError("unsupported year")
    load_dotenv()
    start = date(year - 1, 1, 1)
    end = args.through or date(year, 12, 31)
    if end.year != year:
        raise ValueError("through date must be in the audit year")
    cal = xc.get_calendar("XSHG", start=start.isoformat(), end=end.isoformat())
    sessions = [stamp.date() for stamp in cal.sessions]
    year_sessions = [day for day in sessions if day.year == year]
    with get_connection() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        gaps = conn.execute("""
            SELECT b.ts_code,b.trade_date,i.list_date,f.ts_code IS NULL AS row_absent,
                   f.ma_qfq_60 IS NULL AS ma60_missing,
                   (f.atr_qfq IS NULL OR f.atr_qfq<=0) AS atr_missing
            FROM market.instrument_daily b
            JOIN market.instrument i ON i.ts_code=b.ts_code AND i.instrument_type='stock'
            LEFT JOIN market.factor_daily f ON f.ts_code=b.ts_code AND f.trade_date=b.trade_date
            WHERE b.trade_date BETWEEN %s AND %s
              AND b.trade_date=ANY(%s::date[])
              AND i.list_date<=b.trade_date
              AND (i.delist_date IS NULL OR b.trade_date<i.delist_date)
              AND (b.ts_code NOT LIKE '%%.BJ' OR b.trade_date>=DATE '2021-11-15')
              AND (f.ma_qfq_5 IS NULL OR f.ma_qfq_20 IS NULL OR f.ma_qfq_60 IS NULL
                   OR f.boll_mid_qfq IS NULL OR f.boll_upper_qfq IS NULL
                   OR f.boll_lower_qfq IS NULL OR f.macd_dif_qfq IS NULL
                   OR f.macd_dea_qfq IS NULL OR f.macd_qfq IS NULL
                   OR f.rsi_qfq_6 IS NULL OR f.atr_qfq IS NULL OR f.atr_qfq<=0)
            ORDER BY b.ts_code,b.trade_date
        """, (date(year, 1, 1), end, year_sessions)).fetchall()
        codes = sorted({row[0] for row in gaps})
        effective_listing = {
            code: max(listed, date(2021, 11, 15)) if code.endswith(".BJ") else listed
            for code, _day, listed, *_ in gaps
        }
        bars = conn.execute("""
            SELECT ts_code,trade_date FROM market.instrument_daily
            WHERE ts_code=ANY(%s) AND trade_date<=%s
            ORDER BY ts_code,trade_date
        """, (codes, end)).fetchall()
        bar_days = defaultdict(set)
        bar_rank = {}
        for code, day in bars:
            if day < effective_listing[code]:
                continue
            bar_days[code].add(day)
            bar_rank[code, day] = len(bar_days[code])
        first_bar = {code: min(days) for code, days in bar_days.items()}
        trusted = conn.execute("""
            SELECT ts_code,trade_date FROM market.suspension_source_daily
            WHERE ts_code=ANY(%s) AND trade_date BETWEEN %s AND %s
              AND scope='full_day' AND source='tushare_suspend_d'
            UNION SELECT ts_code,trade_date FROM market.suspension_evidence
            WHERE ts_code=ANY(%s) AND trade_date BETWEEN %s AND %s
              AND scope='full_day'
            UNION SELECT ts_code,trade_date FROM market.trade_status_effective
            WHERE ts_code=ANY(%s) AND trade_date BETWEEN %s AND %s
              AND is_suspended AND suspension_scope='full_day'
              AND source IN ('tushare','tushare+baostock')
        """, (codes, start, end, codes, start, end, codes, start, end)).fetchall()
    pause_days = set(trusted)
    first_listing = {}
    max_gap = {}
    for code, day, listed, *_ in gaps:
        first_listing[code] = effective_listing[code]
        max_gap[code] = max(day, max_gap.get(code, day))
    untrusted_sessions = []
    for code in codes:
        for day in sessions:
            if (first_listing[code] <= day <= max_gap[code]
                    and day not in bar_days[code] and (code, day) not in pause_days):
                untrusted_sessions.append((code, day.isoformat()))
    absent_after20, ma60_after59, atr_after20 = [], [], []
    for code, day, _listed, absent, ma60, atr in gaps:
        rank = bar_rank[code, day]
        record = (code, day.isoformat(), rank)
        if absent and rank > 20:
            absent_after20.append(record)
        if ma60 and rank > 59:
            ma60_after59.append(record)
        if atr and rank > 20:
            atr_after20.append(record)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "year": year, "through": end.isoformat(), "calendar": "exchange_calendars XSHG",
        "calendar_sha256": hashlib.sha256("|".join(map(str, sessions)).encode()).hexdigest(),
        "factor_gap_rows": len(gaps), "factor_gap_codes": len(codes),
        "listed_before_calendar_start": [code for code in codes if first_listing[code] < start],
        "first_bar_not_listing_date": [code for code in codes if first_bar[code] != first_listing[code]],
        "pre_effective_listing_bar_codes": sorted({
            code for code, day in bars if day < effective_listing[code]
        }),
        "max_local_bar_rank": max((bar_rank[code, day] for code, day, *_ in gaps), default=0),
        "untrusted_no_bar_session_count": len(untrusted_sessions),
        "untrusted_no_bar_by_year": {
            str(check_year): sum(int(day[:4]) == check_year for _, day in untrusted_sessions)
            for check_year in (year - 1, year)
        },
        "untrusted_no_bar_codes": sorted({code for code, _ in untrusted_sessions}),
        "untrusted_no_bar_session_examples": untrusted_sessions[:30],
        "factor_absent_after_bar20_count": len(absent_after20),
        "factor_absent_after_bar20_examples": absent_after20[:20],
        "ma60_missing_after_bar59_count": len(ma60_after59),
        "ma60_missing_after_bar59_examples": ma60_after59[:20],
        "atr_missing_after_bar20_count": len(atr_after20),
        "atr_missing_after_bar20_examples": atr_after20[:20],
    }
    path = Path(__file__).with_name(f"factor_warmup_{year}_verification.json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print({k: value for k, value in report.items() if k.endswith(("count", "rows", "codes"))})


if __name__ == "__main__":
    main()
