"""Permanent target trial deployment binding; this is not admission."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.shared.db import Base


class TargetTrialBinding(Base):
    __tablename__ = "quant_target_trial_bindings"

    strategy_version_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("quant_strategy_versions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    trial_id: Mapped[str] = mapped_column(String(128), nullable=False)
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    family_id: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_scope: Mapped[str] = mapped_column(String(16), nullable=False)
    scanner_source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    lifecycle_policy_version_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("lifecycle_policy_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    trial_spec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    management_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp(),
    )

    __table_args__ = (
        CheckConstraint("length(trim(trial_id)) > 0 AND length(trim(family_id)) > 0",
                        name="ck_target_binding_identity"),
        CheckConstraint("asset_scope IN ('CN_STOCK','CN_ETF')",
                        name="ck_target_binding_scope"),
        CheckConstraint("definition_hash ~ '^[0-9a-f]{64}$' AND scanner_source_hash ~ '^[0-9a-f]{64}$'",
                        name="ck_target_binding_hashes"),
    )
