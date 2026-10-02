"""Import independently verified company/exchange suspension notices."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from db.instrument.db import get_connection
from db.instrument.ingest.guard import IngestGuard, locked_ingestion
from db.instrument.ingest.notifications import market_changed_notifier_from_env

_OFFICIAL_HOSTS = {
    "static.cninfo.com.cn", "dataclouds.cninfo.com.cn",
    "disc.static.szse.cn", "star.sse.com.cn", "static.sse.com.cn",
    "www.bse.cn",
}


def _official_notice_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.query or parsed.fragment:
        return False
    if parsed.hostname in _OFFICIAL_HOSTS:
        return parsed.path.lower().endswith(".pdf")
    return (parsed.hostname == "www.sse.com.cn"
            and (re.fullmatch(
                r"/disclosure/announcement/listing/(?:stock/)?c/c_\d+_\d+\.shtml",
                parsed.path,
            ) is not None or re.fullmatch(
                r"/services/information/delisting/c/\d{6}_\d{8}_\d+\.pdf",
                parsed.path,
            ) is not None)) or (parsed.hostname == "www.szse.cn"
            and re.fullmatch(
                r"/(?:aboutus/trends/news|disclosure/notice)/t\d+_\d+\.html",
                parsed.path,
            ) is not None)


@locked_ingestion("CN_STOCK_DAILY", "CN_STOCK_QUANT_INPUTS")
def import_evidence(conn, path: Path) -> int:
    items = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(items, list) or not items:
        raise ValueError("suspension evidence must be a nonempty list")
    seen = set()
    checked = []
    for item in items:
        code = item["ts_code"]
        day = date.fromisoformat(item["trade_date"])
        published = date.fromisoformat(item["published_on"])
        scope = item["scope"]
        timing = item.get("suspend_timing")
        note = item.get("evidence_note")
        url = item["source_url"]
        if (scope not in ("full_day", "intraday")
                or (scope == "full_day" and timing)
                or (scope == "intraday" and not (timing or note))
                or not _official_notice_url(url)
                or not isinstance(code, str) or len(code) > 16):
            raise ValueError(f"invalid suspension evidence: {code} {day}")
        key = (code, day)
        if key in seen:
            raise ValueError(f"duplicate evidence day: {code} {day}")
        seen.add(key)
        checked.append((code, day, scope, timing, url, note, published))
    for code, day, scope, timing, url, note, published in checked:
        bar = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM market.instrument_daily "
            "WHERE ts_code=%s AND trade_date=%s)",
            (code, day),
        ).fetchone()[0]
        if bool(bar) != (scope == "intraday"):
            raise ValueError(f"announcement scope disagrees with daily bar: {code} {day}")
        conflicts = conn.execute(
            "SELECT DISTINCT scope FROM market.suspension_evidence "
            "WHERE ts_code=%s AND trade_date=%s",
            (code, day),
        ).fetchall()
        if any(row[0] != scope for row in conflicts):
            raise ValueError(f"conflicting existing evidence: {code} {day}")
    for item in checked:
        conn.execute(
            "INSERT INTO market.suspension_evidence "
            "(ts_code,trade_date,scope,suspend_timing,source_url,evidence_note,published_on) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            item,
        )
    conn.commit()
    return len(checked)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    args = parser.parse_args()
    with get_connection() as conn, IngestGuard(
        conn, changed=market_changed_notifier_from_env(),
    ) as guard:
        print("verified", import_evidence(conn, args.file, guard=guard))


if __name__ == "__main__":
    main()
