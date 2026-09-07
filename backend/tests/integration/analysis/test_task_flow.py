"""T4 集成测试：可靠执行闭环（真实 PG + Redis + fake graph，无 LLM）。

覆盖验收：重复投递只执行一次；崩溃恢复 RETRYING 且不发 failed；心跳续租；
迟到 complete 被 fencing 拒绝；attempt 1 可重试 → RETRYING REST → attempt 2
queued/started/SUCCEEDED；取消协作收口；Dispatcher SKIP LOCKED 并发与发布失败重试。
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import date

import pytest

from backend.modules.analysis.application.contracts import (
    AnalysisArtifact,
    CreateAnalysisTaskCommand,
)
from backend.modules.analysis.application.errors import (
    LeaseConflictError,
    ReportNotFoundError,
    RetryableAnalysisError,
)
from backend.modules.analysis.application.reporting import ReportService
from backend.modules.analysis.application.task_events import TaskEventService
from backend.modules.analysis.application.task_lifecycle import OutboxDispatcherService, TaskService
from backend.modules.analysis.domain.enums import TaskEventType, TaskStatus, TaskType
from backend.modules.analysis.infrastructure.redis_task_event_stream import RedisTaskEventStream
from backend.modules.analysis.infrastructure.repositories import SqlAlchemyAnalysisUnitOfWork
from backend.modules.analysis.infrastructure.trading_graph_adapter import TradingGraphAdapter
from backend.shared.clock import SystemClock
from backend.tests.unit.analysis.fakes import FakeCalendar, FakeClock
from backend.workers.analysis_actor import run_analysis_task
from backend.workers.analysis_executor import AnalysisExecutor

RETRY_CFG = type("Retry", (), {"max_retry_attempts": 3, "retry_base_delay_seconds": 60})()


def _artifact(state=None) -> AnalysisArtifact:
    state = state or {}
    return AnalysisArtifact(
        report_json=state.get("report_json") or {"sections": [{"block": "decision", "status": "AVAILABLE"}]},
        conclusion_summary=state.get("conclusion_summary"),
        risk_flag=state.get("risk_flag", False),
        risk_hint=state.get("risk_hint"),
        decision=state.get("decision"),
        artifact_uri=None,
        checksum=None,
        duration_ms=100,
    )


class ServiceContext:
    """一次独立 Session 的 bundle（心跳/进度/终态各自 open，不共享 Session）。"""

    def __init__(self, env, clock, *, lease_ttl_seconds: int = 120) -> None:
        self._sf = env["session_factory"]
        self._stream = RedisTaskEventStream(env["redis"])
        self._clock = clock
        self._ttl = lease_ttl_seconds

    def __enter__(self) -> "ServiceContext":
        self._uow = SqlAlchemyAnalysisUnitOfWork(self._sf).__enter__()
        self.uow = self._uow
        self.outbox = self._uow.outbox
        self.events = TaskEventService(self._uow, clock=self._clock, stream=self._stream)
        self.tasks = TaskService(
            self._uow,
            clock=self._clock,
            calendar=FakeCalendar(),
            events=self.events,
            lease_ttl_seconds=self._ttl,
            retry=RETRY_CFG,
        )
        self.reports = ReportService(clock=self._clock)
        self.build_artifact = _artifact
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._uow.__exit__(exc_type, exc, tb)


class BundleFactory:
    def __init__(self, env, clock, *, lease_ttl_seconds: int = 120) -> None:
        self._env = env
        self._clock = clock
        self._ttl = lease_ttl_seconds

    def open(self) -> ServiceContext:
        return ServiceContext(self._env, self._clock, lease_ttl_seconds=self._ttl)


def _bundle_factory(env, clock, *, lease_ttl_seconds: int = 120) -> BundleFactory:
    return BundleFactory(env, clock, lease_ttl_seconds=lease_ttl_seconds)


class RecordingPublisher:
    def __init__(self, fail_times: int = 0) -> None:
        self.published: list[dict] = []
        self.calls = 0
        self._fail_times = fail_times

    def publish(self, payload: dict) -> None:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError("broker down")
        self.published.append(payload)


class FakeGraph:
    def __init__(self, *, messages=(), final_state=None, error=None, block: threading.Event | None = None, runs: list | None = None):
        self._messages = messages
        self._final_state = final_state or {}
        self._error = error
        self._block = block
        self._runs = runs

    def propagate(self, init_state, progress_callback):
        if self._runs is not None:
            self._runs.append(init_state)
        if self._block is not None:
            self._block.wait(timeout=10)
        for message in self._messages:
            progress_callback(message)
        if self._error is not None:
            raise self._error
        return self._final_state


def _create_task(bundle_factory) -> uuid.UUID:
    with bundle_factory.open() as b:
        return b.tasks.create_task(
            CreateAnalysisTaskCommand(
                task_type=TaskType.SINGLE_STOCK,
                ticker="000001.SZ",
                requested_trade_date=date(2026, 9, 4),
                selected_layers=("market", "sector", "stock"),
            ),
            idempotency_key=None,
            trace_id="trace-t4",
        ).task_id


def _dispatch_all(bundle_factory, publisher, clock, limit: int = 10) -> int:
    with bundle_factory.open() as b:
        dispatcher = OutboxDispatcherService(
            b.uow, task_service=b.tasks, publisher=publisher, clock=clock, dispatch_lease_seconds=30
        )
        return dispatcher.dispatch_due(limit=limit)


def _stream_events(env, task_id: uuid.UUID) -> list[str]:
    stream = RedisTaskEventStream(env["redis"])
    return [event_type for _, event_type, _ in stream.read(task_id, after=None)]




def _wait_for_status(bf, task_id, status, timeout: float = 5.0) -> bool:
    """轮询任务状态（替代固定 sleep：慢 CI 不静默走错分支）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with bf.open() as b:
            current = b.tasks.get_task(task_id).status
        if current is status:
            return True
        time.sleep(0.02)
    return False


