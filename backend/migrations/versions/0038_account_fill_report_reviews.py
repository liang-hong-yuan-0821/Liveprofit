"""Immutable signed manual reviews of reported fill evidence."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_fill_report_reviews",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("portfolio_id", UUID(as_uuid=True),
                  sa.ForeignKey("portfolios.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("report_id", UUID(as_uuid=True), nullable=False),
        sa.Column("report_sha256", sa.String(64), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("review_ref", sa.String(256), nullable=False),
        sa.Column("reviewed_by", sa.String(256), nullable=False),
        sa.Column("reason", sa.String(512), nullable=False),
        sa.Column("review_signature", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.clock_timestamp()),
        sa.ForeignKeyConstraint(["report_id", "portfolio_id"],
                                ["account_fill_reports.id", "account_fill_reports.portfolio_id"],
                                ondelete="RESTRICT", name="fk_fill_review_report_portfolio"),
        sa.UniqueConstraint("report_id", name="uq_fill_review_report"),
        sa.UniqueConstraint("portfolio_id", "review_ref", name="uq_fill_review_ref"),
        sa.CheckConstraint(
            "report_sha256 ~ '^[0-9a-f]{64}$' AND evidence_sha256 ~ '^[0-9a-f]{64}$' AND "
            "review_signature ~ '^[0-9a-f]{64}$' AND length(trim(review_ref)) > 0 AND "
            "length(trim(reviewed_by)) > 0 AND length(trim(reason)) > 0",
            name="ck_fill_review_values"),
    )
    op.execute("""CREATE FUNCTION validate_fill_review_binding() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE r account_fill_reports%ROWTYPE;
        BEGIN
            SELECT * INTO r FROM account_fill_reports WHERE id = NEW.report_id;
            IF r.id IS NULL OR r.portfolio_id <> NEW.portfolio_id OR
               r.evidence_sha256 IS NULL OR r.evidence_uri IS NULL OR
               NEW.report_sha256 IS DISTINCT FROM r.payload_sha256 OR
               NEW.evidence_sha256 IS DISTINCT FROM r.evidence_sha256 OR
               EXISTS (SELECT 1 FROM account_fill_report_resolutions WHERE report_id = r.id) THEN
                RAISE EXCEPTION 'fill review report evidence mismatch';
            END IF;
            RETURN NEW;
        END; $$""")
    op.execute("CREATE TRIGGER fill_review_validate BEFORE INSERT ON "
               "account_fill_report_reviews FOR EACH ROW EXECUTE FUNCTION validate_fill_review_binding()")
    op.execute("""CREATE FUNCTION reject_fill_review_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'fill review history is immutable';
        END; $$""")
    op.execute("CREATE TRIGGER fill_review_immutable BEFORE UPDATE OR DELETE ON "
               "account_fill_report_reviews FOR EACH ROW EXECUTE FUNCTION reject_fill_review_mutation()")
    op.execute("CREATE TRIGGER fill_review_no_truncate BEFORE TRUNCATE ON "
               "account_fill_report_reviews FOR EACH STATEMENT EXECUTE FUNCTION reject_fill_review_mutation()")


def downgrade() -> None:
    op.drop_table("account_fill_report_reviews")
    op.execute("DROP FUNCTION validate_fill_review_binding()")
    op.execute("DROP FUNCTION reject_fill_review_mutation()")
