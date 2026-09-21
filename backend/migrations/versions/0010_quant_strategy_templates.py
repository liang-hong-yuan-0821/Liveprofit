"""quant strategy template audit metadata

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("quant_strategy_versions", sa.Column("template_id", sa.String(64), nullable=True))
    op.add_column("quant_strategy_versions", sa.Column("template_params", postgresql.JSONB(), nullable=True))
    op.add_column(
        "quant_strategy_versions",
        sa.Column("template_renderer_version", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("quant_strategy_versions", "template_renderer_version")
    op.drop_column("quant_strategy_versions", "template_params")
    op.drop_column("quant_strategy_versions", "template_id")
