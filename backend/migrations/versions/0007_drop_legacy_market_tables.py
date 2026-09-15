"""drop legacy market tables

DROP 旧市场三表（market_assets / market_bars_daily / market_index_factors）——
market schema 由 db/instrument/schema.sql 建，alembic 不再建市场表。
清理窗口执行（用户 2026-09-13 拍板直接清理；DROP 前已 pg_dump 备份
logs/backups/pg_backup_20260913_legacy_tables.sql）。

downgrade 重建三表（与 0006 同先例）：0005/0003/0001 的 downgrade 无条件
drop_index/drop_table，本迁移 downgrade 不重建则降级链到 base 时报
UndefinedObject（CR B3）。

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-13

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_FACTOR_COLS = (
    "ma_bfq_5", "ma_bfq_10", "ma_bfq_20", "ma_bfq_60",
    "boll_mid_bfq", "boll_upper_bfq", "boll_lower_bfq",
    "macd_dif_bfq", "macd_dea_bfq", "macd_bfq",
)


def upgrade() -> None:
    for table in ("market_index_factors", "market_bars_daily", "market_assets"):
        op.execute(f"DROP TABLE IF EXISTS {table}")


def downgrade() -> None:
    op.create_table(
        "market_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("market", sa.String(8), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("market_timezone", sa.String(64), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("enabled", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
        sa.Column("supported_intervals", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False),
        sa.Column("availability_status", sa.String(16), nullable=False),
        sa.Column("data_source", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("market", "symbol", name="uq_market_assets_market_symbol"),
    )
    op.create_index("ix_market_assets_market_order", "market_assets",
                    ["market", "display_order"])
    op.create_table(
        "market_bars_daily",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("market_assets.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(18, 4), nullable=False),
        sa.Column("high", sa.Numeric(18, 4), nullable=False),
        sa.Column("low", sa.Numeric(18, 4), nullable=False),
        sa.Column("close", sa.Numeric(18, 4), nullable=False),
        sa.Column("volume", sa.Numeric(24, 4), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("asset_id", "trading_date",
                            name="uq_market_bars_daily_asset_date"),
    )
    op.create_index("ix_market_bars_daily_asset_date", "market_bars_daily",
                    ["asset_id", "trading_date"])
    op.create_table(
        "market_index_factors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("market_assets.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        *[sa.Column(name, sa.Numeric(18, 6), nullable=True) for name in _FACTOR_COLS],
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("asset_id", "trading_date",
                            name="uq_market_index_factors_asset_date"),
    )
    op.create_index("ix_market_index_factors_asset_date", "market_index_factors",
                    ["asset_id", "trading_date"])
