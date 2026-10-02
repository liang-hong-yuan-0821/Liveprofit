"""Immutable diagnostic assessments of reported fills."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_account_fill_report_identity", "account_fill_reports",
                                ["id", "portfolio_id"])
    op.create_table(
        "account_fill_report_assessments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("portfolio_id", UUID(as_uuid=True),
                  sa.ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("report_id", UUID(as_uuid=True), nullable=False),
        sa.Column("order_revision", sa.Integer, nullable=True),
        sa.Column("issues", JSONB, nullable=False),
        sa.Column("assessment_ref", sa.String(256), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.ForeignKeyConstraint(["report_id", "portfolio_id"],
                                ["account_fill_reports.id", "account_fill_reports.portfolio_id"],
                                ondelete="RESTRICT", name="fk_fill_assessment_report_portfolio"),
        sa.UniqueConstraint("portfolio_id", "assessment_ref", name="uq_fill_assessment_ref"),
        sa.CheckConstraint("jsonb_typeof(issues) = 'array' AND length(trim(assessment_ref)) > 0 AND "
                           "payload_sha256 ~ '^[0-9a-f]{64}$'", name="ck_fill_assessment_values"),
    )
    op.create_index("ix_fill_assessment_report_time", "account_fill_report_assessments",
                    ["report_id", "recorded_at", "id"])
    op.execute("""CREATE FUNCTION reject_fill_assessment_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'fill assessment history is immutable';
        END; $$""")
    op.execute("CREATE TRIGGER fill_assessment_immutable BEFORE UPDATE OR DELETE ON "
               "account_fill_report_assessments FOR EACH ROW EXECUTE FUNCTION reject_fill_assessment_mutation()")
    op.execute("CREATE TRIGGER fill_assessment_no_truncate BEFORE TRUNCATE ON "
               "account_fill_report_assessments FOR EACH STATEMENT EXECUTE FUNCTION reject_fill_assessment_mutation()")


def downgrade() -> None:
    op.drop_index("ix_fill_assessment_report_time", table_name="account_fill_report_assessments")
    op.drop_table("account_fill_report_assessments")
    op.execute("DROP FUNCTION reject_fill_assessment_mutation()")
    op.drop_constraint("uq_account_fill_report_identity", "account_fill_reports", type_="unique")
