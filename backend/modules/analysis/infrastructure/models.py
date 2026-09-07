"""analysis ORM：analysis_tasks / task_outbox / analysis_reports（§2.5）。

任务状态唯一真相；条件更新必须校验状态、attempt 与租约（T3 应用服务执行）。
Repository 返回 ORM 实体仅限本模块 application/infrastructure 内部消费。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.shared.db import Base, TimestampMixin, uuid_pk


class AnalysisTask(Base, TimestampMixin):
    __tablename__ = "analysis_tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    task_type: Mapped[str] = mapped_column(String(32), nullable=False)  # TaskType
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # TaskStatus
    request_params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    selected_layers: Mapped[list] = mapped_column(JSONB, nullable=False)
    ticker: Mapped[str | None] = mapped_column(String(32), nullable=True)
    requested_trade_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_trade_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_correction: Mapped[str | None] = mapped_column(String(64), nullable=True)

    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)

    # 执行尝试与租约 fencing
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(512), nullable=True)
    config_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # 脱敏 hash
    core_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    outbox_records: Mapped[list["TaskOutbox"]] = relationship(back_populates="task", cascade="all, delete-orphan")
    reports: Mapped[list["AnalysisReport"]] = relationship(back_populates="task", cascade="all, delete-orphan")

    __table_args__ = (
        # 列表 keyset 分页排序：updated_at DESC, id DESC
        Index("ix_analysis_tasks_updated_id", "updated_at", "id"),
    )


class TaskOutbox(Base, TimestampMixin):
    __tablename__ = "task_outbox"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("analysis_tasks.id", ondelete="CASCADE"), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    message_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)  # 最小 payload：task_id + attempt_no
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # OutboxStatus

    dispatch_lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dispatch_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trace_context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    task: Mapped[AnalysisTask] = relationship(back_populates="outbox_records")

    __table_args__ = (
        UniqueConstraint("task_id", "attempt_no", name="uq_task_outbox_task_attempt"),
        Index("ix_task_outbox_status_next_attempt", "status", "next_attempt_at"),
    )


class AnalysisReport(Base, TimestampMixin):
    __tablename__ = "analysis_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("analysis_tasks.id", ondelete="CASCADE"), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    report_version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="v1")

    # 结构化报告与看板投影（§2.6.2 / §5.1 P-5：保存版本时投影落库，看板只读）
    report_json: Mapped[dict] = mapped_column(JSONB, nullable=False)  # 区块结构化报告
    conclusion_summary: Mapped[str | None] = mapped_column(String(512), nullable=True)  # ≤200 字
    risk_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    risk_hint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    has_report: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    has_unavailable_sections: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # 看板 attention 读模型投影
    decision: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # 受控产物引用（完整 State 在 task-scoped artifact storage，受保留期限制）
    artifact_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    core_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    task: Mapped[AnalysisTask] = relationship(back_populates="reports")

    __table_args__ = (
        UniqueConstraint("task_id", "report_version", name="uq_analysis_reports_task_version"),
        Index("ix_analysis_reports_task_completed", "task_id", "generated_at"),
    )
