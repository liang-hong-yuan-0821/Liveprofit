"""方案评审只读 POC，不是业务实现；依赖安装在工作区 var 隔离目录。"""
from __future__ import annotations

import importlib.metadata
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "var/market-refresh-review/deps"))
import exchange_calendars as xc  # noqa: E402


def main() -> None:
    result = {
        "version": xc.__version__,
        "pandas": importlib.metadata.version("pandas"),
        "numpy": importlib.metadata.version("numpy"),
        "calendars": {},
    }
    for name in ("XSHG", "XNYS", "XKRX"):
        report = {}
        for year in (2026, 2027):
            try:
                calendar = xc.get_calendar(name, start=f"{year}-01-01", end=f"{year}-12-31")
                report[str(year)] = {
                    "sessions": len(calendar.sessions),
                    "first": str(calendar.first_session),
                    "last": str(calendar.last_session),
                }
                if year == 2026:
                    for day in ("2026-03-06", "2026-03-09", "2026-09-21", "2026-09-22",
                                "2026-09-23", "2026-10-01", "2026-11-27"):
                        report[day] = (
                            {"open": str(calendar.session_open(day)),
                             "close": str(calendar.session_close(day))}
                            if calendar.is_session(day) else {"closed": True}
                        )
            except Exception as exc:
                report[str(year)] = {"error": type(exc).__name__, "detail": str(exc)}
        result["calendars"][name] = report
    calendar = xc.get_calendar("XSHG", start="2026-01-01", end="2026-12-31")
    actual = {day.strftime("%Y-%m-%d") for day in calendar.sessions}
    cached = set(json.loads((ROOT / "AI/dataflows/data/trade_cal_2026.json").read_text(encoding="utf-8"))["2026"])
    result["cn_cache_comparison"] = {
        "cache_count": len(cached), "only_library": sorted(actual - cached),
        "only_cache": sorted(cached - actual),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
