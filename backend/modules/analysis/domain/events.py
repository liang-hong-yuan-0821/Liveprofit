"""任务业务事件（SSE 六种业务事件；§2.4 / §3.1.4 字段契约）。

事件仅在对应 PostgreSQL 事实提交后由 TaskEventService 发布；
RETRYING 没有对应事件（新 attempt 确认投递后以新 queued 重新进入流）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from backend.modules.analysis.domain.enums import TaskEventType


@dataclass(frozen=True)
class BusinessTaskEvent:
    event_type: TaskEventType
    task_id: uuid.UUID
    attempt_no: int
    occurred_at: datetime
    sequence: int | None = None  # progress 专用：严格递增业务序号
    phase: str | None = None  # progress 专用：market/sector/stock/decision 等稳定阶段
    message: str | None = None
    worker_id: str | None = None  # started
    report_id: uuid.UUID | None = None  # completed
    duration_ms: int | None = None  # completed
    error_code: str | None = None  # failed（不可含 retryable=true）
    extra: dict = field(default_factory=dict)

    def payload(self) -> dict:
        """SSE data 固定公共字段 + 事件专属字段（schema_version 字面量 "v1"）。"""
        payload: dict = {
            "task_id": str(self.task_id),
            "attempt_no": self.attempt_no,
            "occurred_at": self.occurred_at.isoformat(),
            "schema_version": "v1",
        }
        if self.event_type is TaskEventType.PROGRESS:
            payload.update({"sequence": self.sequence, "phase": self.phase, "message": self.message})
        elif self.event_type is TaskEventType.STARTED:
            payload.update({"worker_id": self.worker_id, "message": self.message})
        elif self.event_type is TaskEventType.COMPLETED:
            payload.update(
                {
                    "report_id": str(self.report_id) if self.report_id else None,
                    "duration_ms": self.duration_ms,
                    "message": self.message,
                }
            )
        elif self.event_type is TaskEventType.FAILED:
            payload.update({"error_code": self.error_code, "message": self.message})
        elif self.event_type is TaskEventType.CANCELLED:
            payload.update({"message": self.message})
        payload.update(self.extra)
        return payload
