"""Permanent research dataset identity, separate from expiring task artifacts."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.shared.db import Base


class QuantResearchDataset(Base):
    __tablename__ = "quant_research_datasets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    manifest_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint("status IN ('READY', 'INVALID')", name="ck_quant_research_datasets_status"),
        CheckConstraint("length(manifest_sha256) = 64", name="ck_quant_research_datasets_sha256"),
    )


class QuantFundRepresentativeFreeze(Base):
    """Immutable research-only ETF month selection; never an execution grant."""

    __tablename__ = "quant_fund_representative_freezes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("quant_research_datasets.id", ondelete="RESTRICT"), nullable=False,
    )
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    trial_id: Mapped[str] = mapped_column(String(128), nullable=False)
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluation_as_of: Mapped[date] = mapped_column(Date, nullable=False)
    decision_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("snapshot_id", "trial_id", "evaluation_as_of", "decision_date",
                         name="uq_fund_rep_freeze_identity"),
        CheckConstraint("status = 'DIAGNOSTIC'", name="ck_fund_rep_freeze_status"),
        CheckConstraint("disposition IN ('SELECTED','NO_ELIGIBLE_REPRESENTATIVE')",
                        name="ck_fund_rep_freeze_disposition"),
        CheckConstraint("evaluation_as_of <= decision_date", name="ck_fund_rep_freeze_dates"),
        CheckConstraint("manifest_sha256 ~ '^[0-9a-f]{64}$' AND definition_hash ~ '^[0-9a-f]{64}$' "
                        "AND input_sha256 ~ '^[0-9a-f]{64}$'", name="ck_fund_rep_freeze_hashes"),
    )
