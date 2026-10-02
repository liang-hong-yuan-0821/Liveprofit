"""Persisted joint allocation decisions; these records do not reserve funds."""
import uuid
from datetime import date, datetime
from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from backend.shared.db import Base


class AllocationBatch(Base):
    __tablename__ = "quant_allocation_batches"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("portfolios.id", ondelete="RESTRICT"))
    request_key: Mapped[str] = mapped_column(String(128))
    input_hash: Mapped[str] = mapped_column(String(64))
    asset_scope: Mapped[str] = mapped_column(String(16))
    valuation_date: Mapped[date] = mapped_column(Date)
    decision_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    account_snapshot: Mapped[dict] = mapped_column(JSONB)
    result: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        UniqueConstraint("portfolio_id", "request_key", name="uq_allocation_request"),
        CheckConstraint("status IN ('PROJECTED','BLOCKED')", name="ck_allocation_status"),
        CheckConstraint("asset_scope IN ('CN_STOCK','CN_ETF')", name="ck_allocation_scope"),
        CheckConstraint("length(trim(request_key)) > 0 AND input_hash ~ '^[0-9a-f]{64}$'", name="ck_allocation_identity"),
    )


class AllocationMember(Base):
    __tablename__ = "quant_allocation_members"
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quant_allocation_batches.id", ondelete="RESTRICT"), primary_key=True)
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quant_strategy_versions.id", ondelete="RESTRICT"), primary_key=True)
    family_id: Mapped[str] = mapped_column(String(64))
    admission_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("strategy_admission_events.id", ondelete="RESTRICT"), nullable=True)
    admission_code: Mapped[str] = mapped_column(String(64))
    intent: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (UniqueConstraint("batch_id", "family_id", name="uq_allocation_family"),)


class AllocationExecution(Base):
    """One terminal entry-order receipt per joint allocation batch."""
    __tablename__ = "quant_allocation_executions"
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quant_allocation_batches.id", ondelete="RESTRICT"), primary_key=True)
    refreshed_batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quant_allocation_batches.id", ondelete="RESTRICT"))
    input_hash: Mapped[str] = mapped_column(String(64))
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="ck_allocation_execution_identity"),)


class AllocationScan(Base):
    """Immutable member-to-task registration; attempts/status live in analysis_tasks."""
    __tablename__ = "quant_allocation_scans"
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_tasks.id", ondelete="RESTRICT"), unique=True)
    __table_args__ = (ForeignKeyConstraint(
        ["batch_id", "strategy_version_id"],
        ["quant_allocation_members.batch_id", "quant_allocation_members.strategy_version_id"],
        ondelete="RESTRICT",
    ),)


class AllocationOutcome(Base):
    """One immutable automatic orchestration outcome, atomic with any orders."""
    __tablename__ = "quant_allocation_outcomes"
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quant_allocation_batches.id", ondelete="RESTRICT"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    reason_code: Mapped[str] = mapped_column(String(64))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entry_receipt_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("quant_allocation_executions.batch_id", ondelete="RESTRICT"), nullable=True)
    details: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        CheckConstraint("status IN ('COMPLETED','BLOCKED')", name="ck_allocation_outcome_status"),
        CheckConstraint("(status = 'COMPLETED' AND entry_receipt_id IS NOT NULL AND entry_receipt_id = batch_id) OR (status = 'BLOCKED' AND entry_receipt_id IS NULL)", name="ck_allocation_outcome_receipt"),
    )
