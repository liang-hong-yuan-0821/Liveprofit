"""Persist the strategy version that produced each quant scan signal."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "quant_execution_signals",
        sa.Column("strategy_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_quant_execution_signals_strategy_version",
        "quant_execution_signals",
        "quant_strategy_versions",
        ["strategy_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_quant_execution_signals_task_strategy_score",
        "quant_execution_signals",
        ["task_id", "strategy_version_id", sa.text("score DESC"), "ts_code", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_quant_execution_signals_task_strategy_score",
        table_name="quant_execution_signals",
    )
    op.drop_constraint(
        "fk_quant_execution_signals_strategy_version",
        "quant_execution_signals",
        type_="foreignkey",
    )
    op.drop_column("quant_execution_signals", "strategy_version_id")
