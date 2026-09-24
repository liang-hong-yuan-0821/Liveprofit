from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from backend.modules.analysis.application.task_lifecycle import (
    _should_retry_task_readiness,
    _task_readiness_deadline,
)


def _task(kind: str, wait_until: str):
    return SimpleNamespace(request_params={"daily_research": {"kind": kind, "wait_until": wait_until}})


def test_news_lock_contention_retries_only_until_its_bounded_window():
    task = _task("news", "2026-09-23T21:30:00+08:00")
    before_deadline = datetime(2026, 9, 23, 13, 29, tzinfo=timezone.utc)
    after_deadline = datetime(2026, 9, 23, 13, 31, tzinfo=timezone.utc)

    assert _task_readiness_deadline(task, "DAILY_NEWS_NOT_READY") == datetime(
        2026, 9, 23, 21, 30, tzinfo=ZoneInfo("Asia/Shanghai")
    )
    assert _should_retry_task_readiness(task, before_deadline, "DAILY_NEWS_NOT_READY") is True
    assert _should_retry_task_readiness(task, before_deadline, "PROVIDER_UNAVAILABLE") is False
    assert _should_retry_task_readiness(task, after_deadline, "DAILY_NEWS_NOT_READY") is False


def test_quant_waits_for_market_or_same_day_news_readiness_until_deadline():
    task = _task("quant", "2026-09-23T22:00:00+08:00")
    now = datetime(2026, 9, 23, 13, 59, tzinfo=timezone.utc)

    assert _should_retry_task_readiness(task, now, "QUANT_DATA_NOT_READY") is True
    assert _should_retry_task_readiness(task, now, "DAILY_NEWS_NOT_READY") is True
    assert _should_retry_task_readiness(task, now, "PROVIDER_UNAVAILABLE") is False


def test_quant_preflight_errors_bypass_attempt_limit_only_until_deadline():
    task = _task("quant", "2026-09-23T22:00:00+08:00")
    before_deadline = datetime(2026, 9, 23, 13, 59, tzinfo=timezone.utc)
    after_deadline = datetime(2026, 9, 23, 14, 1, tzinfo=timezone.utc)

    for error_code in (
        "QUANT_INPUTS_NOT_READY",
        "QUANT_INPUT_TARGET_MISMATCH",
        "QUANT_UNIVERSE_CHANGED",
    ):
        assert _task_readiness_deadline(task, error_code) is not None
        assert _should_retry_task_readiness(task, before_deadline, error_code) is True
        assert _should_retry_task_readiness(task, after_deadline, error_code) is False
