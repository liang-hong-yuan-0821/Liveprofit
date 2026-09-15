"""drop concept_hotness_snapshots

热点快照表整链删除（证券市场数据库统一方案决策 13）：热度改由
market.sector_daily 现场计算，不落快照表不进 Redis。
实施阶段随消费方切换一并落地（表 0 行、写链占位、读链同步切换，
DROP 无"跑通 3 天"前置）。

downgrade 重建表（与 0001 建表同构）：0001 的 downgrade 无条件 drop 本表，
本迁移 downgrade 不重建则降级链到 base 时报 UndefinedTable。

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-13

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 索引随表一并删除（PG 表删索引自动级联）
    op.drop_table("concept_hotness_snapshots")


def downgrade() -> None:
    op.create_table(
        "concept_hotness_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("market", sa.String(8), nullable=False),
        sa.Column("concept_code", sa.String(32), nullable=False),
        sa.Column("concept_name", sa.String(128), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("score", sa.Numeric(12, 4), nullable=True),
        sa.Column("hotness_reason", sa.String(512), nullable=True),
        sa.Column("algorithm_version", sa.String(16), nullable=False),
        sa.Column("period_return", sa.Numeric(12, 4), nullable=True),
        sa.Column("daily_changes", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint(
            "market", "concept_code", "as_of_date", "algorithm_version",
            name="uq_concept_hotness_market_code_date_version"),
    )
    op.create_index("ix_concept_hotness_market_date", "concept_hotness_snapshots",
                    ["market", "as_of_date"])
