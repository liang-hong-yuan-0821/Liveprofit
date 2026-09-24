"""quant execution price bases and trading constraints

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("quant_execution_signals", sa.Column("signal_trade_date", sa.Date(), nullable=True))
    op.add_column("quant_execution_signals", sa.Column("signal_price_basis", sa.String(8), nullable=True))
    op.add_column("quant_execution_signals", sa.Column("execution_price_basis", sa.String(8), nullable=True))
    op.add_column("quant_execution_signals", sa.Column("adj_factor_version", sa.String(32), nullable=True))
    op.add_column("quant_execution_signals", sa.Column("execution_market", postgresql.JSONB(), nullable=True))
    op.add_column("quant_execution_signals", sa.Column("order_entry_price", sa.Numeric(18, 4), nullable=True))
    op.add_column("quant_execution_signals", sa.Column("order_stop_price", sa.Numeric(18, 4), nullable=True))
    op.add_column("quant_execution_signals", sa.Column("order_take_price", sa.Numeric(18, 4), nullable=True))
    op.add_column("quant_execution_signals", sa.Column("earliest_execution_trade_date", sa.Date(), nullable=True))
    op.add_column("quant_execution_signals", sa.Column("available_sell_quantity", sa.Numeric(20, 4), nullable=True))
    op.add_column("quant_execution_signals", sa.Column("estimated_fees", sa.Numeric(20, 4), nullable=True))
    op.add_column("quant_execution_signals", sa.Column("estimated_slippage", sa.Numeric(20, 4), nullable=True))
    op.add_column("quant_execution_signals", sa.Column("execution_policy_version", sa.String(32), nullable=True))


def downgrade() -> None:
    for name in (
        "execution_policy_version", "estimated_slippage", "estimated_fees",
        "available_sell_quantity", "earliest_execution_trade_date", "order_take_price",
        "order_stop_price", "order_entry_price", "execution_market", "adj_factor_version",
        "execution_price_basis", "signal_price_basis", "signal_trade_date",
    ):
        op.drop_column("quant_execution_signals", name)
