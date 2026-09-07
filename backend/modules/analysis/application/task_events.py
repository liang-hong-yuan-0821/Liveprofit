"""TaskEventService：在状态提交后追加事件、终态业务事件幂等补写（§3.1.3）。

- 只接收六种业务事件；在对应 PostgreSQL 状态已提交后校验统一任务字段。
- failed 需额外校验 Task 已是 FAILED；写 Stream 失败记录告警但不回滚 PG。
- ensure_terminal_business_event：PG 已终态而 Stream 缺少对应业务终态帧时，
  基于已提交的 Task/Report 数据幂等补写（SSE Router / reconciliation 使用）。
"""

from __future__ import annotations

import logging
import uuid

from backend.modules.analysis.application.errors import InvalidStateConflictError, TaskNotFoundError
from backend.modules.analysis.domain.enums import BUSINESS_EVENT_TYPES, TaskEventType, TaskStatus
from backend.modules.analysis.domain.events import BusinessTaskEvent
from backend.modules.analysis.domain.ports import Clock, SystemClock, TaskEventStreamPort

logger = logging.getLogger(__name__)

_TERMINAL_EVENT_BY_STATUS = {
    TaskStatus.SUCCEEDED: TaskEventType.COMPLETED,
    TaskStatus.FAILED: TaskEventType.FAILED,
    TaskStatus.CANCELLED: TaskEventType.CANCELLED,
}


class TaskEventService:
    def __init__(self, uow, *, clock: Clock | None = None, stream: TaskEventStreamPort) -> None:
        self._uow = uow
        self._clock = clock or SystemClock()
        self._stream = stream

    def publish(
        self,
        task_id: uuid.UUID,
        event_type: TaskEventType,
        *,
        attempt_no: int,
        sequence: int | None = None,
        phase: str | None = None,
        message: str | None = None,
        worker_id: str | None = None,
        report_id: uuid.UUID | None = None,
        duration_ms: int | None = None,
        error_code: str | None = None,
    ) -> str:
        if event_type not in BUSINESS_EVENT_TYPES:
            raise InvalidStateConflictError(f"非法业务事件类型：{event_type}")
        task = self._uow.tasks.get(task_id)
        if task is None:
            raise TaskNotFoundError(f"任务不存在：{task_id}")
        self._validate_status(task.status, task.attempt_no, attempt_no, event_type, error_code)

        event = BusinessTaskEvent(
            event_type=event_type,
            task_id=task_id,
            attempt_no=attempt_no,
            occurred_at=self._clock.now(),
            sequence=sequence,
            phase=phase,
            message=message,
            worker_id=worker_id,
            report_id=report_id,
            duration_ms=duration_ms,
            error_code=error_code,
        )
        try:
            return self._stream.append(task_id, event_type.value, event.payload())
        except Exception:  # noqa: BLE001 - Redis 故障只记录告警：写 Stream 失败不回滚 PG，也不得影响任务结局
            # （§2.4：终态业务帧缺失由 ensure_terminal_business_event 在回放时幂等补写兜底）
            logger.warning("任务事件写入 Redis Stream 失败（不回滚 PG、不影响任务结局）：task=%s event=%s",
                           task_id, event_type.value)
            return ""

    def ensure_terminal_business_event(self, task_id: uuid.UUID) -> str | None:
        """PG 已终态而 Stream 缺少对应业务终态帧时，按已提交状态幂等补写；返回 stream id 或 None。"""
        task = self._uow.tasks.get(task_id)
        if task is None:
            return None
        status = TaskStatus(task.status)
        expected = _TERMINAL_EVENT_BY_STATUS.get(status)
        if expected is None:
            return None  # 非终态：无需补写

        last_type = self._last_business_event_type(task_id)
        if last_type == expected.value:
            return None  # 已存在规范终态帧：幂等跳过

        if status is TaskStatus.SUCCEEDED:
            report = self._uow.reports.get_latest(task_id)
            if report is None:
                return None
            return self.publish(
                task_id,
                TaskEventType.COMPLETED,
                attempt_no=task.attempt_no,
                report_id=report.id,
                duration_ms=None,
            )
        if status is TaskStatus.FAILED:
            return self.publish(
                task_id,
                TaskEventType.FAILED,
                attempt_no=task.attempt_no,
                error_code=task.error_code or "INTERNAL_ERROR",
                message=task.error_summary,
            )
        return self.publish(
            task_id,
            TaskEventType.CANCELLED,
            attempt_no=task.attempt_no,
            message="任务已取消",
        )

    def _last_business_event_type(self, task_id: uuid.UUID) -> str | None:
        events = self._stream.read(task_id, after=None)
        if not events:
            return None
        return events[-1][1]

    @staticmethod
    def _validate_status(
        task_status: str,
        task_attempt: int,
        event_attempt: int,
        event_type: TaskEventType,
        error_code: str | None,
    ) -> None:
        if event_attempt != task_attempt:
            raise InvalidStateConflictError(
                f"事件 attempt_no({event_attempt}) 与任务当前 attempt_no({task_attempt}) 不一致"
            )
        required: dict[TaskEventType, set[TaskStatus]] = {
            TaskEventType.QUEUED: {TaskStatus.QUEUED},
            TaskEventType.STARTED: {TaskStatus.RUNNING},
            TaskEventType.PROGRESS: {TaskStatus.RUNNING},
            TaskEventType.COMPLETED: {TaskStatus.SUCCEEDED},
            TaskEventType.FAILED: {TaskStatus.FAILED},
            TaskEventType.CANCELLED: {TaskStatus.CANCELLED},
        }
        if TaskStatus(task_status) not in required[event_type]:
            raise InvalidStateConflictError(
                f"事件 {event_type.value} 与任务状态 {task_status} 不一致（仅允许对应 PG 状态提交后发布）"
            )
        if event_type is TaskEventType.FAILED and error_code is not None and error_code.endswith("_RETRYABLE"):
            raise InvalidStateConflictError("failed 事件不可携带 retryable 语义")
