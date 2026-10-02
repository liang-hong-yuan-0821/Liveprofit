# test-catalog-begin
# {
#   "purpose": "Worker 量化分支冒烟（plan 4.5.1 盲点 2/3）：_is_quant_task 判定与 _on_progress 三参兼容。",
#   "keywords": [
#     "分析任务",
#     "批次",
#     "连接",
#     "每日",
#     "策略族",
#     "分析图",
#     "市场分析",
#     "行情刷新",
#     "任务",
#     "后台任务",
#     "quant_executor_branch",
#     "batch",
#     "connection",
#     "daily",
#     "family",
#     "graph",
#     "market",
#     "refresh",
#     "task",
#     "worker"
#   ],
#   "covers": [
#     "backend/modules/analysis/application/contracts.py",
#     "backend/modules/analysis/application/errors.py",
#     "backend/modules/analysis/domain/enums.py",
#     "backend/modules/daily_research/application/quant_pipeline.py",
#     "backend/modules/quant_strategy/application/batch_completion.py",
#     "backend/modules/quant_strategy/application/execution.py",
#     "backend/workers/analysis_executor.py",
#     "db/instrument/db.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Worker 量化分支冒烟（plan 4.5.1 盲点 2/3）：_is_quant_task 判定与 _on_progress 三参兼容。"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

from backend.modules.analysis.application.contracts import ClaimedTask
from backend.modules.analysis.domain.enums import TaskStatus, TaskType
from backend.workers.analysis_executor import AnalysisExecutor, _is_quant_task


def _claimed(**overrides) -> ClaimedTask:
    base = dict(
        task_id=uuid.uuid4(),
        attempt_no=1,
        lease_token="lease-1",
        task_type=TaskType.MARKET_WIDE,
        ticker=None,
        selected_layers=("position",),
        effective_trade_date=None,
        request_params={},
    )
    base.update(overrides)
    return ClaimedTask(**base)


def test_is_quant_task_discriminator():
    quant = _claimed(request_params={"execution_snapshot": {"strategy": {}}})
    assert _is_quant_task(quant) is True
    assert _is_quant_task(_claimed(selected_layers=("market",))) is False
    assert _is_quant_task(_claimed(request_params={"analysis_options": {}})) is False
    # 同选 AI 层仍是量化任务（AI 层照旧运行，报告合并）
    assert _is_quant_task(_claimed(selected_layers=("market", "position"), request_params={"execution_snapshot": {}})) is True


@dataclass
class _FakeTaskRow:
    status: str = "RUNNING"
    lease_token: str = "lease-1"


class _FakeBundle:
    """最小 bundle：uow.tasks.get + events.publish。"""

    def __init__(self, task: _FakeTaskRow) -> None:
        self.task = task
        self.published: list[tuple] = []
        self.marked_cancelled: list[tuple] = []
        self.completed: list[tuple] = []
        self.uow = SimpleNamespace(tasks=SimpleNamespace(get=lambda task_id: self.task), session=object())
        self.tasks = SimpleNamespace(
            mark_cancelled=lambda task_id, attempt_no, lease_token: self.marked_cancelled.append((task_id, attempt_no)),
            complete_task=lambda *args: self.completed.append(args),
        )
        self.events = SimpleNamespace(publish=lambda *a, **kw: self.published.append((a, kw)))
        self.reports = object()

    @staticmethod
    def build_artifact(state):
        return {"report_json": state}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeBundleFactory:
    def __init__(self, bundle: _FakeBundle) -> None:
        self.bundle = bundle

    def open(self):
        return self.bundle


def _executor(bundle: _FakeBundle) -> AnalysisExecutor:
    return AnalysisExecutor(
        graph_adapter=None,
        bundle_factory=_FakeBundleFactory(bundle),
        worker_id="w1",
        heartbeat_interval_seconds=3600,
    )


def test_on_progress_three_arg_quant_form_publishes_message():
    """量化扫描按 (stage, done, total) 三参调用（B2 回归保护）：归一为消息并发布。"""
    bundle = _FakeBundle(_FakeTaskRow())
    executor = _executor(bundle)
    executor._claimed = _claimed()  # noqa: SLF001

    executor._on_progress("scan", 200, 5300)

    assert len(bundle.published) == 1
    assert bundle.published[0][1]["message"] == "scan 200/5300"
    assert bundle.published[0][1]["attempt_no"] == 1


def test_on_progress_single_arg_graph_form_still_works():
    """图路径单参调用不受影响。"""
    bundle = _FakeBundle(_FakeTaskRow())
    executor = _executor(bundle)
    executor._claimed = _claimed()  # noqa: SLF001

    executor._on_progress("market 阶段完成")

    assert bundle.published[0][1]["message"] == "market 阶段完成"


