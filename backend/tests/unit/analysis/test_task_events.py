"""TaskEventService 校验与终态补写单测。"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from backend.modules.analysis.application.contracts import (
    AnalysisArtifact,
    ClassifiedError,
    CreateAnalysisTaskCommand,
)
from backend.modules.analysis.application.errors import InvalidStateConflictError
from backend.modules.analysis.application.reporting import ReportService
from backend.modules.analysis.application.task_events import TaskEventService
from backend.modules.analysis.application.task_lifecycle import TaskService
from backend.modules.analysis.domain.enums import TaskEventType, TaskStatus, TaskType
from backend.tests.unit.analysis.fakes import FakeCalendar, FakeClock, FakeStream, FakeUnitOfWork


def _service():
    uow = FakeUnitOfWork()
    stream = FakeStream()
    events = TaskEventService(uow, clock=FakeClock(), stream=stream)
    service = TaskService(
        uow,
        clock=FakeClock(),
        calendar=FakeCalendar(),
        events=events,
        lease_ttl_seconds=120,
        retry=type("Retry", (), {"max_retry_attempts": 3, "retry_base_delay_seconds": 60})(),
    )
    return service, uow, stream, events


def _create_and_queue(service, uow) -> uuid.UUID:
    task_id = service.create_task(
        CreateAnalysisTaskCommand(
            task_type=TaskType.SINGLE_STOCK,
            ticker="000001.SZ",
            requested_trade_date=date(2026, 9, 4),
            selected_layers=("market", "sector", "stock"),
        ),
        idempotency_key=None,
        trace_id=None,
    ).task_id
    uow.outbox.claim_due(FakeClock().now(), limit=10, lease_seconds=30, lease_token="dt")
    uow.commit()
    service.confirm_outbox_published(uow.outbox.get_by_task_attempt(task_id, 1).id, "dt", trace_id=None)
    return task_id


def test_publish_requires_matching_status_and_attempt():
    service, uow, _, events = _service()
    store = uow.store
    task_id = _create_and_queue(service, uow)

    # started 必须先 RUNNING
    with pytest.raises(InvalidStateConflictError):
        events.publish(task_id, TaskEventType.STARTED, attempt_no=1)
    # attempt 不匹配
    with pytest.raises(InvalidStateConflictError):
        events.publish(task_id, TaskEventType.QUEUED, attempt_no=2)


def test_failed_event_requires_failed_status():
    service, uow, stream, events = _service()
    store = uow.store
    task_id = _create_and_queue(service, uow)
    claimed = service.claim_for_execution(task_id, 1, worker_id="w1")

    # FAILED 提交前禁止发布 failed
    with pytest.raises(InvalidStateConflictError):
        events.publish(task_id, TaskEventType.FAILED, attempt_no=1, error_code="X", message="x")
    # FAILED 提交后可以发布（服务内已发布一次；此处直接补验合法性）
    service.fail_or_retry(task_id, 1, claimed.lease_token, ClassifiedError("FATAL", "不可重试", False))
    assert stream.events[task_id][-1][1] == TaskEventType.FAILED.value
    assert "retryable" not in stream.events[task_id][-1][2]


def test_retrying_publishes_no_events():
    service, uow, stream, _ = _service()
    store = uow.store
    task_id = _create_and_queue(service, uow)
    claimed = service.claim_for_execution(task_id, 1, worker_id="w1")
    service.fail_or_retry(task_id, 1, claimed.lease_token, ClassifiedError("RATE_LIMITED", "限流", True))
    # 只有 queued（attempt 1 投递确认时），无 failed / 无重试专用事件
    assert [e[1] for e in stream.events[task_id]] == [TaskEventType.QUEUED.value]


def test_publish_swallows_stream_failure():
    """§2.4：写 Stream 失败不回滚 PG、不影响任务结局（终态帧由回放时幂等补写兜底）。"""
    service, uow, stream, events = _service()
    task_id = _create_and_queue(service, uow)
    service.claim_for_execution(task_id, 1, worker_id="w1")  # → RUNNING（started 事件前置状态）
    stream.fail_append = True
    result = events.publish(task_id, TaskEventType.STARTED, attempt_no=1, worker_id="w1")
    assert result == ""  # 不抛异常
    # 校验类错误仍抛出（协议一致性不可吞）
    with pytest.raises(InvalidStateConflictError):
        events.publish(task_id, TaskEventType.FAILED, attempt_no=1, error_code="X", message="x")


def test_ensure_terminal_business_event_backfills_and_is_idempotent():
    service, uow, stream, events = _service()
    store = uow.store
    task_id = _create_and_queue(service, uow)
    claimed = service.claim_for_execution(task_id, 1, worker_id="w1")
    # 直接置终态但不发布事件（模拟 PG 已终态而 Stream 缺失终态帧）
    store.committed[task_id].status = TaskStatus.FAILED.value
    store.committed[task_id].error_code = "FATAL"
    store.committed[task_id].error_summary = "补写场景"

    stream_id = events.ensure_terminal_business_event(task_id)
    assert stream_id is not None
    assert stream.events[task_id][-1][1] == TaskEventType.FAILED.value
    assert stream.events[task_id][-1][2]["error_code"] == "FATAL"
    # 已存在规范终态帧：幂等跳过
    assert events.ensure_terminal_business_event(task_id) is None

    # 非终态任务：无需补写
    other = _create_and_queue(service, uow)
    assert events.ensure_terminal_business_event(other) is None


def test_ensure_terminal_completed_uses_latest_report():
    service, uow, stream, events = _service()
    store = uow.store
    task_id = _create_and_queue(service, uow)
    claimed = service.claim_for_execution(task_id, 1, worker_id="w1")
    report = service.complete_task(
        task_id,
        1,
        claimed.lease_token,
        AnalysisArtifact(
            report_json={"sections": []},
            conclusion_summary=None,
            risk_flag=False,
            risk_hint=None,
            decision=None,
            artifact_uri=None,
            checksum=None,
            duration_ms=1234,
        ),
        ReportService(clock=FakeClock()),
    )
    # 删除 completed 帧模拟缺失
    stream.events[task_id].pop()
    stream_id = events.ensure_terminal_business_event(task_id)
    assert stream_id is not None
    payload = stream.events[task_id][-1][2]
    assert payload["report_id"] == str(report.report_id)
