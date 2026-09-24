"""position lifecycle persistence contract

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    jsonb = postgresql.JSONB(astext_type=sa.Text())

    op.create_table(
        "lifecycle_policy_versions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("policy_key", sa.String(64), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("required_fields", jsonb, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("config", jsonb, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("policy_key", "version_no", name="uq_lifecycle_policy_key_version"),
        sa.CheckConstraint("status IN ('DRAFT','PUBLISHED','ARCHIVED')", name="ck_lifecycle_policy_status"),
    )
    op.add_column("quant_strategy_versions", sa.Column("lifecycle_policy_version_id", uuid, nullable=True))
    op.create_foreign_key(
        "fk_strategy_version_lifecycle_policy", "quant_strategy_versions", "lifecycle_policy_versions",
        ["lifecycle_policy_version_id"], ["id"], ondelete="RESTRICT",
    )

    op.create_table(
        "position_lifecycle_states",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("portfolio_id", uuid, sa.ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("position_id", uuid, sa.ForeignKey("portfolio_positions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("market", sa.String(8), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("strategy_version_id", uuid, sa.ForeignKey("quant_strategy_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("lifecycle_policy_version_id", uuid, sa.ForeignKey("lifecycle_policy_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("initial_fill_id", uuid, nullable=True),
        sa.Column("initial_fill_price", sa.Numeric(20, 4), nullable=True),
        sa.Column("initial_stop_price", sa.Numeric(20, 4), nullable=True),
        sa.Column("risk_capacity_shares", sa.Numeric(20, 4), nullable=True),
        sa.Column("target_exposure_pct", sa.Numeric(8, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("target_shares", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("phase", sa.String(32), nullable=False, server_default=sa.text("'PENDING_FILL'")),
        sa.Column("profit_take_price", sa.Numeric(20, 4), nullable=True),
        sa.Column("profit_target_reached", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("confirmation_completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("profit_trim_completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("arc_neckline_price", sa.Numeric(20, 4), nullable=True),
        sa.Column("state_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("last_processed_trade_date", sa.Date(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("target_exposure_pct >= 0 AND target_exposure_pct <= 1", name="ck_lifecycle_target_exposure"),
        sa.CheckConstraint("target_shares >= 0", name="ck_lifecycle_target_shares"),
    )
    op.create_index(
        "uq_position_lifecycle_active", "position_lifecycle_states",
        ["portfolio_id", "market", "symbol"], unique=True,
        postgresql_where=sa.text("closed_at IS NULL"),
    )

    op.create_table(
        "position_intents",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("lifecycle_id", uuid, sa.ForeignKey("position_lifecycle_states.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_signal_id", sa.BigInteger(), sa.ForeignKey("quant_execution_signals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("target_shares", sa.Numeric(20, 4), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'ACTIVE'")),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        *_timestamps(),
        sa.UniqueConstraint("lifecycle_id", "trade_date", "target_shares", "reason_code", name="uq_position_intent_daily_target"),
        sa.CheckConstraint("target_shares >= 0", name="ck_position_intent_target"),
        sa.CheckConstraint("status IN ('ACTIVE','EXECUTING','COMPLETED','CANCELLED','SUPERSEDED','RECONCILIATION_REQUIRED')", name="ck_position_intent_status"),
    )
    op.create_index(
        "uq_position_intent_active", "position_intents", ["lifecycle_id"], unique=True,
        postgresql_where=sa.text("status IN ('ACTIVE','EXECUTING','RECONCILIATION_REQUIRED')"),
    )

    op.create_table(
        "suggested_orders",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("portfolio_id", uuid, sa.ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("position_id", uuid, sa.ForeignKey("portfolio_positions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("lifecycle_id", uuid, sa.ForeignKey("position_lifecycle_states.id", ondelete="SET NULL"), nullable=True),
        sa.Column("intent_id", uuid, sa.ForeignKey("position_intents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_signal_id", sa.BigInteger(), sa.ForeignKey("quant_execution_signals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("market", sa.String(8), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("industry_code", sa.String(32), nullable=True),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 4), nullable=False),
        sa.Column("filled_quantity", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("limit_price", sa.Numeric(20, 4), nullable=False),
        sa.Column("stop_price", sa.Numeric(20, 4), nullable=True),
        sa.Column("reserved_cash", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("reserved_risk", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'PROPOSED'")),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("earliest_execution_trade_date", sa.Date(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("side IN ('BUY','SELL')", name="ck_suggested_order_side"),
        sa.CheckConstraint("quantity > 0 AND filled_quantity >= 0 AND filled_quantity <= quantity", name="ck_suggested_order_quantities"),
        sa.CheckConstraint("limit_price > 0 AND reserved_cash >= 0 AND reserved_risk >= 0", name="ck_suggested_order_amounts"),
        sa.CheckConstraint("status IN ('PROPOSED','EXECUTING','PARTIALLY_FILLED','FILLED','REJECTED','CANCELLED','RECONCILIATION_REQUIRED','SUPERSEDED')", name="ck_suggested_order_status"),
    )
    op.create_index("ix_suggested_orders_portfolio_status", "suggested_orders", ["portfolio_id", "status"])
    op.create_index(
        "uq_suggested_orders_source_signal", "suggested_orders", ["source_signal_id"],
        unique=True, postgresql_where=sa.text("source_signal_id IS NOT NULL"),
    )

    op.create_table(
        "order_fill_events",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("order_id", uuid, sa.ForeignKey("suggested_orders.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("portfolio_id", uuid, sa.ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("position_id", uuid, sa.ForeignKey("portfolio_positions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("reverses_fill_id", uuid, sa.ForeignKey("order_fill_events.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("quantity", sa.Numeric(20, 4), nullable=False),
        sa.Column("fill_price", sa.Numeric(20, 4), nullable=False),
        sa.Column("fill_trade_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False, server_default=sa.text("'MANUAL'")),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("idempotency_key", name="uq_order_fill_event_idempotency"),
        sa.UniqueConstraint("reverses_fill_id", name="uq_order_fill_event_reversal"),
        sa.CheckConstraint("event_type IN ('CONFIRM','CORRECT','VOID')", name="ck_order_fill_event_type"),
        sa.CheckConstraint("quantity > 0 AND fill_price > 0", name="ck_order_fill_event_values"),
    )
    op.create_foreign_key(
        "fk_lifecycle_initial_fill", "position_lifecycle_states", "order_fill_events",
        ["initial_fill_id"], ["id"], ondelete="RESTRICT",
    )

    op.create_table(
        "position_daily_facts",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("lifecycle_id", uuid, sa.ForeignKey("position_lifecycle_states.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("price_basis", sa.String(16), nullable=False),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_payload", jsonb, nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column("state_version_before", sa.Integer(), nullable=False),
        sa.Column("state_version_after", sa.Integer(), nullable=False),
        sa.Column("final_target_shares", sa.Numeric(20, 4), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("lifecycle_id", "trade_date", name="uq_position_daily_fact_day"),
    )

    op.create_table(
        "position_expectations",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("lifecycle_id", uuid, sa.ForeignKey("position_lifecycle_states.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("fill_trade_date", sa.Date(), nullable=False),
        sa.Column("window_trading_days", sa.Integer(), nullable=False),
        sa.Column("observed_trading_days", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("fulfilled_trade_date", sa.Date(), nullable=True),
        sa.Column("last_processed_trade_date", sa.Date(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("window_trading_days > 0 AND observed_trading_days >= 0", name="ck_position_expectation_window"),
        sa.CheckConstraint("status IN ('PENDING','FULFILLED','EXPIRED','UNAVAILABLE')", name="ck_position_expectation_status"),
    )

    op.create_table(
        "position_trailing_stops",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("lifecycle_id", uuid, sa.ForeignKey("position_lifecycle_states.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("initial_stop_price", sa.Numeric(20, 4), nullable=False),
        sa.Column("high_water_mark", sa.Numeric(20, 4), nullable=False),
        sa.Column("active_stop_price", sa.Numeric(20, 4), nullable=False),
        sa.Column("phase", sa.String(16), nullable=False, server_default=sa.text("'PROTECT'")),
        sa.Column("config_snapshot", jsonb, nullable=True),
        sa.Column("last_processed_trade_date", sa.Date(), nullable=True),
        sa.Column("exit_intent_id", uuid, sa.ForeignKey("position_intents.id", ondelete="SET NULL"), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("initial_stop_price > 0 AND high_water_mark > 0 AND active_stop_price > 0", name="ck_position_trailing_stop_prices"),
    )


def downgrade() -> None:
    op.drop_table("position_trailing_stops")
    op.drop_table("position_expectations")
    op.drop_table("position_daily_facts")
    op.drop_constraint("fk_lifecycle_initial_fill", "position_lifecycle_states", type_="foreignkey")
    op.drop_table("order_fill_events")
    op.drop_table("suggested_orders")
    op.drop_table("position_intents")
    op.drop_index("uq_position_lifecycle_active", table_name="position_lifecycle_states")
    op.drop_table("position_lifecycle_states")
    op.drop_constraint("fk_strategy_version_lifecycle_policy", "quant_strategy_versions", type_="foreignkey")
    op.drop_column("quant_strategy_versions", "lifecycle_policy_version_id")
    op.drop_table("lifecycle_policy_versions")
