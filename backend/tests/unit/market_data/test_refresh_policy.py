"""Publication policy tests use local schedules only (no provider/network)."""
from dataclasses import replace
from datetime import date, datetime, timezone
import json
from pathlib import Path

import pytest

from backend.modules.market_data.application.refresh_policy import (
    CalendarSchedule, DEFAULT_PUBLISH_LAG_SECONDS, PUBLIC_RESOURCES, RefreshPolicy, Resource,
    classify_freshness,
)
from backend.modules.market_data.infrastructure.calendar_adapter import MarketCalendarAdapter


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.mark.parametrize("time,expected", [
    ("2026-09-22T16:10:00Z", "2026-09-22"),  # Shanghai midnight
    ("2026-09-23T02:00:00Z", "2026-09-22"),
    ("2026-09-23T11:59:59Z", "2026-09-22"),
    ("2026-09-23T12:00:00Z", "2026-09-23"),
    ("2026-09-20T12:00:00Z", "2026-09-18"),
    ("2026-10-07T12:00:00Z", "2026-09-30"),
])
def test_cn_publication_boundaries(time, expected):
    target = RefreshPolicy().target(Resource.CN_INDEX_BARS, utc(time))
    assert target.expected_trade_date == date.fromisoformat(expected)
    assert len(target.window_dates) == 3
    assert target.window_dates[-1] == target.expected_trade_date


def test_all_six_groups_use_configurable_publication_lag():
    now = utc("2026-09-23T08:00:00Z")
    assert len(PUBLIC_RESOURCES) == 6
    for resource in PUBLIC_RESOURCES:
        target = RefreshPolicy().target(resource, now)
        assert target.resource == resource
        assert DEFAULT_PUBLISH_LAG_SECONDS[resource.value] == (18000 if target.market == "CN" else 14400)
        assert len(target.window_dates) == 3
    fast = RefreshPolicy(publish_lag_seconds={Resource.CN_INDEX_FACTORS.value: 0})
    assert fast.target(Resource.CN_INDEX_FACTORS, now).expected_trade_date == date(2026, 9, 23)
    assert fast.target(Resource.CN_INDEX_BARS, now).expected_trade_date == date(2026, 9, 22)
    with pytest.raises(ValueError):
        RefreshPolicy(publish_lag_seconds={"CN_INDEX_BARS": -1})
    two = RefreshPolicy(window_sessions=2).target(Resource.CN_INDEX_BARS, now)
    assert len(two.window_dates) == 2
    assert two.window_dates[-1] == RefreshPolicy().target(Resource.CN_INDEX_BARS, now).expected_trade_date
    with pytest.raises(ValueError):
        RefreshPolicy(window_sessions=0)
    with pytest.raises(ValueError):
        fast.target("CN_INDEX_BARS", datetime(2026, 9, 23))


def test_internal_quant_resource_uses_cn_publication_calendar():
    result = RefreshPolicy().target(
        Resource.CN_STOCK_QUANT_INPUTS, utc("2026-09-23T12:00:00Z"),
    )
    assert result.market == "CN"
    assert result.expected_trade_date == date(2026, 9, 23)


@pytest.mark.parametrize("time,expected", [
    ("2026-03-07T00:59:59Z", "2026-03-05"),
    ("2026-03-07T01:00:00Z", "2026-03-06"),
    ("2026-03-09T23:59:59Z", "2026-03-06"),
    ("2026-03-10T00:00:00Z", "2026-03-09"),
    ("2026-11-27T21:59:59Z", "2026-11-25"),
    ("2026-11-27T22:00:00Z", "2026-11-27"),
])
def test_new_york_dst_and_early_close(time, expected):
    assert RefreshPolicy().target("US_INDEX_BARS", utc(time)).expected_trade_date.isoformat() == expected


@pytest.mark.parametrize("time,status", [
    ("2026-09-23T01:29:59Z", "CLOSED"),
    ("2026-09-23T01:30:00Z", "OPEN"),
    ("2026-09-23T03:30:00Z", "BREAK"),
    ("2026-09-23T05:00:00Z", "OPEN"),
    ("2026-09-23T07:00:00Z", "CLOSED"),
    ("2026-09-20T02:00:00Z", "CLOSED"),
])
def test_session_open_break_close_are_intraday(time, status):
    assert RefreshPolicy().session_status("CN", utc(time)) == status


