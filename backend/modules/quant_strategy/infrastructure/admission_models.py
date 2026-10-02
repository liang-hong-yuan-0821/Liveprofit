"""Append-only strategy qualification history, independent of code publication."""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.shared.db import Base


class StrategyAdmissionEvent(Base):
    __tablename__ = "strategy_admission_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("quant_strategy_versions.id", ondelete="RESTRICT"))
    family_id: Mapped[str] = mapped_column(String(64))
    asset_scope: Mapped[str] = mapped_column(String(16))
    risk_profile: Mapped[str] = mapped_column(String(16))
    revision: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    request_key: Mapped[str] = mapped_column(String(128))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.clock_timestamp())
    evidence_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    net_expectancy_lower_bound: Mapped[Decimal | None] = mapped_column(Numeric(24, 12), nullable=True)

    __table_args__ = (
        UniqueConstraint("strategy_version_id", "asset_scope", "risk_profile", "revision", name="uq_admission_revision"),
        UniqueConstraint("strategy_version_id", "asset_scope", "risk_profile", "request_key", name="uq_admission_request"),
        CheckConstraint("revision > 0 AND length(trim(family_id)) > 0 AND length(trim(reason)) > 0 AND length(trim(request_key)) > 0", name="ck_admission_identity"),
        CheckConstraint("state IN ('EXPERIMENTAL','VALIDATED','SHADOW','ADVISORY','SUSPENDED','RETIRED')", name="ck_admission_state"),
        CheckConstraint("asset_scope IN ('CN_STOCK','CN_ETF')", name="ck_admission_scope"),
        CheckConstraint("risk_profile IN ('CONSERVATIVE','BALANCED','AGGRESSIVE')", name="ck_admission_profile"),
        CheckConstraint("state NOT IN ('VALIDATED','SHADOW','ADVISORY') OR (evidence_ref IS NOT NULL AND length(trim(evidence_ref)) > 0 AND evidence_sha256 IS NOT NULL AND evidence_sha256 ~ '^[0-9a-f]{64}$' AND evidence_completed_at IS NOT NULL AND evidence_completed_at <= recorded_at)", name="ck_admission_evidence"),
        CheckConstraint("state != 'ADVISORY' OR (valid_until IS NOT NULL AND valid_until > recorded_at AND net_expectancy_lower_bound IS NOT NULL AND net_expectancy_lower_bound > 0 AND net_expectancy_lower_bound != 'NaN'::numeric)", name="ck_admission_advisory"),
        Index("ix_admission_asof", "strategy_version_id", "asset_scope", "risk_profile", "recorded_at"),
    )
