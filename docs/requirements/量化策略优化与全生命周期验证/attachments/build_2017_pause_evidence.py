"""Rebuild the 2017 paused-listing evidence from exact unexplained no-bar days."""

import json
from datetime import date
from pathlib import Path

import exchange_calendars as xc
from dotenv import load_dotenv

from db.instrument.db import get_connection

# The source date can be later than the pause: research readers gate on published_on.
# Each interval is bounded by the first/last no-bar session and the cited notice.
SOURCES = {
    "000155.SZ": ("https://static.cninfo.com.cn/finalpage/2017-12-09/1204206641.PDF", "2017-12-09", "2016-05-10起暂停上市，2017-12-18恢复上市"),
    "000629.SZ": ("https://static.cninfo.com.cn/finalpage/2017-05-04/1203473701.PDF", "2017-05-04", "2017-05-05起暂停上市，2017年末仍未恢复"),
    "000950.SZ": ("https://static.cninfo.com.cn/finalpage/2017-09-18/1203980485.PDF", "2017-09-18", "2017-05-11起暂停上市，2017-09-18仍在暂停期间"),
    "600806.SH": ("https://www.sse.com.cn/disclosure/announcement/listing/c/c_20180522_4559446.shtml", "2018-05-22", "上交所确认2017-05-23起暂停上市且2018-05-22仍未恢复；追溯公告"),
    "600432.SH": ("https://static.cninfo.com.cn/finalpage/2017-05-20/1203545447.PDF", "2017-05-20", "2017-05-26起暂停上市，2017年末仍未恢复"),
    "600710.SH": ("https://static.cninfo.com.cn/finalpage/2017-07-25/1203730109.PDF", "2017-07-25", "2016-04-20起暂停上市，2017-07-31恢复上市"),
    "300372.SZ": ("https://static.cninfo.com.cn/finalpage/2017-06-16/1203624857.PDF", "2017-06-16", "2016-09-06起暂停上市，2017-07-17进入退市整理期"),
    "000511.SZ": ("https://static.cninfo.com.cn/finalpage/2017-09-07/1203943771.PDF", "2017-09-07", "2017-07-06起暂停上市，2017年末仍未恢复"),
    "600732.SH": ("https://static.cninfo.com.cn/finalpage/2017-08-22/1203836241.PDF", "2017-08-22", "2016-04-08起暂停上市，2017-06-06恢复上市"),
    "000033.SZ": ("https://static.cninfo.com.cn/finalpage/2017-06-30/1203664234.PDF", "2017-06-30", "2015-05-21起暂停上市，2017-05-24进入退市整理期"),
}
RANGES = {
    "000155.SZ": (date(2017, 1, 3), date(2017, 12, 15)),
    "000629.SZ": (date(2017, 5, 5), date(2017, 12, 29)),
    "000950.SZ": (date(2017, 5, 11), date(2017, 12, 29)),
    "600806.SH": (date(2017, 5, 23), date(2017, 12, 29)),
    "600432.SH": (date(2017, 5, 26), date(2017, 12, 29)),
    "600710.SH": (date(2017, 1, 3), date(2017, 7, 28)),
    "300372.SZ": (date(2017, 1, 3), date(2017, 7, 14)),
    "000511.SZ": (date(2017, 7, 6), date(2017, 12, 29)),
    "600732.SH": (date(2017, 1, 3), date(2017, 6, 5)),
    "000033.SZ": (date(2017, 1, 3), date(2017, 5, 23)),
}
COUNTS = dict(zip(SOURCES, (234, 164, 160, 152, 149, 139, 129, 122, 100, 93)))


def main():
    load_dotenv()
    exchange = xc.get_calendar("XSHG", start="2017-01-01", end="2017-12-31")
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
                raise RuntimeError(f"unexpected 2017 gap shape: {code} {len(rows)}")
            for (day,) in rows:
                evidence.append({
                    "ts_code": code, "trade_date": day.isoformat(),
                    "scope": "full_day", "source_url": url,
                    "published_on": published, "evidence_note": note,
                })
    if len(evidence) != 1442:
        raise RuntimeError(f"unexpected total: {len(evidence)}")
    path = Path(__file__).with_name("suspension_evidence_2017_listing_pauses.json")
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(COUNTS, len(evidence), path)


if __name__ == "__main__":
    main()
