"""Common dated stock inputs and opening-risk eligibility for portfolio rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import pairwise

MIN_ADV20_CNY = Decimal(20000000)
MIN_LISTED_SESSIONS = 250


def valid_bars(bars: tuple[tuple[date, Decimal], ...], as_of: date, minimum: int) -> bool:
    if len(bars) < minimum or bars[-1][0] != as_of:
        return False
    if any(first[0] >= second[0] for first, second in pairwise(bars)):
        return False
    return all(isinstance(price, Decimal) and price.is_finite() and price > 0 for _, price in bars)


@dataclass(frozen=True)
class StockCandidate:
    ts_code: str
    adjusted_bars: tuple[tuple[date, Decimal], ...]
    listed_sessions: int
    adv20_cny: Decimal
    is_st: bool
    suspended: bool
    held: bool = False
    cooldown_until: date | None = None

    def eligible(self, decision_date: date, *, allow_held: bool = False,
                 data_as_of: date | None = None) -> bool:
        if data_as_of is None:
            data_as_of = decision_date
        return (
            isinstance(self.ts_code, str) and self.ts_code.endswith((".SH", ".SZ"))
            and isinstance(self.listed_sessions, int) and self.listed_sessions >= MIN_LISTED_SESSIONS
            and isinstance(self.adv20_cny, Decimal) and self.adv20_cny.is_finite()
            and self.adv20_cny >= MIN_ADV20_CNY and not self.is_st and not self.suspended
            and isinstance(self.is_st, bool) and isinstance(self.suspended, bool)
            and isinstance(self.held, bool) and (allow_held or not self.held)
            and (self.cooldown_until is None
                 or (isinstance(self.cooldown_until, date) and decision_date > self.cooldown_until))
            and valid_bars(self.adjusted_bars, data_as_of, MIN_LISTED_SESSIONS)
        )
