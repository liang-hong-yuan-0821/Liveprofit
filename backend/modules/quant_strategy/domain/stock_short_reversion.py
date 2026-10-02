"""As-of short-term stock mean-reversion selection; no execution side effects."""

from __future__ import annotations

import math
import statistics
import uuid
from datetime import date
from decimal import Decimal
from itertools import pairwise

from backend.modules.quant_strategy.domain.portfolio_targets import (
    PortfolioTargetIntent,
    TargetLeg,
)
from backend.modules.quant_strategy.domain.stock_inputs import (
    StockCandidate,
    valid_bars,
)

SLOTS = 10


def _z_score(bars: tuple[tuple[date, Decimal], ...], q: int) -> float | None:
    prices = [float(value) for _, value in bars]
    ma5 = sum(prices[-5:]) / 5
    if prices[-1] >= ma5:  # the rule-based exit fires at/above MA5
        return None
    previous = prices[-22:-1]  # 20 returns ending t-1; excludes today's move
    returns = [math.log(b / a) for a, b in pairwise(previous)]
    sigma20 = statistics.stdev(returns)
    z = -math.log(prices[-1] / prices[-q - 1]) / (max(sigma20, 0.005) * math.sqrt(q))
    return z if math.isfinite(z) else None


def build_short_reversion_target(
    candidates: tuple[StockCandidate, ...], benchmark_bars: tuple[tuple[date, Decimal], ...], *,
    decision_date: date, valid_until: date, evaluation_as_of: date,
    strategy_version_id: uuid.UUID, reversal_days: int, z_threshold: Decimal, exit_days: int,
) -> PortfolioTargetIntent | None:
    """Rank qualifying Z scores; ten fixed slots, unfilled budget stays cash."""
    if reversal_days not in (2, 3) or z_threshold not in (Decimal("1.5"), Decimal(2), Decimal("2.5")):
        raise ValueError("short reversion parameters must be preregistered")
    if exit_days not in (3, 5):
        raise ValueError("short reversion exit horizon must be preregistered")
    if len({candidate.ts_code for candidate in candidates}) != len(candidates):
        raise ValueError("duplicate stock candidate")
    if not valid_bars(benchmark_bars, evaluation_as_of, 120):
        return None
    benchmark_prices = [float(value) for _, value in benchmark_bars[-120:]]
    if benchmark_prices[-1] <= sum(benchmark_prices) / 120:
        return None
    ranked = []
    for candidate in candidates:
        if not candidate.eligible(decision_date, data_as_of=evaluation_as_of):
            continue
        trend_prices = [price for _, price in candidate.adjusted_bars[-120:]]
        if trend_prices[-1] <= sum(trend_prices, Decimal(0)) / 120:
            continue
        z = _z_score(candidate.adjusted_bars, reversal_days)
        if z is not None and z >= float(z_threshold):
            ranked.append((z, candidate.ts_code))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]))
    if not ranked:
        return None
    return PortfolioTargetIntent(
        family_id="stock_short_reversion", strategy_version_id=strategy_version_id,
        evaluation_as_of=evaluation_as_of, decision_date=decision_date,
        valid_until=valid_until, policy_id="STOCK_REVERSION_RULE_BASED",
        reason=f"Z>={z_threshold}; Q={reversal_days}; H={exit_days}",
        legs=tuple(TargetLeg(code, Decimal(1) / SLOTS) for _, code in ranked[:SLOTS]),
    )
