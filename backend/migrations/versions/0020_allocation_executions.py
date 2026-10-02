"""Joint entry-order consumption receipt, committed atomically with orders."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("quant_allocation_executions",
        sa.Column("batch_id", UUID(as_uuid=True), sa.ForeignKey("quant_allocation_batches.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("refreshed_batch_id", UUID(as_uuid=True), sa.ForeignKey("quant_allocation_batches.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", JSONB, nullable=False),
        sa.CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="ck_allocation_execution_identity"),
    )
    op.execute("CREATE TRIGGER allocation_immutable BEFORE UPDATE OR DELETE ON quant_allocation_executions FOR EACH ROW EXECUTE FUNCTION reject_allocation_mutation()")


def downgrade():
    op.drop_table("quant_allocation_executions")
