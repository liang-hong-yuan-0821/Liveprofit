"""自选/组合 Schema（§2.6.3：最小 DTO、revision 并发语义）。"""

from __future__ import annotations

from datetime import date, datetime
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
    # 资金/风控字段（0008）：历史组合迁移后为 0 + 默认参数
    total_assets: float
    available_cash: float
    risk_per_trade_pct: float
    min_risk_reward_ratio: float
    max_total_position_pct: float
    max_single_stock_pct: float
    max_sector_pct: float
    max_portfolio_open_risk_pct: float
    max_sector_open_risk_pct: float
    max_daily_new_risk_pct: float
    max_drawdown_pct: float
    max_daily_loss_pct: float
    net_asset_value: float | None
    peak_net_asset_value: float | None
    day_start_net_asset_value: float | None
    risk_facts_as_of: date | None
    created_at: datetime
    updated_at: datetime


class PortfolioListData(BaseModel):
    items: list[PortfolioDTO]


class PortfolioCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    # 可空 = 使用默认参数（total_assets/available_cash=0，风险参数为方案默认值）
    total_assets: float | None = Field(default=None, ge=0)
    available_cash: float | None = Field(default=None, ge=0)
    risk_per_trade_pct: float | None = Field(default=None, gt=0, le=1)
    min_risk_reward_ratio: float | None = Field(default=None, gt=0)
    max_total_position_pct: float | None = Field(default=None, gt=0, le=1)
    max_single_stock_pct: float | None = Field(default=None, gt=0, le=1)
    max_sector_pct: float | None = Field(default=None, gt=0, le=1)
    max_portfolio_open_risk_pct: float | None = Field(default=None, gt=0, le=1)
    max_sector_open_risk_pct: float | None = Field(default=None, gt=0, le=1)
    max_daily_new_risk_pct: float | None = Field(default=None, gt=0, le=1)
    max_drawdown_pct: float | None = Field(default=None, gt=0, le=1)
    max_daily_loss_pct: float | None = Field(default=None, gt=0, le=1)
    net_asset_value: float | None = Field(default=None, gt=0)
    peak_net_asset_value: float | None = Field(default=None, gt=0)
    day_start_net_asset_value: float | None = Field(default=None, gt=0)
    risk_facts_as_of: date | None = None


class PortfolioUpdateRequest(BaseModel):
    """原子更新名称与全部账户字段（plan 4.2.1：一次条件更新、成功仅 version+1）。"""

    name: str = Field(min_length=1, max_length=64)
    total_assets: float = Field(ge=0)
    available_cash: float = Field(ge=0)
    risk_per_trade_pct: float = Field(gt=0, le=1)
    min_risk_reward_ratio: float = Field(gt=0)
    max_total_position_pct: float = Field(gt=0, le=1)
    max_single_stock_pct: float = Field(gt=0, le=1)
    max_sector_pct: float = Field(gt=0, le=1)
    max_portfolio_open_risk_pct: float = Field(gt=0, le=1)
    max_sector_open_risk_pct: float = Field(gt=0, le=1)
    max_daily_new_risk_pct: float = Field(gt=0, le=1)
    max_drawdown_pct: float = Field(gt=0, le=1)
    max_daily_loss_pct: float = Field(gt=0, le=1)
    net_asset_value: float | None = Field(default=None, gt=0)
    peak_net_asset_value: float | None = Field(default=None, gt=0)
    day_start_net_asset_value: float | None = Field(default=None, gt=0)
    risk_facts_as_of: date | None = None
    expected_version: int = Field(ge=1)


class PortfolioPositionDTO(BaseModel):
    portfolio_id: UUID
    market: Market
    symbol: str
    quantity: float
    average_cost: float
    active_stop_price: float | None
    updated_at: datetime


class PortfolioPositionsData(BaseModel):
    portfolio_id: UUID
    portfolio_revision: int
    items: list[PortfolioPositionDTO]


class PositionUpsertRequest(BaseModel):
    quantity: float
    average_cost: float
    active_stop_price: float | None = Field(default=None, gt=0)
    expected_portfolio_revision: int = Field(ge=1)


class PortfolioPositionMutationData(BaseModel):
    position: PortfolioPositionDTO
    portfolio_revision: int
