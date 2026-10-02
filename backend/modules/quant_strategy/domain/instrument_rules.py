"""Dated instrument trading-rule facts supplied by a separately audited source.

These values are contracts for consumers, not proof that a source document was
published at the claimed time. Historical certification belongs to ingestion.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal


class InstrumentRuleUnavailable(ValueError):
    pass


def _positive_decimal(value) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise InstrumentRuleUnavailable("invalid price tick") from None
    if not result.is_finite() or result <= 0:
        raise InstrumentRuleUnavailable("invalid price tick")
    return result


@dataclass(frozen=True)
class InstrumentTradingRule:
    symbol: str
    asset_type: Literal["stock", "etf"]
    effective_from: date
    effective_through: date | None
    published_on: date
    source_ref: str
    price_tick: Decimal
    min_buy_quantity: int
    buy_quantity_step: int
    max_buy_quantity: int
    roundtrip_days: int

    def validate(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise InstrumentRuleUnavailable("rule requires a symbol")
        if self.asset_type not in ("stock", "etf"):
            raise InstrumentRuleUnavailable("unsupported rule asset type")
        if (type(self.effective_from) is not date or type(self.published_on) is not date
                or (self.effective_through is not None and type(self.effective_through) is not date)
                or (self.effective_through is not None and self.effective_through < self.effective_from)):
            raise InstrumentRuleUnavailable("invalid rule dates")
        if not isinstance(self.source_ref, str) or not self.source_ref.strip():
            raise InstrumentRuleUnavailable("rule requires a source reference")
        if not isinstance(self.price_tick, Decimal):
            raise InstrumentRuleUnavailable("price tick must be Decimal")
        _positive_decimal(self.price_tick)
        if (any(type(value) is not int for value in (
                self.min_buy_quantity, self.buy_quantity_step, self.max_buy_quantity,
                self.roundtrip_days))
                or self.min_buy_quantity <= 0 or self.buy_quantity_step <= 0
                or self.max_buy_quantity < self.min_buy_quantity
                or self.roundtrip_days not in (0, 1)):
            raise InstrumentRuleUnavailable("invalid rule quantity or settlement")

    def permits_buy_quantity(self, quantity: int) -> bool:
        return (type(quantity) is int and self.min_buy_quantity <= quantity <= self.max_buy_quantity
                and (quantity - self.min_buy_quantity) % self.buy_quantity_step == 0)

    def floor_buy_quantity(self, quantity: int) -> int:
        if type(quantity) is not int or quantity < self.min_buy_quantity:
            return 0
        bounded = min(quantity, self.max_buy_quantity)
        return self.min_buy_quantity + ((bounded - self.min_buy_quantity)
                                        // self.buy_quantity_step) * self.buy_quantity_step


def resolve_instrument_rule(rules: list[InstrumentTradingRule] | tuple[InstrumentTradingRule, ...],
                            *, symbol: str, decision_date: date,
                            execution_date: date) -> InstrumentTradingRule:
    """Require exactly one rule valid for the execution day and known by T."""
    if type(decision_date) is not date or type(execution_date) is not date or execution_date <= decision_date:
        raise InstrumentRuleUnavailable("invalid decision or execution date")
    if not isinstance(rules, (list, tuple)):
        raise InstrumentRuleUnavailable("rules must be a dated collection")
    matches = []
    for rule in rules:
        if not isinstance(rule, InstrumentTradingRule):
            raise InstrumentRuleUnavailable("invalid rule record")
        if rule.symbol != symbol:
            continue
        rule.validate()
        if (rule.effective_from <= execution_date
                and (rule.effective_through is None or execution_date <= rule.effective_through)
                # Date-only publication evidence cannot prove availability
                # before that day's close. Same-day rules stay unknown.
                and rule.published_on < decision_date):
            matches.append(rule)
    if len(matches) != 1:
        raise InstrumentRuleUnavailable("no unique known effective instrument rule")
    return matches[0]
