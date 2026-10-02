"""Deterministic next-session opening fill proxy for caller-supplied research inputs.

The caller supplies effective-date trading rules and a dated ADV20. This
module never infers those facts from the next session's full-day turnover.
The evidence reference is carried, not authenticated, by this pure function.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR

from backend.modules.quant_strategy.domain.instrument_rules import (
    InstrumentTradingRule, resolve_instrument_rule,
)


@dataclass(frozen=True)
class OpeningFill:
    code: str
    quantity: int
    price: Decimal | None
    capacity_quantity: int


def _positive(value) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("opening execution requires finite positive inputs") from None
    if not result.is_finite() or result <= 0:
        raise ValueError("opening execution requires finite positive inputs")
    return result


def simulate_opening_buy(*, symbol: str,
                         rules: list[InstrumentTradingRule] | tuple[InstrumentTradingRule, ...],
                         exchange_calendar,
                         decision_date: date, adv20_asof: date,
                         adv20_evidence_ref: str,
                         execution_date: date, requested_quantity: int,
                         entry_lower, entry_upper, next_open, adv20_cny,
                         suspended: bool, limit_up_locked: bool,
                         next_day_turnover_cny=None,
                         slippage_multiplier=1) -> OpeningFill:
    """Apply 1% of T-day ADV20 and an adverse opening price proxy.

    ``next_day_turnover_cny`` is accepted only for downstream diagnostics and
    deliberately has no effect on the fill. Rules must be supplied explicitly.
    """
    if type(requested_quantity) is not int or requested_quantity <= 0:
        raise ValueError("requested quantity must be a positive integer")
    if (type(decision_date) is not date or type(adv20_asof) is not date
            or type(execution_date) is not date or adv20_asof != decision_date
            or execution_date <= decision_date):
        raise ValueError("ADV20 must be known on the decision date before execution")
    if exchange_calendar is None:
        raise ValueError("exchange calendar is required")
    schedule = exchange_calendar.schedule("CN")
    if (not schedule.available or not schedule.supported_from <= decision_date
            or execution_date > schedule.supported_through):
        raise ValueError("exchange calendar does not cover the execution window")
    if not any(session.trade_date == decision_date for session in schedule.sessions):
        raise ValueError("decision date must be an exchange session")
    next_session = min((session.trade_date for session in schedule.sessions
                        if decision_date < session.trade_date <= schedule.supported_through),
                       default=None)
    if next_session != execution_date:
        raise ValueError("execution date must be the next exchange session")
    if not isinstance(adv20_evidence_ref, str) or not adv20_evidence_ref.strip():
        raise ValueError("ADV20 requires an explicit evidence reference")
    rule = resolve_instrument_rule(rules, symbol=symbol,
                                   decision_date=decision_date, execution_date=execution_date)
    if not rule.permits_buy_quantity(requested_quantity):
        raise ValueError("requested quantity violates the effective buy rule")
    if type(suspended) is not bool or type(limit_up_locked) is not bool:
        raise ValueError("effective trading status must be explicit")
    lower = _positive(entry_lower)
    upper = _positive(entry_upper)
    adv = _positive(adv20_cny)
    tick = rule.price_tick
    if lower > upper or any(value / tick != (value / tick).to_integral_value()
                            for value in (lower, upper)):
        raise ValueError("entry interval must align with the effective tick")
    if suspended:
        # A full-day suspension has no opening print to validate or price.
        return OpeningFill("SUSPENDED", 0, None, 0)
    opening = _positive(next_open)
    bps = (Decimal(10) if rule.asset_type == "stock" else Decimal(5)) * _positive(slippage_multiplier)
    price = (opening * (Decimal(1) + bps / Decimal(10000)) / tick).to_integral_value(
        rounding=ROUND_CEILING) * tick
    raw_capacity = int((adv * Decimal("0.01") / price).to_integral_value(rounding=ROUND_FLOOR))
    capacity = rule.floor_buy_quantity(raw_capacity)
    if limit_up_locked:
        return OpeningFill("LIMIT_UP_LOCKED", 0, None, capacity)
    if price < lower or price > upper:
        return OpeningFill("OUTSIDE_ENTRY_INTERVAL", 0, None, capacity)
    quantity = min(requested_quantity, capacity)
    if quantity == 0:
        return OpeningFill("INSUFFICIENT_CAPACITY", 0, None, capacity)
    return OpeningFill("PARTIAL" if quantity < requested_quantity else "FILLED",
                       quantity, price, capacity)
