"""事件研究审核应用契约（冻结 dataclass，风格对齐 contracts.py）。

审核流复用 AI 侧 review_dao（零改动），本模块只定义平台内流转的命令/结果/DTO 形状。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReviewRowCommand:
    draft_id: int
    action: str  # "approve" | "ignore"（skip 是纯 UI 概念，不提交）
    event_type: str | None = None
    event_subtype: str | None = None
    event_condition: str | None = None
    importance: int | None = None  # None → 服务层省略键，委托 review_dao 默认链
    expected_value: float | None = None
    actual_value: float | None = None
    previous_value: float | None = None
    operator: str = "admin"


@dataclass(frozen=True)
class ReviewRowResult:
    draft_id: int
    ok: bool
    event_id: int | None = None
    error_code: str | None = None  # "REVIEW_DRAFT_NOT_FOUND" | "REVIEW_ROW_FAILED"（行级信息码，不抛 HTTP）
    error_message: str | None = None
    compute_status: str | None = None  # "ok" | "failed" | "skipped"（approve 行 ok/failed；ignore 行 skipped；失败行 None）


@dataclass(frozen=True)
class ReviewBatchResult:
    results: list[ReviewRowResult] = field(default_factory=list)
    approved: int = 0
    ignored: int = 0
    computed: int = 0


@dataclass(frozen=True)
class PrelabelResult:
    prelabeled: int
    remaining: int  # 执行后仍无 ai_suggestions 的草稿数（前端循环收敛依据）


@dataclass(frozen=True)
class RefreshResult:
    fetched: int  # 爬虫实际抓回条数（各源合并去重后）
    new_drafts: int  # 新写入 Redis 待审草稿数（PG + Redis 标题去重后）
    skipped_reason: str | None = None  # None=正常完成；"locked"=锁占用跳过；"failed"=采集/保存异常降级


@dataclass(frozen=True)
class ComputeResult:
    event_id: int
    status: str  # "ok" | "failed"
    message: str | None = None


@dataclass(frozen=True)
class ConfirmImpactsResult:
    event_id: int
    inserted: int


@dataclass(frozen=True)
class PendingEventDTO:
    draft_id: int
    title: str
    announced_at: str | None
    source: str | None
    content: str | None
    source_url: str | None
    importance_hint: int | None
    ai_suggestions: dict[str, Any] | None


@dataclass(frozen=True)
class ImpactDraftDTO:
    event_id: int
    title: str
    t0: str | None
    computed_at: str | None
    assets: dict[str, dict[str, Any]]