def test_korea_has_independent_sessions_and_four_hour_lag():
    policy = RefreshPolicy()
    assert policy.session_status("KR", utc("2026-09-23T03:00:00Z")) == "OPEN"
    assert policy.target("KR_INDEX_BARS", utc("2026-09-23T10:29:59Z")).expected_trade_date == date(2026, 9, 22)
    assert policy.target("KR_INDEX_BARS", utc("2026-09-23T10:30:00Z")).expected_trade_date == date(2026, 9, 23)


def test_supported_boundary_preserves_current_target_without_next_year_guess():
    policy = RefreshPolicy()
    before_warning = policy.target("CN_INDEX_BARS", utc("2026-11-30T12:00:00Z"))
    warning = policy.target("CN_INDEX_BARS", utc("2026-12-01T12:00:00Z"))
    last = policy.target("CN_INDEX_BARS", utc("2026-12-31T12:00:00Z"))
    assert before_warning.calendar_status == "OK"
    assert warning.calendar_status == "EXPIRING"
    assert last.expected_trade_date == date(2026, 12, 31)
    assert last.next_ready_at is None
    unknown = policy.target("CN_INDEX_BARS", utc("2027-01-04T12:00:00Z"))
    assert unknown.calendar_status == "UNAVAILABLE"
    assert unknown.expected_trade_date is None
    assert unknown.window_dates == ()
    assert policy.target("US_INDEX_BARS", utc("2027-01-04T12:00:00Z")).expected_trade_date is not None
    assert policy.target("KR_INDEX_BARS", utc("2027-01-04T12:00:00Z")).expected_trade_date is not None


def test_calendar_failure_has_no_weekday_fallback():
    class MissingCalendar:
        def schedule(self, market):
            return CalendarSchedule(market, date(2025, 1, 1), date(2026, 12, 31), (), False)
    result = RefreshPolicy(MissingCalendar()).target("CN_INDEX_BARS", utc("2026-09-23T12:00:00Z"))
    assert result.expected_trade_date is None
    assert result.session_status == "UNKNOWN"


def test_current_year_schedule_equals_existing_cn_cache():
    cache = Path(__file__).resolve().parents[4] / "AI/dataflows/data/trade_cal_2026.json"
    dates = {session.trade_date.isoformat() for session in MarketCalendarAdapter().schedule("CN").sessions if session.trade_date.year == 2026}
    assert dates == set(json.loads(cache.read_text(encoding="utf-8"))["2026"])
    assert len(dates) == 242


def test_transient_calendar_failure_recovers_without_process_restart(monkeypatch):
    import exchange_calendars as xc
    from backend.modules.market_data.infrastructure import calendar_adapter

    calendar_adapter._load_available_schedule.cache_clear()
    original = xc.get_calendar
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary calendar initialization failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(xc, "get_calendar", fail_once)
    try:
        adapter = MarketCalendarAdapter()
        assert adapter.schedule("KR").available is False
        recovered = adapter.schedule("KR")
        assert recovered.available is True
        assert recovered.sessions
        assert calls == 2
    finally:
        calendar_adapter._load_available_schedule.cache_clear()


@pytest.mark.parametrize("expected,latest,total,available,exempt,missing,catalog,result", [
    (None, "2026-09-22", 0, 0, 0, 0, True, "UNKNOWN"),
    ("2026-09-22", None, 0, 0, 0, 0, False, "UNAVAILABLE"),
    ("2026-09-22", None, 11, 0, 0, 33, True, "UNAVAILABLE"),
    ("2026-09-22", "2026-09-21", 11, 0, 0, 11, True, "STALE"),
    ("2026-09-22", "2026-09-22", 11, 10, 0, 1, True, "PARTIAL"),
    ("2026-09-22", "2026-09-22", 11, 11, 0, 1, True, "PARTIAL"),
    ("2026-09-22", "2026-09-22", 11, 11, 0, 0, True, "FRESH"),
    ("2026-09-22", None, 11, 0, 11, 0, True, "FRESH"),
])
def test_freshness_truth_table(expected, latest, total, available, exempt, missing, catalog, result):
    assert classify_freshness(expected_trade_date=expected, latest_observed_date=latest,
                              expected_count=total, available_count=available, exempt_count=exempt,
                              window_missing_count=missing, catalog_available=catalog) == result
