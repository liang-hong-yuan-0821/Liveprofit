"""Immutable reported fills awaiting separate acceptance and projection."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_suggested_order_portfolio_identity", "suggested_orders",
                                ["id", "portfolio_id"])
    op.create_table(
        "account_fill_reports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("portfolio_id", UUID(as_uuid=True),
                  sa.ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("order_id", UUID(as_uuid=True), nullable=True),
        sa.Column("market", sa.String(8), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("fill_trade_date", sa.Date, nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.Column("quantity", sa.Numeric(20, 4), nullable=False),
        sa.Column("fill_price", sa.Numeric(20, 4), nullable=False),
        sa.Column("fee", sa.Numeric(20, 4), nullable=True),
        sa.Column("source_type", sa.String(24), nullable=False),
        sa.Column("source_ref", sa.String(256), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=True),
        sa.Column("evidence_uri", sa.String(1024), nullable=True),
        sa.Column("reporter_claim", sa.String(256), nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["order_id", "portfolio_id"],
                                ["suggested_orders.id", "suggested_orders.portfolio_id"],
                                ondelete="RESTRICT", name="fk_account_fill_report_order_portfolio"),
        sa.UniqueConstraint("portfolio_id", "source_type", "source_ref",
                            name="uq_account_fill_report_source"),
        sa.CheckConstraint("quantity > 0 AND fill_price > 0 AND "
                           "(fee IS NULL OR fee >= 0) AND "
                           "side IN ('BUY','SELL') AND length(trim(market)) > 0 AND length(trim(symbol)) > 0 AND "
                           "source_type IN ('MANUAL_REPORT','BROKER_EXPORT','BROKER_API') AND "
                           "length(trim(source_ref)) > 0 AND "
                           "payload_sha256 ~ '^[0-9a-f]{64}$' AND "
                           "(evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$')",
                           name="ck_account_fill_report_values"),
    )
    op.create_index("ix_account_fill_report_order_time", "account_fill_reports",
                    ["portfolio_id", "order_id", "recorded_at"])
    op.execute("""CREATE FUNCTION reject_account_fill_report_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'account fill report history is immutable';
        END; $$""")
    op.execute("CREATE TRIGGER account_fill_report_immutable BEFORE UPDATE OR DELETE ON account_fill_reports "
               "FOR EACH ROW EXECUTE FUNCTION reject_account_fill_report_mutation()")
    op.execute("CREATE TRIGGER account_fill_report_no_truncate BEFORE TRUNCATE ON account_fill_reports "
               "FOR EACH STATEMENT EXECUTE FUNCTION reject_account_fill_report_mutation()")


def downgrade() -> None:
    op.drop_index("ix_account_fill_report_order_time", table_name="account_fill_reports")
    op.drop_table("account_fill_reports")
    op.execute("DROP FUNCTION reject_account_fill_report_mutation()")
    op.drop_constraint("uq_suggested_order_portfolio_identity", "suggested_orders", type_="unique")
