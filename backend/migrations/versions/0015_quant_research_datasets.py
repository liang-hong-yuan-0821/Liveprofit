"""Persist immutable research dataset references and integrity checksums."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quant_research_datasets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("manifest_path", sa.String(length=1024), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("quality_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('READY', 'INVALID')", name="ck_quant_research_datasets_status"),
        sa.CheckConstraint("length(manifest_sha256) = 64", name="ck_quant_research_datasets_sha256"),
    )
    op.create_index("ix_quant_research_datasets_as_of", "quant_research_datasets", ["as_of"])


def downgrade() -> None:
    op.drop_index("ix_quant_research_datasets_as_of", table_name="quant_research_datasets")
    op.drop_table("quant_research_datasets")
