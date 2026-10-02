"""Read and compare lifecycle projections without changing account state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select

from backend.modules.investment_workspace.infrastructure.models import PortfolioPosition
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    PositionExpectation, PositionLifecycleState, PositionTrailingStop,
)
from .lifecycle_accounting_replay import AccountingReplay
from .lifecycle_policy_replay import PolicyReplay
from .planning_account import lock_portfolio


@dataclass(frozen=True)
class CurrentLifecycleProjection:
    position_quantity: Decimal | None
    position_average_cost: Decimal | None
    initial_fill_price: Decimal | None
    initial_stop_price: Decimal | None
    risk_capacity_shares: Decimal | None
    target_shares: Decimal | None
    target_exposure_pct: Decimal | None
    profit_target_reached: bool | None
    confirmation_completed: bool | None
    expectation_status: str | None
    expectation_observed_days: int | None
    trailing_phase: str | None
    high_water_mark: Decimal | None
    active_stop_price: Decimal | None
    phase: str | None = None
    profit_trim_completed: bool | None = None
    profit_take_price: Decimal | None = None
    arc_neckline_price: Decimal | None = None
    closed_at: datetime | None = None
    last_processed_trade_date: date | None = None
    state_version: int | None = None


@dataclass(frozen=True)
class ProjectionFieldDiff:
    field: str
    expected: Any
    current: Any
    status: Literal["MATCH", "DIFFERENT", "UNKNOWN"]


@dataclass(frozen=True)
class LifecycleProjectionDiff:
    status: Literal["PROVISIONAL_MATCH", "PROVISIONAL_DIFFERENCE", "UNKNOWN"]
    fields: tuple[ProjectionFieldDiff, ...]
    issues: tuple[str, ...]


def load_current_lifecycle_projection(session, *, portfolio_id: UUID,
                                      lifecycle_id: UUID) -> CurrentLifecycleProjection:
    """Lock portfolio first, then read current projection rows in one transaction."""
    if session.new or session.dirty or session.deleted:
        raise ValueError("projection diff requires a clean session")
    if lock_portfolio(session, portfolio_id) is None:
        raise ValueError("portfolio does not exist")
    lifecycle = session.scalar(select(PositionLifecycleState).where(
        PositionLifecycleState.id == lifecycle_id,
        PositionLifecycleState.portfolio_id == portfolio_id,
    ).with_for_update().execution_options(populate_existing=True))
    if lifecycle is None:
        raise ValueError("lifecycle does not belong to portfolio")
    position = session.scalar(select(PortfolioPosition).where(
        PortfolioPosition.id == lifecycle.position_id,
        PortfolioPosition.portfolio_id == portfolio_id,
        PortfolioPosition.market == lifecycle.market,
        PortfolioPosition.symbol == lifecycle.symbol,
    ).with_for_update().execution_options(populate_existing=True)) if lifecycle.position_id else None
    trailing = session.scalar(select(PositionTrailingStop).where(
        PositionTrailingStop.lifecycle_id == lifecycle_id,
    ).with_for_update().execution_options(populate_existing=True))
    expectation = session.scalar(select(PositionExpectation).where(
        PositionExpectation.lifecycle_id == lifecycle_id,
    ).with_for_update().execution_options(populate_existing=True))
    return CurrentLifecycleProjection(
        position.quantity if position else None,
        position.average_cost if position else None,
        lifecycle.initial_fill_price, lifecycle.initial_stop_price,
        lifecycle.risk_capacity_shares, lifecycle.target_shares,
        lifecycle.target_exposure_pct, lifecycle.profit_target_reached,
        lifecycle.confirmation_completed,
        expectation.status if expectation else None,
        expectation.observed_trading_days if expectation else None,
        trailing.phase if trailing else None,
        trailing.high_water_mark if trailing else None,
        trailing.active_stop_price if trailing else None,
        lifecycle.phase,
        lifecycle.profit_trim_completed,
        lifecycle.profit_take_price,
        lifecycle.arc_neckline_price,
        lifecycle.closed_at,
        lifecycle.last_processed_trade_date,
        lifecycle.state_version,
    )


def compare_lifecycle_projection(
    *, accounting: AccountingReplay, policy: PolicyReplay,
    current: CurrentLifecycleProjection,
) -> LifecycleProjectionDiff:
    """Compare covered fields only; matching values do not certify sources."""
    issues: list[str] = []
    fields: list[ProjectionFieldDiff] = []

    def field(name: str, expected: Any, actual: Any, *, known: bool,
              actual_required: bool = False) -> None:
        if not known or (actual_required and actual is None):
            fields.append(ProjectionFieldDiff(name, expected if known else None, actual, "UNKNOWN"))
            if actual_required and actual is None:
                issues.append(f"CURRENT_FIELD_UNKNOWN:{name}")
        else:
            fields.append(ProjectionFieldDiff(name, expected, actual,
                                              "MATCH" if expected == actual else "DIFFERENT"))

    accounting_known = accounting.status == "CALCULATED"
    policy_known = policy.status == "PROVISIONAL" and policy.final_state is not None and bool(policy.days)
    if not accounting_known:
        issues.extend(("ACCOUNTING_REPLAY_UNKNOWN", *accounting.issues))
    if not policy_known:
        issues.extend(("POLICY_REPLAY_UNKNOWN", *policy.issues))
    field("position.quantity", accounting.quantity, current.position_quantity,
          known=accounting_known, actual_required=True)
    field("position.average_cost", accounting.average_cost, current.position_average_cost,
          known=accounting_known, actual_required=accounting.quantity != 0 if accounting_known else False)
    state = policy.final_state if policy_known else None
    last = policy.days[-1] if policy_known else None
    for name, expected, actual in (
        ("lifecycle.initial_fill_price", state.initial_fill_price if state else None,
         current.initial_fill_price),
        ("lifecycle.initial_stop_price", state.initial_stop_price if state else None,
         current.initial_stop_price),
        ("lifecycle.risk_capacity_shares", state.risk_capacity_shares if state else None,
         current.risk_capacity_shares),
        ("lifecycle.target_shares", last.target_shares if last else None,
         current.target_shares),
        ("lifecycle.target_exposure_pct", state.target_exposure_pct if state else None,
         current.target_exposure_pct),
        ("lifecycle.profit_target_reached", state.profit_target_reached if state else None,
         current.profit_target_reached),
        ("lifecycle.confirmation_completed", state.confirmation_completed if state else None,
         current.confirmation_completed),
    ):
        field(name, expected, actual, known=policy_known, actual_required=True)
    field("lifecycle.phase", last.phase if last else None, current.phase,
          known=policy_known and last.phase is not None, actual_required=True)
    field("lifecycle.profit_trim_completed", policy.profit_trim_completed,
          current.profit_trim_completed,
          known=policy_known and type(policy.profit_trim_completed) is bool,
          actual_required=True)
    for name, expected, actual in (
        ("lifecycle.profit_take_price", state.profit_take_price if state else None,
         current.profit_take_price),
        ("lifecycle.arc_neckline_price", state.arc_neckline_price if state else None,
         current.arc_neckline_price),
    ):
        field(name, expected, actual, known=policy_known,
              actual_required=policy_known and expected is not None)
    for name, expected, actual in (
        ("expectation.status", policy.final_expectation.status
         if policy_known and policy.final_expectation else None, current.expectation_status),
        ("expectation.observed_trading_days", policy.final_expectation.observed_trading_days
         if policy_known and policy.final_expectation else None, current.expectation_observed_days),
        ("trailing.phase", policy.final_trailing.phase
         if policy_known and policy.final_trailing else None, current.trailing_phase),
        ("trailing.high_water_mark", policy.final_trailing.high_water_mark
         if policy_known and policy.final_trailing else None, current.high_water_mark),
        ("trailing.active_stop_price", policy.final_trailing.active_stop_price
         if policy_known and policy.final_trailing else None, current.active_stop_price),
    ):
        field(name, expected, actual, known=policy_known,
              actual_required=policy_known and expected is not None)
    status = ("UNKNOWN" if any(item.status == "UNKNOWN" for item in fields)
              else "PROVISIONAL_DIFFERENCE" if any(item.status == "DIFFERENT" for item in fields)
              else "PROVISIONAL_MATCH")
    return LifecycleProjectionDiff(status, tuple(fields), tuple(issues))
