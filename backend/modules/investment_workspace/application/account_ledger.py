"""Deterministic balance replay for a separately audited account event stream.

This arithmetic kernel does not certify an event source or publish balances to
the trading projection. Event persistence and source admission belong upstream.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from .reconciliation import _key


InstrumentKey = tuple[str, str]


def _number(value: object) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError("ledger amount must be finite")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("ledger amount must be finite") from None
    if not number.is_finite():
        raise ValueError("ledger amount must be finite")
    return number


@dataclass(frozen=True)
class LedgerBaseline:
    portfolio_id: uuid.UUID
    effective_at: datetime
    cash: Decimal
    holdings: tuple[tuple[str, str, Decimal], ...]


@dataclass(frozen=True)
class LedgerMovement:
    portfolio_id: uuid.UUID
    event_id: uuid.UUID
    recorded_at: datetime
    effective_at: datetime
    kind: str  # CASH_FLOW, TRADE, CORPORATE_ACTION, ADJUSTMENT, VOID
    cash_delta: Decimal
    holdings_delta: tuple[tuple[str, str, Decimal], ...] = ()
    fill_price: Decimal | None = None
    fee: Decimal = Decimal(0)
    supersedes_id: uuid.UUID | None = None
    reason: str | None = None


@dataclass(frozen=True)
class LedgerBalance:
    cash: Decimal
    holdings: tuple[tuple[str, str, Decimal], ...]
    external_flow_total: Decimal
    effective_event_ids: tuple[uuid.UUID, ...]
    external_flow_event_ids: tuple[uuid.UUID, ...]
    issues: tuple[str, ...] = ()


def _aware(value: object) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None


def _position_deltas(rows: tuple[tuple[str, str, Decimal], ...]) -> dict[InstrumentKey, Decimal]:
    if not isinstance(rows, tuple):
        raise ValueError("holdings deltas must be a tuple")
    values: dict[InstrumentKey, Decimal] = {}
    for row in rows:
        if not isinstance(row, tuple) or len(row) != 3:
            raise ValueError("invalid holding delta")
        market, symbol, raw = row
        key = _key(market, symbol)
        if key is None or key in values:
            raise ValueError("invalid or duplicate holding identity")
        values[key] = _number(raw)
    return values


def replay_account_balance(
    baseline: LedgerBaseline,
    movements: tuple[LedgerMovement, ...],
    *,
    as_of: datetime,
    recorded_as_of: datetime | None = None,
    allow_negative: bool = False,
) -> LedgerBalance:
    """Replay effective replacements at an effective and knowledge cutoff.

    A correction references the exact event it replaces. A replacement may itself
    be replaced, but a fork or missing predecessor is invalid. The function never
    infers a trade from a snapshot difference. By default the knowledge cutoff is
    the effective cutoff, preserving the original as-known-then behavior.
    """
    if not _aware(baseline.effective_at) or not _aware(as_of):
        raise ValueError("baseline and replay times must be timezone aware")
    if recorded_as_of is None:
        recorded_as_of = as_of
    if not _aware(recorded_as_of):
        raise ValueError("recorded_as_of must be timezone aware")
    if not isinstance(baseline.portfolio_id, uuid.UUID):
        raise ValueError("baseline portfolio id must be a UUID")
    if baseline.effective_at > as_of:
        raise ValueError("baseline is later than replay time")
    cash = _number(baseline.cash)
    if cash < 0:
        raise ValueError("baseline cash is negative")
    holdings = _position_deltas(baseline.holdings)
    if any(quantity < 0 for quantity in holdings.values()):
        raise ValueError("baseline holding is negative")
    if not isinstance(movements, tuple):
        raise ValueError("movements must be a tuple")
    if any(not isinstance(event, LedgerMovement) or not isinstance(event.event_id, uuid.UUID)
           or not _aware(event.recorded_at) for event in movements):
        raise ValueError("invalid ledger movement")

    by_id: dict[uuid.UUID, LedgerMovement] = {}
    replaced: set[uuid.UUID] = set()
    for movement in sorted(movements, key=lambda event: (event.recorded_at, event.event_id.int)):
        if not isinstance(movement, LedgerMovement) or not isinstance(movement.event_id, uuid.UUID):
            raise ValueError("invalid ledger movement")
        if movement.portfolio_id != baseline.portfolio_id:
            raise ValueError("movement portfolio mismatch")
        if not _aware(movement.recorded_at) or not _aware(movement.effective_at):
            raise ValueError("movement times must be timezone aware")
        if movement.effective_at < baseline.effective_at:
            raise ValueError("movement predates baseline")
        if movement.recorded_at > recorded_as_of:
            continue
        if movement.event_id in by_id:
            raise ValueError("duplicate event id")
        if movement.supersedes_id is not None:
            predecessor = by_id.get(movement.supersedes_id)
            if predecessor is None or predecessor.event_id in replaced:
                raise ValueError("correction predecessor missing or forked")
            if movement.recorded_at <= predecessor.recorded_at:
                raise ValueError("correction must be recorded after predecessor")
            valid_kind = (movement.kind == predecessor.kind and predecessor.kind != "VOID") or (
                movement.kind == "VOID" and predecessor.kind == "TRADE"
                and movement.effective_at == predecessor.effective_at
            )
            if not valid_kind or not isinstance(movement.reason, str) or not movement.reason.strip():
                raise ValueError("correction needs a valid predecessor and a reason")
            replaced.add(predecessor.event_id)
        by_id[movement.event_id] = movement

    effective = sorted((event for event in by_id.values()
                        if event.event_id not in replaced and event.effective_at <= as_of),
                       key=lambda event: (event.effective_at, event.recorded_at, event.event_id.int))
    external_flow_total = Decimal(0)
    issues: list[str] = []
    for event in effective:
        delta = _number(event.cash_delta)
        fee = _number(event.fee)
        if fee < 0:
            raise ValueError("fee is negative")
        position_delta = _position_deltas(event.holdings_delta)
        if event.kind == "CASH_FLOW":
            if position_delta or event.fill_price is not None or fee != 0:
                raise ValueError("cash flow cannot change holdings or carry a fee")
            external_flow_total += delta
        elif event.kind == "TRADE":
            price = _number(event.fill_price)
            if len(position_delta) != 1 or price <= 0:
                raise ValueError("trade needs one holding and positive fill price")
            quantity_delta = next(iter(position_delta.values()))
            if quantity_delta == 0 or delta != -quantity_delta * price - fee:
                raise ValueError("trade cash, quantity, price and fee disagree")
        elif event.kind == "CORPORATE_ACTION":
            if (not (delta != 0 or any(q != 0 for q in position_delta.values()))
                    or event.fill_price is not None or fee != 0):
                raise ValueError("invalid corporate action")
        elif event.kind == "ADJUSTMENT":
            if (not isinstance(event.reason, str) or not event.reason.strip()
                    or event.fill_price is not None or fee != 0
                    or (delta == 0 and not any(q != 0 for q in position_delta.values()))):
                raise ValueError("adjustment requires a reason and a balance change")
        elif event.kind == "VOID":
            if (event.supersedes_id is None or delta != 0 or position_delta
                    or event.fill_price is not None or fee != 0):
                raise ValueError("void must replace a trade without a balance change")
        else:
            raise ValueError("unsupported ledger event kind")
        cash += delta
        if cash < 0:
            if not allow_negative:
                raise ValueError("replayed cash is negative")
            issues.append(f"NEGATIVE_CASH:{event.event_id}")
        for key, quantity_delta in position_delta.items():
            holdings[key] = holdings.get(key, Decimal(0)) + quantity_delta
            if holdings[key] < 0:
                if not allow_negative:
                    raise ValueError("replayed holding is negative")
                issues.append(f"NEGATIVE_HOLDING:{key[0]}:{key[1]}:{event.event_id}")
    return LedgerBalance(
        cash=cash,
        holdings=tuple((market, symbol, quantity) for (market, symbol), quantity
                       in sorted(holdings.items()) if quantity != 0),
        external_flow_total=external_flow_total,
        effective_event_ids=tuple(event.event_id for event in effective),
        external_flow_event_ids=tuple(event.event_id for event in effective
                                      if event.kind == "CASH_FLOW"),
        issues=tuple(issues),
    )
