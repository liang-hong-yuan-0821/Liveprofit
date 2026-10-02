"""Immutable account observations, separate from portfolio projections and orders."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_observations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("portfolio_id", UUID(as_uuid=True),
                  sa.ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.Column("source_type", sa.String(24), nullable=False),
        sa.Column("source_ref", sa.String(256), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("cash", sa.Numeric(20, 4), nullable=False),
        sa.Column("complete_holdings", sa.Boolean, nullable=False),
        sa.Column("holdings", JSONB, nullable=False),
        sa.UniqueConstraint("portfolio_id", "source_type", "source_ref",
                            name="uq_account_observation_source"),
        sa.UniqueConstraint("id", "portfolio_id", name="uq_account_observation_identity"),
        sa.CheckConstraint("cash >= 0 AND payload_sha256 ~ '^[0-9a-f]{64}$' AND "
                           "jsonb_typeof(holdings) = 'array' AND "
                           "length(trim(source_ref)) > 0 AND "
                           "source_type IN ('MANUAL_IMPORT','BROKER_EXPORT','BROKER_API')",
                           name="ck_account_observation_values"),
    )
    op.create_index("ix_account_observation_date", "account_observations",
                    ["portfolio_id", "trade_date", "received_at"])
    op.execute("""CREATE FUNCTION reject_account_observation_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'account observation history is immutable';
        END; $$""")
    op.execute("CREATE TRIGGER account_observation_immutable BEFORE UPDATE OR DELETE ON account_observations "
               "FOR EACH ROW EXECUTE FUNCTION reject_account_observation_mutation()")
    op.execute("CREATE TRIGGER account_observation_no_truncate BEFORE TRUNCATE ON account_observations "
               "FOR EACH STATEMENT EXECUTE FUNCTION reject_account_observation_mutation()")


def downgrade() -> None:
    op.drop_index("ix_account_observation_date", table_name="account_observations")
    op.drop_table("account_observations")
    op.execute("DROP FUNCTION reject_account_observation_mutation()")
