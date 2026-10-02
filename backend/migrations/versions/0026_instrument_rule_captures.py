"""Keep original exchange fetch events separate from declared rule sources."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quant_instrument_rule_captures",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_id", UUID(as_uuid=True),
                  sa.ForeignKey("quant_instrument_rule_sources.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("requested_uri", sa.Text, nullable=False),
        sa.Column("final_uri", sa.Text, nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.CheckConstraint("length(trim(requested_uri)) > 0 AND length(trim(final_uri)) > 0",
                           name="ck_instrument_rule_capture_uris"),
        sa.CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'",
                           name="ck_instrument_rule_capture_hash"),
    )
    op.create_index("ix_instrument_rule_capture_source", "quant_instrument_rule_captures",
                    ["source_id", "recorded_at"])
    op.execute("CREATE TRIGGER instrument_rule_capture_immutable BEFORE UPDATE OR DELETE "
               "ON quant_instrument_rule_captures FOR EACH ROW EXECUTE FUNCTION reject_allocation_mutation()")
    op.execute("CREATE TRIGGER instrument_rule_capture_no_truncate BEFORE TRUNCATE "
               "ON quant_instrument_rule_captures FOR EACH STATEMENT "
               "EXECUTE FUNCTION reject_allocation_mutation()")


def downgrade() -> None:
    op.drop_table("quant_instrument_rule_captures")
