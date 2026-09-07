"""platform tables

平台新增表（§2.5）：analysis_tasks / task_outbox / analysis_reports /
market_assets / market_bars_daily / concept_hotness_snapshots / macro_information /
watchlists / watchlist_items / portfolios / portfolio_positions。
不改写事件研究既有表（assets/events/market_data/event_impacts/market_context/predictions）。

Revision ID: 0001
Revises:
Create Date: 2026-09-05

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    # ---- analysis：命令真相与运行可靠性 ----
    op.create_table(
        "analysis_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("task_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("request_params", postgresql.JSONB(), nullable=False),
        sa.Column("selected_layers", postgresql.JSONB(), nullable=False),
        sa.Column("ticker", sa.String(32), nullable=True),
        sa.Column("requested_trade_date", sa.Date(), nullable=True),
        sa.Column("effective_trade_date", sa.Date(), nullable=True),
        sa.Column("date_correction", sa.String(64), nullable=True),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("attempt_no", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("lease_token", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(64), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_summary", sa.String(512), nullable=True),
        sa.Column("config_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("core_version", sa.String(32), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_unique_constraint("uq_analysis_tasks_idempotency_key", "analysis_tasks", ["idempotency_key"])
    op.create_index("ix_analysis_tasks_status", "analysis_tasks", ["status"])
    op.create_index("ix_analysis_tasks_updated_id", "analysis_tasks", ["updated_at", "id"])

    op.create_table(
        "task_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("analysis_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("message_type", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("dispatch_lease_token", sa.String(64), nullable=True),
        sa.Column("dispatch_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trace_context", postgresql.JSONB(), nullable=True),
        *_timestamps(),
    )
    op.create_unique_constraint("uq_task_outbox_task_attempt", "task_outbox", ["task_id", "attempt_no"])
    op.create_index("ix_task_outbox_status_next_attempt", "task_outbox", ["status", "next_attempt_at"])

    op.create_table(
        "analysis_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("analysis_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("report_version", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False, server_default=sa.text("'v1'")),
        sa.Column("report_json", postgresql.JSONB(), nullable=False),
        sa.Column("conclusion_summary", sa.String(512), nullable=True),
        sa.Column("risk_flag", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("risk_hint", sa.String(512), nullable=True),
        sa.Column("has_report", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("decision", postgresql.JSONB(), nullable=True),
        sa.Column("artifact_uri", sa.String(512), nullable=True),
        sa.Column("checksum", sa.String(64), nullable=True),
        sa.Column("core_version", sa.String(32), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_unique_constraint("uq_analysis_reports_task_version", "analysis_reports", ["task_id", "report_version"])
    op.create_index("ix_analysis_reports_task_completed", "analysis_reports", ["task_id", "generated_at"])

    # ---- market_data：展示读模型 ----
    op.create_table(
        "market_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("market", sa.String(8), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("market_timezone", sa.String(64), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("supported_intervals", postgresql.JSONB(), nullable=False),
        sa.Column("availability_status", sa.String(16), nullable=False),
        sa.Column("data_source", sa.String(32), nullable=True),
        *_timestamps(),
    )
    op.create_unique_constraint("uq_market_assets_market_symbol", "market_assets", ["market", "symbol"])
    op.create_index("ix_market_assets_market_order", "market_assets", ["market", "display_order"])

    op.create_table(
        "market_bars_daily",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("market_assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(18, 4), nullable=False),
        sa.Column("high", sa.Numeric(18, 4), nullable=False),
        sa.Column("low", sa.Numeric(18, 4), nullable=False),
        sa.Column("close", sa.Numeric(18, 4), nullable=False),
        sa.Column("volume", sa.Numeric(24, 4), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint("uq_market_bars_daily_asset_date", "market_bars_daily", ["asset_id", "trading_date"])
    op.create_index("ix_market_bars_daily_asset_date", "market_bars_daily", ["asset_id", "trading_date"])

    op.create_table(
        "concept_hotness_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("market", sa.String(8), nullable=False),
        sa.Column("concept_code", sa.String(32), nullable=False),
        sa.Column("concept_name", sa.String(128), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("score", sa.Numeric(12, 4), nullable=True),
        sa.Column("hotness_reason", sa.String(512), nullable=True),
        sa.Column("algorithm_version", sa.String(16), nullable=False),
        sa.Column("period_return", sa.Numeric(12, 4), nullable=True),
        sa.Column("daily_changes", postgresql.JSONB(), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_unique_constraint(
        "uq_concept_hotness_market_code_date_version",
        "concept_hotness_snapshots",
        ["market", "concept_code", "as_of_date", "algorithm_version"],
    )
    op.create_index("ix_concept_hotness_market_date", "concept_hotness_snapshots", ["market", "as_of_date"])

    # ---- event_study：宏观信息展示投影 ----
    op.create_table(
        "macro_information",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("market_tags", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("macro_topic", sa.String(128), nullable=True),
        sa.Column("summary", sa.String(), nullable=True),
        sa.Column("source", sa.String(256), nullable=True),
        sa.Column("source_url", sa.String(512), nullable=True),
        sa.Column("related_assets", postgresql.JSONB(), nullable=True),
        sa.Column("review_status", sa.String(16), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("research_status", sa.String(32), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_macro_information_review_occurred", "macro_information", ["review_status", "occurred_at"])

    # ---- investment_workspace：业务资产（两个独立聚合） ----
    op.create_table(
        "watchlists",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        *_timestamps(),
    )
    op.create_unique_constraint("uq_watchlists_name", "watchlists", ["name"])

    op.create_table(
        "watchlist_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("watchlist_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False),
        sa.Column("market", sa.String(8), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        *_timestamps(),
    )
    op.create_unique_constraint("uq_watchlist_items_group_instrument", "watchlist_items", ["watchlist_id", "market", "symbol"])
    op.create_index("ix_watchlist_items_order", "watchlist_items", ["watchlist_id", "display_order"])

    op.create_table(
        "portfolios",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        *_timestamps(),
    )
    op.create_unique_constraint("uq_portfolios_name", "portfolios", ["name"])

    op.create_table(
        "portfolio_positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("portfolio_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False),
        sa.Column("market", sa.String(8), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 4), nullable=False),
        sa.Column("average_cost", sa.Numeric(20, 4), nullable=False),
        *_timestamps(),
    )
    op.create_unique_constraint("uq_portfolio_positions_instrument", "portfolio_positions", ["portfolio_id", "market", "symbol"])


def downgrade() -> None:
    # 逆依赖顺序删除（仅平台表，不触碰事件研究既有表）
    op.drop_table("portfolio_positions")
    op.drop_table("portfolios")
    op.drop_table("watchlist_items")
    op.drop_table("watchlists")
    op.drop_table("macro_information")
    op.drop_table("concept_hotness_snapshots")
    op.drop_table("market_bars_daily")
    op.drop_table("market_assets")
    op.drop_table("analysis_reports")
    op.drop_table("task_outbox")
    op.drop_table("analysis_tasks")