def test_duplicate_message_executes_graph_once(env):
    clock = FakeClock()
    bf = _bundle_factory(env, clock)
    task_id = _create_task(bf)
    assert _dispatch_all(bf, RecordingPublisher(), clock) == 1

    runs: list = []
    adapter = TradingGraphAdapter(lambda layers=None: FakeGraph(messages=("市场层分析完成", "板块层分析完成"), runs=runs))
    executor = AnalysisExecutor(
        graph_adapter=adapter, bundle_factory=bf, worker_id="w1",
        heartbeat_interval_seconds=0.05, max_attempt_runtime_seconds=3600,
    )
    run_analysis_task(str(task_id), 1, bundle_factory=bf, executor=executor)
    run_analysis_task(str(task_id), 1, bundle_factory=bf, executor=executor)  # 重复消息

    assert len(runs) == 1  # 只执行一次
    with bf.open() as b:
        task = b.tasks.get_task(task_id)
        assert task.status is TaskStatus.SUCCEEDED
        assert b.reports.get_latest_report(b.uow, task_id).report_version == 1


def test_retryable_error_flows_attempt1_to_attempt2(env):
    clock = FakeClock()
    bf = _bundle_factory(env, clock)
    task_id = _create_task(bf)
    assert _dispatch_all(bf, RecordingPublisher(), clock) == 1

    attempt_runs = []
    graph_states = [RetryableAnalysisError("限流", code="RATE_LIMITED"), None]  # 第一次失败，第二次成功

    def graph_factory(layers=None):
        index = len(attempt_runs)
        attempt_runs.append(index)
        error = graph_states[min(index, len(graph_states) - 1)]
        return FakeGraph(messages=("市场层分析完成",), error=error, final_state={"report_json": {"sections": []}})

    executor = AnalysisExecutor(
        graph_adapter=TradingGraphAdapter(graph_factory), bundle_factory=bf, worker_id="w1",
        heartbeat_interval_seconds=0.05, max_attempt_runtime_seconds=3600,
    )
    run_analysis_task(str(task_id), 1, bundle_factory=bf, executor=executor)

    # attempt 1：RETRYING（REST 可见），不发布 failed
    with bf.open() as b:
        task = b.tasks.get_task(task_id)
        assert task.status is TaskStatus.RETRYING
        assert task.attempt_no == 2
        assert task.next_retry_at is not None
    events = _stream_events(env, task_id)
    assert events == [TaskEventType.QUEUED.value, TaskEventType.STARTED.value, TaskEventType.PROGRESS.value]

    # 时钟推进越过 next_retry_at → Dispatcher 重新投递 → attempt 2 queued → started → SUCCEEDED
    clock.advance(61)
    assert _dispatch_all(bf, RecordingPublisher(), clock) == 1
    run_analysis_task(str(task_id), 2, bundle_factory=bf, executor=executor)

    with bf.open() as b:
        task = b.tasks.get_task(task_id)
        assert task.status is TaskStatus.SUCCEEDED
        assert task.attempt_no == 2
    events = _stream_events(env, task_id)
    assert events == [
        TaskEventType.QUEUED.value, TaskEventType.STARTED.value, TaskEventType.PROGRESS.value,  # attempt 1
        TaskEventType.QUEUED.value, TaskEventType.STARTED.value, TaskEventType.PROGRESS.value,  # attempt 2
        TaskEventType.COMPLETED.value,
    ]
    assert TaskEventType.FAILED.value not in events


