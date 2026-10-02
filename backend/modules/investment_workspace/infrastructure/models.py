"""investment_workspace ORM：watchlists / watchlist_items / portfolios / portfolio_positions（§2.5 / §2.6.3）。

- watchlists 与 portfolios 是两个独立聚合：各自 version 为资源级乐观并发令牌；
  items/positions 写操作使用父资源的 revision（version 字段），单一事务检查并递增。
- 同分组同标的唯一；持仓按 (portfolio_id, market, symbol) 唯一。
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.shared.db import Base, TimestampMixin


class Watchlist(Base, TimestampMixin):
    __tablename__ = "watchlists"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    items: Mapped[list["WatchlistItem"]] = relationship(
        back_populates="watchlist", cascade="all, delete-orphan", order_by="WatchlistItem.display_order"
    )


class WatchlistItem(Base, TimestampMixin):
    __tablename__ = "watchlist_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    watchlist_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False
    )
    market: Mapped[str] = mapped_column(String(8), nullable=False)  # US/KR/CN
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    watchlist: Mapped[Watchlist] = relationship(back_populates="items")

    __table_args__ = (
        UniqueConstraint("watchlist_id", "market", "symbol", name="uq_watchlist_items_group_instrument"),
        Index("ix_watchlist_items_order", "watchlist_id", "display_order"),
    )


class Portfolio(Base, TimestampMixin):
    __tablename__ = "portfolios"
    __table_args__ = (CheckConstraint(
        "risk_profile IN ('CONSERVATIVE','BALANCED','AGGRESSIVE')",
        name="ck_portfolios_risk_profile"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # 资金/风控列（0008）：历史组合迁移为 0 + 默认参数；
    # 跨字段关系（single<=total、sector<=total）由服务复验，DB 只做单列 CHECK
    total_assets: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False, server_default=sa_text("0"))
    available_cash: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False, server_default=sa_text("0"))
    risk_per_trade_pct: Mapped[float] = mapped_column(Numeric(8, 6), nullable=False, server_default=sa_text("0.01"))
    min_risk_reward_ratio: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False, server_default=sa_text("2"))
    max_total_position_pct: Mapped[float] = mapped_column(Numeric(8, 6), nullable=False, server_default=sa_text("0.8"))
    max_single_stock_pct: Mapped[float] = mapped_column(Numeric(8, 6), nullable=False, server_default=sa_text("0.1"))
    max_sector_pct: Mapped[float] = mapped_column(Numeric(8, 6), nullable=False, server_default=sa_text("0.3"))
    max_portfolio_open_risk_pct: Mapped[float] = mapped_column(Numeric(8, 6), nullable=False, server_default=sa_text("0.06"))
    max_sector_open_risk_pct: Mapped[float] = mapped_column(Numeric(8, 6), nullable=False, server_default=sa_text("0.03"))
    max_daily_new_risk_pct: Mapped[float] = mapped_column(Numeric(8, 6), nullable=False, server_default=sa_text("0.02"))
    max_drawdown_pct: Mapped[float] = mapped_column(Numeric(8, 6), nullable=False, server_default=sa_text("0.10"))
    max_daily_loss_pct: Mapped[float] = mapped_column(Numeric(8, 6), nullable=False, server_default=sa_text("0.03"))
    net_asset_value: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    peak_net_asset_value: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    day_start_net_asset_value: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    risk_facts_as_of: Mapped[date | None] = mapped_column(nullable=True)
    risk_profile: Mapped[str | None] = mapped_column(String(16), nullable=True)

    positions: Mapped[list["PortfolioPosition"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )


class PortfolioPosition(Base, TimestampMixin):
    __tablename__ = "portfolio_positions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    market: Mapped[str] = mapped_column(String(8), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    average_cost: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    active_stop_price: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)

    portfolio: Mapped[Portfolio] = relationship(back_populates="positions")

    __table_args__ = (
        UniqueConstraint("portfolio_id", "market", "symbol", name="uq_portfolio_positions_instrument"),
    )
