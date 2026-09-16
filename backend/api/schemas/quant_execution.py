"""量化执行报告 DTO（plan 4.4.1）：无源码结构化投影 + 信号 cursor 分页。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class StrategyAuditDTO(BaseModel):
    name: str
    version_no: int
    source_hash_prefix: str  # 12 位不可逆前缀，唯一允许落盘/展示的源码标识
    published_at: datetime | None = None


class PortfolioSnapshotDTO(BaseModel):
    name: str
    version: int
    total_assets: str
    available_cash: str
    risk: dict
    snapshot_at: datetime | None = None


class MatchingBuyPreviewDTO(BaseModel):
    symbol: str
    score: float | None
    reason: str | None
    entry_price: float | None
    stop_loss: float | None
    take_profit: float | None
    order_status: str | None


class HoldingSignalPreviewDTO(BaseModel):
    symbol: str
    action: str | None
    reason: str | None
    order_status: str | None


class SuggestedOrderPreviewDTO(BaseModel):
    symbol: str
    action: str | None
    shares: float | None
    notional: float | None
    order_cost_price: float | None
    valuation_price: float | None
    stop_loss: float | None
    take_profit: float | None
    risk_bucket: dict | None


class QuantExecutionSummaryDTO(BaseModel):
    universe_total: int
    data_complete: int
    scanned: int
    buy_matches: int
    suggested_buy_orders: int
    suggested_sell_orders: int
    failed_count: int


class QuantExecutionDTO(BaseModel):
    """decision["quant_execution"] 的无源码投影（旧报告为 null，不渲染面板）。"""

    strategy: StrategyAuditDTO
    portfolio_snapshot: PortfolioSnapshotDTO
    matching_buy_preview: list[MatchingBuyPreviewDTO]
    holding_signal_preview: list[HoldingSignalPreviewDTO]
    suggested_order_preview: list[SuggestedOrderPreviewDTO]
    summary: QuantExecutionSummaryDTO
    warnings: list[str]
    valued_at: str | None = None


class QuantSignalRowDTO(BaseModel):
    id: int
    ts_code: str
    signal_kind: str
    attempt_no: int
    action: str | None = None
    score: float | None = None
    reason: str | None = None
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    sell_ratio: float | None = None
    order_status: str | None = None
    shares: float | None = None
    notional: float | None = None
    order_cost_price: float | None = None
    valuation_price: float | None = None
    risk_bucket: dict | None = None
    error_code: str | None = None


class QuantSignalPageData(BaseModel):
    items: list[QuantSignalRowDTO]
    next_cursor: str | None = None
    attempt_no: int
    kind: str
