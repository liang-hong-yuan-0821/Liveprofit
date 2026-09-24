"""quant_strategy DTO 合同（plan 4.1.1 / 4.4.1）。

源码可见性原则：草稿读取/保存 DTO 是唯一可返回 source_code 的合同；
列表、详情、发布审计、任务、报告一律不含源码。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from AI.strategy_sandbox.validator import StrategyValidationIssue


@dataclass(frozen=True)
class QuantStrategyVersionDTO:
    """版本摘要（列表/审计用，永不含源码）。"""

    id: uuid.UUID
    strategy_id: uuid.UUID
    version_no: int
    status: str
    source_hash: str
    template_id: str | None
    template_params: dict | None
    template_renderer_version: str | None
    lifecycle_policy_version_id: uuid.UUID | None
    published_at: datetime | None
    archived_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class QuantStrategyDTO:
    """策略摘要 + 版本历史（永不含源码）。"""

    id: uuid.UUID
    name: str
    description: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    versions: list[QuantStrategyVersionDTO] = field(default_factory=list)


@dataclass(frozen=True)
class QuantStrategyDraftDTO:
    """当前草稿（唯一可返回 source_code 的合同）。"""

    id: uuid.UUID
    strategy_id: uuid.UUID
    version_no: int
    status: str
    source_code: str
    source_hash: str
    template_id: str | None
    template_params: dict | None
    template_renderer_version: str | None
    lifecycle_policy_version_id: uuid.UUID | None
    version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class DraftSaveResult:
    """草稿保存结果：DTO 或校验问题（校验失败时 DTO 为 None）。"""

    draft: QuantStrategyDraftDTO | None
    validation_issues: list[StrategyValidationIssue] = field(default_factory=list)


@dataclass(frozen=True)
class PublishResult:
    """发布结果：新 PUBLISHED 版本与自动生成的下一版 DRAFT。"""

    published: QuantStrategyVersionDTO
    next_draft: QuantStrategyDraftDTO
