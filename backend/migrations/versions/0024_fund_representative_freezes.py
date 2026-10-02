"""Immutable diagnostic ETF month representatives bound to a research snapshot."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quant_fund_representative_freezes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("snapshot_id", UUID(as_uuid=True),
                  sa.ForeignKey("quant_research_datasets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("trial_id", sa.String(128), nullable=False),
        sa.Column("definition_hash", sa.String(64), nullable=False),
        sa.Column("evaluation_as_of", sa.Date, nullable=False),
        sa.Column("decision_date", sa.Date, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("disposition", sa.String(32), nullable=False),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("result_json", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("snapshot_id", "trial_id", "evaluation_as_of", "decision_date",
                            name="uq_fund_rep_freeze_identity"),
        sa.CheckConstraint("status = 'DIAGNOSTIC'", name="ck_fund_rep_freeze_status"),
        sa.CheckConstraint("disposition IN ('SELECTED','NO_ELIGIBLE_REPRESENTATIVE')",
                           name="ck_fund_rep_freeze_disposition"),
        sa.CheckConstraint("evaluation_as_of <= decision_date", name="ck_fund_rep_freeze_dates"),
        sa.CheckConstraint("manifest_sha256 ~ '^[0-9a-f]{64}$' AND definition_hash ~ '^[0-9a-f]{64}$' "
                           "AND input_sha256 ~ '^[0-9a-f]{64}$'", name="ck_fund_rep_freeze_hashes"),
    )
    op.execute("CREATE TRIGGER fund_rep_freeze_immutable BEFORE UPDATE OR DELETE "
               "ON quant_fund_representative_freezes FOR EACH ROW "
               "EXECUTE FUNCTION reject_allocation_mutation()")


def downgrade() -> None:
    op.drop_table("quant_fund_representative_freezes")
