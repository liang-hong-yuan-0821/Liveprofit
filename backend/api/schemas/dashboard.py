"""AI 投研看板聚合 DTO（§2.6.2：pending_actions/active_tasks/recent_conclusions 10/10/5）。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from backend.api.schemas.tasks import TaskTypeValue

DashboardKind = Literal["FAILED_TASK", "REPORT_SECTION_UNAVAILABLE"]
ReportBlock = Literal["market", "sector", "stock", "decision"]


class UnavailableBlockDTO(BaseModel):
    block: ReportBlock
    reason: str | None = None
    retryable: bool = False


class PendingActionDTO(BaseModel):
    kind: DashboardKind
    task_id: UUID
    task_type: TaskTypeValue
    ticker: str | None
    effective_trade_date: date | None
    updated_at: datetime
    error_code: str | None
    error_summary: str | None
    unavailable_blocks: list[UnavailableBlockDTO] | None
    retryable: bool


class ActiveTaskDTO(BaseModel):
    task_id: UUID
    task_type: TaskTypeValue
    ticker: str | None
    effective_trade_date: date | None
    status: Literal["PENDING", "QUEUED", "RUNNING", "RETRYING"]
    attempt_no: int
    updated_at: datetime
    next_retry_at: datetime | None


class RecentConclusionDTO(BaseModel):
    task_id: UUID
    task_type: TaskTypeValue
    ticker: str | None
    effective_trade_date: date | None
    completed_at: datetime
    conclusion_summary: str | None = Field(default=None, max_length=512)
    risk_flag: bool
    risk_hint: str | None
    has_report: bool
    updated_at: datetime


class AnalysisDashboardDTO(BaseModel):
    pending_actions: list[PendingActionDTO]
    active_tasks: list[ActiveTaskDTO]
    recent_conclusions: list[RecentConclusionDTO]
    generated_at: datetime
