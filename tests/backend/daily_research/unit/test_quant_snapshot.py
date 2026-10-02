# test-catalog-begin
# {
#   "purpose": "每日研究 / quant_snapshot",
#   "keywords": [
#     "每日研究",
#     "接口",
#     "连接",
#     "每日",
#     "市场分析",
#     "新闻",
#     "行情刷新",
#     "quant_snapshot",
#     "api",
#     "connection",
#     "daily",
#     "market",
#     "news",
#     "refresh"
#   ],
#   "covers": [
#     "AI/dataflows/utils/trading_calendar.py",
#     "AI/eventStudy/review/news_dao.py",
#     "backend/modules/analysis/application/errors.py",
#     "backend/modules/analysis/domain/enums.py",
#     "backend/modules/daily_research/application/news_pipeline.py",
#     "backend/modules/daily_research/application/quant_pipeline.py",
#     "db/instrument/db.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

import uuid
from datetime import date, datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from backend.modules.analysis.domain.enums import TaskStatus
from backend.modules.daily_research.application.quant_pipeline import (
    _complete_quant,
    _event_snapshot_times,
    _is_sha256_digest,
    _manual_uses_latest,
    _refresh_market_snapshot_date,
    _reweight_candidate_rows,
    _stock_universe_digest,
    _uses_latest_complete_market_data,
    _workflow_instant,
    run_daily_quant,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_scheduled_quant_uses_completed_assessment_time_but_preserves_news_cutoff():
    workflow = {
        "kind": "quant",
        "trigger": "scheduled",
        "slot": "quant_2100",
        "scheduled_at": "2026-09-23T21:00:00+08:00",
    }
    dependency = {"report_as_of": "2026-09-23T21:17:00+08:00", "complete": True}

    assessment_as_of, news_cutoff_at = _event_snapshot_times(
        workflow=workflow, news_dependency=dependency,
    )

    assert assessment_as_of == datetime(2026, 9, 23, 13, 17, tzinfo=timezone.utc)
    assert news_cutoff_at == datetime(2026, 9, 23, 13, 0, tzinfo=timezone.utc)


def test_scheduled_news_refresh_uses_completed_assessment_time_and_its_cutoff():
    workflow = {
        "kind": "quant",
        "trigger": "scheduled",
        "slot": "quant_news_refresh",
        "scheduled_at": "2026-09-24T01:15:00+08:00",
        "news_cutoff_at": "2026-09-24T01:15:00+08:00",
    }

    assessment_as_of, news_cutoff_at = _event_snapshot_times(
        workflow=workflow,
        news_dependency={"report_as_of": "2026-09-24T01:18:00+08:00", "complete": True},
    )

    assert assessment_as_of == datetime(2026, 9, 23, 17, 18, tzinfo=timezone.utc)
    assert news_cutoff_at == datetime(2026, 9, 23, 17, 15, tzinfo=timezone.utc)


def test_manual_and_partial_news_runs_keep_only_their_known_input_cutoff():
    workflow = {
        "kind": "quant",
        "trigger": "manual",
        "slot": "manual_quant",
        "scheduled_at": "2026-09-23T21:10:00+08:00",
    }

    assessment_as_of, news_cutoff_at = _event_snapshot_times(
        workflow=workflow,
        news_dependency={"report_as_of": "2026-09-23T21:17:00+08:00", "complete": False},
    )

    assert assessment_as_of == news_cutoff_at == datetime(2026, 9, 23, 13, 10, tzinfo=timezone.utc)


def test_manual_quant_uses_today_after_data_ready_cut_and_latest_before_or_on_holidays():
    is_trading_day = lambda day: day == "2026-09-23"

    assert _manual_uses_latest(datetime(2026, 9, 23, 15, 29, tzinfo=SHANGHAI), is_trading_day)
    assert not _manual_uses_latest(datetime(2026, 9, 23, 15, 30, tzinfo=SHANGHAI), is_trading_day)
    assert _manual_uses_latest(datetime(2026, 9, 26, 21, 0, tzinfo=SHANGHAI), is_trading_day)


@pytest.mark.parametrize("value", [None, "not-a-date", "2026-09-23T21:00:00"])
def test_quant_workflow_instants_fail_closed_when_missing_malformed_or_naive(value):
    with pytest.raises(ValueError):
        _workflow_instant({"scheduled_at": value}, "scheduled_at")

    valid_scheduled = {"scheduled_at": "2026-09-23T21:00:00+08:00", "wait_until": value}
    with pytest.raises(ValueError):
        _workflow_instant(valid_scheduled, "wait_until")


def test_quant_universe_digest_is_stable_and_sha256_shaped():
    digest = _stock_universe_digest(["000002.SZ", "000001.SZ"])
    assert digest == _stock_universe_digest(["000001.SZ", "000002.SZ"])
    assert _is_sha256_digest(digest)
    assert not _is_sha256_digest("abc")


def test_scheduled_quant_on_market_holiday_reuses_latest_complete_market_data():
    is_trading_day = lambda day: day == "2026-09-25"

    assert _uses_latest_complete_market_data(
        trigger="scheduled", requested_trade_date=date(2026, 9, 26),
        manual_uses_latest=False, is_trading_day_fn=is_trading_day,
    )
    assert not _uses_latest_complete_market_data(
        trigger="scheduled", requested_trade_date=date(2026, 9, 25),
        manual_uses_latest=False, is_trading_day_fn=is_trading_day,
    )


def test_manual_quant_at_1600_reports_latest_flag_from_actual_preflight_target(monkeypatch):
    from AI.dataflows.utils import trading_calendar
    from backend.modules.daily_research.application import news_pipeline, quant_pipeline
    from db.instrument import db as market_db

    is_trading_day = lambda day: day == "2026-09-23"
    codes = ["000001.SZ"]
    bundle, claimed, tasks = _quant_bundle_for_preflight()
    market_day = date(2026, 9, 22)

    class MarketConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def rollback(self):
            return None

        def execute(self, *_args):
            return None

        def close(self):
            return None

    monkeypatch.setattr(news_pipeline, "ensure_event_schema", lambda _session: None)
    monkeypatch.setattr(quant_pipeline, "_scheduled_news_dependency", lambda *_args, **_kwargs: {
        "status": "completed", "complete": True, "active": False,
        "report_as_of": "2026-09-23T16:00:00+08:00",
    })
    monkeypatch.setattr(trading_calendar, "is_trading_day", is_trading_day)
    monkeypatch.setattr(market_db, "get_connection", lambda *_args, **_kwargs: MarketConnection())
    monkeypatch.setattr(
        quant_pipeline.DataReadinessGate, "resolve",
        lambda *_args, **_kwargs: SimpleNamespace(market_as_of_trade_date=market_day),
    )
    monkeypatch.setattr(
        quant_pipeline.AllMarketUniverseBuilder, "list_active_cn_stocks", lambda _conn: codes,
    )
    monkeypatch.setattr(quant_pipeline, "_published_strategies", lambda _session: [])

    report = run_daily_quant(
        bundle, claimed=claimed,
        workflow={
            "kind": "quant", "trigger": "manual", "slot": "manual_quant",
            "scheduled_at": "2026-09-23T16:00:00+08:00",
            "wait_until": "2099-09-23T22:00:00+08:00",
        },
        execution_control=object(), market_dsn=None, on_progress=lambda *_args: None,
        quant_preflight=lambda **_kwargs: {
            "state": "ready", "effective_trade_date": market_day.isoformat(),
            "universe_digest": _stock_universe_digest(codes),
        },
    )

    assert report["status"] == "empty"
    assert report["requested_trade_date"] == "2026-09-23"
    assert report["target_trade_date"] == "2026-09-22"
    assert report["market_as_of_trade_date"] == "2026-09-22"
    assert report["used_latest_complete_market_data"] is True
    assert tasks.artifact.report_json["daily_research"] == report


def test_news_refresh_reweights_saved_strategy_scan_without_changing_quant_scores():
    base = [
        {"ticker": "000001.SZ", "quant_score": 62.0, "event_score": 0.0, "strategies": []},
        {"ticker": "000002.SZ", "quant_score": 55.0, "event_score": 0.0, "strategies": []},
    ]

    candidates, total = _reweight_candidate_rows(
        base,
        event_adjustments={
            "000001.SZ": [{"score": -0.8, "direction": "bearish"}],
            "000002.SZ": [{"score": 0.8, "direction": "bullish"}],
        },
    )

    assert total == 2
    assert [row["ticker"] for row in candidates] == ["000002.SZ", "000001.SZ"]
    assert candidates[0]["quant_score"] == 55.0
    assert candidates[0]["event_score"] == 16.0
    assert candidates[1]["quant_score"] == 62.0
    assert candidates[1]["event_score"] == -16.0


def test_news_refresh_falls_back_to_parent_market_date_without_losing_candidate_day():
    parent_day = date(2026, 9, 23)

    market_day, fallback = _refresh_market_snapshot_date(
        requested_trade_date=parent_day,
        readiness_trade_date=None,
        parent_market_date=parent_day,
    )
    assert market_day == parent_day
    assert fallback is True

    market_day, fallback = _refresh_market_snapshot_date(
        requested_trade_date=parent_day,
        readiness_trade_date=parent_day,
        parent_market_date=parent_day,
    )
    assert market_day == parent_day
    assert fallback is False

    market_day, fallback = _refresh_market_snapshot_date(
        requested_trade_date=parent_day,
        readiness_trade_date=date(2026, 9, 24),
        parent_market_date=parent_day,
    )
    assert market_day == parent_day
    assert fallback is True


def test_quant_completion_persists_report_under_daily_research_api_key():
    task = SimpleNamespace(
        status=TaskStatus.RUNNING.value,
        attempt_no=1,
        lease_token="lease-token",
        effective_trade_date=None,
        requested_trade_date=date(2026, 9, 23),
        date_correction=None,
    )

    class FakeSession:
        def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: task)

    class FakeTasks:
        artifact = None

        def complete_task(self, _task_id, _attempt_no, _lease_token, artifact, _reports):
            self.artifact = artifact

    tasks = FakeTasks()
    bundle = SimpleNamespace(
        uow=SimpleNamespace(session=FakeSession()),
        tasks=tasks,
        reports=object(),
    )
    claimed = SimpleNamespace(task_id=uuid.uuid4(), attempt_no=1, lease_token="lease-token")
    report = {
        "kind": "quant",
        "status": "completed",
        "target_trade_date": "2026-09-23",
        "strategy_count": 1,
        "candidate_total": 0,
        "candidates": [],
        "risk_gate": "normal",
    }

    _complete_quant(bundle, claimed, report, None)

    assert tasks.artifact.report_json["daily_research"] == report
    assert "daily_quant" not in tasks.artifact.report_json


