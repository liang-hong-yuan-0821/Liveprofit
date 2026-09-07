"""任务状态机（§2.5 / §3.1.3 唯一权威）。

PENDING → QUEUED → RUNNING → SUCCEEDED
   │        │         │
   └────────┴─────────┴→ CANCEL_REQUESTED → CANCELLED
RUNNING(attempt n) ───→ RETRYING(attempt n+1 + outbox PENDING) → QUEUED(attempt n+1)
RUNNING(attempt n) ───→ FAILED

应用服务必须经此校验；任何不满足前置状态的写入返回领域冲突错误，
不得静默覆盖（§3.1.3「任务状态迁移的唯一责任」）。
"""

from __future__ import annotations

from backend.modules.analysis.domain.enums import TaskStatus


class InvalidStateTransitionError(ValueError):
    """前置状态不满足：写入类请求的领域冲突错误（API 映射 409，Worker 记录重复消息安全退出）。"""

    def __init__(self, current: TaskStatus, target: TaskStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"非法状态迁移：{current.value} -> {target.value}")


ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    # create_task 只写 PENDING 任务；Dispatcher 确认投递后 PENDING → QUEUED
    TaskStatus.PENDING: frozenset({TaskStatus.QUEUED}),
    # RETRYING 阶段新 Outbox 确认投递后 RETRYING → QUEUED
    TaskStatus.RETRYING: frozenset({TaskStatus.QUEUED}),
    TaskStatus.QUEUED: frozenset({TaskStatus.RUNNING}),
    TaskStatus.RUNNING: frozenset({TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.RETRYING}),
    TaskStatus.CANCEL_REQUESTED: frozenset({TaskStatus.CANCELLED}),
    # 终态不可再迁移
    TaskStatus.SUCCEEDED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}

# 未运行取消：request_cancel 对这些状态直接收口（finalize_pending_cancellation）
PENDING_CANCELLABLE_STATUSES = frozenset(
    {TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.RETRYING}
)


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def assert_transition(current: TaskStatus, target: TaskStatus) -> None:
    if not can_transition(current, target):
        raise InvalidStateTransitionError(current, target)


def is_terminal(status: TaskStatus) -> bool:
    return not ALLOWED_TRANSITIONS.get(status, frozenset())
