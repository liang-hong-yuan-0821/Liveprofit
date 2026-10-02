# test-catalog-begin
# {
#   "purpose": "事件研究 / daily_job_market_status：An incomplete market refresh cannot produce a daily-job completion marker.",
#   "keywords": [
#     "事件研究",
#     "每日",
#     "市场分析",
#     "状态",
#     "daily_job_market_status",
#     "daily",
#     "market",
#     "status"
#   ],
#   "covers": [
#     "AI/eventStudy/scheduler/daily_job.py",
#     "db/instrument/ingest/incremental.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""An incomplete market refresh cannot produce a daily-job completion marker."""

from unittest.mock import MagicMock

import pytest

from AI.eventStudy.scheduler import daily_job


def test_market_summary_error_is_not_treated_as_success(monkeypatch):
    from db.instrument.ingest import incremental

    class Guard:
        def __init__(self, conn, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("db.instrument.ingest.guard.IngestGuard", Guard)
    monkeypatch.setattr(incremental, "_providers_from_env", lambda: (None, lambda: None))
    monkeypatch.setattr(incremental, "collect_incremental",
                        lambda *args, **kwargs: {"error": "INCREMENTAL_INCOMPLETE"})

    with pytest.raises(RuntimeError, match="MARKET_INGEST_INCOMPLETE"):
        daily_job.step_collect_market(MagicMock(), changed=lambda _: None)


def test_market_failure_propagates_to_daily_job_exit(monkeypatch):
    conn = MagicMock()
    monkeypatch.setattr(daily_job, "get_connection", lambda: conn)
    monkeypatch.setattr(daily_job, "init_schema", lambda _conn: True)
    monkeypatch.setattr(daily_job.market_data_dao, "list_assets",
                        lambda _conn: [object()] * len(daily_job.TARGET_ASSETS))
    monkeypatch.setattr(daily_job, "step_collect_market",
                        MagicMock(side_effect=RuntimeError("MARKET_INGEST_INCOMPLETE")))
    monkeypatch.setattr(daily_job.sys, "argv", [
        "daily_job", "--skip", "crawl", "context", "vectorize", "study",
    ])

    with pytest.raises(SystemExit) as exc:
        daily_job.run_daily_job()

    assert exc.value.code == 1
    conn.close.assert_called_once()
