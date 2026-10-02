"""N5 lifecycle/order persistence models.

The fill journal is append-only.  Actual portfolio positions are changed only by
the lifecycle application service in the same transaction as a fill event.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Integer, LargeBinary, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.shared.db import Base, TimestampMixin

# Register the referenced signal table in Base.metadata when this module is
# imported directly by fill services/tests.
from backend.modules.quant_strategy.infrastructure.signals import QuantExecutionSignal as _QuantExecutionSignal  # noqa: F401,E402


def _operation_link_constraints(table):
    return (
        ForeignKeyConstraint(
            ["operation_id", "business_command_id"],
            ["position_lifecycle_operations.id", "position_lifecycle_operations.business_command_id"],
            ondelete="RESTRICT", deferrable=True, initially="DEFERRED", name=f"fk_{table}_operation"),
        CheckConstraint(
            "(operation_link_origin = 'LEGACY_UNLINKED' AND operation_id IS NULL AND business_command_id IS NULL) OR "
            "(operation_link_origin = 'LINKED' AND operation_id IS NOT NULL AND business_command_id IS NOT NULL)",
            name=f"ck_{table}_operation_link"),
    )


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
    __table_args__ = (
        UniqueConstraint("initial_fill_id", name="uq_position_lifecycle_initial_fill"),
        UniqueConstraint("id", "portfolio_id", name="uq_position_lifecycle_portfolio_identity"),
    )

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


class PositionIntentRevision(Base):
    """Immutable local intent writes; migrated baselines have unknown past."""

    __tablename__ = "position_intent_revisions"
    __table_args__ = (
        UniqueConstraint("intent_id", "revision_no", name="uq_position_intent_revision_no"),
        UniqueConstraint("previous_revision_id", name="uq_position_intent_revision_previous"),
        *_operation_link_constraints("position_intent_revisions"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    intent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("position_intents.id", ondelete="RESTRICT"), nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("position_intent_revisions.id", ondelete="RESTRICT"))
    baseline_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    lifecycle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_signal_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    target_shares: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    intent_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    operation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    business_command_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    operation_link_origin: Mapped[str] = mapped_column(String(24), nullable=False, server_default="LEGACY_UNLINKED")


class SuggestedOrder(Base, TimestampMixin):
    __tablename__ = "suggested_orders"
    __table_args__ = (UniqueConstraint("id", "portfolio_id", name="uq_suggested_order_portfolio_identity"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False)
    position_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("portfolio_positions.id", ondelete="SET NULL"), nullable=True)
    lifecycle_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("position_lifecycle_states.id", ondelete="SET NULL"), nullable=True)
    intent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("position_intents.id", ondelete="SET NULL"), nullable=True)
    source_signal_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("quant_execution_signals.id", ondelete="SET NULL"), nullable=True)
    rule_certificate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("quant_instrument_rule_certificates.id", ondelete="RESTRICT"), nullable=True)
    rule_authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    __table_args__ = (UniqueConstraint("id", "portfolio_id", name="uq_order_fill_event_portfolio_identity"),)

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
    lifecycle_id_at_fill: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    intent_id_at_fill: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    binding_origin: Mapped[str] = mapped_column(String(16), nullable=False, server_default="LIVE")


class FillLifecycleVersionStep(Base):
    """Immutable local snapshot; source and transaction visibility remain uncertified."""

    __tablename__ = "fill_lifecycle_version_steps"
    __table_args__ = (
        *_operation_link_constraints("fill_lifecycle_version_steps"),
        CheckConstraint(
            "(origin = 'LOCAL_CAUSAL' AND version_before IS NOT NULL "
            "AND version_after IS NOT NULL AND version_before >= 1 "
            "AND version_after = version_before + 1) OR "
            "(origin = 'UNATTRIBUTED' AND version_before IS NULL "
            "AND version_after IS NULL)",
            name="ck_fill_lifecycle_version_step_shape",
        ),
    )

    fill_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("order_fill_events.id", ondelete="RESTRICT",
                                      deferrable=True, initially="DEFERRED"), primary_key=True)
    lifecycle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("position_lifecycle_states.id", ondelete="RESTRICT"), nullable=False)
    operation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    business_command_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    operation_link_origin: Mapped[str] = mapped_column(String(24), nullable=False, server_default="LEGACY_UNLINKED")
    version_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)


class PositionDailyFact(Base, TimestampMixin):
    __tablename__ = "position_daily_facts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lifecycle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("position_lifecycle_states.id", ondelete="RESTRICT"), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    price_basis: Mapped[str] = mapped_column(String(16), nullable=False)
    data_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    input_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    planning_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    state_version_before: Mapped[int] = mapped_column(Integer, nullable=False)
    state_version_after: Mapped[int] = mapped_column(Integer, nullable=False)
    final_target_shares: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)


class PositionDailyFactRevision(Base):
    """Immutable local write history for the single current daily fact row.

    recorded_at is a database write event time, not a source publication or
    historical visibility certificate.
    """

    __tablename__ = "position_daily_fact_revisions"
    __table_args__ = (
        UniqueConstraint("daily_fact_id", "revision_no", name="uq_daily_fact_revision_no"),
        UniqueConstraint("previous_revision_id", name="uq_daily_fact_revision_previous"),
        *_operation_link_constraints("position_daily_fact_revisions"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    daily_fact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("position_daily_facts.id", ondelete="RESTRICT"), nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_revision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("position_daily_fact_revisions.id", ondelete="RESTRICT"), nullable=True)
    baseline_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    lifecycle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    price_basis: Mapped[str] = mapped_column(String(16), nullable=False)
    data_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    input_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    planning_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    state_version_before: Mapped[int] = mapped_column(Integer, nullable=False)
    state_version_after: Mapped[int] = mapped_column(Integer, nullable=False)
    final_target_shares: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    operation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    business_command_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    operation_link_origin: Mapped[str] = mapped_column(String(24), nullable=False, server_default="LEGACY_UNLINKED")


class PositionDailyFactInputProposal(Base):
    """Immutable diagnostic input correction; it never changes current state."""

    __tablename__ = "position_daily_fact_input_proposals"
    __table_args__ = (
        UniqueConstraint("daily_fact_id", "request_key", name="uq_daily_fact_input_proposal_request"),
        UniqueConstraint("daily_fact_id", "base_revision_id", name="uq_daily_fact_input_proposal_base"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    daily_fact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("position_daily_facts.id", ondelete="RESTRICT"), nullable=False)
    base_revision_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("position_daily_fact_revisions.id", ondelete="RESTRICT"), nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    proposed_price_basis: Mapped[str] = mapped_column(String(16), nullable=False)
    proposed_data_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    proposed_input_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    canonical_input_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    proposed_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=func.clock_timestamp())


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
