"""One-off, reproducible 2016 listing-pause evidence manifest builder."""

import json
from datetime import date
from pathlib import Path

import exchange_calendars as xc
from dotenv import load_dotenv

from db.instrument.db import get_connection

SOURCES = {
    "000033.SZ": ("https://static.cninfo.com.cn/finalpage/2016-12-06/1202859526.PDF", "2016-12-06", "2015-05-21起暂停上市，2016-12-06仍在暂停上市及恢复申请审核期间"),
    "600732.SH": ("https://static.cninfo.com.cn/finalpage/2017-04-25/1203379202.PDF", "2017-04-25", "2016-04-08起暂停上市，2017年一季度仍在恢复上市工作期间；追溯公告"),
    "600710.SH": ("https://static.cninfo.com.cn/finalpage/2017-07-25/1203730109.PDF", "2017-07-25", "2016-04-20起暂停上市，2017-07-31才恢复上市；追溯公告"),
    "000155.SZ": ("https://static.cninfo.com.cn/finalpage/2017-02-07/1203065804.PDF", "2017-02-07", "2016-05-10起暂停上市，2017-02-07仍在暂停上市期间；追溯公告"),
    "300372.SZ": ("https://static.cninfo.com.cn/finalpage/2017-06-16/1203624857.PDF", "2017-06-16", "2016-09-06起暂停上市，2017-06-16仍在暂停上市期间；追溯公告"),
    "600656.SH": ("https://static.cninfo.com.cn/finalpage/2016-01-09/1201902930.PDF", "2016-01-09", "2015-05-28起暂停上市，2016-03-29才进入退市整理交易"),
}
RANGES = {
    "000033.SZ": (date(2016, 1, 4), date(2016, 12, 30)),
    "600732.SH": (date(2016, 4, 8), date(2016, 12, 30)),
    "600710.SH": (date(2016, 4, 20), date(2016, 12, 30)),
    "000155.SZ": (date(2016, 5, 10), date(2016, 12, 30)),
    "300372.SZ": (date(2016, 9, 6), date(2016, 12, 30)),
    "600656.SH": (date(2016, 1, 4), date(2016, 3, 28)),
}


def main():
    load_dotenv()
    exchange = xc.get_calendar("XSHG", start="2016-01-01", end="2016-12-31")
    days = [s.date() for s in exchange.sessions if s.year == 2016]
    evidence = []
    with get_connection() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        for code, (url, published, note) in SOURCES.items():
            rows = conn.execute(
                "SELECT d.day FROM unnest(%s::date[]) AS d(day) "
                "JOIN market.instrument i ON i.ts_code=%s AND i.instrument_type='stock' "
                "AND i.list_date<=d.day AND (i.delist_date IS NULL OR d.day<i.delist_date) "
                "LEFT JOIN market.instrument_daily b ON b.ts_code=i.ts_code AND b.trade_date=d.day "
                "WHERE b.ts_code IS NULL AND d.day BETWEEN %s AND %s "
                "ORDER BY d.day",
                (days, code, *RANGES[code]),
            ).fetchall()
            for (day,) in rows:
                if not RANGES[code][0] <= day <= RANGES[code][1]:
                    raise RuntimeError(f"gap outside published listing pause: {code} {day}")
                evidence.append({
                    "ts_code": code,
                    "trade_date": day.isoformat(),
                    "scope": "full_day",
                    "source_url": url,
                    "published_on": published,
                    "evidence_note": note,
                })
    counts = {code: sum(row["ts_code"] == code for row in evidence) for code in SOURCES}
    if counts != {"000033.SZ": 244, "600732.SH": 181, "600710.SH": 173,
                  "000155.SZ": 160, "300372.SZ": 77, "600656.SH": 56}:
        raise RuntimeError(f"unexpected 2016 gap shape: {counts}")
    path = Path(__file__).with_name("suspension_evidence_2016_listing_pauses.json")
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(counts, len(evidence), path)


if __name__ == "__main__":
    main()
