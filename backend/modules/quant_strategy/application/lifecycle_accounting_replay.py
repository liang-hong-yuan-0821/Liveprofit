"""Pure diagnostic replay of lifecycle quantity and cost by actual event time.

Callers must first resolve immutable correction/void chains into one active
fill set and authenticate broker fills, baseline cost and corporate actions
separately. A correction is a replacement for its original fill, never an
additional fill. This arithmetic result never authorizes an order or a
projection write and does not derive strategy targets or sellable quantity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Context, Decimal, DecimalException, ROUND_HALF_EVEN, localcontext
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo


_CN_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class ReplayFill:
    event_id: UUID
    executed_at: datetime
    fill_trade_date: date
    side: Literal["BUY", "SELL"]
    quantity: Decimal
    price: Decimal
    fee: Decimal | None
    source_ref: str
    intent_id: UUID | None = None


@dataclass(frozen=True)
class ReplaySplit:
    event_id: UUID
    effective_at: datetime
    quantity_ratio: Decimal
    source_ref: str


@dataclass(frozen=True)
class AccountingEventState:
    event_id: UUID
    effective_at: datetime
    quantity: Decimal
    total_cost: Decimal
    average_cost: Decimal | None
    realized_pnl: Decimal
    intent_id: UUID | None = None
    side: Literal["BUY", "SELL"] | None = None


@dataclass(frozen=True)
class AccountingReplay:
    status: Literal["CALCULATED", "UNKNOWN"]
    issues: tuple[str, ...]
    quantity: Decimal | None
    total_cost: Decimal | None
    average_cost: Decimal | None
    realized_pnl: Decimal | None
    event_ids: tuple[UUID, ...]
    event_states: tuple[AccountingEventState, ...] = ()
    baseline_as_of: datetime | None = None
    baseline_quantity: Decimal | None = None


def _bounded(value: object, *, positive: bool = False) -> bool:
    if not isinstance(value, Decimal) or not value.is_finite():
        return False
    if (value <= 0 if positive else value < 0):
        return False
    digits = value.as_tuple()
    return (len(digits.digits) <= 40 and -12 <= digits.exponent <= 18
            and value.adjusted() <= 18)


def _aware(value: object) -> bool:
    return (isinstance(value, datetime) and value.tzinfo is not None
            and value.utcoffset() is not None)


def replay_position_accounting(
    *, baseline_as_of: datetime, baseline_quantity: Decimal,
    baseline_total_cost: Decimal, baseline_source_ref: str,
    fills: tuple[ReplayFill, ...], splits: tuple[ReplaySplit, ...] = (),
) -> AccountingReplay:
    """Return calculated arithmetic or explicit unknown; never mutate input.

    Only splits with exact integral resulting share quantity are modeled.
    Dividends, rights, cash in lieu and tax effects require a distinct event
    contract and are not silently represented as a split.
    """
    issues: list[str] = []
    if not _aware(baseline_as_of):
        issues.append("BASELINE_TIME_UNKNOWN")
    if not isinstance(baseline_source_ref, str) or not baseline_source_ref.strip():
        issues.append("BASELINE_SOURCE_UNKNOWN")
    if (not _bounded(baseline_quantity)
            or not _bounded(baseline_total_cost)
            or (baseline_quantity == 0 and baseline_total_cost != 0)
            or (baseline_quantity > 0 and baseline_total_cost <= 0)):
        issues.append("BASELINE_COST_INVALID")

    events: list[tuple[datetime, UUID, ReplayFill | ReplaySplit]] = []
    seen_ids: set[UUID] = set()
    seen_times: set[datetime] = set()
    for event in (*fills, *splits):
        at = event.executed_at if isinstance(event, ReplayFill) else event.effective_at
        if not isinstance(event.event_id, UUID) or event.event_id in seen_ids:
            issues.append(f"EVENT_ID_INVALID:{event.event_id}")
        seen_ids.add(event.event_id)
        if not _aware(at):
            issues.append(f"EVENT_TIME_UNKNOWN:{event.event_id}")
        elif at in seen_times:
            issues.append(f"EVENT_ORDER_AMBIGUOUS:{event.event_id}")
        else:
            seen_times.add(at)
        if _aware(at) and _aware(baseline_as_of) and at <= baseline_as_of:
            issues.append(f"EVENT_PRECEDES_BASELINE:{event.event_id}")
        if not isinstance(event.source_ref, str) or not event.source_ref.strip():
            issues.append(f"EVENT_SOURCE_UNKNOWN:{event.event_id}")
        if isinstance(event, ReplayFill):
            if (event.side not in ("BUY", "SELL")
                    or (event.intent_id is not None and not isinstance(event.intent_id, UUID))
                    or not _bounded(event.quantity, positive=True)
                    or not _bounded(event.price, positive=True)
                    or not _bounded(event.fee)):
                issues.append(f"FILL_VALUES_UNKNOWN:{event.event_id}")
            if (_aware(at) and (not isinstance(event.fill_trade_date, date)
                                or isinstance(event.fill_trade_date, datetime)
                                or at.astimezone(_CN_TZ).date() != event.fill_trade_date)):
                issues.append(f"FILL_TRADE_DATE_MISMATCH:{event.event_id}")
        elif not _bounded(event.quantity_ratio, positive=True):
            issues.append(f"SPLIT_RATIO_INVALID:{event.event_id}")
        if _aware(at) and isinstance(event.event_id, UUID):
            events.append((at, event.event_id, event))
    if issues:
        return AccountingReplay("UNKNOWN", tuple(issues), None, None, None, None, ())

    quantity = baseline_quantity
    total_cost = baseline_total_cost
    realized = Decimal(0)
    event_ids: list[UUID] = []
    event_states: list[AccountingEventState] = []
    try:
        with localcontext(Context(prec=80, rounding=ROUND_HALF_EVEN,
                                  Emax=999, Emin=-999)):
            for _at, event_id, event in sorted(events, key=lambda item: (item[0], item[1])):
                if isinstance(event, ReplaySplit):
                    new_quantity = quantity * event.quantity_ratio
                    if new_quantity != new_quantity.to_integral_value():
                        return AccountingReplay("UNKNOWN", (f"SPLIT_FRACTIONAL_RIGHTS:{event_id}",),
                                                None, None, None, None, tuple(event_ids))
                    quantity = new_quantity
                    if quantity == 0 and total_cost != 0:
                        return AccountingReplay("UNKNOWN", (f"SPLIT_ZERO_QUANTITY_WITH_COST:{event_id}",),
                                                None, None, None, None, tuple(event_ids))
                elif event.side == "BUY":
                    quantity += event.quantity
                    total_cost += event.quantity * event.price + event.fee
                else:
                    if event.quantity > quantity:
                        return AccountingReplay("UNKNOWN", (f"SELL_EXCEEDS_REPLAY_HOLDING:{event_id}",),
                                                None, None, None, None, tuple(event_ids))
                    average = total_cost / quantity
                    allocated_cost = total_cost if event.quantity == quantity else average * event.quantity
                    realized += event.quantity * event.price - event.fee - allocated_cost
                    quantity -= event.quantity
                    total_cost -= allocated_cost
                    if quantity == 0:
                        total_cost = Decimal(0)
                if any(value.adjusted() > 36 for value in (quantity, total_cost, realized)
                       if value != 0):
                    return AccountingReplay("UNKNOWN", (f"REPLAY_VALUE_OUT_OF_RANGE:{event_id}",),
                                            None, None, None, None, tuple(event_ids))
                event_ids.append(event_id)
                event_states.append(AccountingEventState(
                    event_id, _at, quantity, total_cost,
                    total_cost / quantity if quantity > 0 else None, realized,
                    event.intent_id if isinstance(event, ReplayFill) else None,
                    event.side if isinstance(event, ReplayFill) else None,
                ))
            average_cost = total_cost / quantity if quantity > 0 else None
    except DecimalException:
        return AccountingReplay("UNKNOWN", ("REPLAY_DECIMAL_ARITHMETIC_INVALID",),
                                None, None, None, None, tuple(event_ids))
    return AccountingReplay(
        "CALCULATED", (), quantity, total_cost, average_cost, realized,
        tuple(event_ids), tuple(event_states), baseline_as_of, baseline_quantity,
    )


@dataclass(frozen=True)
class DailyQuantityReplay:
    status: Literal["PROVISIONAL", "UNKNOWN"]
    issues: tuple[str, ...]
    quantities: tuple[tuple[date, Decimal], ...]


def project_daily_quantities(
    *, accounting: AccountingReplay, calendar_dates: tuple[date, ...],
) -> DailyQuantityReplay:
    """Carry replayed post-event holdings across declared exchange sessions."""
    if (not isinstance(accounting, AccountingReplay)
            or accounting.status != "CALCULATED"
            or not _bounded(accounting.quantity)
            or accounting.issues
            or not _aware(accounting.baseline_as_of)
            or not _bounded(accounting.baseline_quantity)
            or not isinstance(calendar_dates, tuple) or not calendar_dates
            or any(type(day) is not date for day in calendar_dates)
            or tuple(sorted(set(calendar_dates))) != calendar_dates):
        return DailyQuantityReplay("UNKNOWN", ("DAILY_QUANTITY_INPUT_UNKNOWN",), ())
    states = accounting.event_states
    if (not isinstance(states, tuple)
            or any(not isinstance(state, AccountingEventState) for state in states)
            or not isinstance(accounting.event_ids, tuple)):
        return DailyQuantityReplay("UNKNOWN", ("DAILY_QUANTITY_EVENT_CHAIN_UNKNOWN",), ())
    baseline_as_of = accounting.baseline_as_of
    baseline_quantity = accounting.baseline_quantity
    if (len(states) != len(accounting.event_ids)
            or tuple(state.event_id for state in states) != accounting.event_ids
            or (states and states[-1].quantity != accounting.quantity)
            or (not states and accounting.quantity != baseline_quantity)
            or any(not _aware(state.effective_at) for state in states)
            or any(_aware(state.effective_at) and state.effective_at <= baseline_as_of
                   for state in states)
            or any(not _bounded(state.quantity) for state in states)
            or any(states[i].effective_at >= states[i + 1].effective_at
                   for i in range(len(states) - 1))
            or calendar_dates[0] < baseline_as_of.astimezone(_CN_TZ).date()):
        return DailyQuantityReplay("UNKNOWN", ("DAILY_QUANTITY_EVENT_CHAIN_UNKNOWN",), ())
    quantity = baseline_quantity
    index = 0
    projected: list[tuple[date, Decimal]] = []
    for day in calendar_dates:
        while index < len(states) and states[index].effective_at.astimezone(_CN_TZ).date() <= day:
            quantity = states[index].quantity
            index += 1
        projected.append((day, quantity))
    return DailyQuantityReplay("PROVISIONAL", (), tuple(projected))
