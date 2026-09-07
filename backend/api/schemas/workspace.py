"""自选/组合 Schema（§2.6.3：最小 DTO、revision 并发语义）。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

Market = Literal["US", "KR", "CN"]


class WatchlistDTO(BaseModel):
    id: UUID
    name: str
    version: int
    item_count: int
    created_at: datetime
    updated_at: datetime


class WatchlistListData(BaseModel):
    items: list[WatchlistDTO]


class WatchlistCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class WatchlistUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    expected_version: int = Field(ge=1)


class WatchlistItemDTO(BaseModel):
    id: UUID
    watchlist_id: UUID
    market: Market
    symbol: str
    display_order: int
    created_at: datetime
    updated_at: datetime


class WatchlistItemsData(BaseModel):
    watchlist_id: UUID
    watchlist_revision: int
    items: list[WatchlistItemDTO]


class WatchlistItemCreateRequest(BaseModel):
    market: Market
    symbol: str = Field(min_length=1, max_length=32)
    expected_watchlist_revision: int = Field(ge=1)


class WatchlistItemOrderEntry(BaseModel):
    market: Market
    symbol: str
    display_order: int = Field(ge=0)


class WatchlistItemOrderUpdateRequest(BaseModel):
    expected_watchlist_revision: int = Field(ge=1)
    items: list[WatchlistItemOrderEntry] = Field(min_length=1)


class WatchlistItemMutationData(BaseModel):
    item: WatchlistItemDTO | None
    watchlist_revision: int


class WatchlistOrderData(BaseModel):
    watchlist_id: UUID
    watchlist_revision: int
    items: list[WatchlistItemDTO]


class PortfolioDTO(BaseModel):
    id: UUID
    name: str
    version: int
    position_count: int
    created_at: datetime
    updated_at: datetime


class PortfolioListData(BaseModel):
    items: list[PortfolioDTO]


class PortfolioCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class PortfolioUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    expected_version: int = Field(ge=1)


class PortfolioPositionDTO(BaseModel):
    portfolio_id: UUID
    market: Market
    symbol: str
    quantity: float
    average_cost: float
    updated_at: datetime


class PortfolioPositionsData(BaseModel):
    portfolio_id: UUID
    portfolio_revision: int
    items: list[PortfolioPositionDTO]


class PositionUpsertRequest(BaseModel):
    quantity: float
    average_cost: float
    expected_portfolio_revision: int = Field(ge=1)


class PortfolioPositionMutationData(BaseModel):
    position: PortfolioPositionDTO
    portfolio_revision: int
