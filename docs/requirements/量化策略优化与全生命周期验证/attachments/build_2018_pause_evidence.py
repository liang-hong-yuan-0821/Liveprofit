"""Rebuild 2018 paused-listing evidence from exact unexplained no-bar days."""

import json
from datetime import date
from pathlib import Path

import exchange_calendars as xc
from dotenv import load_dotenv

from db.instrument.db import get_connection

# Later notices remain valid retrospective evidence; readers gate on published_on.
SOURCES = {
    "000950.SZ": ("https://static.cninfo.com.cn/finalpage/2018-08-28/1205338902.PDF", "2018-08-28", "2018-08-28恢复上市，此前处于暂停上市期"),
    "000629.SZ": ("https://static.cninfo.com.cn/finalpage/2018-08-16/1205290543.PDF", "2018-08-16", "2018-08-24恢复上市，此前处于暂停上市期"),
    "002070.SZ": ("https://static.cninfo.com.cn/finalpage/2018-12-08/1205657727.PDF", "2018-12-08", "2018-05-15起暂停上市，年末仍未恢复"),
    "600401.SH": ("https://static.cninfo.com.cn/finalpage/2018-12-27/1205693024.PDF", "2018-12-27", "2018-05-29起暂停上市，年末仍未恢复"),
    "600680.SH": ("https://static.cninfo.com.cn/finalpage/2018-05-23/1204983113.PDF", "2018-05-23", "2018-05-29起暂停上市，年末仍未恢复"),
    "000693.SZ": ("https://static.cninfo.com.cn/finalpage/2019-05-18/1206283352.PDF", "2019-05-18", "2018-07-13起暂停上市；追溯公告"),
    "000511.SZ": ("https://static.cninfo.com.cn/finalpage/2018-07-18/1205184453.PDF", "2018-07-18", "2017-07-06起暂停上市，2018-06-05进入退市整理期；追溯公告"),
    "600806.SH": ("https://www.sse.com.cn/disclosure/announcement/listing/c/c_20180522_4559446.shtml", "2018-05-22", "2017-05-23起暂停上市，2018-05-30进入退市整理期"),
    "600432.SH": ("https://static.cninfo.com.cn/finalpage/2018-05-23/1204983114.PDF", "2018-05-23", "2017-05-26起暂停上市，2018-05-30进入退市整理期"),
}
RANGES = {
    "000950.SZ": (date(2018, 1, 2), date(2018, 8, 27)),
    "000629.SZ": (date(2018, 1, 2), date(2018, 8, 23)),
    "002070.SZ": (date(2018, 5, 15), date(2018, 12, 28)),
    "600401.SH": (date(2018, 5, 29), date(2018, 12, 28)),
    "600680.SH": (date(2018, 5, 29), date(2018, 12, 28)),
    "000693.SZ": (date(2018, 7, 13), date(2018, 12, 28)),
    "000511.SZ": (date(2018, 1, 2), date(2018, 6, 4)),
    "600806.SH": (date(2018, 1, 2), date(2018, 5, 29)),
    "600432.SH": (date(2018, 1, 2), date(2018, 5, 29)),
}
COUNTS = dict(zip(SOURCES, (160, 158, 157, 147, 147, 115, 101, 97, 97)))


def main():
    load_dotenv()
    exchange = xc.get_calendar("XSHG", start="2018-01-01", end="2018-12-31")
    days = [session.date() for session in exchange.sessions]
    evidence = []
    with get_connection() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        for code, (url, published, note) in SOURCES.items():
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
                (days, code, *RANGES[code]),
            ).fetchall()
            if len(rows) != COUNTS[code] or rows[0][0] != RANGES[code][0] or rows[-1][0] != RANGES[code][1]:
                raise RuntimeError(f"unexpected 2018 gap shape: {code} {len(rows)}")
            for (day,) in rows:
                evidence.append({"ts_code": code, "trade_date": day.isoformat(),
                                 "scope": "full_day", "source_url": url,
                                 "published_on": published, "evidence_note": note})
    if len(evidence) != 1179:
        raise RuntimeError(f"unexpected total: {len(evidence)}")
    path = Path(__file__).with_name("suspension_evidence_2018_listing_pauses.json")
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(COUNTS, len(evidence), path)


if __name__ == "__main__":
    main()
