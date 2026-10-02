# test-catalog-begin
# {
#   "purpose": "AI 看板聚合单测（§2.6.2 / §5.1 P-5：10/10/5 上限、两类 kind、投影字段）。",
#   "keywords": [
#     "分析任务",
#     "接口",
#     "任务",
#     "dashboard",
#     "api",
#     "task"
#   ],
#   "covers": [
#     "backend/api/routers/analysis_tasks.py",
#     "backend/api/schemas/dashboard.py",
#     "backend/modules/analysis/application/contracts.py",
#     "backend/modules/analysis/application/reporting.py",
#     "backend/modules/analysis/application/task_events.py",
#     "backend/modules/analysis/application/task_lifecycle.py",
#     "backend/modules/analysis/domain/enums.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""AI 看板聚合单测（§2.6.2 / §5.1 P-5：10/10/5 上限、两类 kind、投影字段）。"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from backend.modules.analysis.application.contracts import (
    AnalysisArtifact,
    ClassifiedError,
    CreateAnalysisTaskCommand,
)
from backend.modules.analysis.application.reporting import ReportService
from backend.modules.analysis.application.task_events import TaskEventService
from backend.modules.analysis.application.task_lifecycle import TaskService
from backend.modules.analysis.domain.enums import TaskStatus, TaskType
from tests.backend.analysis.support.fakes import FakeCalendar, FakeClock, FakeStream, FakeUnitOfWork


def _artifact(sections: list[dict], **kwargs) -> AnalysisArtifact:
    defaults = dict(
        report_json={"sections": sections},
        conclusion_summary=None,
        risk_flag=False,
        risk_hint=None,
        decision=None,
        artifact_uri=None,
        checksum=None,
        duration_ms=100,
    )
    defaults.update(kwargs)
    return AnalysisArtifact(**defaults)


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
    return service, uow


def _run_to_success(service, uow, task_id: uuid.UUID, sections: list[dict], **artifact_kwargs):
    report_service = ReportService(clock=FakeClock())
    uow.outbox.claim_due(FakeClock().now(), limit=10, lease_seconds=30, lease_token="dt")
    uow.commit()
    service.confirm_outbox_published(uow.outbox.get_by_task_attempt(task_id, 1).id, "dt", trace_id=None)
    claimed = service.claim_for_execution(task_id, 1, worker_id="w1")
    service.complete_task(task_id, 1, claimed.lease_token, _artifact(sections, **artifact_kwargs), report_service)


def _run_to_failed(service, uow, task_id: uuid.UUID):
    uow.outbox.claim_due(FakeClock().now(), limit=10, lease_seconds=30, lease_token="dt")
    uow.commit()
    service.confirm_outbox_published(uow.outbox.get_by_task_attempt(task_id, 1).id, "dt", trace_id=None)
    claimed = service.claim_for_execution(task_id, 1, worker_id="w1")
    service.fail_or_retry(task_id, 1, claimed.lease_token, ClassifiedError("PROVIDER_UNAVAILABLE", "上游不可用", False))


def _make_task(service, ticker: str = "000001.SZ") -> uuid.UUID:
    return service.create_task(
        CreateAnalysisTaskCommand(
            task_type=TaskType.SINGLE_STOCK,
            ticker=ticker,
            requested_trade_date=date(2026, 9, 4),
            selected_layers=("market", "sector", "stock"),
        ),
        idempotency_key=None,
        trace_id=None,
    ).task_id


def test_dashboard_failed_task_becomes_failed_kind_item():
    service, uow = _service()
    store = uow.store
    task_id = _make_task(service)
    _run_to_failed(service, uow, task_id)

    dashboard = service.get_dashboard()
    assert len(dashboard.pending_actions) == 1
    item = dashboard.pending_actions[0]
    assert item.kind == "FAILED_TASK"
    assert item.task_id == task_id
    assert item.selected_layers == ["market", "sector", "stock"]
    assert item.error_code == "PROVIDER_UNAVAILABLE"
    assert item.error_summary == "上游不可用"
    assert item.unavailable_blocks is None
    assert item.retryable is False
    assert dashboard.active_tasks == []


def test_dashboard_unavailable_sections_aggregate_one_item_per_task():
    service, uow = _service()
    store = uow.store
    task_id = _make_task(service)
    _run_to_success(
        service,
        uow,
        task_id,
        sections=[
            {"block": "market", "status": "AVAILABLE"},
            {"block": "sector", "status": "UNAVAILABLE", "unavailable_reason": "数据源失败", "retryable": True},
            {"block": "stock", "status": "UNAVAILABLE", "unavailable_reason": "样本不足", "retryable": False},
            {"block": "decision", "status": "NOT_REQUESTED"},
        ],
    )

    dashboard = service.get_dashboard()
    assert len(dashboard.pending_actions) == 1  # 同任务多区块不可用聚合为一条
    item = dashboard.pending_actions[0]
    assert item.kind == "REPORT_SECTION_UNAVAILABLE"
    assert {b["block"] for b in item.unavailable_blocks} == {"sector", "stock"}
    assert item.error_code is None
    assert item.retryable is True  # 任一区块可重试


def test_dashboard_not_requested_and_cancelled_not_in_attention():
    service, uow = _service()
    store = uow.store
    task_id = _make_task(service)
    _run_to_success(
        service, uow, task_id,
        sections=[
            {"block": "market", "status": "NOT_REQUESTED"},
            {"block": "decision", "status": "AVAILABLE"},
        ],
    )
    dashboard = service.get_dashboard()
    assert dashboard.pending_actions == []  # NOT_REQUESTED 不进入待处理

    task2 = _make_task(service, ticker="600000.SH")
    service.request_cancel(task2)
    dashboard = service.get_dashboard()
    assert dashboard.pending_actions == []  # CANCELLED 不进入待处理


