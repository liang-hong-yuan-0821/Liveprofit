"""Read-only, two-sided check for isolated historical stock_st outages.

This is an evidence probe, not an importer. A matching bridge does not prove
the vendor event feed is complete, so its output must not fill negative ST facts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import exchange_calendars as xc
import pandas as pd
from dotenv import load_dotenv

from AI.dataflows.providers.cn.tushare import TushareProvider

_ST_PREFIX = re.compile(r"^(?:S)?\*?ST", re.IGNORECASE)
_FIELDS = "ts_code,name,pub_date,imp_date,st_type"


def _members(provider: TushareProvider, day: date) -> set[str] | None:
    frame = provider.get_historical_st_df(day.isoformat())
    if frame is None or frame["ts_code"].duplicated().any():
        return None
    return set(frame["ts_code"].astype(str))


def _events(provider: TushareProvider, day: date) -> list[dict] | None:
    for attempt in range(3):
        try:
            frame = provider._api_call(provider.api.st, imp_date=day.strftime("%Y%m%d"),
                                       fields=_FIELDS)
            break
        except Exception:  # noqa: BLE001 - proxy raises a plain Exception on intermittent auth denial
            if attempt == 2:
                return None
    if frame is None or len(frame) >= 1000 or not set(_FIELDS.split(",")) <= set(frame):
        return None
    if frame["ts_code"].isna().any() or frame["ts_code"].duplicated().any():
        return None
    if not frame.empty:
        imp = pd.to_datetime(frame["imp_date"].astype(str), errors="coerce")
        pub = pd.to_datetime(frame["pub_date"].astype(str), errors="coerce")
        if (imp.isna().any() or pub.isna().any() or (pub > imp).any()
                or not imp.dt.date.eq(day).all() or frame["name"].isna().any()):
            return None
    return frame.sort_values("ts_code").to_dict("records")


def _apply(members: set[str], events: list[dict]) -> set[str]:
    result = set(members)
    for event in events:
        code = str(event["ts_code"])
        if _ST_PREFIX.match(str(event["name"])):
            result.add(code)
        else:
            result.discard(code)
    return result


def verify(provider: TushareProvider, target: date) -> dict:
    sessions = [session.date() for session in xc.get_calendar(
        "XSHG", start=(target - pd.Timedelta(days=8)).isoformat(),
        end=(target + pd.Timedelta(days=8)).isoformat(),
    ).sessions]
    if target not in sessions:
        return {"day": target.isoformat(), "result": "NOT_TRADING_DAY"}
    index = sessions.index(target)
    if index == 0 or index + 1 == len(sessions):
        return {"day": target.isoformat(), "result": "NO_NEIGHBOR"}
    gap_set = _members(provider, target)
    before_index = next((i for i in range(index - 1, -1, -1)
                         if _members(provider, sessions[i]) is not None), None)
    after_index = next((i for i in range(index + 1, len(sessions))
                        if _members(provider, sessions[i]) is not None), None)
    if before_index is None or after_index is None:
        return {"day": target.isoformat(), "result": "NO_NEIGHBOR"}
    before, after = sessions[before_index], sessions[after_index]
    start_set, end_set = _members(provider, before), _members(provider, after)
    bridge_days = sessions[before_index + 1:after_index + 1]
    events_by_day = {day.isoformat(): _events(provider, day) for day in bridge_days}
    result = {"day": target.isoformat(), "before": before.isoformat(),
              "after": after.isoformat(), "before_count": None if start_set is None else len(start_set),
              "after_count": None if end_set is None else len(end_set),
              "target_source_empty": gap_set is None,
              "bridge_events": events_by_day}
    if start_set is None or end_set is None or any(
        events is None for events in events_by_day.values()
    ):
        result["result"] = "UNAVAILABLE"
        return result
    if gap_set is not None:
        result["result"] = "NOT_A_GAP"
        return result
    bridge = set(start_set)
    candidates = {}
    for day in bridge_days:
        bridge = _apply(bridge, events_by_day[day.isoformat()])
        if day < after:
            candidates[day.isoformat()] = {
                "count": len(bridge),
                "sha256": hashlib.sha256("\n".join(sorted(bridge)).encode("utf-8")).hexdigest(),
            }
    result["candidates"] = candidates
    result["candidate_count"] = candidates[target.isoformat()]["count"]
    result["next_match"] = bridge == end_set
    result["next_added_unexplained"] = sorted(end_set - bridge)
    result["next_removed_unexplained"] = sorted(bridge - end_set)
    result["candidate_sha256"] = candidates[target.isoformat()]["sha256"]
    result["result"] = "MATCHED_CANDIDATE_ONLY" if bridge == end_set else "MISMATCH"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(".env")
    provider = TushareProvider()
    if not provider.connected:
        raise SystemExit("Tushare is unavailable")
    reports = [verify(provider, date.fromisoformat(day)) for day in args.days]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(),
                                       "reports": reports}, ensure_ascii=False, indent=2), encoding="utf-8")
    print([(report["day"], report["result"]) for report in reports])


if __name__ == "__main__":
    main()
