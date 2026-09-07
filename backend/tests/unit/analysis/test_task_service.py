"""TaskService 状态迁移、幂等与事务边界单测（fake uow/clock/stream，无网络）。"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from backend.modules.analysis.application.contracts import (
    AnalysisArtifact,
    ClassifiedError,
    CreateAnalysisTaskCommand,
)
from backend.modules.analysis.application.errors import (
    IdempotencyKeyInvalidError,
    IdempotencyKeyReusedError,
    LeaseConflictError,
    TaskCreateInvalidError,
)
from backend.modules.analysis.application.reporting import ReportService
from backend.modules.analysis.application.task_events import TaskEventService
from backend.modules.analysis.application.task_lifecycle import (
    MESSAGE_TYPE_ANALYSIS_TASK,
    OutboxDispatcherService,
    TaskService,
)
from backend.modules.analysis.domain.enums import TaskEventType, TaskStatus, TaskType
from backend.tests.unit.analysis.fakes import (
    FakeCalendar,
    FakeClock,
    FakePublisher,
    FakeStream,
    FakeUnitOfWork,
)


def _artifact(report_json: dict | None = None, **kwargs) -> AnalysisArtifact:
    defaults = dict(
        report_json=report_json or {"sections": [{"block": "market", "status": "AVAILABLE"}]},
        conclusion_summary="结论摘要",
        risk_flag=False,
        risk_hint=None,
        decision={"direction": "up"},
        artifact_uri="var/runs/t1/1/artifact.json",
        checksum="abc",
        duration_ms=1500,
    )
    defaults.update(kwargs)
    return AnalysisArtifact(**defaults)


def _service(*, events: bool = True, clock: FakeClock | None = None) -> tuple[TaskService, FakeUnitOfWork, FakeStream]:
    uow = FakeUnitOfWork()
    stream = FakeStream()
    clock = clock or FakeClock()
    event_service = TaskEventService(uow, clock=clock, stream=stream) if events else None
    service = TaskService(
        uow,
        clock=clock,
        calendar=FakeCalendar(),
        events=event_service,
        lease_ttl_seconds=120,
        retry=type("Retry", (), {"max_retry_attempts": 3, "retry_base_delay_seconds": 60})(),
    )
    return service, uow, stream


def _create_task(service: TaskService, task_type: TaskType = TaskType.SINGLE_STOCK, ticker: str = "000001.SZ", layers: tuple[str, ...] = ("market", "sector", "stock"), key: str | None = None) -> uuid.UUID:
    result = service.create_task(
        CreateAnalysisTaskCommand(
            task_type=task_type,
            ticker=ticker if task_type is TaskType.SINGLE_STOCK else None,
            requested_trade_date=date(2026, 9, 4),
            selected_layers=layers,
        ),
        idempotency_key=key,
        trace_id="trace-1",
    )
    return result.task_id


def test_create_task_writes_pending_task_and_outbox_in_one_transaction():
    service, uow, _ = _service(events=False)
    store = uow.store
    task_id = _create_task(service, key="key-1")

    task = store.committed[task_id]
    assert task.status == TaskStatus.PENDING.value
    assert task.attempt_no == 1
    assert task.ticker == "000001.SZ"
    outbox = uow.outbox.get_by_task_attempt(task_id, 1)
    assert outbox is not None
    assert outbox.status == "PENDING"
    assert outbox.message_type == MESSAGE_TYPE_ANALYSIS_TASK
    assert outbox.payload == {"task_id": str(task_id), "attempt_no": 1}
    assert uow.commit_count == 1  # 同一事务一次提交


def test_idempotent_replay_same_key_same_input_returns_original_task():
    service, uow, _ = _service(events=False)
    store = uow.store
    first = _create_task(service, key="key-1")
    replay = service.create_task(
        CreateAnalysisTaskCommand(
            task_type=TaskType.SINGLE_STOCK,
            ticker="000001.SZ",
            requested_trade_date=date(2026, 9, 4),
            selected_layers=("market", "sector", "stock"),
        ),
        idempotency_key="key-1",
        trace_id="trace-2",
    )
    assert replay.task_id == first
    assert replay.idempotent_replay is True
    assert len(store.committed) == 1  # 不产生第二个任务
    assert uow.commit_count == 1


def test_idempotency_key_reused_with_different_input_raises():
    service, _, _ = _service(events=False)
    _create_task(service, key="key-1")
    with pytest.raises(IdempotencyKeyReusedError):
        service.create_task(
            CreateAnalysisTaskCommand(
                task_type=TaskType.SINGLE_STOCK,
                ticker="000001.SZ",
                requested_trade_date=date(2026, 9, 3),  # 不同输入
                selected_layers=("market", "sector", "stock"),
            ),
            idempotency_key="key-1",
            trace_id="trace-2",
        )


@pytest.mark.parametrize("bad_key", ["", "a" * 129, "key with space", "key/带斜杠", "中文键"])
def test_invalid_idempotency_key_rejected(bad_key):
    service, _, _ = _service(events=False)
    with pytest.raises(IdempotencyKeyInvalidError):
        _create_task(service, key=bad_key)


def test_layer_validation_rules():
    service, _, _ = _service(events=False)
    with pytest.raises(TaskCreateInvalidError, match="ticker"):
        _create_task(service, task_type=TaskType.SINGLE_STOCK, ticker="", layers=("market",))
    with pytest.raises(TaskCreateInvalidError, match="只允许"):
        _create_task(service, layers=("market", "screening"))
    with pytest.raises(TaskCreateInvalidError, match="禁止提供 ticker"):
        service.create_task(
            CreateAnalysisTaskCommand(
                task_type=TaskType.MARKET_WIDE, ticker="000001.SZ",
                requested_trade_date=None, selected_layers=("market", "sector", "screening"),
            ),
            idempotency_key=None, trace_id=None,
        )
    # 产品决策 2026-09-06 v3：全市场调研层级自由组合，market+sector 是合法子集
    _create_task(service, task_type=TaskType.MARKET_WIDE, layers=("market", "sector"))
    with pytest.raises(TaskCreateInvalidError, match="position"):
        _create_task(service, task_type=TaskType.MARKET_WIDE, layers=("market", "sector", "position"))
    # position 随 screening 出现是合法组合（互斥规则的另一面）
    _create_task(service, task_type=TaskType.MARKET_WIDE, layers=("market", "sector", "screening", "position"))


def test_confirm_outbox_published_moves_to_queued_and_publishes_event():
    service, uow, stream = _service()
    store = uow.store
    task_id = _create_task(service)
    clock = FakeClock()

    dispatcher = OutboxDispatcherService(
        uow, task_service=service, publisher=FakePublisher(), clock=clock, dispatch_lease_seconds=30
    )
    assert dispatcher.dispatch_due(limit=10) == 1

    task = store.committed[task_id]
    assert task.status == TaskStatus.QUEUED.value
    outbox = uow.outbox.get_by_task_attempt(task_id, 1)
    assert outbox.status == "PUBLISHED"
    assert outbox.published_at is not None
    # queued 业务事件在提交后发布
    assert stream.events[task_id][0][1] == TaskEventType.QUEUED.value


def test_confirm_with_wrong_lease_token_returns_false():
    service, uow, _ = _service(events=False)
    store = uow.store
    task_id = _create_task(service)
    claimed = uow.outbox.claim_due(FakeClock().now(), limit=10, lease_seconds=30, lease_token="t1")
    uow.commit()
    assert service.confirm_outbox_published(claimed[0].id, "wrong-token", trace_id=None) is False
    assert store.committed[task_id].status == TaskStatus.PENDING.value


def test_claim_for_execution_fences_duplicate_and_stale_attempts():
    service, uow, _ = _service(events=False)
    store = uow.store
    task_id = _create_task(service)
    uow.outbox.claim_due(FakeClock().now(), limit=10, lease_seconds=30, lease_token="dt")
    uow.commit()
    service.confirm_outbox_published(uow.outbox.get_by_task_attempt(task_id, 1).id, "dt", trace_id=None)

    claimed = service.claim_for_execution(task_id, 1, worker_id="w1")
    assert claimed is not None
    assert claimed.lease_token
    task = store.committed[task_id]
    assert task.status == TaskStatus.RUNNING.value
    assert task.worker_id == "w1"
    assert task.lease_expires_at is not None
    # 重复/过期消息（同 attempt 再次领取）被条件更新拒绝
    assert service.claim_for_execution(task_id, 1, worker_id="w2") is None
    assert service.claim_for_execution(task_id, 0, worker_id="w2") is None


def test_renew_lease_fencing():
    service, uow, _ = _service(events=False)
    store = uow.store
    task_id = _create_task(service)
    uow.outbox.claim_due(FakeClock().now(), limit=10, lease_seconds=30, lease_token="dt")
    uow.commit()
    service.confirm_outbox_published(uow.outbox.get_by_task_attempt(task_id, 1).id, "dt", trace_id=None)
    claimed = service.claim_for_execution(task_id, 1, worker_id="w1")
    assert service.renew_lease(task_id, 1, claimed.lease_token) is True
    assert service.renew_lease(task_id, 1, "wrong-token") is False
    assert service.renew_lease(task_id, 2, claimed.lease_token) is False


def test_complete_task_saves_report_and_succeeds_in_same_transaction():
    service, uow, stream = _service()
    store = uow.store
    report_service = ReportService(clock=FakeClock())
    task_id = _create_task(service)
    uow.outbox.claim_due(FakeClock().now(), limit=10, lease_seconds=30, lease_token="dt")
    uow.commit()
    service.confirm_outbox_published(uow.outbox.get_by_task_attempt(task_id, 1).id, "dt", trace_id=None)
    claimed = service.claim_for_execution(task_id, 1, worker_id="w1")

    report = service.complete_task(task_id, 1, claimed.lease_token, _artifact(), report_service)
    assert report.report_version == 1
    task = store.committed[task_id]
    assert task.status == TaskStatus.SUCCEEDED.value
    assert task.lease_token is None
    assert stream.events[task_id][-1][1] == TaskEventType.COMPLETED.value
    assert stream.events[task_id][-1][2]["report_id"] == str(report.report_id)


def test_complete_task_with_stale_lease_rolls_back_report():
    service, uow, _ = _service(events=False)
    store = uow.store
    report_service = ReportService(clock=FakeClock())
    task_id = _create_task(service)
    uow.outbox.claim_due(FakeClock().now(), limit=10, lease_seconds=30, lease_token="dt")
    uow.commit()
    service.confirm_outbox_published(uow.outbox.get_by_task_attempt(task_id, 1).id, "dt", trace_id=None)
    service.claim_for_execution(task_id, 1, worker_id="w1")

    with pytest.raises(LeaseConflictError):
        service.complete_task(task_id, 1, "wrong-token", _artifact(), report_service)
    assert store.committed[task_id].status == TaskStatus.RUNNING.value  # 状态未推进
    # 报告与终态均被回滚：无任何 report 落库
    assert uow.reports.get_latest(task_id) is None
    assert uow.rollback_count >= 1


def test_fail_or_retry_retryable_moves_to_retrying_without_events():
    service, uow, stream = _service()
    store = uow.store
    task_id = _create_task(service)
    uow.outbox.claim_due(FakeClock().now(), limit=10, lease_seconds=30, lease_token="dt")
    uow.commit()
    service.confirm_outbox_published(uow.outbox.get_by_task_attempt(task_id, 1).id, "dt", trace_id=None)
    claimed = service.claim_for_execution(task_id, 1, worker_id="w1")

    dto = service.fail_or_retry(task_id, 1, claimed.lease_token, ClassifiedError("PROVIDER_UNAVAILABLE", "行情不可用", True))
    assert dto.status is TaskStatus.RETRYING
    assert dto.attempt_no == 2
    assert dto.next_retry_at is not None
    task = store.committed[task_id]
    assert task.status == TaskStatus.RETRYING.value
    assert task.lease_token is None
    outbox = uow.outbox.get_by_task_attempt(task_id, 2)
    assert outbox is not None and outbox.status == "PENDING"
    # 不发布任何重试专用 SSE（无 failed、无 queued）
    assert TaskEventType.FAILED.value not in [e[1] for e in stream.events.get(task_id, [])]


def test_fail_or_retry_exhausted_or_fatal_commits_failed_and_publishes():
    service, uow, stream = _service()
    store = uow.store
    task_id = _create_task(service)
    uow.outbox.claim_due(FakeClock().now(), limit=10, lease_seconds=30, lease_token="dt")
    uow.commit()
    service.confirm_outbox_published(uow.outbox.get_by_task_attempt(task_id, 1).id, "dt", trace_id=None)
    claimed = service.claim_for_execution(task_id, 1, worker_id="w1")

    # 不可重试：直接 FAILED
    dto = service.fail_or_retry(task_id, 1, claimed.lease_token, ClassifiedError("INPUT_INVALID", "参数非法", False))
    assert dto.status is TaskStatus.FAILED
    assert store.committed[task_id].status == TaskStatus.FAILED.value
    assert stream.events[task_id][-1][1] == TaskEventType.FAILED.value

    # 重试耗尽（attempt 3 = max）：FAILED；时钟推进越过 next_retry_at 后 Dispatcher 才能再次投递
    clock2 = FakeClock()
    service2, uow2, stream2 = _service(clock=clock2)
    store2 = uow2.store
    task2_id = _create_task(service2)
    uow2.outbox.claim_due(clock2.now(), limit=10, lease_seconds=30, lease_token="dt")
    uow2.commit()
    service2.confirm_outbox_published(uow2.outbox.get_by_task_attempt(task2_id, 1).id, "dt", trace_id=None)
    claimed2 = service2.claim_for_execution(task2_id, 1, worker_id="w1")
    service2.fail_or_retry(task2_id, 1, claimed2.lease_token, ClassifiedError("RATE_LIMITED", "限流", True))
    clock2.advance(61)
    uow2.outbox.claim_due(clock2.now(), limit=10, lease_seconds=30, lease_token="dt")
    uow2.commit()
    service2.confirm_outbox_published(uow2.outbox.get_by_task_attempt(task2_id, 2).id, "dt", trace_id=None)
    claimed2b = service2.claim_for_execution(task2_id, 2, worker_id="w1")
    service2.fail_or_retry(task2_id, 2, claimed2b.lease_token, ClassifiedError("RATE_LIMITED", "限流", True))
    clock2.advance(121)
    uow2.outbox.claim_due(clock2.now(), limit=10, lease_seconds=30, lease_token="dt")
    uow2.commit()
    service2.confirm_outbox_published(uow2.outbox.get_by_task_attempt(task2_id, 3).id, "dt", trace_id=None)
    claimed3 = service2.claim_for_execution(task2_id, 3, worker_id="w1")
    dto3 = service2.fail_or_retry(task2_id, 3, claimed3.lease_token, ClassifiedError("RATE_LIMITED", "限流", True))
    assert dto3.status is TaskStatus.FAILED
    assert store2.committed[task2_id].attempt_no == 3
    # 重试耗尽提交 FAILED 后发布 failed 事件
    assert stream2.events[task2_id][-1][1] == TaskEventType.FAILED.value


def test_request_cancel_paths():
    service, uow, stream = _service()
    store = uow.store
    # 未运行任务：直接收口 CANCELLED 并废弃 Outbox
    task_id = _create_task(service)
    dto = service.request_cancel(task_id)
    assert dto.status is TaskStatus.CANCELLED
    assert store.committed[task_id].status == TaskStatus.CANCELLED.value
    assert uow.outbox.get_by_task_attempt(task_id, 1).status == "CANCELLED"
    assert stream.events[task_id][-1][1] == TaskEventType.CANCELLED.value

    # 运行中任务：协作式取消（置 CANCEL_REQUESTED，由 Worker 收口）
    task2_id = _create_task(service)
    uow.outbox.claim_due(FakeClock().now(), limit=10, lease_seconds=30, lease_token="dt")
    uow.commit()
    service.confirm_outbox_published(uow.outbox.get_by_task_attempt(task2_id, 1).id, "dt", trace_id=None)
    service.claim_for_execution(task2_id, 1, worker_id="w1")
    dto2 = service.request_cancel(task2_id)
    assert dto2.status is TaskStatus.CANCEL_REQUESTED
    assert store.committed[task2_id].cancel_requested_at is not None

    # 重复取消幂等返回当前状态
    dto3 = service.request_cancel(task2_id)
    assert dto3.status is TaskStatus.CANCEL_REQUESTED

    # 持有 lease 的 Worker 收口
    claimed = service.claim_for_execution(task2_id, 1, worker_id="w1")  # 状态已非 QUEUED → None
    assert claimed is None
    task2 = store.committed[task2_id]
    dto4 = service.mark_cancelled(task2_id, task2.attempt_no, task2.lease_token)
    assert dto4.status is TaskStatus.CANCELLED


def test_mark_cancelled_requires_cancel_requested_and_lease():
    service, uow, _ = _service(events=False)
    store = uow.store
    task_id = _create_task(service)
    uow.outbox.claim_due(FakeClock().now(), limit=10, lease_seconds=30, lease_token="dt")
    uow.commit()
    service.confirm_outbox_published(uow.outbox.get_by_task_attempt(task_id, 1).id, "dt", trace_id=None)
    claimed = service.claim_for_execution(task_id, 1, worker_id="w1")
    with pytest.raises(LeaseConflictError):
        service.mark_cancelled(task_id, 1, claimed.lease_token)  # 仍是 RUNNING
    service.request_cancel(task_id)
    with pytest.raises(LeaseConflictError):
        service.mark_cancelled(task_id, 1, "wrong-token")
    dto = service.mark_cancelled(task_id, 1, claimed.lease_token)
    assert dto.status is TaskStatus.CANCELLED


def test_recover_expired_leases_creates_retry_without_failed_event():
    service, uow, stream = _service()
    store = uow.store
    task_id = _create_task(service)
    uow.outbox.claim_due(FakeClock().now(), limit=10, lease_seconds=30, lease_token="dt")
    uow.commit()
    service.confirm_outbox_published(uow.outbox.get_by_task_attempt(task_id, 1).id, "dt", trace_id=None)
    claimed = service.claim_for_execution(task_id, 1, worker_id="w1")
    # 使租约过期（测试精确控时，宽限期取 0）
    task = store.committed[task_id]
    task.lease_expires_at = FakeClock().now() - timedelta(seconds=1)

    recovered = service.recover_expired_leases(grace_seconds=0)
    assert recovered == 1
    task_after = store.committed[task_id]
    assert task_after.status == TaskStatus.RETRYING.value
    assert task_after.attempt_no == 2
    assert task_after.lease_token is None
    assert uow.outbox.get_by_task_attempt(task_id, 2).status == "PENDING"
    assert TaskEventType.FAILED.value not in [e[1] for e in stream.events.get(task_id, [])]

    # 迟到 attempt 的写入被条件更新拒绝：用恢复前真实租约 token（token 已被清空，fencing 依据是状态+attempt）
    with pytest.raises(LeaseConflictError):
        service.complete_task(task_id, 1, claimed.lease_token, _artifact(), ReportService(clock=FakeClock()))


def test_recover_expired_cancel_requested_finalizes_zombie():
    """取消在最后 progress 后到达：Worker 消失、租约过期 → 恢复作业收口 CANCELLED。"""
    service, uow, stream = _service()
    store = uow.store
    task_id = _create_task(service)
    uow.outbox.claim_due(FakeClock().now(), limit=10, lease_seconds=30, lease_token="dt")
    uow.commit()
    service.confirm_outbox_published(uow.outbox.get_by_task_attempt(task_id, 1).id, "dt", trace_id=None)
    service.claim_for_execution(task_id, 1, worker_id="w1")
    service.request_cancel(task_id)  # RUNNING → CANCEL_REQUESTED（租约保留）
    assert store.committed[task_id].status == TaskStatus.CANCEL_REQUESTED.value
    task = store.committed[task_id]
    task.lease_expires_at = FakeClock().now() - timedelta(seconds=1)

    recovered = service.recover_expired_leases(grace_seconds=0)
    assert recovered == 1
    after = store.committed[task_id]
    assert after.status == TaskStatus.CANCELLED.value
    assert after.lease_token is None
    assert uow.outbox.get_by_task_attempt(task_id, 1).status in ("CANCELLED", "PUBLISHED")
    assert stream.events[task_id][-1][1] == TaskEventType.CANCELLED.value


def test_recover_expired_running_exhausts_to_failed():
    """Worker 确定性崩溃循环：attempt 达上限 → 恢复作业落 FAILED 并发布 failed，不再无限重试。"""
    service, uow, stream = _service()
    store = uow.store
    task_id = _create_task(service)
    uow.outbox.claim_due(FakeClock().now(), limit=10, lease_seconds=30, lease_token="dt")
    uow.commit()
    service.confirm_outbox_published(uow.outbox.get_by_task_attempt(task_id, 1).id, "dt", trace_id=None)
    service.claim_for_execution(task_id, 1, worker_id="w1")
    task = store.committed[task_id]
    task.attempt_no = 3  # 已是最后一次尝试
    task.lease_expires_at = FakeClock().now() - timedelta(seconds=1)

    recovered = service.recover_expired_leases(grace_seconds=0)
    assert recovered == 1
    after = store.committed[task_id]
    assert after.status == TaskStatus.FAILED.value
    assert after.error_code == "LEASE_EXPIRED"
    assert stream.events[task_id][-1][1] == TaskEventType.FAILED.value
    assert uow.outbox.get_by_task_attempt(task_id, 4) is None  # 不再生成新 Outbox


def test_get_task_and_list_tasks():
    service, uow, _ = _service(events=False)
    store = uow.store
    task_id = _create_task(service)
    dto = service.get_task(task_id)
    assert dto.status is TaskStatus.PENDING
    assert dto.selected_layers == ("market", "sector", "stock")

    from backend.modules.analysis.application.contracts import TaskListQuery

    items, next_cursor = service.list_tasks(TaskListQuery(cursor=None, limit=20, status="all"))
    assert [i.id for i in items] == [task_id]
    assert next_cursor is None
    items, next_cursor = service.list_tasks(TaskListQuery(cursor=None, limit=20, status="succeeded"))
    assert items == []
    # 非法筛选值
    from backend.modules.analysis.application.errors import InvalidStateConflictError

    with pytest.raises(InvalidStateConflictError):
        service.list_tasks(TaskListQuery(cursor=None, limit=20, status="bogus"))
