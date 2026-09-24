"""Verified exchange sessions for refresh; legacy AI CN interface remains intact."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from functools import lru_cache
import logging

from backend.modules.market_data.application.refresh_policy import CalendarSchedule, MarketSession


_LOGGER = logging.getLogger(__name__)
_CALENDARS = {"CN": "XSHG", "US": "XNYS", "KR": "XKRX"}
SUPPORTED_THROUGH = {"CN": date(2026, 12, 31), "US": date(2027, 12, 31), "KR": date(2027, 12, 31)}
SUPPORTED_FROM = date(2025, 1, 1)


@lru_cache(maxsize=3)
def _load_available_schedule(market: str) -> CalendarSchedule:
    """Cache only successful schedules; exceptions must remain retryable."""
    through = SUPPORTED_THROUGH[market]
    import exchange_calendars as xc
    import pandas as pd

    # Never construct the next unsupported year merely to find next_ready_at.
    calendar = xc.get_calendar(_CALENDARS[market], start=SUPPORTED_FROM.isoformat(), end=through.isoformat())
    sessions = []
    for label, row in calendar.schedule.iterrows():
        def timestamp(column: str) -> datetime | None:
            value = row[column]
            return None if pd.isna(value) else value.to_pydatetime()

        sessions.append(MarketSession(label.date(), timestamp("open"), timestamp("close"),
                                      timestamp("break_start"), timestamp("break_end")))
    return CalendarSchedule(market, SUPPORTED_FROM, through, tuple(sessions))


def _load_schedule(market: str) -> CalendarSchedule:
    through = SUPPORTED_THROUGH[market]
    try:
        return _load_available_schedule(market)
    except Exception as exc:
        # Unavailability is a safe, explicit result; never guess weekdays.
        # Keep failures uncached so transient dependency/data issues can recover
        # within a long-lived API process without requiring a restart.
        _LOGGER.warning("Market calendar unavailable market=%s error=%s", market, type(exc).__name__)
        return CalendarSchedule(market, SUPPORTED_FROM, through, (), available=False)


class MarketCalendarAdapter:
    """Process-local schedules, bounded by the dependency POC's verified range."""

    def schedule(self, market: str) -> CalendarSchedule:
        if market not in _CALENDARS:
            raise ValueError(f"Unsupported market: {market}")
        return _load_schedule(market)


class CNCalendarAdapter:
    def schedule(self, market: str) -> CalendarSchedule:
        return MarketCalendarAdapter().schedule(market)

    def is_trading_day(self, day: date) -> bool:
        from AI.dataflows.utils.trading_calendar import is_trading_day

        return is_trading_day(day.strftime("%Y%m%d"), exchange="SSE")

    def last_trading_day(self, day: date) -> date:
        from AI.dataflows.utils.trading_calendar import get_last_trading_day

        result = get_last_trading_day(day.strftime("%Y%m%d"), exchange="SSE")
        return datetime.strptime(result, "%Y%m%d").date()


class FakeCalendar:
    """测试注入：可配置交易日与最近交易日。"""

    def __init__(self, *, trading_day: bool = True, last_day: date | None = None) -> None:
        self._trading_day = trading_day
        self._last_day = last_day

    def is_trading_day(self, day: date) -> bool:
        return self._trading_day

    def last_trading_day(self, day: date) -> date:
        return self._last_day or day

    def schedule(self, market: str) -> CalendarSchedule:
        """Explicit test-only schedule; production never fabricates sessions."""
        day = self._last_day or date(2000, 1, 1)
        return CalendarSchedule(market, date(1900, 1, 1), date(2100, 12, 31), (
            MarketSession(day, datetime.combine(day, time(0), timezone.utc),
                          datetime.combine(day, time(7), timezone.utc)),
        ))
