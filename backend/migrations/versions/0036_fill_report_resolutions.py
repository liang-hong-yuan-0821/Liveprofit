"""Unverified append-only report correction and void declarations."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_fill_report_resolutions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("portfolio_id", UUID(as_uuid=True),
                  sa.ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("report_id", UUID(as_uuid=True), nullable=False),
        sa.Column("replacement_report_id", UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(8), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.Column("source_type", sa.String(24), nullable=False),
        sa.Column("source_ref", sa.String(256), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=True),
        sa.Column("evidence_uri", sa.String(1024), nullable=True),
        sa.Column("reporter_claim", sa.String(256), nullable=True),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["report_id", "portfolio_id"],
                                ["account_fill_reports.id", "account_fill_reports.portfolio_id"],
                                ondelete="RESTRICT", name="fk_fill_resolution_report_portfolio"),
        sa.ForeignKeyConstraint(["replacement_report_id", "portfolio_id"],
                                ["account_fill_reports.id", "account_fill_reports.portfolio_id"],
                                ondelete="RESTRICT", name="fk_fill_resolution_replacement_portfolio"),
        sa.UniqueConstraint("report_id", name="uq_fill_resolution_report"),
        sa.UniqueConstraint("replacement_report_id", name="uq_fill_resolution_replacement"),
        sa.UniqueConstraint("portfolio_id", "source_type", "source_ref",
                            name="uq_fill_resolution_source"),
        sa.CheckConstraint("source_type IN ('MANUAL_REPORT','BROKER_EXPORT','BROKER_API') AND "
                           "report_id IS DISTINCT FROM replacement_report_id AND "
                           "(action = 'CORRECT' AND replacement_report_id IS NOT NULL OR "
                           "action = 'VOID' AND replacement_report_id IS NULL) AND "
                           "length(trim(reason)) > 0 AND length(trim(source_ref)) > 0 AND "
                           "payload_sha256 ~ '^[0-9a-f]{64}$' AND "
                           "(evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$')",
                           name="ck_fill_resolution_values"),
    )
    op.execute("""CREATE FUNCTION reject_fill_resolution_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'fill resolution history is immutable';
        END; $$""")
    op.execute("CREATE TRIGGER fill_resolution_immutable BEFORE UPDATE OR DELETE ON "
               "account_fill_report_resolutions FOR EACH ROW EXECUTE FUNCTION reject_fill_resolution_mutation()")
    op.execute("CREATE TRIGGER fill_resolution_no_truncate BEFORE TRUNCATE ON "
               "account_fill_report_resolutions FOR EACH STATEMENT EXECUTE FUNCTION reject_fill_resolution_mutation()")


def downgrade() -> None:
    op.drop_table("account_fill_report_resolutions")
    op.execute("DROP FUNCTION reject_fill_resolution_mutation()")