def test_news_refresh_keeps_parent_candidates_when_readiness_probe_fails(monkeypatch):
    from AI.dataflows.utils import trading_calendar
    from AI.eventStudy.review import news_dao
    from backend.modules.daily_research.application import news_pipeline, quant_pipeline
    from db.instrument import db as market_db

    parent_id = uuid.uuid4()
    news_id = uuid.uuid4()
    ticker = "000001.SZ"
    parent_block = {
        "kind": "quant",
        "status": "partial",
        "requested_trade_date": "2026-09-23",
        "target_trade_date": "2026-09-23",
        "market_as_of_trade_date": "2026-09-23",
        "strategy_count": 1,
        "strategies": [{"name": "base strategy", "summary": {"failed_count": 0}}],
        "candidate_total": 1,
        "candidate_coverage": {"complete": True},
        "candidates": [{
            "ticker": ticker,
            "quant_score": 55.0,
            "event_score": 0.0,
            "total_score": 55.0,
            "strategies": [{"strategy_name": "base strategy"}],
        }],
    }
    parent_report = SimpleNamespace(report_json={"daily_research": parent_block})
    news_report = SimpleNamespace(report_json={"daily_research": {
        "status": "completed",
        "report_as_of": "2026-09-24T01:10:00+00:00",
        "market_outlook": {"risk_gate": "normal"},
        "source_coverage": {"complete": True},
    }})
    news_task = SimpleNamespace(id=news_id, status=TaskStatus.SUCCEEDED.value)
    current_task = SimpleNamespace(
        status=TaskStatus.RUNNING.value,
        attempt_no=1,
        lease_token="lease-token",
        effective_trade_date=date(2026, 9, 23),
        requested_trade_date=date(2026, 9, 23),
        date_correction=None,
    )

    class FakeSession:
        def __init__(self):
            self.scalar_results = iter([parent_report, news_report])

        def scalar(self, _statement):
            return next(self.scalar_results)

        def get(self, _model, task_id):
            assert task_id == news_id
            return news_task

        def scalars(self, _statement):
            return []

        def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: current_task)

        def rollback(self):
            pass

    class FakeMarketConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def rollback(self):
            pass

        def execute(self, *_args):
            pass

        def close(self):
            pass

    class FakeTasks:
        artifact = None

        def complete_task(self, _task_id, _attempt_no, _lease_token, artifact, _reports):
            self.artifact = artifact

    session = FakeSession()
    tasks = FakeTasks()
    bundle = SimpleNamespace(
        uow=SimpleNamespace(session=session),
        tasks=tasks,
        reports=object(),
    )

    monkeypatch.setattr(news_pipeline, "ensure_event_schema", lambda _session: None)
    monkeypatch.setattr(news_pipeline, "dbapi_connection", lambda _session: object())
    monkeypatch.setattr(news_dao, "count_unprocessed_news", lambda _conn, *, as_of: 0)
    monkeypatch.setattr(news_dao, "list_event_candidates", lambda *_args, **_kwargs: [
        {"assessment_id": str(uuid.uuid4()), "total_count": 1},
    ])
    monkeypatch.setattr(news_dao, "count_unassessed_legacy_events", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        quant_pipeline.DataReadinessGate,
        "resolve",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(quant_pipeline.DataReadinessError("temporary")),
    )
    monkeypatch.setattr(market_db, "get_connection", lambda *_args, **_kwargs: FakeMarketConnection())
    monkeypatch.setattr(
        quant_pipeline.AllMarketUniverseBuilder,
        "list_active_cn_stocks",
        lambda _conn: [ticker],
    )
    monkeypatch.setattr(quant_pipeline, "_membership_maps", lambda _conn: ({}, {}))
    monkeypatch.setattr(
        quant_pipeline,
        "_event_adjustments",
        lambda *_args, **_kwargs: {ticker: [{"score": 0.5, "direction": "bullish"}]},
    )
    monkeypatch.setattr(trading_calendar, "is_trading_day", lambda _day: True)
    monkeypatch.setattr(
        quant_pipeline,
        "QuantExecutionService",
        lambda **_kwargs: pytest.fail("新闻刷新不得重跑策略扫描"),
    )

    report = run_daily_quant(
        bundle,
        claimed=SimpleNamespace(
            task_id=uuid.uuid4(), attempt_no=1, lease_token="lease-token",
            effective_trade_date=date(2026, 9, 23),
        ),
        workflow={
            "kind": "quant",
            "trigger": "scheduled",
            "slot": "quant_news_refresh",
            "scheduled_at": "2026-09-24T09:10:00+08:00",
            "news_cutoff_at": "2026-09-24T09:10:00+08:00",
            "wait_until": "2026-09-24T10:10:00+08:00",
            "refresh_parent_quant_task_id": str(parent_id),
            "refresh_news_task_id": str(news_id),
        },
        execution_control=object(),
        market_dsn=None,
        on_progress=lambda *_args: None,
    )

    assert report["status"] == "partial"
    assert report["target_trade_date"] == "2026-09-23"
    assert report["candidate_count"] == 1
    assert report["candidates"][0]["ticker"] == ticker
    assert report["candidates"][0]["quant_score"] == 55.0
    assert report["candidates"][0]["event_score"] == 10.0
    assert report["strategy_scan_reused"] is True
    assert tasks.artifact.report_json["daily_research"] == report


