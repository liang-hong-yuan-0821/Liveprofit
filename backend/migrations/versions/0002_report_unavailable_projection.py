"""add report unavailable sections projection

analysis_reports 增加 has_unavailable_sections：看板 pending_actions
（REPORT_SECTION_UNAVAILABLE 聚合）的读模型投影，ReportService 保存版本时落库。

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-05

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analysis_reports",
        sa.Column("has_unavailable_sections", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("analysis_reports", "has_unavailable_sections")
