"""add agent prompt overrides and task rerun_from_node_id

- agent_prompt_overrides：Agent 提示词覆盖（node_id PK → prompt_text），
  执行开始快照注入内核（单Agent重跑与提示词编辑方案 3.3）。
- analysis_tasks.rerun_from_node_id：当前 attempt 为重跑时的起点节点 id，
  仅作展示（重跑触发源为 outbox payload → actor kwarg 的消息级参数）。

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-09

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_prompt_overrides",
        sa.Column("node_id", sa.String(64), primary_key=True),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.add_column(
        "analysis_tasks",
        sa.Column("rerun_from_node_id", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("analysis_tasks", "rerun_from_node_id")
    op.drop_table("agent_prompt_overrides")