def _quant_bundle_for_preflight():
    task = SimpleNamespace(
        status=TaskStatus.RUNNING.value, attempt_no=1, lease_token="lease-token",
        effective_trade_date=None, requested_trade_date=date(2026, 9, 23),
        date_correction=None,
    )

    class Session:
        def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: task)

        def scalar(self, _statement):
            return None

        def rollback(self):
            return None

    class Tasks:
        artifact = None

        def complete_task(self, *_args):
            self.artifact = _args[-2]

    tasks = Tasks()
    bundle = SimpleNamespace(uow=SimpleNamespace(session=Session()), tasks=tasks, reports=object())
    claimed = SimpleNamespace(task_id=uuid.uuid4(), attempt_no=1, lease_token="lease-token",
                              effective_trade_date=date(2026, 9, 23))
    return bundle, claimed, tasks


def test_quant_preflight_pending_after_deadline_completes_without_market_connection(monkeypatch):
    from AI.dataflows.utils import trading_calendar
    from backend.modules.daily_research.application import news_pipeline, quant_pipeline
    from db.instrument import db as market_db

    bundle, claimed, tasks = _quant_bundle_for_preflight()
    monkeypatch.setattr(news_pipeline, "ensure_event_schema", lambda _session: None)
    monkeypatch.setattr(trading_calendar, "is_trading_day", lambda _day: True)
    news_dependency = {
        "status": "completed", "complete": True, "active": False,
        "task_id": "news-task", "report_as_of": "2026-09-23T21:17:00+08:00",
        "market_outlook": {"risk_gate": "normal", "summary": "新闻结论"},
    }
    monkeypatch.setattr(quant_pipeline, "_scheduled_news_dependency",
                        lambda *_args, **_kwargs: news_dependency)
    monkeypatch.setattr(market_db, "get_connection",
                        lambda *_args, **_kwargs: pytest.fail("pending preflight must not open market DB"))
    calls = []

    def preflight(**kwargs):
        calls.append(kwargs)
        return {"state": "pending", "effective_trade_date": "2026-09-23", "job_id": "refresh-1"}

    report = run_daily_quant(
        bundle, claimed=claimed,
        workflow={"kind": "quant", "trigger": "scheduled", "slot": "quant_2100",
                  "scheduled_at": "2026-09-23T21:00:00+08:00",
                  "wait_until": "2026-09-23T22:00:00+08:00"},
        execution_control=object(), market_dsn=None, on_progress=lambda *_args: None,
        quant_preflight=preflight,
    )

    assert calls == [{
        "at": datetime(2026, 9, 23, 13, 0, tzinfo=timezone.utc),
        "mode": "auto", "trigger": "SCHEDULED_QUANT",
    }]
    assert report["status"] == "partial"
    assert report["reason"] == "QUANT_INPUTS_NOT_READY"
    assert report["strategies"] == report["candidates"] == []
    assert report["risk_gate"] == "block"
    assert report["news_research_dependency"] == news_dependency
    assert report["market_outlook"] == news_dependency["market_outlook"]
    assert report["event_as_of"] == "2026-09-23T13:17:00+00:00"
    assert tasks.artifact.report_json["daily_research"] == report


