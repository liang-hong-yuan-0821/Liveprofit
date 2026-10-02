"""Persist account drawdown pauses and zero-target exit intentions."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quant_portfolio_risk_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("portfolio_id", UUID(as_uuid=True),
                  sa.ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("facts_as_of", sa.Date, nullable=False),
        sa.Column("risk_profile", sa.String(16), nullable=False),
        sa.Column("net_asset_value", sa.Numeric(20, 4), nullable=False),
        sa.Column("peak_net_asset_value", sa.Numeric(20, 4), nullable=False),
        sa.Column("drawdown_limit", sa.Numeric(8, 6), nullable=False),
        sa.Column("facts_sha256", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("reviewed_by", sa.String(128), nullable=True),
        sa.Column("review_signature", sa.String(64), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.UniqueConstraint("portfolio_id", "revision", name="uq_portfolio_risk_event_revision"),
        sa.CheckConstraint("revision > 0 AND kind IN ('PAUSE','RESUME') AND "
                           "risk_profile IN ('CONSERVATIVE','BALANCED','AGGRESSIVE') AND "
                           "net_asset_value > 0 AND peak_net_asset_value >= net_asset_value AND "
                           "drawdown_limit > 0 AND length(trim(reason)) > 0 AND "
                           "facts_sha256 ~ '^[0-9a-f]{64}$' AND "
                           "((kind = 'PAUSE' AND reviewed_by IS NULL AND review_signature IS NULL) OR "
                           "(kind = 'RESUME' AND reviewed_by IS NOT NULL AND length(trim(reviewed_by)) > 0 "
                           "AND review_signature ~ '^[0-9a-f]{64}$'))",
                           name="ck_portfolio_risk_event_values"),
    )
    op.create_table(
        "quant_portfolio_exit_targets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("pause_event_id", UUID(as_uuid=True),
                  sa.ForeignKey("quant_portfolio_risk_events.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("position_id", UUID(as_uuid=True),
                  sa.ForeignKey("portfolio_positions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("market", sa.String(8), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("quantity_at_capture", sa.Numeric(20, 4), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.UniqueConstraint("pause_event_id", "position_id", name="uq_portfolio_exit_target_position"),
        sa.CheckConstraint("quantity_at_capture > 0 AND length(trim(market)) > 0 AND "
                           "length(trim(symbol)) > 0", name="ck_portfolio_exit_target_values"),
    )
    op.create_index("ix_portfolio_risk_event_latest", "quant_portfolio_risk_events",
                    ["portfolio_id", "revision"])
    op.execute("""CREATE FUNCTION validate_portfolio_exit_target_owner() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (SELECT portfolio_id FROM portfolio_positions WHERE id=NEW.position_id)
               IS DISTINCT FROM
               (SELECT portfolio_id FROM quant_portfolio_risk_events WHERE id=NEW.pause_event_id)
            THEN RAISE EXCEPTION 'portfolio exit target owner mismatch'; END IF;
            IF (SELECT (market, symbol) FROM portfolio_positions WHERE id=NEW.position_id)
               IS DISTINCT FROM (NEW.market, NEW.symbol)
            THEN RAISE EXCEPTION 'portfolio exit target instrument mismatch'; END IF;
            IF (SELECT kind FROM quant_portfolio_risk_events WHERE id=NEW.pause_event_id) != 'PAUSE'
            THEN RAISE EXCEPTION 'portfolio exit target requires pause event'; END IF;
            RETURN NEW;
        END; $$""")
    op.execute("CREATE TRIGGER portfolio_exit_target_owner BEFORE INSERT ON quant_portfolio_exit_targets "
               "FOR EACH ROW EXECUTE FUNCTION validate_portfolio_exit_target_owner()")
    for table in ("quant_portfolio_risk_events", "quant_portfolio_exit_targets"):
        op.execute(f"CREATE TRIGGER portfolio_risk_immutable BEFORE UPDATE OR DELETE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION reject_allocation_mutation()")
        op.execute(f"CREATE TRIGGER portfolio_risk_no_truncate BEFORE TRUNCATE ON {table} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION reject_allocation_mutation()")


def downgrade() -> None:
    op.drop_table("quant_portfolio_exit_targets")
    op.drop_table("quant_portfolio_risk_events")
    op.execute("DROP FUNCTION validate_portfolio_exit_target_owner()")
