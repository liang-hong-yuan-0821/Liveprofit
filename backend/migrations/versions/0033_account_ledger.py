"""Append-only account replay baselines and movements."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_ledger_baselines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("portfolio_id", UUID(as_uuid=True),
                  sa.ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("observation_id", UUID(as_uuid=True), nullable=False),
        sa.Column("observation_sha256", sa.String(64), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.UniqueConstraint("portfolio_id", name="uq_account_ledger_baseline_portfolio"),
        sa.UniqueConstraint("observation_id", name="uq_account_ledger_baseline_observation"),
        sa.UniqueConstraint("id", "portfolio_id", name="uq_account_ledger_baseline_identity"),
        sa.ForeignKeyConstraint(["observation_id", "portfolio_id"],
                                ["account_observations.id", "account_observations.portfolio_id"],
                                ondelete="RESTRICT", name="fk_account_ledger_observation_portfolio"),
    )
    op.create_table(
        "account_ledger_movements",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("portfolio_id", UUID(as_uuid=True),
                  sa.ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("baseline_id", UUID(as_uuid=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("cash_delta", sa.Numeric(20, 4), nullable=False),
        sa.Column("holdings_delta", JSONB, nullable=False),
        sa.Column("fill_price", sa.Numeric(20, 4), nullable=True),
        sa.Column("fee", sa.Numeric(20, 4), nullable=False),
        sa.Column("supersedes_id", UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.String(512), nullable=True),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_ref", sa.String(256), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.UniqueConstraint("portfolio_id", "source_type", "source_ref",
                            name="uq_account_ledger_movement_source"),
        sa.UniqueConstraint("supersedes_id", name="uq_account_ledger_movement_supersedes"),
        sa.UniqueConstraint("id", "portfolio_id", name="uq_account_ledger_movement_identity"),
        sa.ForeignKeyConstraint(["baseline_id", "portfolio_id"],
                                ["account_ledger_baselines.id", "account_ledger_baselines.portfolio_id"],
                                ondelete="RESTRICT", name="fk_account_ledger_baseline_portfolio"),
        sa.ForeignKeyConstraint(["supersedes_id", "portfolio_id"],
                                ["account_ledger_movements.id", "account_ledger_movements.portfolio_id"],
                                ondelete="RESTRICT", name="fk_account_ledger_supersedes_portfolio"),
        sa.CheckConstraint("kind IN ('CASH_FLOW','TRADE','CORPORATE_ACTION','ADJUSTMENT') AND "
                           "jsonb_typeof(holdings_delta) = 'array' AND "
                           "fee >= 0 AND length(trim(source_ref)) > 0 AND "
                           "payload_sha256 ~ '^[0-9a-f]{64}$'",
                           name="ck_account_ledger_movement_values"),
    )
    op.create_index("ix_account_ledger_movement_portfolio_time", "account_ledger_movements",
                    ["portfolio_id", "recorded_at", "id"])
    op.execute("""CREATE FUNCTION reject_account_ledger_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'account ledger history is immutable';
        END; $$""")
    for table in ("account_ledger_baselines", "account_ledger_movements"):
        op.execute(f"CREATE TRIGGER account_ledger_immutable BEFORE UPDATE OR DELETE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION reject_account_ledger_mutation()")
        op.execute(f"CREATE TRIGGER account_ledger_no_truncate BEFORE TRUNCATE ON {table} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION reject_account_ledger_mutation()")


def downgrade() -> None:
    op.drop_index("ix_account_ledger_movement_portfolio_time", table_name="account_ledger_movements")
    op.drop_table("account_ledger_movements")
    op.drop_table("account_ledger_baselines")
    op.execute("DROP FUNCTION reject_account_ledger_mutation()")
