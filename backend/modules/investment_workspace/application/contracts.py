"""investment_workspace 应用契约（§2.6.3 最小 DTO 字段）。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class WatchlistDTO:
    id: uuid.UUID
    name: str
    version: int
    item_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class WatchlistItemDTO:
    id: uuid.UUID
    watchlist_id: uuid.UUID
    market: str
    symbol: str
    display_order: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class WatchlistItemMutationResult:
    item: WatchlistItemDTO | None
    watchlist_revision: int


@dataclass(frozen=True)
class PortfolioDTO:
    id: uuid.UUID
    name: str
    version: int
    position_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class PortfolioPositionDTO:
    portfolio_id: uuid.UUID
    market: str
    symbol: str
    quantity: float
    average_cost: float
    updated_at: datetime


@dataclass(frozen=True)
class PortfolioPositionMutationResult:
    position: PortfolioPositionDTO
    portfolio_revision: int