def test_on_progress_cancel_requested_raises_and_marks_cancelled():
    bundle = _FakeBundle(_FakeTaskRow(status=TaskStatus.CANCEL_REQUESTED.value))
    executor = _executor(bundle)
    executor._claimed = _claimed()  # noqa: SLF001

    import pytest

    from backend.modules.analysis.application.errors import CooperativeCancelledError

    with pytest.raises(CooperativeCancelledError):
        executor._on_progress("scan", 1, 1)  # noqa: SLF001
    assert executor._cancel_flag is True  # noqa: SLF001


def test_quant_branch_enters_market_connection_context_manager(monkeypatch):
    """真实 get_connection 返回 @contextmanager；Worker 必须 enter 后再传给执行服务。"""
    from backend.modules.quant_strategy.application import execution as quant_execution
    from db.instrument import db as instrument_db

    marker = {"entered": False, "exited": False, "received": None}
    raw_connection = object()

    @contextmanager
    def fake_get_connection(_dsn=None):
        marker["entered"] = True
        try:
            yield raw_connection
        finally:
            marker["exited"] = True

    class FakeQuantExecutionService:
        def __init__(self, **kwargs):
            marker["received"] = kwargs["market_conn"]

        def run(self):
            return {"summary": {"scanned": 1}}

    monkeypatch.setattr(instrument_db, "get_connection", fake_get_connection)
    monkeypatch.setattr(quant_execution, "QuantExecutionService", FakeQuantExecutionService)
    bundle = _FakeBundle(_FakeTaskRow())
    executor = _executor(bundle)
    claimed = _claimed(request_params={"execution_snapshot": {"strategy": {}}})
    heartbeat = SimpleNamespace(fencing_lost=SimpleNamespace(is_set=lambda: False))

    executor._run_quant(claimed, heartbeat)  # noqa: SLF001

    assert marker == {"entered": True, "exited": True, "received": raw_connection}
    assert len(bundle.completed) == 1


def test_daily_quant_branch_receives_market_refresh_preflight(monkeypatch):
    from backend.modules.daily_research.application import quant_pipeline

    preflight = lambda **_kwargs: {"state": "ready"}
    captured = {}
    monkeypatch.setattr(quant_pipeline, "run_daily_quant",
                        lambda _bundle, **kwargs: captured.update(kwargs))
    bundle = _FakeBundle(_FakeTaskRow())
    executor = AnalysisExecutor(
        graph_adapter=None, bundle_factory=_FakeBundleFactory(bundle), worker_id="w1",
        heartbeat_interval_seconds=3600, quant_preflight=preflight,
    )
    claimed = _claimed(request_params={"daily_research": {
        "kind": "quant", "trigger": "manual", "scheduled_at": "2026-09-23T21:00:00+08:00",
    }})
    heartbeat = SimpleNamespace(fencing_lost=SimpleNamespace(is_set=lambda: False))

    executor._run_daily_research(claimed, heartbeat)  # noqa: SLF001

    assert captured["quant_preflight"] is preflight


def test_direct_quant_waits_for_refresh_without_positions(monkeypatch):
    from db.instrument import db as instrument_db

    monkeypatch.setattr(instrument_db, "get_connection", Mock(side_effect=AssertionError("must wait")))
    bundle = _FakeBundle(_FakeTaskRow())
    preflight = Mock(return_value={"state": "pending", "effective_trade_date": "2026-09-23"})
    executor = _executor(bundle)
    executor._quant_preflight = preflight
    executor._fail_or_retry = Mock()
    claimed = _claimed(
        effective_trade_date=date(2026, 9, 23),
        request_params={"execution_snapshot": {"strategy": {}, "positions": []}},
    )
    heartbeat = SimpleNamespace(fencing_lost=SimpleNamespace(is_set=lambda: False))

    executor._run_quant(claimed, heartbeat)

    exc = executor._fail_or_retry.call_args.args[1]
    assert exc.code == "QUANT_INPUTS_NOT_READY"
    assert preflight.call_args.kwargs["trigger"] == "QUANT_EXECUTION"
    assert preflight.call_args.kwargs["at"].utcoffset() is not None
    assert bundle.completed == []


def test_direct_quant_pending_with_positions_forces_protection(monkeypatch):
    from backend.modules.quant_strategy.application import execution as quant_execution
    from db.instrument import db as instrument_db

    captured = {}

    @contextmanager
    def fake_get_connection(_dsn=None):
        yield object()

    class FakeService:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self):
            return {"summary": {"scanned": 1}}

    monkeypatch.setattr(instrument_db, "get_connection", fake_get_connection)
    monkeypatch.setattr(quant_execution, "QuantExecutionService", FakeService)
    executor = _executor(_FakeBundle(_FakeTaskRow()))
    executor._quant_preflight = lambda **kwargs: {"state": "pending", "effective_trade_date": "2026-09-23"}
    claimed = _claimed(
        effective_trade_date=date(2026, 9, 23),
        request_params={"execution_snapshot": {"strategy": {}, "positions": [{"symbol": "000001.SZ"}]}},
    )
    heartbeat = SimpleNamespace(fencing_lost=SimpleNamespace(is_set=lambda: False))

    executor._run_quant(claimed, heartbeat)

    assert captured["protection_only"] is True


