"""quant execution signals + strategy version audit columns

0009：量化执行信号表 + 策略版本审计列。量化策略与实操层方案
（docs/requirements/量化策略与实操层/plan.md）4.1/4.3 落表：

- quant_strategy_versions 增加不可变 published_at 与可选 archived_at
  （禁止用会被归档更新的 updated_at 代替发布时间）。
- quant_execution_signals：id 表级自增主键，以 (task_id, attempt_no, id)
  归属执行批次；signal_kind 判别行类型（BUY/持仓信号/错误样本），
  order 可空列组仅属 BUY/SELL 行；复合索引
  (task_id, attempt_no, score DESC, ts_code, id) 支撑 cursor；
  FK 随 task CASCADE。完整列清单见 plan 4.3.1 信号保留矩阵。

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-16

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- 策略版本审计列 ----
    op.add_column("quant_strategy_versions", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("quant_strategy_versions", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))

    # ---- 全市场逐票结果 ----
    op.create_table(
        "quant_execution_signals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analysis_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("signal_kind", sa.String(16), nullable=False),
        sa.Column("ts_code", sa.String(16), nullable=False),
        sa.Column("action", sa.String(16), nullable=True),
        sa.Column("score", sa.Numeric(5, 2), nullable=True),
        sa.Column("reason", sa.String(240), nullable=True),
        # 脚本七键价格字段：仅 BUY 行有值，SELL/HOLD 恒 NULL
        sa.Column("entry_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("stop_loss", sa.Numeric(18, 4), nullable=True),
        sa.Column("take_profit", sa.Numeric(18, 4), nullable=True),
        sa.Column("sell_ratio", sa.Numeric(8, 6), nullable=True),
        # 系统回写：风控结论 + 建议订单列组（仅 ELIGIBLE 及卖出订单行有值）
        sa.Column("order_status", sa.String(32), nullable=True),
        sa.Column("shares", sa.Numeric(20, 4), nullable=True),
        sa.Column("notional", sa.Numeric(20, 4), nullable=True),
        sa.Column("order_cost_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("valuation_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("risk_bucket", postgresql.JSONB(), nullable=True),
        # 错误样本行
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_check_constraint(
        "ck_quant_execution_signals_kind",
        "quant_execution_signals",
        "signal_kind IN ('BUY', 'HOLDING', 'ERROR')",
    )
    op.create_index(
        "ix_quant_execution_signals_cursor",
        "quant_execution_signals",
        ["task_id", "attempt_no", sa.text("score DESC"), "ts_code", "id"],
    )
    op.create_index("ix_quant_execution_signals_task_attempt", "quant_execution_signals", ["task_id", "attempt_no"])


def downgrade() -> None:
    op.drop_index("ix_quant_execution_signals_task_attempt", table_name="quant_execution_signals")
    op.drop_index("ix_quant_execution_signals_cursor", table_name="quant_execution_signals")
    op.drop_constraint("ck_quant_execution_signals_kind", "quant_execution_signals", type_="check")
    op.drop_table("quant_execution_signals")

    op.drop_column("quant_strategy_versions", "archived_at")
    op.drop_column("quant_strategy_versions", "published_at")
