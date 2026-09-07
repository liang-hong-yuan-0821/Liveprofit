"""market_data ORM：market_assets / market_bars_daily / concept_hotness_snapshots（§2.5）。

- market_assets：大盘资产目录唯一真相；availability_status 反映配置/采集可用性，
  资产禁用（DISABLED）与上游失败/未验收（UNAVAILABLE）不能混同。
- market_bars_daily：规范化日线读模型，唯一写入方为 ingestion；不解析 Provider Markdown。
- concept_hotness_snapshots：热点可追溯快照（heat_v1，top_n=30，保留 10 个交易日）。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import BIGINT, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.shared.db import Base, TimestampMixin


class MarketAsset(Base, TimestampMixin):
    __tablename__ = "market_assets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market: Mapped[str] = mapped_column(String(8), nullable=False)  # US/KR/CN
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    market_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    supported_intervals: Mapped[list] = mapped_column(JSONB, nullable=False)  # 首期 ["1d"]
    availability_status: Mapped[str] = mapped_column(String(16), nullable=False)  # AVAILABLE/DISABLED/UNAVAILABLE
    data_source: Mapped[str | None] = mapped_column(String(32), nullable=True)  # tushare/akshare

    bars: Mapped[list["MarketBarDaily"]] = relationship(back_populates="asset", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("market", "symbol", name="uq_market_assets_market_symbol"),
        Index("ix_market_assets_market_order", "market", "display_order"),
    )


class MarketBarDaily(Base):
    __tablename__ = "market_bars_daily"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("market_assets.id", ondelete="CASCADE"), nullable=False
    )
    trading_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    high: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    low: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    close: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    volume: Mapped[float | None] = mapped_column(Numeric(24, 4), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    asset: Mapped[MarketAsset] = relationship(back_populates="bars")

    __table_args__ = (
        UniqueConstraint("asset_id", "trading_date", name="uq_market_bars_daily_asset_date"),
        Index("ix_market_bars_daily_asset_date", "asset_id", "trading_date"),
    )


class ConceptHotnessSnapshot(Base):
    __tablename__ = "concept_hotness_snapshots"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(8), nullable=False)  # 首期仅 CN
    concept_code: Mapped[str] = mapped_column(String(32), nullable=False)
    concept_name: Mapped[str] = mapped_column(String(128), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    hotness_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    algorithm_version: Mapped[str] = mapped_column(String(16), nullable=False)  # heat_v1
    period_return: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    daily_changes: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # 近 10 日 [{date, change_pct}]
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "market", "concept_code", "as_of_date", "algorithm_version",
            name="uq_concept_hotness_market_code_date_version",
        ),
        Index("ix_concept_hotness_market_date", "market", "as_of_date"),
    )
