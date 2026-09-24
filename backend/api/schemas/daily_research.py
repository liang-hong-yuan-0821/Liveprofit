"""Daily research batches: compact list rows and frozen report detail."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from backend.api.schemas.tasks import TaskStatusValue

DailyResearchKind = Literal["news", "quant"]


class DailyResearchManualRequest(BaseModel):
    kind: DailyResearchKind


class DailyResearchRunSummary(BaseModel):
    task_id: UUID
    kind: DailyResearchKind
    trigger: Literal["scheduled", "manual"]
    slot: str | None = None
    status: TaskStatusValue
    report_status: str | None = None
    target_trade_date: date | None = None
    scheduled_at: datetime | None = None
    news_cutoff_at: datetime | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime
    attempt_no: int
    error_code: str | None = None
    error_summary: str | None = None
    conclusion_summary: str | None = None
    has_report: bool = False


class DailyResearchRunListData(BaseModel):
    items: list[DailyResearchRunSummary]


class DailyResearchManualData(BaseModel):
    task_id: UUID
    kind: DailyResearchKind
    status: TaskStatusValue
    trigger: Literal["manual"] = "manual"
    idempotent_replay: bool = False


class DailyResearchRunDetail(BaseModel):
    run: DailyResearchRunSummary
    report: dict[str, Any] | None = None
    report_version: int | None = None
    report_generated_at: datetime | None = None


class DailyResearchRunDetailData(BaseModel):
    item: DailyResearchRunDetail | None = None


class DisputedAssessment(BaseModel):
    assessment_id: UUID
    news_id: UUID
    event_id: int
    fact_key: str
    revision: int
    novelty: Literal["new", "update"]
    review_status: Literal["disputed"]
    labels: dict[str, Any]
    evidence: list[dict[str, Any]]
    available_at: datetime
    title: str
    raw_content: str
    source: str
    source_label: str | None = None
    source_url: str | None = None


class DisputedAssessmentListData(BaseModel):
    items: list[DisputedAssessment]


class AssessmentReviewRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    review_status: Literal["accepted", "rejected", "retracted"]
    labels: dict[str, Any] | None = None
    review_note: str = Field(min_length=1, max_length=2000)

    @field_validator("review_note")
    @classmethod
    def require_non_blank_note(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("review_note 不能为空")
        return value


class AssessmentReviewData(BaseModel):
    assessment_id: UUID
    event_id: int
    fact_key: str
    revision: int
    review_status: Literal["accepted", "rejected", "retracted"]
    task_id: UUID | None = None
    task_status: TaskStatusValue
    idempotent_replay: bool = False


class AssessmentReviewResponseData(BaseModel):
    item: AssessmentReviewData
