"""portfolio open-risk controls and facts

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-21
"""

from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, default in (
        ("max_portfolio_open_risk_pct", "0.06"),
        ("max_sector_open_risk_pct", "0.03"),
        ("max_daily_new_risk_pct", "0.02"),
        ("max_drawdown_pct", "0.10"),
        ("max_daily_loss_pct", "0.03"),
    ):
        op.add_column("portfolios", sa.Column(name, sa.Numeric(8, 6), nullable=False, server_default=sa.text(default)))
        op.create_check_constraint(f"ck_portfolios_{name}", "portfolios", f"{name} > 0 AND {name} <= 1")
    op.add_column("portfolios", sa.Column("net_asset_value", sa.Numeric(20, 4), nullable=True))
    op.add_column("portfolios", sa.Column("peak_net_asset_value", sa.Numeric(20, 4), nullable=True))
    op.add_column("portfolios", sa.Column("day_start_net_asset_value", sa.Numeric(20, 4), nullable=True))
    op.add_column("portfolios", sa.Column("risk_facts_as_of", sa.Date(), nullable=True))
    op.add_column("portfolio_positions", sa.Column("active_stop_price", sa.Numeric(20, 4), nullable=True))
    op.create_check_constraint(
        "ck_portfolio_positions_active_stop_positive",
        "portfolio_positions", "active_stop_price IS NULL OR active_stop_price > 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_portfolio_positions_active_stop_positive", "portfolio_positions", type_="check")
    op.drop_column("portfolio_positions", "active_stop_price")
    for name in ("risk_facts_as_of", "day_start_net_asset_value", "peak_net_asset_value", "net_asset_value"):
        op.drop_column("portfolios", name)
    for name in (
        "max_daily_loss_pct", "max_drawdown_pct", "max_daily_new_risk_pct",
        "max_sector_open_risk_pct", "max_portfolio_open_risk_pct",
    ):
        op.drop_constraint(f"ck_portfolios_{name}", "portfolios", type_="check")
        op.drop_column("portfolios", name)
