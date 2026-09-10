"""SQLAlchemy Repository 与 UnitOfWork（同步 Session，Worker/Dispatcher 路径）。

- Repository 不 commit；事务由 Application Service（经 UoW）控制。
- conditional_update 的 expect 值支持 set（IN 语义）与普通值（等值语义），
  单一 UPDATE 语句条件更新，返回是否命中——租约 fencing 的原子基础。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from backend.modules.analysis.domain.enums import TaskStatus
from backend.modules.analysis.infrastructure.models import (
    AgentPromptOverride,
    AnalysisReport,
    AnalysisTask,
    TaskOutbox,
)

# Outbox 可被取消/可被 claim 的状态
_OUTBOX_CANCELLABLE = ("PENDING", "DISPATCHING")


class SqlAlchemyTaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, task: AnalysisTask) -> None:
        self._session.add(task)

    def get(self, task_id: uuid.UUID) -> AnalysisTask | None:
        return self._session.get(AnalysisTask, task_id)

    def get_by_idempotency_key(self, key: str) -> AnalysisTask | None:
        return self._session.execute(
            select(AnalysisTask).where(AnalysisTask.idempotency_key == key)
        ).scalar_one_or_none()

    def delete(self, task_id: uuid.UUID) -> bool:
        """删除任务行（FK ondelete=CASCADE 级联删除报告与 Outbox）。"""
        task = self._session.get(AnalysisTask, task_id)
        if task is None:
            return False
        self._session.delete(task)
        return True

    def conditional_update(self, task_id: uuid.UUID, expect: dict, changes: dict) -> bool:
        stmt = (
            update(AnalysisTask)
            .where(AnalysisTask.id == task_id, *_expect_clauses(AnalysisTask, expect))
            .values(**changes)
            .execution_options(synchronize_session="fetch")  # fetch 策略：WHERE 含比较运算时避免 evaluate 的 Python 比较异常，且保持会话内对象新鲜
        )
        return self._session.execute(stmt).rowcount > 0

    def list_by_statuses(
        self,
        statuses: set[str],
        *,
        limit: int,
        before: tuple[datetime, uuid.UUID] | None = None,
    ) -> list[AnalysisTask]:
        stmt = select(AnalysisTask).where(AnalysisTask.status.in_(statuses))
        if before is not None:
            cursor_updated, cursor_id = before
            stmt = stmt.where(
                or_(
                    AnalysisTask.updated_at < cursor_updated,
                    and_(AnalysisTask.updated_at == cursor_updated, AnalysisTask.id < cursor_id),
                )
            )
        stmt = stmt.order_by(AnalysisTask.updated_at.desc(), AnalysisTask.id.desc()).limit(limit)
        return list(self._session.execute(stmt).scalars())

    def list_succeeded_with_unavailable_sections(self, *, limit: int) -> list[tuple[AnalysisTask, AnalysisReport]]:
        latest = (
            select(AnalysisReport.task_id, func.max(AnalysisReport.report_version).label("max_version"))
            .group_by(AnalysisReport.task_id)
            .subquery()
        )
        stmt = (
            select(AnalysisTask, AnalysisReport)
            .join(AnalysisReport, AnalysisReport.task_id == AnalysisTask.id)
            .join(
                latest,
                and_(latest.c.task_id == AnalysisReport.task_id, latest.c.max_version == AnalysisReport.report_version),
            )
            .where(
                AnalysisTask.status == TaskStatus.SUCCEEDED.value,
                AnalysisReport.has_unavailable_sections.is_(True),
            )
            .order_by(AnalysisTask.updated_at.desc(), AnalysisTask.id.desc())
            .limit(limit)
        )
        return [tuple(row) for row in self._session.execute(stmt)]

    def list_recent_succeeded(self, *, limit: int) -> list[tuple[AnalysisTask, AnalysisReport]]:
        latest = (
            select(AnalysisReport.task_id, func.max(AnalysisReport.report_version).label("max_version"))
            .group_by(AnalysisReport.task_id)
            .subquery()
        )
        stmt = (
            select(AnalysisTask, AnalysisReport)
            .join(AnalysisReport, AnalysisReport.task_id == AnalysisTask.id)
            .join(
                latest,
                and_(latest.c.task_id == AnalysisReport.task_id, latest.c.max_version == AnalysisReport.report_version),
            )
            .where(AnalysisTask.status == TaskStatus.SUCCEEDED.value)
            .order_by(
                func.coalesce(
                    AnalysisReport.generated_at, AnalysisTask.finished_at, AnalysisTask.updated_at
                ).desc(),
                AnalysisTask.updated_at.desc(),
                AnalysisTask.id.desc(),
            )
            .limit(limit)
        )
        return [tuple(row) for row in self._session.execute(stmt)]

    def find_expired_running(self, cutoff: datetime, *, limit: int = 100) -> list[AnalysisTask]:
        stmt = (
            select(AnalysisTask)
            .where(
                AnalysisTask.status == TaskStatus.RUNNING.value,
                AnalysisTask.lease_expires_at.is_not(None),
                AnalysisTask.lease_expires_at < cutoff,
            )
            .order_by(AnalysisTask.lease_expires_at.asc())
            .limit(limit)
        )
        return list(self._session.execute(stmt).scalars())

    def find_expired_cancel_requested(self, cutoff: datetime, *, limit: int = 100) -> list[AnalysisTask]:
        stmt = (
            select(AnalysisTask)
            .where(
                AnalysisTask.status == TaskStatus.CANCEL_REQUESTED.value,
                AnalysisTask.lease_expires_at.is_not(None),
                AnalysisTask.lease_expires_at < cutoff,
            )
            .order_by(AnalysisTask.lease_expires_at.asc())
            .limit(limit)
        )
        return list(self._session.execute(stmt).scalars())


class SqlAlchemyTaskOutboxRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, record: TaskOutbox) -> None:
        self._session.add(record)

    def get(self, outbox_id: uuid.UUID) -> TaskOutbox | None:
        return self._session.get(TaskOutbox, outbox_id)

    def get_by_task_attempt(self, task_id: uuid.UUID, attempt_no: int) -> TaskOutbox | None:
        return self._session.execute(
            select(TaskOutbox).where(TaskOutbox.task_id == task_id, TaskOutbox.attempt_no == attempt_no)
        ).scalar_one_or_none()

    def claim_due(
        self, now: datetime, *, limit: int, lease_seconds: int, lease_token: str
    ) -> list[TaskOutbox]:
        """短事务 FOR UPDATE SKIP LOCKED 抢占到期 PENDING / 过期 DISPATCHING Outbox（不 commit）。"""
        stmt = (
            select(TaskOutbox)
            .where(
                or_(
                    and_(
                        TaskOutbox.status == "PENDING",
                        or_(
                            TaskOutbox.next_attempt_at.is_(None),
                            TaskOutbox.next_attempt_at <= now,
                        ),
                    ),
                    and_(
                        TaskOutbox.status == "DISPATCHING",
                        TaskOutbox.dispatch_lease_expires_at.is_not(None),
                        TaskOutbox.dispatch_lease_expires_at < now,
                    ),
                )
            )
            .order_by(TaskOutbox.created_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        records = list(self._session.execute(stmt).scalars())
        for record in records:
            record.status = "DISPATCHING"
            record.dispatch_lease_token = lease_token
            record.dispatch_lease_expires_at = now + timedelta(seconds=lease_seconds)
            record.updated_at = now
        return records

    def conditional_update(self, outbox_id: uuid.UUID, expect: dict, changes: dict) -> bool:
        stmt = (
            update(TaskOutbox)
            .where(TaskOutbox.id == outbox_id, *_expect_clauses(TaskOutbox, expect))
            .values(**changes)
            .execution_options(synchronize_session="fetch")
        )
        return self._session.execute(stmt).rowcount > 0

    def cancel_pending_for_task(self, task_id: uuid.UUID) -> int:
        stmt = (
            update(TaskOutbox)
            .where(TaskOutbox.task_id == task_id, TaskOutbox.status.in_(_OUTBOX_CANCELLABLE))
            .values(status="CANCELLED")
        )
        return self._session.execute(stmt).rowcount


class SqlAlchemyReportRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, report: AnalysisReport) -> None:
        self._session.add(report)

    def get_latest(self, task_id: uuid.UUID) -> AnalysisReport | None:
        return self._session.execute(
            select(AnalysisReport)
            .where(AnalysisReport.task_id == task_id)
            .order_by(AnalysisReport.report_version.desc())
            .limit(1)
        ).scalar_one_or_none()

    def next_version(self, task_id: uuid.UUID) -> int:
        current = self._session.execute(
            select(func.coalesce(func.max(AnalysisReport.report_version), 0)).where(
                AnalysisReport.task_id == task_id
            )
        ).scalar_one()
        return int(current) + 1


def _expect_clauses(model, expect: dict) -> list:
    """expect 值：set → in_ 语义；None → IS NULL；(op, value) 二元组 → 比较运算；其余等值。"""
    clauses = []
    for key, value in expect.items():
        column = getattr(model, key)
        if isinstance(value, (set, frozenset)):
            clauses.append(column.in_(value))
        elif value is None:
            clauses.append(column.is_(None))
        elif isinstance(value, tuple) and len(value) == 2 and value[0] == "<":
            clauses.append(column < value[1])
        else:
            clauses.append(column == value)
    return clauses


class SqlAlchemyPromptOverrideRepository:
    """Agent 提示词覆盖仓库（单Agent重跑与提示词编辑方案 3.3）。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, node_id: str) -> AgentPromptOverride | None:
        return self._session.get(AgentPromptOverride, node_id)

    def list_as_map(self) -> dict[str, str]:
        """全部覆盖 node_id → prompt_text（worker 执行开始快照用）。"""
        rows = self._session.execute(select(AgentPromptOverride)).scalars().all()
        return {row.node_id: row.prompt_text for row in rows}

    def upsert(self, node_id: str, prompt_text: str, now: datetime) -> AgentPromptOverride:
        """get + add 短事务 upsert（PK 约束防重复；并发双写由唯一键收口）。"""
        row = self._session.get(AgentPromptOverride, node_id)
        if row is None:
            row = AgentPromptOverride(node_id=node_id, prompt_text=prompt_text)
            self._session.add(row)
        else:
            row.prompt_text = prompt_text
            row.updated_at = now
        return row

    def delete(self, node_id: str) -> bool:
        """删除覆盖（恢复默认）；幂等——不存在返回 False 不抛错。"""
        row = self._session.get(AgentPromptOverride, node_id)
        if row is None:
            return False
        self._session.delete(row)
        return True


class SqlAlchemyAnalysisUnitOfWork:
    """Application Service 的数据库事务边界（同步）。

    用法：with build_analysis_uow(session_factory) as uow: service(uow)；事务由 Service 内部
    调用 uow.commit()/rollback()；退出时确保 Session 关闭（API 路径的 async 变体在 T5 提供）。
    """

    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None
        self.tasks: SqlAlchemyTaskRepository | None = None
        self.outbox: SqlAlchemyTaskOutboxRepository | None = None
        self.reports: SqlAlchemyReportRepository | None = None
        self.prompts: SqlAlchemyPromptOverrideRepository | None = None

    def __enter__(self) -> "SqlAlchemyAnalysisUnitOfWork":
        self._session = self._session_factory()
        self.tasks = SqlAlchemyTaskRepository(self._session)
        self.outbox = SqlAlchemyTaskOutboxRepository(self._session)
        self.reports = SqlAlchemyReportRepository(self._session)
        self.prompts = SqlAlchemyPromptOverrideRepository(self._session)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    def commit(self) -> None:
        assert self._session is not None, "UoW 未进入上下文"
        self._session.commit()

    def rollback(self) -> None:
        assert self._session is not None, "UoW 未进入上下文"
        self._session.rollback()
