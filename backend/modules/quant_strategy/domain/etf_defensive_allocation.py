"""Defensive ETF allocation with common-date covariance and cash residuals."""

from __future__ import annotations

import math
import statistics
import uuid
from datetime import date
from decimal import Decimal
from itertools import pairwise

from backend.modules.quant_strategy.domain.etf_dual_momentum import FundCandidate
from backend.modules.quant_strategy.domain.portfolio_targets import (
    PortfolioTargetIntent,
    TargetLeg,
)

BASE_WEIGHTS = {
    "EQUITY_300": Decimal("0.4"),
    "BOND_MEDIUM": Decimal("0.4"),
    "GOLD": Decimal("0.2"),
}
ANNUAL_SESSIONS = 252


def select_defensive_representatives(
    candidates: tuple[FundCandidate, ...], *, decision_date: date,
    data_as_of: date | None = None,
) -> tuple[tuple[str, str], ...]:
    """Freeze one highest-ADV symbol per defensive sleeve at monthly selection."""
    if len({candidate.ts_code for candidate in candidates}) != len(candidates):
        raise ValueError("duplicate ETF candidate")
    selected: dict[str, FundCandidate] = {}
    for candidate in candidates:
        if candidate.category not in BASE_WEIGHTS or not candidate.eligible(
            decision_date=decision_date, lookback=120, data_as_of=data_as_of,
        ):
            continue
        current = selected.get(candidate.category)
        if current is None or (-candidate.adv20_cny, candidate.ts_code) < (-current.adv20_cny, current.ts_code):
            selected[candidate.category] = candidate
    return tuple(sorted((category, item.ts_code) for category, item in selected.items()))


def _volatility(weights: dict[str, float], prices: dict[str, list[float]]) -> float:
    returns = {code: [math.log(after / before) for before, after in pairwise(values)]
               for code, values in prices.items()}
    variance = 0.0
    for left, left_weight in weights.items():
        for right, right_weight in weights.items():
            variance += left_weight * right_weight * statistics.covariance(returns[left], returns[right])
    return math.sqrt(max(variance, 0.0) * ANNUAL_SESSIONS)


def build_defensive_target(
    candidates: tuple[FundCandidate, ...], *, frozen_symbols: tuple[tuple[str, str], ...],
    decision_date: date, valid_until: date, evaluation_as_of: date,
    strategy_version_id: uuid.UUID, cov_window: int, vol_target: Decimal,
    trend_ma: int, is_weekly_rebalance: bool,
    previous_weights: dict[str, Decimal] | None = None,
) -> PortfolioTargetIntent | None:
    if (cov_window not in (20, 60) or vol_target not in (Decimal("0.04"), Decimal("0.06"), Decimal("0.08"))
            or trend_ma not in (60, 120)):
        raise ValueError("defensive allocation parameters must be preregistered")
    if not isinstance(is_weekly_rebalance, bool):
        raise TypeError("weekly rebalance flag must be trusted")
    if not is_weekly_rebalance and previous_weights is None:
        raise ValueError("daily reduction requires previous target weights")
    if previous_weights is not None and (
        any(not isinstance(weight, Decimal) or not weight.is_finite() or not 0 <= weight <= 1
            for weight in previous_weights.values())
        or sum(previous_weights.values(), Decimal(0)) > 1
    ):
        raise ValueError("previous defensive weights are invalid")
    if len(set(dict(frozen_symbols))) != len(frozen_symbols) or len(set(dict(frozen_symbols).values())) != len(frozen_symbols):
        raise ValueError("frozen defensive symbols must be unique by sleeve and code")
    by_code = {candidate.ts_code: candidate for candidate in candidates}
    selected = []
    for category, code in frozen_symbols:
        candidate = by_code.get(code)
        if category not in BASE_WEIGHTS or candidate is None or candidate.category != category:
            return None
        if not candidate.eligible(decision_date=decision_date, lookback=max(cov_window, trend_ma),
                                  data_as_of=evaluation_as_of):
            return None
        values = [price for _, price in candidate.adjusted_bars[-trend_ma:]]
        if values[-1] > sum(values, Decimal(0)) / trend_ma:
            selected.append(candidate)
    if not selected:
        return PortfolioTargetIntent(
            family_id="etf_defensive_allocation", strategy_version_id=strategy_version_id,
            evaluation_as_of=evaluation_as_of, decision_date=decision_date,
            valid_until=valid_until, policy_id="ETF_DEFENSIVE_3ATR",
            reason="all defensive sleeves screened to cash", legs=(), cash_only=True,
        )

    # Require exactly the same W+1 observed dates for covariance; never align by row index.
    windows = {candidate.ts_code: candidate.adjusted_bars[-cov_window - 1:] for candidate in selected}
    date_sequences = {tuple(day for day, _ in bars) for bars in windows.values()}
    if len(date_sequences) != 1:
        return None
    prices = {code: [float(price) for _, price in bars] for code, bars in windows.items()}
    weights = {candidate.ts_code: float(BASE_WEIGHTS[candidate.category]) for candidate in selected}
    sigma = _volatility(weights, prices)
    scale = min(1.0, float(vol_target) / sigma) if sigma > 0 else 1.0
    allocations = {
        code: BASE_WEIGHTS[by_code[code].category] * Decimal(str(scale)) for code in weights
    }
    if not is_weekly_rebalance:
        allocations = {code: min(weight, previous_weights.get(code, Decimal(0)))
                       for code, weight in allocations.items()}
    legs = tuple(TargetLeg(code, weight) for code, weight in sorted(allocations.items()) if weight > 0)
    return PortfolioTargetIntent(
        family_id="etf_defensive_allocation", strategy_version_id=strategy_version_id,
        evaluation_as_of=evaluation_as_of, decision_date=decision_date,
        valid_until=valid_until, policy_id="ETF_DEFENSIVE_3ATR",
        reason=f"MA{trend_ma}; covariance {cov_window}; annual vol target {vol_target}",
        legs=legs,
        cash_only=not legs,
    )
