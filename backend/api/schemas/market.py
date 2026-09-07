"""市场数据 Schema（§2.6.1：资产目录、K 线、热点快照）。"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Market = Literal["US", "KR", "CN"]
Availability = Literal["AVAILABLE", "DISABLED", "UNAVAILABLE"]
Freshness = Literal["FRESH", "STALE", "UNAVAILABLE"]
Session = Literal["OPEN", "CLOSED"]


class MarketAssetDTO(BaseModel):
    market: Market
    symbol: str
    name: str
    currency: str
    market_timezone: str
    display_order: int
    enabled: bool
    supported_intervals: list[str]
    availability_status: Availability


class MarketAssetsData(BaseModel):
    items: list[MarketAssetDTO]


class BarDTO(BaseModel):
    timestamp: date
    open: float
    high: float
    low: float
    close: float
    volume: float | None


class MaLineDTO(BaseModel):
    """单条均线：values 与 bars 等长按 index 对齐，窗口不足处为 null。"""

    period: int
    values: list[float | None]


class BollBandsDTO(BaseModel):
    """布林带三线：各数组与 bars 等长按 index 对齐，窗口不足处为 null。"""

    period: int
    k: float
    mid: list[float | None]
    upper: list[float | None]
    lower: list[float | None]


class IndicatorsDTO(BaseModel):
    """MA/BOLL 技术指标（K线指标叠加方案 §2.1）；bars 为空时整个字段为 null。"""

    ma: list[MaLineDTO]
    boll: BollBandsDTO


class BarsAssetInfo(BaseModel):
    market: Market
    symbol: str
    name: str
    currency: str
    market_timezone: str
    supported_intervals: list[str]


class BarsData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    asset: BarsAssetInfo
    interval: str
    from_: date = Field(alias="from")
    to: date
    bars: list[BarDTO]
    indicators: IndicatorsDTO | None = None
    source: str | None
    as_of: date | None
    source_updated_at: datetime | None
    freshness_status: Freshness
    market_session_status: Session
    market_closed_reason: str | None


class DailyChangeDTO(BaseModel):
    date: date
    change_pct: float


class HotConceptDTO(BaseModel):
    concept_code: str
    concept_name: str
    rank: int
    hotness_reason: str | None
    period_return: float | None
    daily_changes: list[DailyChangeDTO] | None
    updated_at: datetime | None
    bars: list[BarDTO]


class HotConceptsData(BaseModel):
    as_of: date | None
    algorithm_version: str
    result_status: Literal["OK", "NO_HOT_CONCEPTS"]
    items: list[HotConceptDTO]
    source: str | None
    source_updated_at: datetime | None
    freshness_status: Literal["FRESH", "STALE"]
