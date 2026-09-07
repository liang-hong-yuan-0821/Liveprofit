"""SSE payload Schema（§2.6 / §3.2.3：六业务事件 + reset/heartbeat 控制帧，均纳入 OpenAPI）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SSE_SCHEMA_VERSION = "v1"

BusinessEventName = Literal["queued", "started", "progress", "completed", "failed", "cancelled"]


class BusinessEventData(BaseModel):
    """六种业务帧 data 的公共字段 + 事件专属可选字段。"""

    task_id: str
    attempt_no: int = Field(gt=0)
    occurred_at: datetime
    schema_version: Literal["v1"] = SSE_SCHEMA_VERSION
    # progress
    sequence: int | None = None
    phase: Literal["market", "sector", "stock", "decision", "queue", "finish"] | None = None
    message: str | None = None
    # started
    worker_id: str | None = None
    # completed
    report_id: str | None = None
    duration_ms: int | None = None
    # failed
    error_code: str | None = None


class ResetEventData(BaseModel):
    """reset 控制帧 data（固定且仅此四字段，无 id）。"""

    task_url: str
    earliest_event_id: str
    occurred_at: datetime
    schema_version: Literal["v1"] = SSE_SCHEMA_VERSION


class HeartbeatEventData(BaseModel):
    """heartbeat 控制帧 data（固定且仅此三字段，无 id）。"""

    connection_id: str
    sent_at: datetime
    schema_version: Literal["v1"] = SSE_SCHEMA_VERSION


class SSEContract(BaseModel):
    """SSE 协议帧集合（OpenAPI 文档用途：运行时为 text/event-stream 分帧）。"""

    business: BusinessEventData
    reset: ResetEventData
    heartbeat: HeartbeatEventData
