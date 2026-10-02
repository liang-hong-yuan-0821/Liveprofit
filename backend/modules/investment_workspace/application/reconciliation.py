"""Compare an external account observation with the current local projection.

This is a diagnostic only.  It neither certifies the observation's provenance
nor writes holdings, cash, or sellable quantity into a trading account.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable
from uuid import UUID
from zoneinfo import ZoneInfo


InstrumentKey = tuple[str, str]


@dataclass(frozen=True)
class ObservedHolding:
    market: str
    symbol: str
    quantity: object
    sellable_quantity: object | None


@dataclass(frozen=True)
class AccountObservation:
    portfolio_id: UUID
    trade_date: date
    captured_at: datetime
    cash: object
    holdings: tuple[ObservedHolding, ...]
    complete_holdings: bool
    source_ref: str


@dataclass(frozen=True)
class AccountDifference:
    kind: str
    instrument: InstrumentKey | None
    local: Decimal | None
    observed: Decimal | None


@dataclass(frozen=True)
class AccountReconciliation:
    portfolio_id: UUID
    observation_trade_date: date
    source_ref: str
    local_issues: tuple[str, ...]
    observation_issues: tuple[str, ...]
    differences: tuple[AccountDifference, ...]

    @property
    def local_values_match(self) -> bool:
        """A comparison result, never a source or settlement certificate."""
        return not (self.local_issues or self.observation_issues or self.differences)


def _amount(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() and result >= 0 else None


def _key(market: object, symbol: object) -> InstrumentKey | None:
    if not isinstance(market, str) or not isinstance(symbol, str):
        return None
    if not market or not symbol or market != market.strip() or symbol != symbol.strip():
        return None
    return market, symbol


def reconcile_account_observation(
    *,
    expected_portfolio_id: UUID,
    expected_trade_date: date,
    local_cash: object,
    local_holdings: Iterable[tuple[str, str, object]],
    observation: AccountObservation,
) -> AccountReconciliation:
    """Report local differences without inferring trades from a snapshot delta.

    A partial observation can compare listed securities, but absence of a local
    security in that observation is not interpreted as zero holdings.
    """
    local_issues: list[str] = []
    observed_issues: list[str] = []
    differences: list[AccountDifference] = []
    if not isinstance(expected_portfolio_id, UUID):
        raise ValueError("expected_portfolio_id must be a UUID")
    if observation.portfolio_id != expected_portfolio_id:
        observed_issues.append("PORTFOLIO_MISMATCH")
    if type(expected_trade_date) is not date:
        raise ValueError("expected_trade_date must be a date")
    valid_observation_date = type(observation.trade_date) is date
    if not valid_observation_date or observation.trade_date != expected_trade_date:
        observed_issues.append("TRADE_DATE_MISMATCH")
    if (not isinstance(observation.captured_at, datetime)
            or observation.captured_at.utcoffset() is None
            or (valid_observation_date and observation.captured_at.astimezone(
                ZoneInfo("Asia/Shanghai")).date() < observation.trade_date)):
        observed_issues.append("CAPTURE_TIME_INVALID")
    if not isinstance(observation.source_ref, str) or not observation.source_ref.strip():
        observed_issues.append("SOURCE_REF_MISSING")
    if type(observation.complete_holdings) is not bool:
        observed_issues.append("COVERAGE_UNKNOWN")
    elif not observation.complete_holdings:
        observed_issues.append("COVERAGE_PARTIAL")

    cash = _amount(local_cash)
    observed_cash = _amount(observation.cash)
    if cash is None:
        local_issues.append("LOCAL_CASH_INVALID")
    if observed_cash is None:
        observed_issues.append("OBSERVED_CASH_INVALID")
    if cash is not None and observed_cash is not None and cash != observed_cash:
        differences.append(AccountDifference("CASH", None, cash, observed_cash))

    local: dict[InstrumentKey, Decimal] = {}
    try:
        local_rows = iter(local_holdings)
    except TypeError:
        local_issues.append("LOCAL_HOLDINGS_INVALID")
        local_rows = iter(())
    for row in local_rows:
        if not isinstance(row, (tuple, list)) or len(row) != 3:
            local_issues.append("LOCAL_HOLDING_INVALID_OR_DUPLICATE")
            continue
        market, symbol, raw_quantity = row
        key = _key(market, symbol)
        quantity = _amount(raw_quantity)
        if key is None or quantity is None or key in local:
            local_issues.append("LOCAL_HOLDING_INVALID_OR_DUPLICATE")
            continue
        local[key] = quantity

    seen: set[InstrumentKey] = set()
    observed: dict[InstrumentKey, Decimal] = {}
    try:
        observed_rows = iter(observation.holdings)
    except TypeError:
        observed_issues.append("OBSERVED_HOLDINGS_INVALID")
        observed_rows = iter(())
    for holding in observed_rows:
        if not isinstance(holding, ObservedHolding):
            observed_issues.append("OBSERVED_HOLDING_INVALID_OR_DUPLICATE")
            continue
        key = _key(holding.market, holding.symbol)
        quantity = _amount(holding.quantity)
        sellable = _amount(holding.sellable_quantity)
        if key is None or quantity is None or key in seen:
            observed_issues.append("OBSERVED_HOLDING_INVALID_OR_DUPLICATE")
            continue
        seen.add(key)
        if sellable is None or sellable > quantity:
            observed_issues.append(f"SELLABLE_INVALID:{key[0]}:{key[1]}")
        observed[key] = quantity

    for key in sorted(observed):
        local_quantity = local.get(key)
        if local_quantity is None or local_quantity != observed[key]:
            differences.append(AccountDifference("HOLDING", key, local_quantity, observed[key]))
    if observation.complete_holdings is True:
        for key in sorted(local.keys() - observed.keys()):
            differences.append(AccountDifference("HOLDING", key, local[key], None))

    return AccountReconciliation(
        portfolio_id=expected_portfolio_id,
        observation_trade_date=observation.trade_date,
        source_ref=observation.source_ref,
        local_issues=tuple(local_issues),
        observation_issues=tuple(observed_issues),
        differences=tuple(differences),
    )
