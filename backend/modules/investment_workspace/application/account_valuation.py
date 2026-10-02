"""Pure account valuation and external-flow unitization diagnostics.

Callers must supply independently sourced prices at each stated time. These
calculations do not certify price provenance or authorize trading.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from .account_ledger import _aware, _number
from .reconciliation import _key


@dataclass(frozen=True)
class AccountValueSnapshot:
    portfolio_id: UUID
    as_of: datetime
    cash: Decimal
    holdings: tuple[tuple[str, str, Decimal], ...]
    prices: tuple[tuple[str, str, Decimal], ...]
    external_flow_total: Decimal


@dataclass(frozen=True)
class ExternalFlowAtValue:
    event_id: UUID
    portfolio_id: UUID
    effective_at: datetime
    amount: Decimal
    pre_flow: AccountValueSnapshot


@dataclass(frozen=True)
class AccountValuation:
    value: Decimal | None
    issues: tuple[str, ...]


@dataclass(frozen=True)
class UnitizedAccountValuation:
    units: Decimal | None
    unit_nav: Decimal | None
    total_return: Decimal | None
    issues: tuple[str, ...]


def value_account(snapshot: AccountValueSnapshot) -> AccountValuation:
    """Value one complete position set; missing or invalid evidence stays unknown."""
    if not isinstance(snapshot, AccountValueSnapshot):
        raise ValueError("valuation snapshot is required")
    if not isinstance(snapshot.portfolio_id, UUID) or not _aware(snapshot.as_of):
        raise ValueError("valuation identity and time are required")
    try:
        cash = _number(snapshot.cash)
    except ValueError:
        return AccountValuation(None, ("INVALID_CASH",))
    try:
        _number(snapshot.external_flow_total)
    except ValueError:
        return AccountValuation(None, ("INVALID_FLOW_TOTAL",))
    if not isinstance(snapshot.holdings, tuple):
        raise ValueError("holdings must be a tuple")
    issues: list[str] = []
    holdings: dict[tuple[str, str], Decimal] = {}
    for row in snapshot.holdings:
        if not isinstance(row, tuple) or len(row) != 3:
            raise ValueError("invalid holding row")
        key = _key(row[0], row[1])
        if key is None or key in holdings:
            raise ValueError("invalid or duplicate holding identity")
        try:
            holdings[key] = _number(row[2])
        except ValueError:
            holdings[key] = Decimal(0)
            issues.append(f"INVALID_HOLDING_QUANTITY:{key[0]}:{key[1]}")
    if not isinstance(snapshot.prices, tuple):
        raise ValueError("prices must be a tuple")
    prices: dict[tuple[str, str], Decimal] = {}
    for row in snapshot.prices:
        if not isinstance(row, tuple) or len(row) != 3:
            raise ValueError("invalid price row")
        key = _key(row[0], row[1])
        if key is None or key in prices:
            raise ValueError("invalid or duplicate price identity")
        try:
            prices[key] = _number(row[2])
        except ValueError:
            prices[key] = Decimal(0)
    if cash < 0:
        issues.append("NEGATIVE_CASH")
    value = cash
    for key, quantity in holdings.items():
        if quantity < 0:
            issues.append(f"NEGATIVE_HOLDING:{key[0]}:{key[1]}")
        if quantity == 0:
            continue
        price = prices.get(key)
        if price is None:
            issues.append(f"MISSING_PRICE:{key[0]}:{key[1]}")
        elif price <= 0:
            issues.append(f"INVALID_PRICE:{key[0]}:{key[1]}")
        else:
            value += quantity * price
    if issues:
        return AccountValuation(None, tuple(issues))
    if value <= 0:
        return AccountValuation(None, ("NONPOSITIVE_VALUE",))
    return AccountValuation(value, ())


def unitize_account(
    baseline: AccountValueSnapshot,
    flows: tuple[ExternalFlowAtValue, ...],
    ending: AccountValueSnapshot,
    *,
    expected_flow_event_ids: tuple[UUID, ...] | None = None,
) -> UnitizedAccountValuation:
    """Adjust units at each pre-flow value using a complete ledger flow inventory.

    The inventory must come from the effective ledger events for this period;
    net flow totals alone cannot detect omitted offsetting deposits/withdrawals.
    """
    if not isinstance(flows, tuple):
        raise ValueError("flows must be a tuple")
    if (not isinstance(baseline, AccountValueSnapshot)
            or not isinstance(ending, AccountValueSnapshot)
            or not _aware(baseline.as_of) or not _aware(ending.as_of)
            or baseline.portfolio_id != ending.portfolio_id
            or ending.as_of < baseline.as_of):
        raise ValueError("valuation portfolio or time mismatch")
    start = value_account(baseline)
    finish = value_account(ending)
    issues = [f"BASELINE:{issue}" for issue in start.issues]
    issues.extend(f"ENDING:{issue}" for issue in finish.issues)
    if expected_flow_event_ids is None:
        issues.append("MISSING_FLOW_INVENTORY")
    elif (not isinstance(expected_flow_event_ids, tuple)
          or any(not isinstance(event_id, UUID) for event_id in expected_flow_event_ids)
          or len(set(expected_flow_event_ids)) != len(expected_flow_event_ids)):
        raise ValueError("invalid flow inventory")
    seen: set[UUID] = set()
    previous_time = baseline.as_of
    previous_flow: ExternalFlowAtValue | None = None
    try:
        expected_flow_total = _number(baseline.external_flow_total)
    except ValueError:
        expected_flow_total = Decimal(0)
    units = start.value
    for flow in flows:
        if (not isinstance(flow, ExternalFlowAtValue) or not isinstance(flow.event_id, UUID)
                or not isinstance(flow.pre_flow, AccountValueSnapshot)):
            raise ValueError("invalid external flow")
        if flow.event_id in seen:
            issues.append("DUPLICATE_FLOW")
        seen.add(flow.event_id)
        if (flow.portfolio_id != baseline.portfolio_id or not _aware(flow.effective_at)
                or not (previous_time <= flow.effective_at <= ending.as_of)
                or flow.pre_flow.portfolio_id != flow.portfolio_id
                or flow.pre_flow.as_of != flow.effective_at):
            issues.append(f"FLOW_TIME_OR_PORTFOLIO:{flow.event_id}")
        if _aware(flow.effective_at) and flow.effective_at >= previous_time:
            previous_time = flow.effective_at
        invalid_amount = False
        try:
            amount = _number(flow.amount)
        except ValueError:
            issues.append(f"INVALID_FLOW_AMOUNT:{flow.event_id}")
            amount = Decimal(0)
            invalid_amount = True
        if amount == 0 and not invalid_amount:
            issues.append(f"ZERO_FLOW:{flow.event_id}")
        try:
            flow_total = _number(flow.pre_flow.external_flow_total)
        except ValueError:
            flow_total = None
        if flow_total != expected_flow_total:
            issues.append(f"FLOW_TOTAL_MISMATCH:{flow.event_id}")
        if previous_flow is not None and flow.effective_at == previous_flow.effective_at:
            previous_snapshot = previous_flow.pre_flow
            try:
                cash_continuous = (_number(flow.pre_flow.cash)
                                   == _number(previous_snapshot.cash) + _number(previous_flow.amount))
            except ValueError:
                cash_continuous = False
            if (sorted(flow.pre_flow.holdings) != sorted(previous_snapshot.holdings)
                    or sorted(flow.pre_flow.prices) != sorted(previous_snapshot.prices)
                    or not cash_continuous):
                issues.append(f"SAME_TIME_FLOW_DISCONTINUITY:{flow.event_id}")
        if _aware(flow.pre_flow.as_of):
            pre = value_account(flow.pre_flow)
        else:
            pre = AccountValuation(None, ("INVALID_TIME",))
        issues.extend(f"PRE_FLOW:{flow.event_id}:{issue}" for issue in pre.issues)
        if units is not None and pre.value is not None and units > 0:
            units += amount * units / pre.value
            if units <= 0:
                issues.append(f"NONPOSITIVE_UNITS:{flow.event_id}")
        expected_flow_total += amount
        if _aware(flow.effective_at):
            previous_flow = flow
    try:
        ending_flow_total = _number(ending.external_flow_total)
    except ValueError:
        ending_flow_total = None
    if ending_flow_total != expected_flow_total:
        issues.append("ENDING_FLOW_TOTAL_MISMATCH")
    if expected_flow_event_ids is not None and expected_flow_event_ids != tuple(
            flow.event_id for flow in flows):
        issues.append("FLOW_INVENTORY_MISMATCH")
    if issues or units is None or finish.value is None:
        return UnitizedAccountValuation(None, None, None, tuple(issues))
    unit_nav = finish.value / units
    return UnitizedAccountValuation(units, unit_nav, unit_nav - 1, ())
