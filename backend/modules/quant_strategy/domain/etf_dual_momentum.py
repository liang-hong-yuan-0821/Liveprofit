"""Deterministic ETF dual-momentum target selection on a frozen decision day."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import pairwise

from backend.modules.quant_strategy.domain.portfolio_targets import (
    PortfolioTargetIntent,
    TargetLeg,
)

MIN_LISTED_SESSIONS = 250
MIN_ADV20_CNY = Decimal(20000000)
ELIGIBLE_CATEGORIES = frozenset({"EQUITY_300", "EQUITY_500", "EQUITY_GROWTH", "GOLD", "BOND_MEDIUM"})


@dataclass(frozen=True)
class FundCandidate:
    ts_code: str
    tracking_index: str
    category: str
    trade_date: date
    listed_sessions: int
    adv20_cny: Decimal
    adjusted_bars: tuple[tuple[date, Decimal], ...]
    atr20_raw: Decimal
    suspended: bool = False

    def eligible(self, *, decision_date: date, lookback: int,
                 data_as_of: date | None = None) -> bool:
        if data_as_of is None:
            data_as_of = decision_date
        if (self.category not in ELIGIBLE_CATEGORIES or not self.tracking_index
                or self.trade_date != data_as_of or self.suspended
                or self.listed_sessions < MIN_LISTED_SESSIONS
                or not isinstance(self.adv20_cny, Decimal) or not self.adv20_cny.is_finite()
                or self.adv20_cny < MIN_ADV20_CNY or len(self.adjusted_bars) < lookback + 1
                or not isinstance(self.atr20_raw, Decimal) or not self.atr20_raw.is_finite()
                or self.atr20_raw <= 0):
            return False
        window = self.adjusted_bars[-lookback - 1:]
        if window[-1][0] != data_as_of:
            return False
        if any(current[0] >= following[0] for current, following in pairwise(window)):
            return False
        return all(isinstance(price, Decimal) and price.is_finite() and price > 0 for _, price in window)

    def momentum(self, lookback: int) -> Decimal:
        return self.adjusted_bars[-1][1] / self.adjusted_bars[-lookback - 1][1] - 1


def select_monthly_representatives(
    candidates: tuple[FundCandidate, ...], *, decision_date: date, lookback: int,
    data_as_of: date | None = None,
) -> frozenset[str]:
    """Freeze one tradable ETF per category/tracking-index at month selection."""
    if len({candidate.ts_code for candidate in candidates}) != len(candidates):
        raise ValueError("duplicate ETF candidate")
    representatives: dict[tuple[str, str], FundCandidate] = {}
    for candidate in candidates:
        if not candidate.eligible(decision_date=decision_date, lookback=lookback,
                                  data_as_of=data_as_of):
            continue
        key = (candidate.category, candidate.tracking_index)
        current = representatives.get(key)
        if current is None or (-candidate.adv20_cny, candidate.ts_code) < (-current.adv20_cny, current.ts_code):
            representatives[key] = candidate
    return frozenset(candidate.ts_code for candidate in representatives.values())


def build_dual_momentum_target(
    candidates: tuple[FundCandidate, ...], *, monthly_representative_codes: frozenset[str], decision_date: date,
    valid_until: date, evaluation_as_of: date, strategy_version_id: uuid.UUID,
    lookback: int, slots: int,
) -> PortfolioTargetIntent | None:
    """Pick highest-ADV representative per category/index; cash absorbs empty slots."""
    if lookback not in (60, 120, 180) or slots not in (1, 2):
        raise ValueError("dual momentum parameters must be preregistered")
    if not isinstance(monthly_representative_codes, frozenset):
        raise TypeError("monthly ETF representative set must be frozen")
    if len({candidate.ts_code for candidate in candidates}) != len(candidates):
        raise ValueError("duplicate ETF candidate")
    by_code = {candidate.ts_code: candidate for candidate in candidates}
    if any(code not in by_code or not by_code[code].eligible(decision_date=decision_date,
            lookback=lookback, data_as_of=evaluation_as_of)
           for code in monthly_representative_codes):
        return None  # absent evidence must not become an all-cash sell signal
    representatives = [by_code[code] for code in monthly_representative_codes]

    positive = [candidate for candidate in representatives
                if candidate.category != "BOND_MEDIUM" and candidate.momentum(lookback) > 0]
    positive.sort(key=lambda candidate: (-candidate.momentum(lookback), candidate.ts_code))
    selected = positive[:slots]
    if len(selected) < slots:
        bonds = [candidate for candidate in representatives
                 if candidate.category == "BOND_MEDIUM" and candidate.momentum(lookback) > 0]
        bonds.sort(key=lambda candidate: (-candidate.momentum(lookback), candidate.ts_code))
        selected.extend(bonds[:slots - len(selected)])
    weight = Decimal(1) / Decimal(slots)
    return PortfolioTargetIntent(
        family_id="etf_dual_momentum", strategy_version_id=strategy_version_id,
        evaluation_as_of=evaluation_as_of, decision_date=decision_date,
        valid_until=valid_until, policy_id="ETF_DUAL_MOMENTUM_3ATR",
        reason=f"positive {lookback}-session momentum; {len(selected)}/{slots} slots",
        legs=tuple(TargetLeg(candidate.ts_code, weight) for candidate in selected),
        cash_only=not selected,
    )
