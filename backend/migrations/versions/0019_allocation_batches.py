"""Independent joint allocation audit, without adding a scheduler."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("quant_allocation_batches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("portfolio_id", UUID(as_uuid=True), sa.ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("asset_scope", sa.String(16), nullable=False),
        sa.Column("valuation_date", sa.Date, nullable=False),
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("account_snapshot", JSONB, nullable=False),
        sa.Column("result", JSONB, nullable=False),
        sa.UniqueConstraint("portfolio_id", "request_key", name="uq_allocation_request"),
        sa.CheckConstraint("status IN ('PROJECTED','BLOCKED')", name="ck_allocation_status"),
        sa.CheckConstraint("asset_scope IN ('CN_STOCK','CN_ETF')", name="ck_allocation_scope"),
        sa.CheckConstraint("length(trim(request_key)) > 0 AND input_hash ~ '^[0-9a-f]{64}$'", name="ck_allocation_identity"),
    )
    op.create_table("quant_allocation_members",
        sa.Column("batch_id", UUID(as_uuid=True), sa.ForeignKey("quant_allocation_batches.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("strategy_version_id", UUID(as_uuid=True), sa.ForeignKey("quant_strategy_versions.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("family_id", sa.String(64), nullable=False),
        sa.Column("admission_event_id", UUID(as_uuid=True), sa.ForeignKey("strategy_admission_events.id", ondelete="RESTRICT")),
        sa.Column("admission_code", sa.String(64), nullable=False),
        sa.Column("intent", JSONB, nullable=False),
        sa.UniqueConstraint("batch_id", "family_id", name="uq_allocation_family"),
    )
    op.execute("""CREATE FUNCTION reject_allocation_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'allocation decision history is immutable'; END; $$""")
    for table in ("quant_allocation_batches", "quant_allocation_members"):
        op.execute(f"CREATE TRIGGER allocation_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_allocation_mutation()")


def downgrade():
    op.drop_table("quant_allocation_members")
    op.drop_table("quant_allocation_batches")
    op.execute("DROP FUNCTION reject_allocation_mutation()")
