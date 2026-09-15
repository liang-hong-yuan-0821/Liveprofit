"""事件研究审核 Schema（方案：事件研究审核界面平台集成方案 3.1.1）。"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

# 宽松上限：仅防超大 payload（评审 m18）。业务字段长度按**行级**校验
# （服务层 _validate_row_lengths，与 events 表列宽一致，失败 → 该行
# REVIEW_ROW_FAILED，同批其余行照常提交），Schema 级 max_length 一旦命中
# 是整批 422，会让单个超长字段拖垮同批合法行。
_FIELD_HARD_MAX = 10_000


class ReviewRowRequest(BaseModel):
    draft_id: int
    action: Literal["approve", "ignore"]
    # 不用 Literal 约束类型/条件取值——Streamlit Selectbox 选项是 UI 层约束
    event_type: str | None = Field(default=None, max_length=_FIELD_HARD_MAX)
    event_subtype: str | None = Field(default=None, max_length=_FIELD_HARD_MAX)
    event_condition: str | None = Field(default=None, max_length=_FIELD_HARD_MAX)
    importance: int | None = Field(default=None, ge=1, le=5)
    expected_value: float | None = None
    actual_value: float | None = None
    previous_value: float | None = None
    # 三级路由（方案第三章）：作用域与目标引用。同样不用 Literal 约束
    # （scope 非法走服务层行级校验 → REVIEW_ROW_FAILED，草稿保留可修正重提；
    # 422 会整批失败，不符合"行级失败"语义）；缺省 None → market + []
    event_scope: str | None = Field(default=None, max_length=_FIELD_HARD_MAX)
    affected_scope_refs: list[Annotated[str, Field(max_length=_FIELD_HARD_MAX)]] | None = Field(
        default=None, max_length=200
    )
    # 宽松上限（评审 m18 残留）：业务 64 上限在服务层行级 `_ROW_TEXT_LIMITS`
    # 判定（超长 → 该行 REVIEW_ROW_FAILED），Schema 级 max_length=64 会让
    # 单行超长 operator 拖垮整批 422
    operator: str = Field(default="admin", max_length=_FIELD_HARD_MAX)


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
