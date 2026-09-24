"""Pure market session, publication and coverage policy (no IO or source calls)."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Mapping, Protocol
from zoneinfo import ZoneInfo


class Resource(str, Enum):
    CN_INDEX_BARS = "CN_INDEX_BARS"
    CN_INDEX_FACTORS = "CN_INDEX_FACTORS"
    US_INDEX_BARS = "US_INDEX_BARS"
    KR_INDEX_BARS = "KR_INDEX_BARS"
    CN_STOCK_DAILY = "CN_STOCK_DAILY"
    CN_SECTOR_DAILY = "CN_SECTOR_DAILY"
    # Private analysis prerequisite. It is intentionally absent from the API
    # resource enum and public scheduler admission list.
    CN_STOCK_QUANT_INPUTS = "CN_STOCK_QUANT_INPUTS"


PUBLIC_RESOURCES = tuple(resource for resource in Resource if resource != Resource.CN_STOCK_QUANT_INPUTS)
INTERNAL_RESOURCES = (Resource.CN_STOCK_QUANT_INPUTS,)
RESOURCES = tuple(resource.value for resource in PUBLIC_RESOURCES)
RESOURCE_MARKETS = {resource: resource.value.split("_", 1)[0] for resource in Resource}
MARKET_TIMEZONES = {"CN": "Asia/Shanghai", "US": "America/New_York", "KR": "Asia/Seoul"}
DEFAULT_PUBLISH_LAG_SECONDS = {
    resource.value: 18000 if RESOURCE_MARKETS[resource] == "CN" else 14400
    for resource in Resource
}
KLINE_FACTOR_COLUMNS = [
    "ma_bfq_5", "ma_bfq_10", "ma_bfq_20", "ma_bfq_60",
    "boll_mid_bfq", "boll_upper_bfq", "boll_lower_bfq",
    "macd_dif_bfq", "macd_dea_bfq", "macd_bfq",
]


@dataclass(frozen=True)
class MarketSession:
    trade_date: date
    open: datetime
    close: datetime
    break_start: datetime | None = None
    break_end: datetime | None = None


@dataclass(frozen=True)
class CalendarSchedule:
    market: str
    supported_from: date
    supported_through: date
    sessions: tuple[MarketSession, ...]
    available: bool = True


class Calendar(Protocol):
    def schedule(self, market: str) -> CalendarSchedule: ...


def iso_utc(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


@dataclass(frozen=True)
class RefreshTarget:
    resource: Resource
    market: str
    market_date: date
    calendar_status: str
    supported_through: date
    expected_trade_date: date | None
    next_ready_at: datetime | None
    window_dates: tuple[date, ...]
    session_status: str
    # Only used to diagnose known older holes, never added to a collection spec.
    history_dates: tuple[date, ...] = ()

    def as_dict(self) -> dict:
        return {
            "resource": self.resource.value, "market": self.market,
            "market_date": self.market_date.isoformat(),
            "calendar_status": self.calendar_status,
            "supported_through": self.supported_through.isoformat(),
            "expected_trade_date": self.expected_trade_date.isoformat() if self.expected_trade_date else None,
            "next_ready_at": iso_utc(self.next_ready_at),
        }


class RefreshPolicy:
    def __init__(self, calendar: Calendar | None = None, *,
                 publish_lag_seconds: Mapping[str, int | float] | None = None,
                 window_sessions: int = 3) -> None:
        if calendar is None:
            from backend.modules.market_data.infrastructure.calendar_adapter import MarketCalendarAdapter
            calendar = MarketCalendarAdapter()
        self.calendar = calendar
        self.publish_lag_seconds = {**DEFAULT_PUBLISH_LAG_SECONDS, **(publish_lag_seconds or {})}
        if any(value < 0 for value in self.publish_lag_seconds.values()):
            raise ValueError("publish_lag_seconds must be nonnegative")
        if window_sessions < 1:
            raise ValueError("window_sessions must be positive")
        self.window_sessions = window_sessions

    def target(self, resource: Resource | str, now: datetime) -> RefreshTarget:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        resource = Resource(resource)
        market = RESOURCE_MARKETS[resource]
        local_date = now.astimezone(ZoneInfo(MARKET_TIMEZONES[market])).date()
        schedule = self.calendar.schedule(market)
        if (not schedule.available or not schedule.sessions
                or not schedule.supported_from <= local_date <= schedule.supported_through):
            return RefreshTarget(resource, market, local_date, "UNAVAILABLE",
                                 schedule.supported_through, None, None, (), "UNKNOWN")
        status = "EXPIRING" if (schedule.supported_through - local_date).days <= 30 else "OK"
        lag = timedelta(seconds=self.publish_lag_seconds[resource.value])
        sessions = schedule.sessions
        ready = tuple(session.close + lag for session in sessions)
        end = bisect_right(ready, now)
        window = sessions[max(0, end - self.window_sessions):end]
        expected = window[-1].trade_date if window else None
        today = next((session for session in sessions if session.trade_date == local_date), None)
        session_status = "CLOSED"
        if today and today.open <= now < today.close:
            session_status = "OPEN"
            if today.break_start and today.break_end and today.break_start <= now < today.break_end:
                session_status = "BREAK"
        return RefreshTarget(
            resource, market, local_date, status, schedule.supported_through, expected,
            ready[end] if end < len(ready) else None,
            tuple(session.trade_date for session in window), session_status,
            tuple(session.trade_date for session in sessions[:max(0, end - self.window_sessions)]),
        )

    def session_status(self, market: str, now: datetime) -> str:
        return self.target(Resource(f"{market}_INDEX_BARS"), now).session_status


def classify_freshness(*, expected_trade_date: date | str | None,
                       latest_observed_date: date | str | None, expected_count: int,
                       available_count: int, exempt_count: int, window_missing_count: int,
                       catalog_available: bool = True) -> str:
    """Daily target and short-window coverage have separate completeness tests."""
    if expected_trade_date is None:
        return "UNKNOWN"
    if not catalog_available or expected_count == 0:
        return "UNAVAILABLE"
    if available_count + exempt_count == expected_count and window_missing_count == 0:
        return "FRESH"
    if available_count + exempt_count:
        return "PARTIAL"
    if latest_observed_date is None:
        return "UNAVAILABLE"
    return "STALE"
