"""quant strategies + portfolio risk columns

0008：量化策略实体（quant_strategies / quant_strategy_versions）+ 组合资金风控列。
量化策略与实操层方案（docs/requirements/量化策略与实操层/plan.md）4.1/4.2 落表：

- quant_strategies：稳定 ID、名称（唯一）、描述、version（元数据乐观锁）。
- quant_strategy_versions：(strategy_id, version_no) 唯一；状态 CHECK 仅
  DRAFT/PUBLISHED/ARCHIVED；partial unique index 保证每策略至多一个 DRAFT；
  version 为草稿源码乐观锁（与语义版本号 version_no 区分）。
- portfolios 加 7 个风控列：历史组合迁移为 total_assets=0、available_cash=0
  和默认风险参数（未补齐账户资金只能获得 HOLD/SELL 建议）；
  DB 层 CHECK 非负与比例区间，跨字段关系（single<=total、sector<=total）
  由服务复验。0009 为策略版本增加 published_at/archived_at 审计列。

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-16

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    # ---- quant_strategies：策略实体（元数据 + 乐观锁） ----
    op.create_table(
        "quant_strategies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        *_timestamps(),
    )
    op.create_unique_constraint("uq_quant_strategies_name", "quant_strategies", ["name"])

    # ---- quant_strategy_versions：版本化源码 ----
    op.create_table(
        "quant_strategy_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "strategy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("quant_strategies.id"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source_code", sa.Text(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        *_timestamps(),
    )
    op.create_unique_constraint(
        "uq_quant_strategy_versions_strategy_version",
        "quant_strategy_versions",
        ["strategy_id", "version_no"],
    )
    op.create_check_constraint(
        "ck_quant_strategy_versions_status",
        "quant_strategy_versions",
        "status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')",
    )
    # 每策略至多一个 DRAFT：当前草稿由该 partial unique index 的唯一 DRAFT 查询
    op.create_index(
        "uq_quant_strategy_versions_one_draft",
        "quant_strategy_versions",
        ["strategy_id"],
        unique=True,
        postgresql_where=sa.text("status = 'DRAFT'"),
    )

    # ---- portfolios：资金/风控列（历史组合迁移为 0 + 默认参数） ----
    op.add_column("portfolios", sa.Column("total_assets", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")))
    op.add_column("portfolios", sa.Column("available_cash", sa.Numeric(20, 4), nullable=False, server_default=sa.text("0")))
    op.add_column("portfolios", sa.Column("risk_per_trade_pct", sa.Numeric(8, 6), nullable=False, server_default=sa.text("0.01")))
    op.add_column("portfolios", sa.Column("min_risk_reward_ratio", sa.Numeric(8, 4), nullable=False, server_default=sa.text("2")))
    op.add_column("portfolios", sa.Column("max_total_position_pct", sa.Numeric(8, 6), nullable=False, server_default=sa.text("0.8")))
    op.add_column("portfolios", sa.Column("max_single_stock_pct", sa.Numeric(8, 6), nullable=False, server_default=sa.text("0.1")))
    op.add_column("portfolios", sa.Column("max_sector_pct", sa.Numeric(8, 6), nullable=False, server_default=sa.text("0.3")))

    op.create_check_constraint("ck_portfolios_total_assets_nonneg", "portfolios", "total_assets >= 0")
    op.create_check_constraint(
        "ck_portfolios_cash_bounds", "portfolios", "available_cash >= 0 AND available_cash <= total_assets"
    )
    op.create_check_constraint(
        "ck_portfolios_ratio_ranges",
        "portfolios",
        "risk_per_trade_pct > 0 AND risk_per_trade_pct <= 1"
        " AND max_total_position_pct > 0 AND max_total_position_pct <= 1"
        " AND max_single_stock_pct > 0 AND max_single_stock_pct <= 1"
        " AND max_sector_pct > 0 AND max_sector_pct <= 1",
    )
    op.create_check_constraint("ck_portfolios_rr_positive", "portfolios", "min_risk_reward_ratio > 0")


def downgrade() -> None:
    # 组合风控列：先约束后列（与 upgrade 逆序）
    op.drop_constraint("ck_portfolios_rr_positive", "portfolios", type_="check")
    op.drop_constraint("ck_portfolios_ratio_ranges", "portfolios", type_="check")
    op.drop_constraint("ck_portfolios_cash_bounds", "portfolios", type_="check")
    op.drop_constraint("ck_portfolios_total_assets_nonneg", "portfolios", type_="check")
    op.drop_column("portfolios", "max_sector_pct")
    op.drop_column("portfolios", "max_single_stock_pct")
    op.drop_column("portfolios", "max_total_position_pct")
    op.drop_column("portfolios", "min_risk_reward_ratio")
    op.drop_column("portfolios", "risk_per_trade_pct")
    op.drop_column("portfolios", "available_cash")
    op.drop_column("portfolios", "total_assets")

    op.drop_index("uq_quant_strategy_versions_one_draft", table_name="quant_strategy_versions")
    op.drop_constraint("ck_quant_strategy_versions_status", "quant_strategy_versions", type_="check")
    op.drop_constraint("uq_quant_strategy_versions_strategy_version", "quant_strategy_versions", type_="unique")
    op.drop_table("quant_strategy_versions")
    op.drop_constraint("uq_quant_strategies_name", "quant_strategies", type_="unique")
    op.drop_table("quant_strategies")
