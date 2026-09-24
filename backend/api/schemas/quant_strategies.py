"""量化策略 API schema（plan 4.1.1/4.4.1）。

源码可见性原则：仅草稿读取/保存合同（QuantStrategyDraftDTO）可携带 source_code；
列表、详情、发布审计一律不含源码。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class QuantStrategyVersionDTO(BaseModel):
    id: UUID
    strategy_id: UUID
    version_no: int
    status: str
    source_hash: str
    template_id: str | None = None
    template_params: dict | None = None
    template_renderer_version: str | None = None
    lifecycle_policy_version_id: UUID | None = None
    published_at: datetime | None
    archived_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class QuantStrategyDTO(BaseModel):
    id: UUID
    name: str
    description: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    versions: list[QuantStrategyVersionDTO]


class QuantStrategyListData(BaseModel):
    items: list[QuantStrategyDTO]


class QuantStrategyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=4096)
    source_code: str = Field(min_length=1, max_length=12 * 1024)
    template_id: str | None = Field(default=None, max_length=64)
    template_params: dict | None = None

    @field_validator("source_code")
    @classmethod
    def require_source(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("新建策略必须输入代码")
        if len(value.encode("utf-8")) > 12 * 1024:
            raise ValueError("策略源码不能超过 12 KiB")
        return value


class QuantStrategyDraftDTO(BaseModel):
    """当前草稿（唯一可返回 source_code 的合同）。"""

    id: UUID
    strategy_id: UUID
    version_no: int
    status: str
    source_code: str
    source_hash: str
    template_id: str | None = None
    template_params: dict | None = None
    template_renderer_version: str | None = None
    lifecycle_policy_version_id: UUID | None = None
    version: int
    created_at: datetime
    updated_at: datetime


class QuantStrategyDraftUpdateRequest(BaseModel):
    """草稿保存：元数据 + 源码，一个原子请求（plan 4.2.1 风格）。"""

    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=4096)
    source_code: str = Field(max_length=12 * 1024)
    expected_strategy_version: int = Field(ge=1)
    expected_draft_version: int = Field(ge=1)


class QuantStrategyFormatRequest(BaseModel):
    """代码格式化：本地 ruff 格式化（前端编辑器「格式化」按钮）。"""

    source_code: str = Field(max_length=12 * 1024)


class QuantStrategyFormatData(BaseModel):
    source_code: str


class QuantStrategyPublishRequest(BaseModel):
    expected_version: int = Field(ge=1)


class LifecyclePolicyBindingRequest(BaseModel):
    lifecycle_policy_version_id: UUID | None
    expected_version: int = Field(ge=1)


class QuantStrategyPublishData(BaseModel):
    published: QuantStrategyVersionDTO
    next_draft: QuantStrategyDraftDTO


class QuantStrategyTemplateDTO(BaseModel):
    template_id: str
    display_name: str
    description: str
    required_bars: int
    wave: int
    renderer_version: str
    default_params: dict
    source_code: str


class QuantStrategyTemplateListData(BaseModel):
    items: list[QuantStrategyTemplateDTO]
