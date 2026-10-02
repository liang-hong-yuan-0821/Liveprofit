"""Durable automatic allocation completion or blocking outcome."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("quant_allocation_outcomes",
        sa.Column("batch_id", UUID(as_uuid=True), sa.ForeignKey("quant_allocation_batches.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_receipt_id", UUID(as_uuid=True), sa.ForeignKey("quant_allocation_executions.batch_id", ondelete="RESTRICT"), nullable=True),
        sa.Column("details", JSONB, nullable=False),
        sa.CheckConstraint("status IN ('COMPLETED','BLOCKED')", name="ck_allocation_outcome_status"),
        sa.CheckConstraint("(status = 'COMPLETED' AND entry_receipt_id IS NOT NULL AND entry_receipt_id = batch_id) OR (status = 'BLOCKED' AND entry_receipt_id IS NULL)", name="ck_allocation_outcome_receipt"),
    )
    op.execute("CREATE TRIGGER allocation_immutable BEFORE UPDATE OR DELETE ON quant_allocation_outcomes FOR EACH ROW EXECUTE FUNCTION reject_allocation_mutation()")


def downgrade():
    op.drop_table("quant_allocation_outcomes")
