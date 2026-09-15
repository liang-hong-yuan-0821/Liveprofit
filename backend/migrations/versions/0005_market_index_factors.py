"""market_index_factors

指数技术因子读模型（技术指标数据源切换方案 §2.1）：
market_assets 下的 10 列 idx_factor_pro 因子（ma/boll/macd，_bfq 不复权），
与 market_bars_daily 同 (asset_id, trading_date) 对齐；指标不自算（2026-09-12 决策）。

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-12

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_FACTOR_COLS = (
    "ma_bfq_5", "ma_bfq_10", "ma_bfq_20", "ma_bfq_60",
    "boll_mid_bfq", "boll_upper_bfq", "boll_lower_bfq",
    "macd_dif_bfq", "macd_dea_bfq", "macd_bfq",
)


def upgrade() -> None:
    op.create_table(
        "market_index_factors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("market_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trading_date", sa.Date(), nullable=False),
        *[sa.Column(name, sa.Numeric(18, 6), nullable=True) for name in _FACTOR_COLS],
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("asset_id", "trading_date", name="uq_market_index_factors_asset_date"),
    )
    op.create_index(
        "ix_market_index_factors_asset_date",
        "market_index_factors",
        ["asset_id", "trading_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_market_index_factors_asset_date", table_name="market_index_factors")
    op.drop_table("market_index_factors")
