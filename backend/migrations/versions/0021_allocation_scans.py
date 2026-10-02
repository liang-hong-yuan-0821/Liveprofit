"""Immutable family scan registration, reusing analysis task/outbox dispatch."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("quant_allocation_scans",
        sa.Column("batch_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("strategy_version_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("task_id", UUID(as_uuid=True), sa.ForeignKey("analysis_tasks.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.ForeignKeyConstraint(["batch_id", "strategy_version_id"],
            ["quant_allocation_members.batch_id", "quant_allocation_members.strategy_version_id"], ondelete="RESTRICT"),
    )
    op.execute("CREATE TRIGGER allocation_immutable BEFORE UPDATE OR DELETE ON quant_allocation_scans FOR EACH ROW EXECUTE FUNCTION reject_allocation_mutation()")


def downgrade():
    op.drop_table("quant_allocation_scans")