def test_quant_preflight_pending_before_deadline_retries_without_market_connection(monkeypatch):
    from AI.dataflows.utils import trading_calendar
    from backend.modules.analysis.application.errors import RetryableAnalysisError
    from backend.modules.daily_research.application import news_pipeline
    from db.instrument import db as market_db

    bundle, claimed, _tasks = _quant_bundle_for_preflight()
    monkeypatch.setattr(news_pipeline, "ensure_event_schema", lambda _session: None)
    monkeypatch.setattr(trading_calendar, "is_trading_day", lambda _day: True)
    monkeypatch.setattr(market_db, "get_connection",
                        lambda *_args, **_kwargs: pytest.fail("pending preflight must not open market DB"))

    with pytest.raises(RetryableAnalysisError) as caught:
        run_daily_quant(
            bundle, claimed=claimed,
            workflow={"kind": "quant", "trigger": "scheduled", "slot": "quant_2100",
                      "scheduled_at": "2026-09-23T21:00:00+08:00",
                      "wait_until": "2099-09-23T22:00:00+08:00"},
            execution_control=object(), market_dsn=None, on_progress=lambda *_args: None,
            quant_preflight=lambda **_kwargs: {"state": "pending", "job_id": "refresh-1"},
        )

    assert caught.value.code == "QUANT_INPUTS_NOT_READY"


