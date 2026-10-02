"""Append-only account replay facts; these rows do not certify trading balances."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.shared.db import Base


class AccountLedgerBaselineRow(Base):
    __tablename__ = "account_ledger_baselines"
    __table_args__ = (
        ForeignKeyConstraint(["observation_id", "portfolio_id"],
                             ["account_observations.id", "account_observations.portfolio_id"],
                             ondelete="RESTRICT", name="fk_account_ledger_observation_portfolio"),
        UniqueConstraint("portfolio_id", name="uq_account_ledger_baseline_portfolio"),
        UniqueConstraint("observation_id", name="uq_account_ledger_baseline_observation"),
        UniqueConstraint("id", "portfolio_id", name="uq_account_ledger_baseline_identity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False)
    observation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    observation_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=func.clock_timestamp())


class AccountLedgerMovementRow(Base):
    __tablename__ = "account_ledger_movements"
    __table_args__ = (
        ForeignKeyConstraint(["baseline_id", "portfolio_id"],
                             ["account_ledger_baselines.id", "account_ledger_baselines.portfolio_id"],
                             ondelete="RESTRICT", name="fk_account_ledger_baseline_portfolio"),
        ForeignKeyConstraint(["supersedes_id", "portfolio_id"],
                             ["account_ledger_movements.id", "account_ledger_movements.portfolio_id"],
                             ondelete="RESTRICT", name="fk_account_ledger_supersedes_portfolio"),
        UniqueConstraint("portfolio_id", "source_type", "source_ref", name="uq_account_ledger_movement_source"),
        UniqueConstraint("supersedes_id", name="uq_account_ledger_movement_supersedes"),
        UniqueConstraint("id", "portfolio_id", name="uq_account_ledger_movement_identity"),
        CheckConstraint("kind IN ('CASH_FLOW','TRADE','CORPORATE_ACTION','ADJUSTMENT','VOID') AND "
                        "jsonb_typeof(holdings_delta) = 'array' AND "
                        "fee >= 0 AND length(trim(source_ref)) > 0 AND "
                        "payload_sha256 ~ '^[0-9a-f]{64}$' AND "
                        "(kind <> 'VOID' OR (supersedes_id IS NOT NULL AND cash_delta = 0 "
                        "AND holdings_delta = '[]'::jsonb AND fill_price IS NULL "
                        "AND fee = 0 AND reason IS NOT NULL AND length(trim(reason)) > 0))",
                        name="ck_account_ledger_movement_values"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False)
    baseline_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    cash_delta: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    holdings_delta: Mapped[list] = mapped_column(JSONB, nullable=False)
    fill_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    fee: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