def test_direct_quant_without_requested_day_uses_provider_target(monkeypatch):
    from backend.modules.quant_strategy.application import execution as quant_execution
    from db.instrument import db as instrument_db

    captured = {}

    @contextmanager
    def fake_get_connection(_dsn=None):
        yield object()

    class FakeService:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self):
            return {"summary": {"scanned": 1}}

    monkeypatch.setattr(instrument_db, "get_connection", fake_get_connection)
    monkeypatch.setattr(quant_execution, "QuantExecutionService", FakeService)
    executor = _executor(_FakeBundle(_FakeTaskRow()))
    executor._quant_preflight = lambda **kwargs: {
        "state": "ready", "effective_trade_date": "2026-09-23",
    }
    claimed = _claimed(request_params={"execution_snapshot": {"strategy": {}, "positions": []}})
    heartbeat = SimpleNamespace(fencing_lost=SimpleNamespace(is_set=lambda: False))

    executor._run_quant(claimed, heartbeat)

    assert captured["effective_trade_date"] == date(2026, 9, 23)
    assert captured["protection_only"] is False


def test_worker_retries_keep_frozen_batch_mode(monkeypatch):
    from backend.modules.quant_strategy.application import execution
    from db.instrument import db as instrument_db

    seen = []
    triggered = []
    monkeypatch.setattr(AnalysisExecutor, "_complete_family_batch", lambda self, task_id: triggered.append(task_id))

    @contextmanager
    def connection(_dsn=None):
        yield object()

    def run(service):
        seen.append((service._attempt_no, service._defer_new_risk))
        return {"summary": {"scanned": 0}}

    monkeypatch.setattr(instrument_db, "get_connection", connection)
    monkeypatch.setattr(execution.QuantExecutionService, "run", run)
    bundle = _FakeBundle(_FakeTaskRow())
    executor = _executor(bundle)
    heartbeat = SimpleNamespace(fencing_lost=SimpleNamespace(is_set=lambda: False))
    for attempt in (1, 2):
        executor._run_quant(_claimed(attempt_no=attempt, request_params={
            "execution_snapshot": {"strategy": {}, "new_risk_mode": "FAMILY_BATCH"},
        }), heartbeat)
    assert seen == [(1, True), (2, True)]
    assert len(triggered) == 2
    assert len(bundle.completed) == 2


def test_family_completion_callback_commits_and_does_not_fail_scan_on_error(monkeypatch):
    from backend.modules.quant_strategy.application.batch_completion import BatchCompletionService

    bundle = _FakeBundle(_FakeTaskRow())
    bundle.uow.session = Mock()
    task_id = uuid.uuid4()
    consumer = Mock(return_value=None)
    monkeypatch.setattr(BatchCompletionService, "for_task", consumer)
    executor = _executor(bundle)
    executor._complete_family_batch(task_id)
    consumer.assert_called_once_with(task_id)
    bundle.uow.session.commit.assert_called_once()
    bundle.uow.session.commit.reset_mock()
    consumer.side_effect = RuntimeError("transient database failure")
    executor._complete_family_batch(task_id)  # failure is recoverable by dispatcher
    bundle.uow.session.commit.assert_not_called()


def test_failed_family_scan_also_triggers_convergence(monkeypatch):
    from db.instrument import db as instrument_db
    from backend.modules.quant_strategy.application import execution

    @contextmanager
    def connection(_dsn=None):
        yield object()

    monkeypatch.setattr(instrument_db, "get_connection", connection)
    monkeypatch.setattr(execution.QuantExecutionService, "run", Mock(side_effect=RuntimeError("scan failed")))
    bundle = _FakeBundle(_FakeTaskRow())
    executor = _executor(bundle)
    events = []
    executor._fail_or_retry = lambda *a: events.append("task_terminal")
    executor._complete_family_batch = lambda *a: events.append("batch_trigger")
    executor._run_quant(_claimed(request_params={"execution_snapshot": {
        "strategy": {}, "new_risk_mode": "FAMILY_BATCH",
    }}), SimpleNamespace(fencing_lost=SimpleNamespace(is_set=lambda: False)))
    assert events == ["task_terminal", "batch_trigger"]
    assert not bundle.completed
