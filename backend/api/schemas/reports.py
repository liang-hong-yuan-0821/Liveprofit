"""报告 Schema（§2.6 / §3.1.5：区块三态、数据来源与风险说明）。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from backend.api.schemas.tasks import TaskTypeValue

BlockStatus = Literal["AVAILABLE", "UNAVAILABLE", "NOT_REQUESTED"]
ReportBlockName = Literal["market", "sector", "stock", "decision"]


class ReportTaskInfo(BaseModel):
    task_id: UUID
    task_type: TaskTypeValue
    ticker: str | None
    effective_trade_date: date | None
    duration_ms: int | None = None


class ReportSectionDTO(BaseModel):
    block: ReportBlockName
    status: BlockStatus
    title: str | None = None
    summary: str | None = None
    content: str | None = None
    charts: list[dict] | None = None
    unavailable_reason: str | None = None
    retryable: bool | None = None


class DataSourceDTO(BaseModel):
    label: str
    source: str
    as_of: str | None = None


class ReportDTO(BaseModel):
    schema_version: str
    report_version: int
    generated_at: datetime
    task: ReportTaskInfo
    sections: list[ReportSectionDTO]
    data_sources: list[DataSourceDTO] | None = None
    risk_note: str | None = None