@pytest.mark.parametrize("scheduled_at", [None, "not-a-date", "2026-09-23T21:00:00"])
def test_quant_invalid_scheduled_at_retries_without_using_fallback_dates(monkeypatch, scheduled_at):
    from backend.modules.analysis.application.errors import RetryableAnalysisError
    from backend.modules.daily_research.application import news_pipeline
    from db.instrument import db as market_db

    bundle, claimed, _tasks = _quant_bundle_for_preflight()
    monkeypatch.setattr(news_pipeline, "ensure_event_schema", lambda _session: None)
    monkeypatch.setattr(market_db, "get_connection",
                        lambda *_args, **_kwargs: pytest.fail("invalid schedule must not open market DB"))

    with pytest.raises(RetryableAnalysisError) as caught:
        run_daily_quant(
            bundle, claimed=claimed,
            workflow={"kind": "quant", "trigger": "scheduled", "slot": "quant_2100",
                      "scheduled_at": scheduled_at,
                      "wait_until": "2099-09-23T22:00:00+08:00"},
            execution_control=object(), market_dsn=None, on_progress=lambda *_args: None,
            quant_preflight=lambda **_kwargs: pytest.fail("invalid schedule must not request a target"),
        )

    assert caught.value.code == "QUANT_INPUTS_NOT_READY"


