"""Suggested-order and fill confirmation API contracts (N5)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LifecyclePolicyVersionDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    policy_key: str
    version_no: int
    status: str
    required_fields: list[str]
    config: dict
    content_hash: str
    published_at: datetime | None
    created_at: datetime


class LifecyclePolicyListData(BaseModel):
    items: list[LifecyclePolicyVersionDTO]


class LifecyclePolicyCreateRequest(BaseModel):
    policy_key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    required_fields: list[str] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)


class SuggestedOrderDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    portfolio_id: UUID
    position_id: UUID | None
    lifecycle_id: UUID | None
    intent_id: UUID | None
    source_signal_id: int | None
    market: str
    symbol: str
    industry_code: str | None
    side: Literal["BUY", "SELL"]
    quantity: Decimal
    filled_quantity: Decimal
    limit_price: Decimal
    stop_price: Decimal | None
    reserved_cash: Decimal
    reserved_risk: Decimal
    reason_code: str
    status: str
    revision: int
    earliest_execution_trade_date: date | None
    created_at: datetime
    updated_at: datetime


class SuggestedOrderListData(BaseModel):
    items: list[SuggestedOrderDTO]
    next_cursor: str | None = None


class OrderFillEventDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    order_id: UUID
    portfolio_id: UUID
    position_id: UUID | None
    event_type: Literal["CONFIRM", "CORRECT", "VOID"]
    reverses_fill_id: UUID | None
    quantity: Decimal
    fill_price: Decimal
    fill_trade_date: date
    source: str
    idempotency_key: str
    note: str | None
    created_at: datetime


class OrderFillListData(BaseModel):
    items: list[OrderFillEventDTO]
    next_cursor: str | None = None


class PositionLifecycleDTO(BaseModel):
    id: UUID
    portfolio_id: UUID
    position_id: UUID | None
    market: str
    symbol: str
    strategy_version_id: UUID
    lifecycle_policy_version_id: UUID
    actual_shares: Decimal
    initial_fill_price: Decimal | None
    initial_stop_price: Decimal | None
    risk_capacity_shares: Decimal | None
    target_exposure_pct: Decimal
    target_shares: Decimal
    phase: str
    profit_take_price: Decimal | None
    profit_target_reached: bool
    confirmation_completed: bool
    profit_trim_completed: bool
    arc_neckline_price: Decimal | None
    active_stop_price: Decimal | None
    high_water_mark: Decimal | None
    trailing_phase: str | None
    expectation_status: str | None
    expectation_observed_days: int | None
    expectation_window_days: int | None
    last_processed_trade_date: date | None
    state_version: int
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PositionLifecyclePageData(BaseModel):
    items: list[PositionLifecycleDTO]
    next_cursor: str | None = None


class PositionIntentDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    lifecycle_id: UUID
    source_signal_id: int | None
    trade_date: date
    target_shares: Decimal
    reason_code: str
    state_version: int
    status: str
    revision: int
    created_at: datetime
    updated_at: datetime


class PositionIntentPageData(BaseModel):
    items: list[PositionIntentDTO]
    next_cursor: str | None = None


class PositionDailyFactDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    lifecycle_id: UUID
    trade_date: date
    price_basis: str
    data_as_of: datetime
    input_payload: dict
    input_hash: str
    rule_version: str
    state_version_before: int
    state_version_after: int
    final_target_shares: Decimal
    created_at: datetime


class PositionDailyFactPageData(BaseModel):
    items: list[PositionDailyFactDTO]
    next_cursor: str | None = None


class FillMutationData(BaseModel):
    fill: OrderFillEventDTO
    order: SuggestedOrderDTO


class FillConfirmRequest(BaseModel):
    quantity: Decimal = Field(gt=0)
    fill_price: Decimal = Field(gt=0)
    fill_trade_date: date
    idempotency_key: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)
    source: Literal["MANUAL", "BROKER"] = "MANUAL"
    note: str | None = Field(default=None, max_length=500)


class FillCorrectRequest(BaseModel):
    quantity: Decimal = Field(gt=0)
    fill_price: Decimal = Field(gt=0)
    fill_trade_date: date
    idempotency_key: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=500)


class FillVoidRequest(BaseModel):
    fill_trade_date: date
    idempotency_key: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=500)


class OrderStatusRequest(BaseModel):
    status: Literal["EXECUTING", "REJECTED", "CANCELLED", "RECONCILIATION_REQUIRED", "SUPERSEDED"]
    expected_revision: int = Field(ge=1)
