"""investment_workspace ORM：watchlists / watchlist_items / portfolios / portfolio_positions（§2.5 / §2.6.3）。

- watchlists 与 portfolios 是两个独立聚合：各自 version 为资源级乐观并发令牌；
  items/positions 写操作使用父资源的 revision（version 字段），单一事务检查并递增。
- 同分组同标的唯一；持仓按 (portfolio_id, market, symbol) 唯一。
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
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

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

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

    portfolio: Mapped[Portfolio] = relationship(back_populates="positions")

    __table_args__ = (
        UniqueConstraint("portfolio_id", "market", "symbol", name="uq_portfolio_positions_instrument"),
    )
