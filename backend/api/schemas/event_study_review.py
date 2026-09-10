"""事件研究审核 Schema（方案：事件研究审核界面平台集成方案 3.1.1）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ReviewRowRequest(BaseModel):
    draft_id: int
    action: Literal["approve", "ignore"]
    # 不用 Literal 约束类型/条件取值——Streamlit Selectbox 选项是 UI 层约束
    event_type: str | None = Field(default=None, max_length=64)
    event_subtype: str | None = Field(default=None, max_length=64)
    event_condition: str | None = Field(default=None, max_length=32)
    importance: int | None = Field(default=None, ge=1, le=5)
    expected_value: float | None = None
    actual_value: float | None = None
    previous_value: float | None = None
    operator: str = Field(default="admin", max_length=64)


class ReviewBatchRequest(BaseModel):
    items: list[ReviewRowRequest] = Field(min_length=1, max_length=50)


class ReviewRowResult(BaseModel):
    draft_id: int
    ok: bool
    event_id: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    compute_status: Literal["ok", "failed", "skipped"] | None = None


class BatchSummary(BaseModel):
    approved: int
    ignored: int
    computed: int


class ReviewBatchData(BaseModel):
    results: list[ReviewRowResult]
    summary: BatchSummary


class PrelabelRequest(BaseModel):
    limit: int = Field(default=50, ge=1, le=200)


class PrelabelData(BaseModel):
    prelabeled: int
    remaining: int


class RefreshData(BaseModel):
    fetched: int
    new_drafts: int
    skipped_reason: Literal["locked", "failed"] | None = None


class ComputeRequest(BaseModel):
    operator: str = Field(default="admin", max_length=64)


class ComputeData(BaseModel):
    event_id: int
    status: Literal["ok", "failed"]
    message: str | None = None


class PendingEventDTO(BaseModel):
    draft_id: int
    title: str
    announced_at: str | None = None
    source: str | None = None
    content: str | None = None
    source_url: str | None = None
    importance_hint: int | None = None
    ai_suggestions: dict[str, Any] | None = None


class PendingEventListData(BaseModel):
    items: list[PendingEventDTO]


class ImpactDraftDTO(BaseModel):
    event_id: int
    title: str
    t0: str | None = None
    computed_at: str | None = None
    assets: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ImpactDraftListData(BaseModel):
    items: list[ImpactDraftDTO]


class ConfirmImpactsRequest(BaseModel):
    tickers: list[str] = Field(min_length=1)
    operator: str = Field(default="admin", max_length=64)


class ConfirmImpactsData(BaseModel):
    event_id: int
    inserted: int