def test_worker_crash_recovery_without_failed_event_and_late_complete_rejected(env):
    clock = FakeClock()
    bf = _bundle_factory(env, clock)
    task_id = _create_task(bf)
    assert _dispatch_all(bf, RecordingPublisher(), clock) == 1

    # Worker 领取后崩溃（未收口）
    with bf.open() as b:
        claimed = b.tasks.claim_for_execution(task_id, 1, worker_id="crashed-w1")
    assert claimed is not None

    # 租约过期 → 恢复作业：RETRYING(2) + 新 Outbox，不发布 failed（测试精确控时，宽限期取 0）
    clock.advance(121)
    with bf.open() as b:
        assert b.tasks.recover_expired_leases(grace_seconds=0) == 1
    with bf.open() as b:
        task = b.tasks.get_task(task_id)
        assert task.status is TaskStatus.RETRYING
        assert task.attempt_no == 2
        assert b.uow.tasks.get(task_id).lease_token is None  # ORM 层断言租约已清空（DTO 不暴露租约）
    assert TaskEventType.FAILED.value not in _stream_events(env, task_id)

    # 迟到 attempt 的完成被 fencing 拒绝：无报告、状态不回退
    with bf.open() as b:
        with pytest.raises(LeaseConflictError):
            b.tasks.complete_task(task_id, 1, claimed.lease_token, _artifact(), b.reports)
    with bf.open() as b:
        task = b.tasks.get_task(task_id)
        assert task.status is TaskStatus.RETRYING
        with pytest.raises(ReportNotFoundError):
            b.reports.get_latest_report(b.uow, task_id)

    # 迟到消息的 claim 也被条件领取过滤
    adapter = TradingGraphAdapter(lambda layers=None: FakeGraph())
    executor = AnalysisExecutor(
        graph_adapter=adapter, bundle_factory=bf, worker_id="w2",
        heartbeat_interval_seconds=0.05, max_attempt_runtime_seconds=3600,
    )
    run_analysis_task(str(task_id), 1, bundle_factory=bf, executor=executor)
    with bf.open() as b:
        assert b.tasks.get_task(task_id).status is TaskStatus.RETRYING


def test_heartbeat_renews_lease_while_graph_running(env):
    real_clock = SystemClock()
    bf = _bundle_factory(env, real_clock, lease_ttl_seconds=120)
    task_id = _create_task(bf)
    assert _dispatch_all(bf, RecordingPublisher(), real_clock) == 1
    with bf.open() as b:
        claimed = b.tasks.claim_for_execution(task_id, 1, worker_id="hb-w1")

    block = threading.Event()
    adapter = TradingGraphAdapter(lambda layers=None: FakeGraph(messages=("市场层分析完成",), block=block))
    executor = AnalysisExecutor(
        graph_adapter=adapter, bundle_factory=bf, worker_id="hb-w1",
        heartbeat_interval_seconds=0.05, max_attempt_runtime_seconds=3600,
    )
    thread = threading.Thread(target=executor.execute, args=(claimed,))
    thread.start()

    try:
        # 轮询等待图进入阻塞（RUNNING + 心跳首次落库），再采样两次心跳对比
        deadline = time.monotonic() + 5
        first = None
        while time.monotonic() < deadline:
            with bf.open() as b:
                first = b.uow.tasks.get(task_id).heartbeat_at  # ORM 层读取（DTO 不暴露心跳）
            if first is not None:
                break
            time.sleep(0.02)
        time.sleep(0.3)
        with bf.open() as b:
            renewed = b.uow.tasks.get(task_id).heartbeat_at
    finally:
        block.set()
        thread.join(timeout=10)

    assert first is not None
    assert renewed is not None and renewed >= first
    with bf.open() as b:
        assert b.tasks.get_task(task_id).status is TaskStatus.SUCCEEDED


def test_cancel_cooperative_at_stage_boundary(env):
    clock = FakeClock()
    bf = _bundle_factory(env, clock)
    task_id = _create_task(bf)
    assert _dispatch_all(bf, RecordingPublisher(), clock) == 1
    with bf.open() as b:
        claimed = b.tasks.claim_for_execution(task_id, 1, worker_id="cancel-w1")

    # 图开始后阻塞，用户请求取消，随后图进入阶段边界 → 协作收口 CANCELLED
    block = threading.Event()
    adapter = TradingGraphAdapter(
        lambda layers=None: FakeGraph(messages=("市场层分析完成", "板块层分析完成"), block=block)
    )
    executor = AnalysisExecutor(
        graph_adapter=adapter, bundle_factory=bf, worker_id="cancel-w1",
        heartbeat_interval_seconds=0.05, max_attempt_runtime_seconds=3600,
    )
    thread = threading.Thread(target=executor.execute, args=(claimed,))
    thread.start()
    import time

    try:
        # 轮询等待 started 已发布（RUNNING），再请求取消（避免慢 CI 上取消早于抢占）
        assert _wait_for_status(bf, task_id, TaskStatus.RUNNING, timeout=5)
        with bf.open() as b:
            b.tasks.request_cancel(task_id)
        with bf.open() as b:
            assert b.tasks.get_task(task_id).status is TaskStatus.CANCEL_REQUESTED
    finally:
        block.set()  # 图继续 → 进度回调检测取消 → mark_cancelled → 停止后续推进
        thread.join(timeout=10)

    with bf.open() as b:
        task = b.tasks.get_task(task_id)
        assert task.status is TaskStatus.CANCELLED
    events = _stream_events(env, task_id)
    assert events[-1] == TaskEventType.CANCELLED.value
    assert TaskEventType.COMPLETED.value not in events


