"""应用层契约：Command、Result、DTO 形状（§3.1.3）。

这些是应用层数据类型；HTTP Schema 在 backend/api/schemas/（T5）另行定义并映射。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from backend.modules.analysis.domain.enums import TaskStatus, TaskType


@dataclass(frozen=True)
class CreateAnalysisTaskCommand:
    task_type: TaskType
    ticker: str | None
    requested_trade_date: date | None
    selected_layers: tuple[str, ...]
    analysis_options: dict = field(default_factory=dict)
    trace_id: str | None = None


@dataclass(frozen=True)
class TaskCreatedResult:
    task_id: uuid.UUID
    status: TaskStatus
    requested_trade_date: date | None
    effective_trade_date: date | None
    date_correction: str | None
    idempotent_replay: bool = False  # 同 key 同 hash 返回原任务时为 True


@dataclass(frozen=True)
class ClaimedTask:
    task_id: uuid.UUID
    attempt_no: int
    lease_token: str
    task_type: TaskType
    ticker: str | None
    selected_layers: tuple[str, ...]
    effective_trade_date: date | None
    request_params: dict


@dataclass(frozen=True)
class ClassifiedError:
    """Worker 层分类后的执行错误（§3.1.4）：retryable 仅描述业务语义。"""

    code: str
    message: str
    retryable: bool


@dataclass(frozen=True)
class AnalysisArtifact:
    """TradingGraphAdapter 产出（T4/T6）：结构化报告 + 摘要投影 + 产物元数据。"""

    report_json: dict
    conclusion_summary: str | None
    risk_flag: bool
    risk_hint: str | None
    decision: dict | None
    artifact_uri: str | None
    checksum: str | None
    duration_ms: int | None


@dataclass(frozen=True)
class ReportDTO:
    report_id: uuid.UUID
    task_id: uuid.UUID
    attempt_no: int
    report_version: int
    schema_version: str
    generated_at: datetime
    report_json: dict
    conclusion_summary: str | None
    risk_flag: bool
    risk_hint: str | None
    has_report: bool


@dataclass(frozen=True)
class DashboardAttentionItemDTO:
    kind: str  # FAILED_TASK / REPORT_SECTION_UNAVAILABLE
    task_id: uuid.UUID
    task_type: TaskType
    ticker: str | None
    effective_trade_date: date | None
    updated_at: datetime
    error_code: str | None
    error_summary: str | None
    unavailable_blocks: list[dict] | None
    retryable: bool


@dataclass(frozen=True)
class DashboardActiveTaskDTO:
    task_id: uuid.UUID
    task_type: TaskType
    ticker: str | None
    effective_trade_date: date | None
    status: TaskStatus
    attempt_no: int
    updated_at: datetime
    next_retry_at: datetime | None


@dataclass(frozen=True)
class DashboardConclusionDTO:
    task_id: uuid.UUID
    task_type: TaskType
    ticker: str | None
    effective_trade_date: date | None
    completed_at: datetime
    conclusion_summary: str | None
    risk_flag: bool
    risk_hint: str | None
    has_report: bool
    updated_at: datetime


@dataclass(frozen=True)
class AnalysisDashboardDTO:
    pending_actions: list[DashboardAttentionItemDTO]
    active_tasks: list[DashboardActiveTaskDTO]
    recent_conclusions: list[DashboardConclusionDTO]
    generated_at: datetime


@dataclass(frozen=True)
class TaskListItemDTO:
    id: uuid.UUID
    task_type: TaskType
    ticker: str | None
    effective_trade_date: date | None
    status: TaskStatus
    attempt_no: int
    error_code: str | None
    error_summary: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class TaskDTO:
    id: uuid.UUID
    task_type: TaskType
    ticker: str | None
    requested_trade_date: date | None
    effective_trade_date: date | None
    date_correction: str | None
    selected_layers: tuple[str, ...]
    status: TaskStatus
    attempt_no: int
    next_retry_at: datetime | None
    error_code: str | None
    error_summary: str | None
    created_at: datetime
    updated_at: datetime
    # events_url / report_url 由 Router 从 data.id 派生（T5），不在此携带


@dataclass(frozen=True)
class TaskListQuery:
    cursor: tuple[datetime, uuid.UUID] | None
    limit: int
    status: str  # all/active/succeeded/failed/cancelled
