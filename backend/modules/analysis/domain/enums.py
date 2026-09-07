"""analysis 领域枚举（OpenAPI v1 冻结取值；前端经由 OpenAPI 生成，不手写）。"""

from __future__ import annotations

import enum


class TaskType(str, enum.Enum):
    SINGLE_STOCK = "SINGLE_STOCK"
    MARKET_WIDE = "MARKET_WIDE"


class TaskStatus(str, enum.Enum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRYING = "RETRYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"


class OutboxStatus(str, enum.Enum):
    PENDING = "PENDING"
    DISPATCHING = "DISPATCHING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AnalysisLayer(str, enum.Enum):
    """复用 TradingAgentsGraph.selectedLayer 的既有值。"""

    MARKET = "market"
    SECTOR = "sector"
    STOCK = "stock"
    SCREENING = "screening"
    POSITION = "position"


class TaskEventType(str, enum.Enum):
    """SSE 白名单：六种业务事件（持久化）+ 两种控制事件（不持久化）。"""

    QUEUED = "queued"
    STARTED = "started"
    PROGRESS = "progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RESET = "reset"
    HEARTBEAT = "heartbeat"


class ReportBlock(str, enum.Enum):
    MARKET = "market"
    SECTOR = "sector"
    STOCK = "stock"
    DECISION = "decision"


class ReportSectionStatus(str, enum.Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_REQUESTED = "NOT_REQUESTED"


TERMINAL_STATUSES = frozenset({TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED})
ACTIVE_STATUSES = frozenset(
    {
        TaskStatus.PENDING,
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        TaskStatus.RETRYING,
        TaskStatus.CANCEL_REQUESTED,
    }
)
BUSINESS_EVENT_TYPES = frozenset(
    {
        TaskEventType.QUEUED,
        TaskEventType.STARTED,
        TaskEventType.PROGRESS,
        TaskEventType.COMPLETED,
        TaskEventType.FAILED,
        TaskEventType.CANCELLED,
    }
)
