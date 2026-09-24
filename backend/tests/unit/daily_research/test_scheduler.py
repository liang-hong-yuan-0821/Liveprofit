from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.dialects import postgresql

from backend.modules.daily_research.application.scheduler import (
    _create_run,
    _incremental_bucket,
    _needs_quant_news_refresh,
    _quant_refresh_idempotency_key,
    ensure_incremental_news_run,
    ensure_quant_news_refresh_run,
)


def test_incremental_news_buckets_are_fixed_to_shanghai_interval_boundaries():
    shanghai = ZoneInfo("Asia/Shanghai")
    assert _incremental_bucket(
        datetime(2026, 9, 23, 10, 17, tzinfo=shanghai), 1800
    ) == datetime(2026, 9, 23, 10, 0, tzinfo=shanghai)
    assert _incremental_bucket(
        datetime(2026, 9, 23, 10, 47, tzinfo=shanghai), 1800
    ) == datetime(2026, 9, 23, 10, 30, tzinfo=shanghai)


def test_incremental_scheduler_leaves_fixed_news_slots_to_daily_batch():
    def must_not_open_session():
        raise AssertionError("固定整点附近不得准入 interval 批次")

    now = datetime(2026, 9, 23, 0, 57, tzinfo=timezone.utc)  # 08:57 Shanghai
    assert ensure_incremental_news_run(
        must_not_open_session,
        now,
        interval_seconds=1800,
    ) is False


def test_news_batch_persists_its_bounded_retry_deadline():
    class FakeSession:
        def __init__(self):
            self.added = []

        def scalar(self, _statement):
            return None

        def add(self, item):
            self.added.append(item)

        def commit(self):
            pass

        def rollback(self):
            pass

    session = FakeSession()
    scheduled = datetime(2026, 9, 23, 1, 0, tzinfo=timezone.utc)

    assert _create_run(session, scheduled_at=scheduled, slot="news_0900", kind="news")
    task = next(item for item in session.added if item.__class__.__name__ == "AnalysisTask")

    assert task.request_params["daily_research"]["wait_until"] == "2026-09-23T01:30:00+00:00"


def test_quant_news_refresh_requires_an_incomplete_quant_run_with_pending_news():
    base = {
        "daily_research": {
            "kind": "quant",
            "status": "partial",
            "strategies": [{"summary": {"failed_count": 0}}],
            "candidates": [],
            "candidate_total": 0,
            "news_research_dependency": {"complete": False, "pending_news_count": 3},
        },
    }
    assert _needs_quant_news_refresh(base)

    no_backlog = {"daily_research": {**base["daily_research"],
        "news_research_dependency": {"complete": False, "pending_news_count": 0}}}
    assert not _needs_quant_news_refresh(no_backlog)
    complete = {"daily_research": {**base["daily_research"], "status": "completed"}}
    assert not _needs_quant_news_refresh(complete)
    data_not_ready = {"daily_research": {**base["daily_research"],
        "reason": "QUANT_DATA_NOT_READY"}}
    assert not _needs_quant_news_refresh(data_not_ready)
    no_scan = {"daily_research": {**base["daily_research"], "strategies": []}}
    assert not _needs_quant_news_refresh(no_scan)


def test_refresh_task_freezes_original_trade_date_and_news_provenance():
    class FakeSession:
        def __init__(self):
            self.added = []

        def scalar(self, _statement):
            return None

        def add(self, item):
            self.added.append(item)

        def commit(self):
            pass

        def rollback(self):
            pass

    session = FakeSession()
    scheduled = datetime(2026, 9, 24, 1, 15, tzinfo=timezone.utc)
    target_trade_date = date(2026, 9, 23)
    extra = {
        "refresh_parent_quant_task_id": "parent-id",
        "refresh_news_task_id": "news-id",
        "news_cutoff_at": "2026-09-24T01:15:00+00:00",
    }

    assert _create_run(
        session,
        scheduled_at=scheduled,
        slot="quant_news_refresh",
        kind="quant",
        target_trade_date=target_trade_date,
        workflow_extra=extra,
    )
    task = next(item for item in session.added if item.__class__.__name__ == "AnalysisTask")
    workflow = task.request_params["daily_research"]
    assert task.requested_trade_date == target_trade_date
    assert task.effective_trade_date == target_trade_date
    assert workflow["refresh_parent_quant_task_id"] == "parent-id"
    assert workflow["refresh_news_task_id"] == "news-id"
    assert workflow["news_cutoff_at"] == extra["news_cutoff_at"]


def test_quant_refresh_idempotency_uses_the_root_quant_and_completed_news_ids():
    root_id = uuid4()
    news_id = uuid4()
    original_start = datetime(2026, 9, 23, 13, 0, tzinfo=timezone.utc)

    key = _quant_refresh_idempotency_key(root_id, news_id, original_start)

    assert key == _quant_refresh_idempotency_key(root_id, news_id, original_start)
    assert key.startswith(f"daily-research-20260923-quant-refresh-{root_id.hex}-{news_id.hex}")


def test_quant_refresh_admission_includes_next_morning_news_slot():
    quant_task = SimpleNamespace(
        id=uuid4(),
        request_params={"daily_research": {
            "slot": "quant_2100",
            "scheduled_at": "2026-09-23T21:00:00+08:00",
        }},
        effective_trade_date=date(2026, 9, 23),
    )
    quant_report = SimpleNamespace(
        report_json={"daily_research": {
                "kind": "quant",
                "status": "partial",
                "strategies": [{"summary": {"failed_count": 0}}],
                "candidates": [],
                "candidate_total": 0,
                "news_research_dependency": {"complete": False, "pending_news_count": 2},
        }},
        generated_at=datetime(2026, 9, 23, 13, 30, tzinfo=timezone.utc),
    )

    class FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class FakeSession:
        def __init__(self):
            self.statements = []

        def execute(self, statement):
            self.statements.append(statement)
            return FakeResult([(quant_task, quant_report)] if len(self.statements) == 1 else [])

        def rollback(self):
            pass

    class FakeSessionContext(FakeSession):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    session = FakeSessionContext()
    assert ensure_quant_news_refresh_run(
        lambda: session,
        datetime(2026, 9, 24, 1, 20, tzinfo=timezone.utc),
    ) == 0

    later_news_query = session.statements[1].compile(dialect=postgresql.dialect())
    assert any(
        "news_0900" in str(value)
        for value in later_news_query.params.values()
    )