@pytest.mark.parametrize("scheduled_at", [None, "not-a-date", "2026-09-23T21:00:00"])
def test_quant_invalid_scheduled_at_completes_metadata_partial_after_deadline(monkeypatch, scheduled_at):
    from backend.modules.daily_research.application import news_pipeline
    from db.instrument import db as market_db

    bundle, claimed, tasks = _quant_bundle_for_preflight()
    monkeypatch.setattr(news_pipeline, "ensure_event_schema", lambda _session: None)
    monkeypatch.setattr(market_db, "get_connection",
                        lambda *_args, **_kwargs: pytest.fail("invalid schedule must not open market DB"))

    report = run_daily_quant(
        bundle, claimed=claimed,
        workflow={"kind": "quant", "trigger": "scheduled", "slot": "quant_2100",
                  "scheduled_at": scheduled_at,
                  "wait_until": "2000-09-23T22:00:00+08:00"},
        execution_control=object(), market_dsn=None, on_progress=lambda *_args: None,
        quant_preflight=lambda **_kwargs: pytest.fail("invalid schedule must not request a target"),
    )

    assert report["status"] == "partial"
    assert report["reason"] == "QUANT_METADATA_INVALID"
    assert report["target_trade_date"] is None
    assert report["market_as_of_trade_date"] is None
    assert report["event_as_of"] is None
    assert report["news_research_dependency"]["reason"] == "INVALID_SCHEDULED_AT"
    assert report["strategies"] == report["candidates"] == []
    assert tasks.artifact.report_json["daily_research"] == report


@pytest.mark.parametrize("wait_until", [None, "not-a-date", "2026-09-23T22:00:00"])
def test_quant_invalid_wait_until_fails_before_market_preflight(monkeypatch, wait_until):
    from backend.modules.daily_research.application import news_pipeline
    from db.instrument import db as market_db

    bundle, claimed, _tasks = _quant_bundle_for_preflight()
    monkeypatch.setattr(news_pipeline, "ensure_event_schema", lambda _session: None)
    monkeypatch.setattr(market_db, "get_connection",
                        lambda *_args, **_kwargs: pytest.fail("invalid wait_until must not open market DB"))

    with pytest.raises(ValueError):
        run_daily_quant(
            bundle, claimed=claimed,
            workflow={"kind": "quant", "trigger": "scheduled", "slot": "quant_2100",
                      "scheduled_at": "2026-09-23T21:00:00+08:00",
                      "wait_until": wait_until},
            execution_control=object(), market_dsn=None, on_progress=lambda *_args: None,
            quant_preflight=lambda **_kwargs: pytest.fail("invalid wait_until must not request a target"),
        )


