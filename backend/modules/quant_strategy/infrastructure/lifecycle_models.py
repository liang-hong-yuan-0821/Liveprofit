"""N5 lifecycle/order persistence models.

The fill journal is append-only.  Actual portfolio positions are changed only by
the lifecycle application service in the same transaction as a fill event.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.shared.db import Base, TimestampMixin

# Register the referenced signal table in Base.metadata when this module is
# imported directly by fill services/tests.
from backend.modules.quant_strategy.infrastructure.signals import QuantExecutionSignal as _QuantExecutionSignal  # noqa: F401,E402


class LifecyclePolicyVersion(Base, TimestampMixin):
    __tablename__ = "lifecycle_policy_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    policy_key: Mapped[str] = mapped_column(String(64), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    required_fields: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PositionLifecycleState(Base, TimestampMixin):
    __tablename__ = "position_lifecycle_states"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False)
    position_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("portfolio_positions.id", ondelete="SET NULL"), nullable=True)
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("quant_strategy_versions.id", ondelete="RESTRICT"), nullable=False)
    lifecycle_policy_version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("lifecycle_policy_versions.id", ondelete="RESTRICT"), nullable=False)
    initial_fill_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("order_fill_events.id", ondelete="RESTRICT"), nullable=True)
    initial_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    initial_stop_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    risk_capacity_shares: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    target_exposure_pct: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False, default=Decimal(0))
    target_shares: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False, default=Decimal(0))
    phase: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING_FILL")
    profit_take_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    profit_target_reached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confirmation_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    profit_trim_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    arc_neckline_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_processed_trade_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PositionIntent(Base, TimestampMixin):
    __tablename__ = "position_intents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lifecycle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("position_lifecycle_states.id", ondelete="RESTRICT"), nullable=False)
    source_signal_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("quant_execution_signals.id", ondelete="SET NULL"), nullable=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_shares: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class SuggestedOrder(Base, TimestampMixin):
    __tablename__ = "suggested_orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False)
    position_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("portfolio_positions.id", ondelete="SET NULL"), nullable=True)
    lifecycle_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("position_lifecycle_states.id", ondelete="SET NULL"), nullable=True)
    intent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("position_intents.id", ondelete="SET NULL"), nullable=True)
    source_signal_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("quant_execution_signals.id", ondelete="SET NULL"), nullable=True)
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    industry_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    filled_quantity: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False, default=Decimal(0))
    limit_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    reserved_cash: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False, default=Decimal(0))
    reserved_risk: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False, default=Decimal(0))
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PROPOSED")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    earliest_execution_trade_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class OrderFillEvent(Base, TimestampMixin):
    __tablename__ = "order_fill_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suggested_orders.id", ondelete="RESTRICT"), nullable=False)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False)
    position_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("portfolio_positions.id", ondelete="SET NULL"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    reverses_fill_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("order_fill_events.id", ondelete="RESTRICT"), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    fill_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    fill_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="MANUAL")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class PositionDailyFact(Base, TimestampMixin):
    __tablename__ = "position_daily_facts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lifecycle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("position_lifecycle_states.id", ondelete="RESTRICT"), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    price_basis: Mapped[str] = mapped_column(String(16), nullable=False)
    data_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    input_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    state_version_before: Mapped[int] = mapped_column(Integer, nullable=False)
    state_version_after: Mapped[int] = mapped_column(Integer, nullable=False)
    final_target_shares: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)


class PositionExpectation(Base, TimestampMixin):
    __tablename__ = "position_expectations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lifecycle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("position_lifecycle_states.id", ondelete="RESTRICT"), nullable=False, unique=True)
    fill_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    window_trading_days: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_trading_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    fulfilled_trade_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_processed_trade_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class PositionTrailingStop(Base, TimestampMixin):
    __tablename__ = "position_trailing_stops"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lifecycle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("position_lifecycle_states.id", ondelete="RESTRICT"), nullable=False, unique=True)
    initial_stop_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    high_water_mark: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    active_stop_price: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False, default="PROTECT")
    config_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_processed_trade_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    exit_intent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("position_intents.id", ondelete="SET NULL"), nullable=True)
