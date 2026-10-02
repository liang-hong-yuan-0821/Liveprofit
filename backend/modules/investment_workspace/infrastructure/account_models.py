"""Immutable, unverified account observations; no trading authorization lives here."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.shared.db import Base


class AccountObservationRow(Base):
    __tablename__ = "account_observations"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "source_type", "source_ref", name="uq_account_observation_source"),
        UniqueConstraint("id", "portfolio_id", name="uq_account_observation_identity"),
        CheckConstraint("cash >= 0 AND payload_sha256 ~ '^[0-9a-f]{64}$' AND "
                        "jsonb_typeof(holdings) = 'array' AND "
                        "length(trim(source_ref)) > 0 AND "
                        "source_type IN ('MANUAL_IMPORT','BROKER_EXPORT','BROKER_API')",
                        name="ck_account_observation_values"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=func.clock_timestamp())
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    cash: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    complete_holdings: Mapped[bool] = mapped_column(Boolean, nullable=False)
    holdings: Mapped[list] = mapped_column(JSONB, nullable=False)