def test_quant_preflight_universe_change_retries_before_any_strategy(monkeypatch):
    from AI.dataflows.utils import trading_calendar
    from backend.modules.analysis.application.errors import RetryableAnalysisError
    from backend.modules.daily_research.application import news_pipeline, quant_pipeline
    from db.instrument import db as market_db

    bundle, claimed, _tasks = _quant_bundle_for_preflight()
    monkeypatch.setattr(news_pipeline, "ensure_event_schema", lambda _session: None)
    monkeypatch.setattr(trading_calendar, "is_trading_day", lambda _day: True)
    monkeypatch.setattr(quant_pipeline.DataReadinessGate, "resolve",
                        lambda *_args, **_kwargs: SimpleNamespace(market_as_of_trade_date=date(2026, 9, 23)))

    class MarketConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def rollback(self):
            return None

        def execute(self, *_args):
            return None

        def close(self):
            return None

    monkeypatch.setattr(market_db, "get_connection", lambda *_args, **_kwargs: MarketConnection())
    monkeypatch.setattr(quant_pipeline.AllMarketUniverseBuilder, "list_active_cn_stocks",
                        lambda _conn: ["000001.SZ", "000002.SZ"])
    quant_preflight = {
        "state": "ready", "effective_trade_date": "2026-09-23",
        "universe_digest": "0" * 64,
    }

    with pytest.raises(RetryableAnalysisError) as caught:
        run_daily_quant(
            bundle, claimed=claimed,
            workflow={"kind": "quant", "trigger": "manual", "slot": "manual_quant",
                      "scheduled_at": "2026-09-23T21:00:00+08:00",
                      "wait_until": "2099-09-23T22:00:00+08:00"},
            execution_control=object(), market_dsn=None, on_progress=lambda *_args: None,
            quant_preflight=lambda **_kwargs: quant_preflight,
        )

    assert caught.value.code == "QUANT_UNIVERSE_CHANGED"


def test_quant_preflight_requires_same_data_readiness_watermark(monkeypatch):
    from AI.dataflows.utils import trading_calendar
    from backend.modules.analysis.application.errors import RetryableAnalysisError
    from backend.modules.daily_research.application import news_pipeline
    from backend.modules.daily_research.application import quant_pipeline
    from db.instrument import db as market_db

    bundle, claimed, _tasks = _quant_bundle_for_preflight()
    monkeypatch.setattr(news_pipeline, "ensure_event_schema", lambda _session: None)
    monkeypatch.setattr(trading_calendar, "is_trading_day", lambda _day: True)
    monkeypatch.setattr(quant_pipeline.DataReadinessGate, "resolve",
                        lambda *_args, **_kwargs: SimpleNamespace(market_as_of_trade_date=date(2026, 9, 22)))

    class MarketConnection:
        rolled_back = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def rollback(self):
            self.rolled_back = True

        def execute(self, *_args):
            return None

        def close(self):
            return None

    market = MarketConnection()
    monkeypatch.setattr(market_db, "get_connection", lambda *_args, **_kwargs: market)
    with pytest.raises(RetryableAnalysisError) as caught:
        run_daily_quant(
            bundle, claimed=claimed,
            workflow={"kind": "quant", "trigger": "manual", "slot": "manual_quant",
                      "scheduled_at": "2026-09-23T21:00:00+08:00",
                      "wait_until": "2099-09-23T22:00:00+08:00"},
            execution_control=object(), market_dsn=None, on_progress=lambda *_args: None,
            quant_preflight=lambda **_kwargs: {
                "state": "ready", "effective_trade_date": "2026-09-23",
                "universe_digest": "0" * 64,
            },
        )

    assert caught.value.code == "QUANT_DATA_NOT_READY"
    assert market.rolled_back is True
