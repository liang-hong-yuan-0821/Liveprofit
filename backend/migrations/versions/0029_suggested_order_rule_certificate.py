"""Bind new risk suggestions to the forward rule certificate used at planning."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("suggested_orders", sa.Column(
        "rule_certificate_id", UUID(as_uuid=True), sa.ForeignKey(
            "quant_instrument_rule_certificates.id", ondelete="RESTRICT"), nullable=True))
    op.create_index("ix_suggested_order_rule_certificate", "suggested_orders",
                    ["rule_certificate_id"])


def downgrade() -> None:
    op.drop_index("ix_suggested_order_rule_certificate", table_name="suggested_orders")
    op.drop_column("suggested_orders", "rule_certificate_id")
