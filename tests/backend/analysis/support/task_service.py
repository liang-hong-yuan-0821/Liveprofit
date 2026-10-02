"""Shared fixtures/builders for tests.backend.analysis.unit.test_task_service; no test cases."""

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
from tests.backend.analysis.support.fakes import (
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
    quant = "position" in layers
    result = service.create_task(
        CreateAnalysisTaskCommand(
            task_type=task_type,
            ticker=ticker if task_type is TaskType.SINGLE_STOCK else None,
            requested_trade_date=date(2026, 9, 4),
            selected_layers=layers,
            strategy_version_id=uuid.uuid4() if quant else None,
            portfolio_id=uuid.uuid4() if quant else None,
            expected_portfolio_version=1 if quant else None,
        ),
        idempotency_key=key,
        trace_id="trace-1",
    )
    return result.task_id
