"""TaskService 与 OutboxDispatcherService（§3.1.3「任务状态迁移的唯一责任」）。

- TaskService 是任务状态机的唯一写入口；Worker、Dispatcher、恢复作业和取消 API 都必须调用它。
- 所有状态迁移经 state_machine.assert_transition 校验；条件更新失败抛出领域冲突错误，不静默覆盖。
- 业务事件只在对应 PostgreSQL 状态提交后发布；写 Stream 失败不回滚 PG。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Protocol

from backend.modules.analysis.application.contracts import (
    AnalysisDashboardDTO,
    AnalysisArtifact,
    ClaimedTask,
    ClassifiedError,
    CreateAnalysisTaskCommand,
    DashboardActiveTaskDTO,
    DashboardAttentionItemDTO,
    DashboardConclusionDTO,
    ReportDTO,
    TaskCreatedResult,
    TaskDTO,
    TaskListItemDTO,
    TaskListQuery,
)
from backend.modules.analysis.application.errors import (
    IdempotencyKeyInvalidError,
    IdempotencyKeyReusedError,
    InvalidStateConflictError,
    LeaseConflictError,
    TaskCreateInvalidError,
    TaskNotFoundError,
    TaskNotTerminalError,
)
from backend.modules.analysis.domain.enums import (
    ACTIVE_STATUSES,
    TaskEventType,
    TaskStatus,
    TaskType,
)
from backend.modules.analysis.domain.ports import (
    AnalysisReportRepository,
    AnalysisTaskRepository,
    Clock,
    SystemClock,
    TaskEventStreamPort,
    TaskMessagePublisherPort,
    TaskOutboxRepository,
    TradeDateCalendarPort,
)
from backend.modules.analysis.domain.state_machine import PENDING_CANCELLABLE_STATUSES
from backend.modules.analysis.infrastructure.models import AnalysisReport, AnalysisTask, TaskOutbox
from backend.shared.ids import new_token, new_uuid

_IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

# 任务创建后即置 PENDING 的 Outbox 投递消息类型
MESSAGE_TYPE_ANALYSIS_TASK = "analysis_task"

DASHBOARD_LIMITS = {"pending_actions": 10, "active_tasks": 10, "recent_conclusions": 5}


class AnalysisUnitOfWork(Protocol):
    """Application Service 的数据库事务边界（SQLAlchemy 实现见 infrastructure/repositories.py）。"""

    tasks: AnalysisTaskRepository
    outbox: TaskOutboxRepository
    reports: AnalysisReportRepository

    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class _RetryConfig(Protocol):
    max_retry_attempts: int
    retry_base_delay_seconds: int


class TaskService:
    """任务状态机唯一写入口。不调用 LangGraph、不直接投递 Broker、不直接读写 Redis Stream。"""

    def __init__(
        self,
        uow: AnalysisUnitOfWork,
        *,
        clock: Clock | None = None,
        calendar: TradeDateCalendarPort | None = None,
        events: "TaskEventService | None" = None,
        lease_ttl_seconds: int = 120,
        retry: _RetryConfig | None = None,
    ) -> None:
        self._uow = uow
        self._clock = clock or SystemClock()
        self._calendar = calendar
        self._events = events
        self._lease_ttl_seconds = lease_ttl_seconds
        self._retry = retry or _DefaultRetryConfig()

    # ---------- 创建与幂等 ----------

    @staticmethod
    def validate_layers(command: CreateAnalysisTaskCommand) -> None:
        """服务层复验创建规则（§2.6.2）：前端只做生成类型驱动的预校验。"""
        layers = set(command.selected_layers)
        valid_layers = {"market", "sector", "stock", "screening", "position"}
        if not layers or not layers <= valid_layers:
            raise TaskCreateInvalidError(f"selected_layers 取值非法：{command.selected_layers}")
        if command.task_type is TaskType.SINGLE_STOCK:
            if not command.ticker:
                raise TaskCreateInvalidError("SINGLE_STOCK 必须提供 ticker")
            if layers - {"market", "sector", "stock"}:
                raise TaskCreateInvalidError("SINGLE_STOCK 只允许 market、sector、stock 及其子集")
        else:  # MARKET_WIDE
            if command.ticker:
                raise TaskCreateInvalidError("MARKET_WIDE 禁止提供 ticker")
            if "position" in layers and "screening" not in layers:
                raise TaskCreateInvalidError("position 仅可随 screening 出现")
            # 产品决策 2026-09-06 v3：全市场调研层级自由组合（非空合法子集即可），
            # 不再要求同时包含 market/sector/screening（内核按 selectedLayer 驱动对应子图）

    def create_task(
        self, command: CreateAnalysisTaskCommand, *, idempotency_key: str | None, trace_id: str | None
    ) -> TaskCreatedResult:
        self.validate_layers(command)
        now = self._clock.now()
        input_hash = _hash_input(command)

        if idempotency_key is not None:
            self._validate_idempotency_key(idempotency_key)
            existing = self._uow.tasks.get_by_idempotency_key(idempotency_key)
            if existing is not None:
                if existing.input_hash != input_hash:
                    raise IdempotencyKeyReusedError("同一 Idempotency-Key 已用于不同输入")
                return TaskCreatedResult(
                    task_id=existing.id,
                    status=TaskStatus(existing.status),
                    requested_trade_date=existing.requested_trade_date,
                    effective_trade_date=existing.effective_trade_date,
                    date_correction=existing.date_correction,
                    idempotent_replay=True,
                )

        effective_date, date_correction = self._correct_trade_date(command.requested_trade_date)

        task = AnalysisTask(
            id=new_uuid(),
            task_type=command.task_type.value,
            status=TaskStatus.PENDING.value,
            request_params={"analysis_options": command.analysis_options or {}},
            selected_layers=list(command.selected_layers),
            ticker=command.ticker,
            requested_trade_date=command.requested_trade_date,
            effective_trade_date=effective_date,
            date_correction=date_correction,
            input_hash=input_hash,
            idempotency_key=idempotency_key,
            attempt_no=1,
            created_at=now,
            updated_at=now,
        )
        outbox = TaskOutbox(
            id=new_uuid(),
            task_id=task.id,
            attempt_no=1,
            message_type=MESSAGE_TYPE_ANALYSIS_TASK,
            payload={"task_id": str(task.id), "attempt_no": 1},
            status="PENDING",
            retry_count=0,
            trace_context={"trace_id": trace_id},
            created_at=now,
            updated_at=now,
        )
        # 同一事务写入 task(PENDING) + outbox(PENDING)
        self._uow.tasks.add(task)
        self._uow.outbox.add(outbox)
        self._uow.commit()
        return TaskCreatedResult(
            task_id=task.id,
            status=TaskStatus.PENDING,
            requested_trade_date=command.requested_trade_date,
            effective_trade_date=effective_date,
            date_correction=date_correction,
        )

    def _correct_trade_date(self, requested: date | None) -> tuple[date | None, str | None]:
        if requested is None or self._calendar is None:
            return requested, None
        return self._calendar.correct(requested)

    @staticmethod
    def _validate_idempotency_key(key: str) -> None:
        if not _IDEMPOTENCY_KEY_RE.match(key):
            raise IdempotencyKeyInvalidError("Idempotency-Key 必须为 1–128 个 URL-safe 字符")

    # ---------- 查询 ----------

    def get_task(self, task_id: uuid.UUID) -> TaskDTO:
        task = self._uow.tasks.get(task_id)
        if task is None:
            raise TaskNotFoundError(f"任务不存在：{task_id}")
        return self._to_task_dto(task)

    def list_tasks(self, query: TaskListQuery) -> tuple[list[TaskListItemDTO], tuple[datetime, uuid.UUID] | None]:
        statuses = _status_filter_to_statuses(query.status)
        limit = query.limit + 1  # 多取一条判断是否有下一页
        rows = self._uow.tasks.list_by_statuses(statuses, limit=limit, before=query.cursor)
        items = [self._to_list_item_dto(t) for t in rows[: query.limit]]
        next_cursor = None
        if len(rows) > query.limit:
            last = rows[query.limit - 1]
            next_cursor = (last.updated_at, last.id)
        return items, next_cursor

    def get_dashboard(self) -> AnalysisDashboardDTO:
        now = self._clock.now()
        failed = self._uow.tasks.list_by_statuses(
            {TaskStatus.FAILED.value}, limit=DASHBOARD_LIMITS["pending_actions"]
        )
        unavailable_pairs = self._uow.tasks.list_succeeded_with_unavailable_sections(
            limit=DASHBOARD_LIMITS["pending_actions"]
        )
        pending: list[DashboardAttentionItemDTO] = []
        for task in failed:
            pending.append(
                DashboardAttentionItemDTO(
                    kind="FAILED_TASK",
                    task_id=task.id,
                    task_type=TaskType(task.task_type),
                    ticker=task.ticker,
                    effective_trade_date=task.effective_trade_date,
                    updated_at=task.updated_at,
                    error_code=task.error_code,
                    error_summary=task.error_summary,
                    unavailable_blocks=None,
                    retryable=False,  # FAILED 为终态：系统级重试已耗尽，用户动作是新建任务
                )
            )
        for task, report in unavailable_pairs:
            blocks = _unavailable_blocks(report)
            pending.append(
                DashboardAttentionItemDTO(
                    kind="REPORT_SECTION_UNAVAILABLE",
                    task_id=task.id,
                    task_type=TaskType(task.task_type),
                    ticker=task.ticker,
                    effective_trade_date=task.effective_trade_date,
                    updated_at=task.updated_at,
                    error_code=None,
                    error_summary=None,
                    unavailable_blocks=blocks,
                    retryable=any(b.get("retryable") for b in blocks),
                )
            )
        pending.sort(key=lambda item: (item.updated_at, item.task_id), reverse=True)
        pending = pending[: DASHBOARD_LIMITS["pending_actions"]]

        active_rows = self._uow.tasks.list_by_statuses(
            {s.value for s in ACTIVE_STATUSES if s is not TaskStatus.CANCEL_REQUESTED},
            limit=DASHBOARD_LIMITS["active_tasks"],
        )
        active = [
            DashboardActiveTaskDTO(
                task_id=t.id,
                task_type=TaskType(t.task_type),
                ticker=t.ticker,
                effective_trade_date=t.effective_trade_date,
                status=TaskStatus(t.status),
                attempt_no=t.attempt_no,
                updated_at=t.updated_at,
                next_retry_at=t.next_retry_at,
            )
            for t in active_rows
        ]

        conclusions = []
        for task, report in self._uow.tasks.list_recent_succeeded(limit=DASHBOARD_LIMITS["recent_conclusions"]):
            conclusions.append(
                DashboardConclusionDTO(
                    task_id=task.id,
                    task_type=TaskType(task.task_type),
                    ticker=task.ticker,
                    effective_trade_date=task.effective_trade_date,
                    completed_at=report.generated_at or task.finished_at or task.updated_at,
                    conclusion_summary=report.conclusion_summary,
                    risk_flag=bool(report.risk_flag),
                    risk_hint=report.risk_hint,
                    has_report=bool(report.has_report),
                    updated_at=task.updated_at,
                )
            )

        return AnalysisDashboardDTO(
            pending_actions=pending,
            active_tasks=active,
            recent_conclusions=conclusions,
            generated_at=now,
        )

    # ---------- 投递确认（Dispatcher 调用） ----------

    def confirm_outbox_published(self, outbox_id: uuid.UUID, dispatch_lease_token: str, trace_id: str | None) -> bool:
        now = self._clock.now()
        ok = self._uow.outbox.conditional_update(
            outbox_id,
            expect={"status": "DISPATCHING", "dispatch_lease_token": dispatch_lease_token},
            changes={"status": "PUBLISHED", "published_at": now, "updated_at": now},
        )
        if not ok:
            return False
        record = self._uow.outbox.get(outbox_id)
        if record is None:  # 理论不可达；保守返回未确认
            self._uow.rollback()
            return False
        # 非取消任务 PENDING/RETRYING → QUEUED（保持该 outbox.attempt_no）
        self._uow.tasks.conditional_update(
            record.task_id,
            expect={
                "status": {TaskStatus.PENDING.value, TaskStatus.RETRYING.value},
                "attempt_no": record.attempt_no,
            },
            changes={"status": TaskStatus.QUEUED.value, "updated_at": now},
        )
        self._uow.commit()
        if self._events is not None:
            self._events.publish(record.task_id, TaskEventType.QUEUED, attempt_no=record.attempt_no)
        return True

    # ---------- Worker 执行（租约 fencing） ----------

    def claim_for_execution(self, task_id: uuid.UUID, attempt_no: int, worker_id: str) -> ClaimedTask | None:
        now = self._clock.now()
        lease_token = new_token()
        ok = self._uow.tasks.conditional_update(
            task_id,
            expect={"status": TaskStatus.QUEUED.value, "attempt_no": attempt_no},
            changes={
                "status": TaskStatus.RUNNING.value,
                "lease_token": lease_token,
                "lease_expires_at": now + timedelta(seconds=self._lease_ttl_seconds),
                "heartbeat_at": now,
                "worker_id": worker_id,
                "started_at": now,
                "updated_at": now,
            },
        )
        if not ok:
            return None  # 重复、过期消息或已终态：安全退出
        self._uow.commit()
        task = self._uow.tasks.get(task_id)
        assert task is not None
        return ClaimedTask(
            task_id=task.id,
            attempt_no=task.attempt_no,
            lease_token=lease_token,
            task_type=TaskType(task.task_type),
            ticker=task.ticker,
            selected_layers=tuple(task.selected_layers or []),
            effective_trade_date=task.effective_trade_date,
            request_params=task.request_params or {},
        )

    def renew_lease(self, task_id: uuid.UUID, attempt_no: int, lease_token: str) -> bool:
        now = self._clock.now()
        ok = self._uow.tasks.conditional_update(
            task_id,
            expect={"status": TaskStatus.RUNNING.value, "attempt_no": attempt_no, "lease_token": lease_token},
            changes={
                "heartbeat_at": now,
                "lease_expires_at": now + timedelta(seconds=self._lease_ttl_seconds),
                "updated_at": now,
            },
        )
        if ok:
            self._uow.commit()
        return ok

    def complete_task(
        self,
        task_id: uuid.UUID,
        attempt_no: int,
        lease_token: str,
        artifact: AnalysisArtifact,
        report_service: "ReportService",
    ) -> ReportDTO:
        """校验 attempt 与租约后保存报告，并在同一事务将 RUNNING 置 SUCCEEDED。"""
        now = self._clock.now()
        report = report_service.save_version(self._uow, task_id, attempt_no, artifact)  # 不 commit
        ok = self._uow.tasks.conditional_update(
            task_id,
            expect={"status": TaskStatus.RUNNING.value, "attempt_no": attempt_no, "lease_token": lease_token},
            changes={
                "status": TaskStatus.SUCCEEDED.value,
                "finished_at": now,
                "error_code": None,
                "error_summary": None,
                "lease_token": None,
                "lease_expires_at": None,
                "heartbeat_at": None,
                "updated_at": now,
            },
        )
        if not ok:
            self._uow.rollback()
            raise LeaseConflictError(f"任务 {task_id} 当前 attempt/租约已失效，报告已放弃")
        self._uow.commit()
        if self._events is not None:
            self._events.publish(
                task_id,
                TaskEventType.COMPLETED,
                attempt_no=attempt_no,
                report_id=report.report_id,
                duration_ms=artifact.duration_ms,
            )
        return report

    def delete_task(self, task_id: uuid.UUID) -> dict:
        """删除任务记录（仅终态可删）：FK ondelete=CASCADE 级联删除报告与 Outbox；
        产物目录由既有保留/回收策略清理，删除路径不操作文件系统。"""
        task = self._uow.tasks.get(task_id)
        if task is None:
            raise TaskNotFoundError(f"任务不存在：{task_id}")
        if TaskStatus(task.status) not in (TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            raise TaskNotTerminalError(f"任务 {task_id} 非终态（{task.status}），请先取消")
        if not self._uow.tasks.delete(task_id):
            raise TaskNotFoundError(f"任务不存在：{task_id}")
        self._uow.commit()
        return {"deleted": True, "resource_id": str(task_id)}

    def fail_or_retry(
        self, task_id: uuid.UUID, attempt_no: int, lease_token: str, error: ClassifiedError
    ) -> TaskDTO:
        """可重试错误同事务迁移 RETRYING(n+1)+新 Outbox 且不发布任何重试 SSE；
        不可重试或耗尽才提交 FAILED，提交后发布 failed（§2.4 / §3.1.3）。"""
        now = self._clock.now()
        task = self._uow.tasks.get(task_id)
        if task is None:
            raise TaskNotFoundError(f"任务不存在：{task_id}")
        if not (task.status == TaskStatus.RUNNING.value and task.attempt_no == attempt_no and task.lease_token == lease_token):
            raise LeaseConflictError(f"任务 {task_id} 当前 attempt/租约已失效，失败/重试被拒绝")

        if error.retryable and attempt_no < self._retry.max_retry_attempts:
            next_attempt = attempt_no + 1
            next_retry_at = now + timedelta(seconds=self._retry.retry_base_delay_seconds * (2 ** (attempt_no - 1)))
            ok = self._uow.tasks.conditional_update(
                task_id,
                expect={"status": TaskStatus.RUNNING.value, "attempt_no": attempt_no, "lease_token": lease_token},
                changes={
                    "status": TaskStatus.RETRYING.value,
                    "attempt_no": next_attempt,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "heartbeat_at": None,
                    "next_retry_at": next_retry_at,
                    "error_code": error.code,
                    "error_summary": error.message,
                    "updated_at": now,
                },
            )
            if not ok:
                self._uow.rollback()
                raise LeaseConflictError(f"任务 {task_id} 当前 attempt/租约已失效，失败/重试被拒绝")
            self._uow.outbox.add(
                TaskOutbox(
                    id=new_uuid(),
                    task_id=task_id,
                    attempt_no=next_attempt,
                    message_type=MESSAGE_TYPE_ANALYSIS_TASK,
                    payload={"task_id": str(task_id), "attempt_no": next_attempt},
                    status="PENDING",
                    retry_count=0,
                    next_attempt_at=next_retry_at,
                    trace_context=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            self._uow.commit()
            # 此迁移不发布、不持久化、不回放任何重试专用 SSE 事件
        else:
            ok = self._uow.tasks.conditional_update(
                task_id,
                expect={"status": TaskStatus.RUNNING.value, "attempt_no": attempt_no, "lease_token": lease_token},
                changes={
                    "status": TaskStatus.FAILED.value,
                    "finished_at": now,
                    "error_code": error.code,
                    "error_summary": error.message,
                    "next_retry_at": None,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "heartbeat_at": None,
                    "updated_at": now,
                },
            )
            if not ok:
                self._uow.rollback()
                raise LeaseConflictError(f"任务 {task_id} 当前 attempt/租约已失效，失败被拒绝")
            self._uow.commit()
            if self._events is not None:
                self._events.publish(task_id, TaskEventType.FAILED, attempt_no=attempt_no, error_code=error.code, message=error.message)
        return self.get_task(task_id)

    def mark_cancelled(self, task_id: uuid.UUID, attempt_no: int, lease_token: str) -> TaskDTO:
        now = self._clock.now()
        ok = self._uow.tasks.conditional_update(
            task_id,
            expect={"status": TaskStatus.CANCEL_REQUESTED.value, "attempt_no": attempt_no, "lease_token": lease_token},
            changes={
                "status": TaskStatus.CANCELLED.value,
                "finished_at": now,
                "lease_token": None,
                "lease_expires_at": None,
                "heartbeat_at": None,
                "updated_at": now,
            },
        )
        if not ok:
            self._uow.rollback()
            raise LeaseConflictError(f"任务 {task_id} 当前 attempt/租约已失效，取消收口被拒绝")
        self._uow.commit()
        if self._events is not None:
            self._events.publish(task_id, TaskEventType.CANCELLED, attempt_no=attempt_no, message="任务已取消")
        return self.get_task(task_id)

    # ---------- 取消与恢复 ----------

    def request_cancel(self, task_id: uuid.UUID) -> TaskDTO:
        now = self._clock.now()
        task = self._uow.tasks.get(task_id)
        if task is None:
            raise TaskNotFoundError(f"任务不存在：{task_id}")
        status = TaskStatus(task.status)
        if status is TaskStatus.RUNNING:
            self._uow.tasks.conditional_update(
                task_id,
                expect={"status": TaskStatus.RUNNING.value},
                changes={"status": TaskStatus.CANCEL_REQUESTED.value, "cancel_requested_at": now, "updated_at": now},
            )
            self._uow.commit()
            return self.get_task(task_id)
        if status in PENDING_CANCELLABLE_STATUSES:
            return self.finalize_pending_cancellation(task_id)
        # 终态/取消请求中：幂等返回当前状态
        return self.get_task(task_id)

    def finalize_pending_cancellation(self, task_id: uuid.UUID) -> TaskDTO:
        """对 PENDING/QUEUED/RETRYING/CANCEL_REQUESTED（无有效运行 lease）原子废弃待消费 Outbox 并置 CANCELLED。"""
        now = self._clock.now()
        ok = self._uow.tasks.conditional_update(
            task_id,
            expect={
                "status": {TaskStatus.PENDING.value, TaskStatus.QUEUED.value, TaskStatus.RETRYING.value}
            },
            changes={
                "status": TaskStatus.CANCELLED.value,
                "finished_at": now,
                "next_retry_at": None,
                "updated_at": now,
            },
        )
        if ok:
            self._uow.outbox.cancel_pending_for_task(task_id)
            self._uow.commit()
            task = self._uow.tasks.get(task_id)
            if self._events is not None and task is not None:
                self._events.publish(task_id, TaskEventType.CANCELLED, attempt_no=task.attempt_no, message="任务已取消")
        else:
            # CANCEL_REQUESTED 且无有效运行 lease（运行中取消由持有 lease 的 Worker 收口）
            ok = self._uow.tasks.conditional_update(
                task_id,
                expect={"status": TaskStatus.CANCEL_REQUESTED.value, "lease_token": None},
                changes={"status": TaskStatus.CANCELLED.value, "finished_at": now, "updated_at": now},
            )
            if ok:
                self._uow.outbox.cancel_pending_for_task(task_id)
                self._uow.commit()
                task = self._uow.tasks.get(task_id)
                if self._events is not None and task is not None:
                    self._events.publish(task_id, TaskEventType.CANCELLED, attempt_no=task.attempt_no, message="任务已取消")
        return self.get_task(task_id)

    def recover_expired_leases(self, *, grace_seconds: int = 60) -> int:
        """过期 Worker 租约恢复（§3.1.3 / §2.4）：

        - RUNNING(n) 且 lease_expires_at < now - grace：→ RETRYING(n+1)+outbox(PENDING)，不发布重试 SSE；
          attempt 已达上限则 FAILED（提交后发布 failed），防止确定性崩溃无限循环。
        - CANCEL_REQUESTED 且租约过期：清租约字段后收口 CANCELLED（取消请求的僵尸兜底，
          finalize_pending_cancellation 因 lease_token 非空收不了，只能由恢复作业处理）。
        """
        now = self._clock.now()
        cutoff = now - timedelta(seconds=grace_seconds)
        expired = self._uow.tasks.find_expired_running(cutoff)
        cancelled_pending = self._uow.tasks.find_expired_cancel_requested(cutoff)
        recovered = 0
        failed_ids: list[tuple] = []
        cancelled_ids: list[tuple] = []

        for task in expired:
            if task.attempt_no >= self._retry.max_retry_attempts:
                ok = self._uow.tasks.conditional_update(
                    task.id,
                    expect={
                        "status": TaskStatus.RUNNING.value,
                        "attempt_no": task.attempt_no,
                        # 原子复检：恢复心跳刚续租的 Worker 不得被误踢（时间过滤仅在 SELECT 端）
                        "lease_expires_at": ("<", cutoff),
                    },
                    changes={
                        "status": TaskStatus.FAILED.value,
                        "finished_at": now,
                        "error_code": "LEASE_EXPIRED",
                        "error_summary": "Worker 租约过期且重试耗尽",
                        "next_retry_at": None,
                        "lease_token": None,
                        "lease_expires_at": None,
                        "heartbeat_at": None,
                        "updated_at": now,
                    },
                )
                if ok:
                    recovered += 1
                    failed_ids.append((task.id, task.attempt_no))
                continue
            next_attempt = task.attempt_no + 1
            next_retry_at = now + timedelta(seconds=self._retry.retry_base_delay_seconds * (2 ** (task.attempt_no - 1)))
            ok = self._uow.tasks.conditional_update(
                task.id,
                expect={
                    "status": TaskStatus.RUNNING.value,
                    "attempt_no": task.attempt_no,
                    "lease_expires_at": ("<", cutoff),  # 原子复检（见上）
                },
                changes={
                    "status": TaskStatus.RETRYING.value,
                    "attempt_no": next_attempt,
                    "lease_token": None,
                    "lease_expires_at": None,
                    "heartbeat_at": None,
                    "next_retry_at": next_retry_at,
                    "error_code": "LEASE_EXPIRED",
                    "error_summary": "Worker 租约过期，自动恢复",
                    "updated_at": now,
                },
            )
            if not ok:
                continue  # 并发恢复/迟到 Worker 已处理：跳过
            recovered += 1
            self._uow.outbox.add(
                TaskOutbox(
                    id=new_uuid(),
                    task_id=task.id,
                    attempt_no=next_attempt,
                    message_type=MESSAGE_TYPE_ANALYSIS_TASK,
                    payload={"task_id": str(task.id), "attempt_no": next_attempt},
                    status="PENDING",
                    retry_count=0,
                    next_attempt_at=next_retry_at,
                    trace_context=None,
                    created_at=now,
                    updated_at=now,
                )
            )

        for task in cancelled_pending:
            # 先清租约字段（finalize 要求无有效运行 lease），再收口 CANCELLED
            ok = self._uow.tasks.conditional_update(
                task.id,
                expect={
                    "status": TaskStatus.CANCEL_REQUESTED.value,
                    "attempt_no": task.attempt_no,
                    "lease_expires_at": ("<", cutoff),  # 原子复检（见上）
                },
                changes={"lease_token": None, "lease_expires_at": None, "heartbeat_at": None, "updated_at": now},
            )
            if not ok:
                continue
            ok = self._uow.tasks.conditional_update(
                task.id,
                expect={"status": TaskStatus.CANCEL_REQUESTED.value, "lease_token": None},
                changes={
                    "status": TaskStatus.CANCELLED.value,
                    "finished_at": now,
                    "updated_at": now,
                },
            )
            if ok:
                self._uow.outbox.cancel_pending_for_task(task.id)
                recovered += 1
                cancelled_ids.append((task.id, task.attempt_no))

        self._uow.commit()
        if self._events is not None:
            for task_id, attempt_no in failed_ids:
                self._events.publish(
                    task_id, TaskEventType.FAILED, attempt_no=attempt_no,
                    error_code="LEASE_EXPIRED", message="Worker 租约过期且重试耗尽",
                )
            for task_id, attempt_no in cancelled_ids:
                self._events.publish(task_id, TaskEventType.CANCELLED, attempt_no=attempt_no, message="任务已取消")
        return recovered

    # ---------- 投影 ----------

    @staticmethod
    def _to_task_dto(task: AnalysisTask) -> TaskDTO:
        return TaskDTO(
            id=task.id,
            task_type=TaskType(task.task_type),
            ticker=task.ticker,
            requested_trade_date=task.requested_trade_date,
            effective_trade_date=task.effective_trade_date,
            date_correction=task.date_correction,
            selected_layers=tuple(task.selected_layers or []),
            status=TaskStatus(task.status),
            attempt_no=task.attempt_no,
            next_retry_at=task.next_retry_at,
            error_code=task.error_code,
            error_summary=task.error_summary,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )

    @staticmethod
    def _to_list_item_dto(task: AnalysisTask) -> TaskListItemDTO:
        return TaskListItemDTO(
            id=task.id,
            task_type=TaskType(task.task_type),
            ticker=task.ticker,
            effective_trade_date=task.effective_trade_date,
            status=TaskStatus(task.status),
            attempt_no=task.attempt_no,
            error_code=task.error_code,
            error_summary=task.error_summary,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )


class OutboxDispatcherService:
    """领取待发布 Outbox、投递消息、确认发布（§3.1.3）。

    短事务 SKIP LOCKED 抢占 DISPATCHING + dispatch lease；提交后才调用 Broker；
    不得在持有 DB 事务时做 Broker I/O。
    """

    def __init__(
        self,
        uow: AnalysisUnitOfWork,
        *,
        task_service: TaskService,
        publisher: TaskMessagePublisherPort,
        clock: Clock | None = None,
        dispatch_lease_seconds: int = 30,
    ) -> None:
        self._uow = uow
        self._task_service = task_service
        self._publisher = publisher
        self._clock = clock or SystemClock()
        self._dispatch_lease_seconds = dispatch_lease_seconds

    def dispatch_due(self, *, limit: int) -> int:
        now = self._clock.now()
        lease_token = new_token()
        claimed = self._uow.outbox.claim_due(
            now, limit=limit, lease_seconds=self._dispatch_lease_seconds, lease_token=lease_token
        )
        if not claimed:
            return 0
        self._uow.commit()  # 抢占提交后才发 Broker
        dispatched = 0
        for record in claimed:
            try:
                self._publisher.publish({"task_id": str(record.task_id), "attempt_no": record.attempt_no})
            except Exception:  # noqa: BLE001 - Broker 投递失败：标记投递失败，允许后续重试
                self._mark_dispatch_failed(record)
                continue
            confirmed = self._task_service.confirm_outbox_published(record.id, lease_token, trace_id=None)
            if not confirmed:
                self._mark_dispatch_failed(record)
                continue
            dispatched += 1
        return dispatched

    def _mark_dispatch_failed(self, record: TaskOutbox) -> None:
        now = self._clock.now()
        next_attempt = now + timedelta(seconds=5 * (record.retry_count + 1))
        self._uow.outbox.conditional_update(
            record.id,
            expect={"status": "DISPATCHING", "dispatch_lease_token": record.dispatch_lease_token},
            changes={
                "status": "PENDING",
                "retry_count": record.retry_count + 1,
                "next_attempt_at": next_attempt,
                "dispatch_lease_token": None,
                "dispatch_lease_expires_at": None,
                "updated_at": now,
            },
        )
        self._uow.commit()


class _DefaultRetryConfig:
    max_retry_attempts = 3
    retry_base_delay_seconds = 60


def _hash_input(command: CreateAnalysisTaskCommand) -> str:
    canonical = json.dumps(
        {
            "task_type": command.task_type.value,
            "ticker": command.ticker,
            "requested_trade_date": command.requested_trade_date.isoformat() if command.requested_trade_date else None,
            "selected_layers": sorted(command.selected_layers),
            "analysis_options": command.analysis_options or {},
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _status_filter_to_statuses(status: str) -> set[str]:
    mapping = {
        "all": {s.value for s in TaskStatus},
        "active": {s.value for s in ACTIVE_STATUSES},
        "succeeded": {TaskStatus.SUCCEEDED.value},
        "failed": {TaskStatus.FAILED.value},
        "cancelled": {TaskStatus.CANCELLED.value},
    }
    if status not in mapping:
        raise InvalidStateConflictError(f"非法 status 筛选值：{status}")
    return mapping[status]


def _unavailable_blocks(report: AnalysisReport) -> list[dict]:
    sections = (report.report_json or {}).get("sections", [])
    blocks = []
    for section in sections or []:
        if isinstance(section, dict) and section.get("status") == "UNAVAILABLE":
            blocks.append(
                {
                    "block": section.get("block"),
                    "reason": section.get("unavailable_reason"),
                    "retryable": section.get("retryable", False),
                }
            )
    return blocks
