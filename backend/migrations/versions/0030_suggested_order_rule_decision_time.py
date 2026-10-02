"""Persist the new order decision time separately from frozen T-day inputs."""

from alembic import op
import sqlalchemy as sa

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("suggested_orders", sa.Column("rule_authorized_at", sa.DateTime(timezone=True)))
    op.add_column("suggested_orders", sa.Column("decision_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "ck_suggested_order_rule_decision_time", "suggested_orders",
        "(rule_certificate_id IS NULL AND rule_authorized_at IS NULL) OR "
        "(rule_certificate_id IS NOT NULL AND rule_authorized_at IS NOT NULL "
        "AND decision_at IS NOT NULL AND decision_at >= rule_authorized_at)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_suggested_order_rule_decision_time", "suggested_orders")
    op.drop_column("suggested_orders", "decision_at")
    op.drop_column("suggested_orders", "rule_authorized_at")
