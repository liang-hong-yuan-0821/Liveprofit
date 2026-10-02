"""Preserve bounded raw account fill evidence bytes."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_fill_evidence_artifacts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("portfolio_id", UUID(as_uuid=True),
                  sa.ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_ref", sa.String(256), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("raw_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.UniqueConstraint("portfolio_id", "source_ref", name="uq_fill_evidence_source"),
        sa.UniqueConstraint("portfolio_id", "content_sha256", name="uq_fill_evidence_content"),
        sa.CheckConstraint(
            "length(trim(source_ref)) > 0 AND length(trim(media_type)) > 0 AND "
            "content_sha256 ~ '^[0-9a-f]{64}$' AND "
            "size_bytes BETWEEN 1 AND 8388608 AND octet_length(raw_bytes) = size_bytes",
            name="ck_fill_evidence_values"),
    )
    op.execute("""CREATE FUNCTION reject_fill_evidence_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'fill evidence history is immutable';
        END; $$""")
    op.execute("CREATE TRIGGER fill_evidence_immutable BEFORE UPDATE OR DELETE ON "
               "account_fill_evidence_artifacts FOR EACH ROW EXECUTE FUNCTION reject_fill_evidence_mutation()")
    op.execute("CREATE TRIGGER fill_evidence_no_truncate BEFORE TRUNCATE ON "
               "account_fill_evidence_artifacts FOR EACH STATEMENT EXECUTE FUNCTION reject_fill_evidence_mutation()")


def downgrade() -> None:
    op.drop_table("account_fill_evidence_artifacts")
    op.execute("DROP FUNCTION reject_fill_evidence_mutation()")
