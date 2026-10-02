"""Immutable account drawdown state changes and per-position exit targets."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, Numeric
from sqlalchemy import String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.shared.db import Base


class PortfolioRiskEvent(Base):
    __tablename__ = "quant_portfolio_risk_events"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    portfolio_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    facts_as_of: Mapped[date] = mapped_column(Date, nullable=False)
    risk_profile: Mapped[str] = mapped_column(String(16), nullable=False)
    net_asset_value: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    peak_net_asset_value: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    drawdown_limit: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    facts_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    review_signature: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp())

    __table_args__ = (
        UniqueConstraint("portfolio_id", "revision", name="uq_portfolio_risk_event_revision"),
        CheckConstraint("revision > 0 AND kind IN ('PAUSE','RESUME') AND "
                        "risk_profile IN ('CONSERVATIVE','BALANCED','AGGRESSIVE') AND "
                        "net_asset_value > 0 AND peak_net_asset_value >= net_asset_value AND "
                        "drawdown_limit > 0 AND length(trim(reason)) > 0 AND "
                        "facts_sha256 ~ '^[0-9a-f]{64}$' AND "
                        "((kind = 'PAUSE' AND reviewed_by IS NULL AND review_signature IS NULL) OR "
                        "(kind = 'RESUME' AND reviewed_by IS NOT NULL AND length(trim(reviewed_by)) > 0 "
                        "AND review_signature ~ '^[0-9a-f]{64}$'))",
                        name="ck_portfolio_risk_event_values"),
    )


class PortfolioExitTarget(Base):
    __tablename__ = "quant_portfolio_exit_targets"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    pause_event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("quant_portfolio_risk_events.id", ondelete="RESTRICT"),
        nullable=False)
    position_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("portfolio_positions.id", ondelete="RESTRICT"), nullable=False)
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity_at_capture: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp())

    __table_args__ = (
        UniqueConstraint("pause_event_id", "position_id", name="uq_portfolio_exit_target_position"),
        CheckConstraint("quantity_at_capture > 0 AND length(trim(market)) > 0 AND "
                        "length(trim(symbol)) > 0", name="ck_portfolio_exit_target_values"),
    )