def test_cancel_arriving_after_last_progress_before_graph_return(env):
    """图返回后 DB 复核分支（executor._run_graph 178-188）：取消在最后一次 progress 之后
    到达、图正常返回（无后续回调窗口）→ 执行器复核发现 CANCEL_REQUESTED → mark_cancelled。"""
    clock = FakeClock()
    bf = _bundle_factory(env, clock)
    task_id = _create_task(bf)
    assert _dispatch_all(bf, RecordingPublisher(), clock) == 1
    with bf.open() as b:
        claimed = b.tasks.claim_for_execution(task_id, 1, worker_id="late-cancel-w1")

    block = threading.Event()

    class LateCancelGraph:
        def __init__(self, gate):
            self._gate = gate

        def propagate(self, init_state, progress_callback):
            progress_callback("市场层分析完成")  # 最后一次 progress
            self._gate.wait(timeout=10)  # 取消在阻塞期间到达：回调窗口已过
            return {"decision": "x"}

    executor = AnalysisExecutor(
        graph_adapter=TradingGraphAdapter(lambda layers=None: LateCancelGraph(block)),
        bundle_factory=bf,
        worker_id="late-cancel-w1",
        heartbeat_interval_seconds=0.05,
        max_attempt_runtime_seconds=3600,
    )
    thread = threading.Thread(target=executor.execute, args=(claimed,))
    thread.start()
    try:
        assert _wait_for_status(bf, task_id, TaskStatus.RUNNING, timeout=5)
        # 等待 progress 已发布（回调已执行完），再取消
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            events = _stream_events(env, task_id)
            if TaskEventType.PROGRESS.value in events:
                break
            time.sleep(0.02)
        with bf.open() as b:
            b.tasks.request_cancel(task_id)
    finally:
        block.set()
        thread.join(timeout=10)

    with bf.open() as b:
        assert b.tasks.get_task(task_id).status is TaskStatus.CANCELLED
    events = _stream_events(env, task_id)
    assert events[-1] == TaskEventType.CANCELLED.value
    assert events.count(TaskEventType.CANCELLED.value) == 1  # 仅一条取消事件（无重复收口）
    assert TaskEventType.COMPLETED.value not in events


def test_dispatcher_concurrent_claims_no_duplicate_dispatch(env):
    clock = FakeClock()
    bf = _bundle_factory(env, clock)
    task_ids = [_create_task(bf) for _ in range(8)]
    publishers = [RecordingPublisher() for _ in range(2)]
    results: list[int] = []

    def worker(publisher):
        results.append(_dispatch_all(bf, publisher, clock, limit=4))

    threads = [threading.Thread(target=worker, args=(p,)) for p in publishers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sum(results) == 8  # 每个 Outbox 恰好被一个 Dispatcher 抢占
    all_published = publishers[0].published + publishers[1].published
    assert len(all_published) == 8
    assert len({p["task_id"] for p in all_published}) == 8  # 无重复投递
    for task_id in task_ids:
        with bf.open() as b:
            assert b.tasks.get_task(task_id).status is TaskStatus.QUEUED


def test_dispatch_retries_after_broker_publish_failure(env):
    clock = FakeClock()
    bf = _bundle_factory(env, clock)
    task_id = _create_task(bf)

    flaky = RecordingPublisher(fail_times=1)
    assert _dispatch_all(bf, flaky, clock) == 0  # 投递失败：确认失败、任务保持 PENDING

    with bf.open() as b:
        task = b.tasks.get_task(task_id)
        assert task.status is TaskStatus.PENDING
        outbox = b.outbox.get_by_task_attempt(task_id, 1)
        assert outbox.status == "PENDING"
        assert outbox.retry_count == 1
        assert outbox.next_attempt_at is not None

    clock.advance(6)  # 越过投递退避（5s * (retry_count+1)）
    assert _dispatch_all(bf, flaky, clock) == 1
    assert len(flaky.published) == 1
    with bf.open() as b:
        assert b.tasks.get_task(task_id).status is TaskStatus.QUEUED