def test_dashboard_active_tasks_excludes_terminal_and_cancel_requested():
    service, uow = _service()
    store = uow.store
    task_id = _make_task(service)
    uow.outbox.claim_due(FakeClock().now(), limit=10, lease_seconds=30, lease_token="dt")
    uow.commit()
    service.confirm_outbox_published(uow.outbox.get_by_task_attempt(task_id, 1).id, "dt", trace_id=None)
    service.claim_for_execution(task_id, 1, worker_id="w1")

    dashboard = service.get_dashboard()
    assert [t.task_id for t in dashboard.active_tasks] == [task_id]
    assert dashboard.active_tasks[0].status is TaskStatus.RUNNING
    assert dashboard.active_tasks[0].attempt_no == 1
    assert dashboard.active_tasks[0].selected_layers == ["market", "sector", "stock"]

    service.request_cancel(task_id)  # RUNNING → CANCEL_REQUESTED：不再属于 active_tasks
    dashboard = service.get_dashboard()
    assert dashboard.active_tasks == []


def test_dashboard_recent_conclusions_projection():
    service, uow = _service()
    store = uow.store
    task_id = _make_task(service)
    _run_to_success(
        service, uow, task_id,
        sections=[{"block": "decision", "status": "AVAILABLE"}],
        conclusion_summary="买入评级，目标价上调",
        risk_flag=True,
        risk_hint="注意流动性风险",
    )

    dashboard = service.get_dashboard()
    assert len(dashboard.recent_conclusions) == 1
    item = dashboard.recent_conclusions[0]
    assert item.selected_layers == ["market", "sector", "stock"]
    assert item.conclusion_summary == "买入评级，目标价上调"
    assert item.risk_flag is True
    assert item.risk_hint == "注意流动性风险"
    assert item.has_report is True


def test_dashboard_conclusion_summary_null_when_not_reliable():
    service, uow = _service()
    store = uow.store
    task_id = _make_task(service)
    _run_to_success(
        service, uow, task_id,
        sections=[{"block": "decision", "status": "AVAILABLE"}],
        conclusion_summary=None,
    )
    dashboard = service.get_dashboard()
    assert dashboard.recent_conclusions[0].conclusion_summary is None


def test_dashboard_pending_actions_capped_and_sorted():
    service, uow = _service()
    store = uow.store
    task_ids = [_make_task(service, ticker=f"{i:06d}.SZ") for i in range(15)]
    for task_id in task_ids:
        _run_to_failed(service, uow, task_id)
    dashboard = service.get_dashboard()
    assert len(dashboard.pending_actions) == 10  # 上限 10
    updates = [item.updated_at for item in dashboard.pending_actions]
    assert updates == sorted(updates, reverse=True)


def test_dashboard_recent_conclusions_capped_at_five():
    service, uow = _service()
    store = uow.store
    for i in range(8):
        task_id = _make_task(service, ticker=f"{i:06d}.SZ")
        _run_to_success(service, uow, task_id, sections=[{"block": "decision", "status": "AVAILABLE"}])
    dashboard = service.get_dashboard()
    assert len(dashboard.recent_conclusions) == 5


def test_recent_conclusion_carries_unavailable_blocks_without_pending_lookup():
    service, uow = _service()
    task_id = _make_task(service)
    _run_to_success(service, uow, task_id, sections=[
        {"block": "market", "status": "UNAVAILABLE", "unavailable_reason": "missing", "retryable": True},
        {"block": "stock", "status": "NOT_REQUESTED"},
    ])
    # Dashboard conclusions must not depend on the limited pending list.
    uow.tasks.list_succeeded_with_unavailable_sections = lambda **kwargs: []
    dashboard = service.get_dashboard()
    assert dashboard.pending_actions == []
    item = dashboard.recent_conclusions[0]
    assert item.unavailable_blocks == [{"block": "market", "reason": "missing", "retryable": True}]


def test_unavailable_blocks_tolerate_malformed_historical_reports():
    from types import SimpleNamespace
    from backend.modules.analysis.application.task_lifecycle import _unavailable_blocks
    for payload in (None, [], "bad", {"sections": {}}, {"sections": None}):
        assert _unavailable_blocks(SimpleNamespace(report_json=payload)) == []
    assert _unavailable_blocks(SimpleNamespace(report_json={"sections": [
        None, "bad", {"block": "invalid", "status": "UNAVAILABLE"},
        {"block": [], "status": "UNAVAILABLE"},
        {"block": "market", "status": "UNAVAILABLE", "unavailable_reason": {"bad": 1}, "retryable": "true"},
    ]})) == [{"block": "market", "reason": None, "retryable": False}]


def test_task_list_selected_layers_and_api_defaults():
    from backend.modules.analysis.application.contracts import TaskListQuery
    from backend.api.routers.analysis_tasks import _to_list_item
    from backend.api.schemas.dashboard import RecentConclusionDTO
    service, _ = _service()
    _make_task(service)
    rows, _ = service.list_tasks(TaskListQuery(cursor=None, limit=20, status="all"))
    assert _to_list_item(rows[0]).selected_layers == ["market", "sector", "stock"]
    assert RecentConclusionDTO.model_fields["unavailable_blocks"].default_factory() == []
