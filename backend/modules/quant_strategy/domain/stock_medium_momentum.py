"""Monthly medium-term stock momentum targets and daily exit predicates."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from backend.modules.quant_strategy.domain.portfolio_targets import (
    PortfolioTargetIntent,
    TargetLeg,
)
from backend.modules.quant_strategy.domain.stock_inputs import (
    StockCandidate,
    valid_bars,
)


def benchmark_above_ma(bars: tuple[tuple[date, Decimal], ...], *, as_of: date, window: int) -> bool:
    if not valid_bars(bars, as_of, window):
        return False
    prices = [value for _, value in bars[-window:]]
    return prices[-1] > sum(prices, Decimal(0)) / window


def build_medium_momentum_target(
    candidates: tuple[StockCandidate, ...], benchmark_bars: tuple[tuple[date, Decimal], ...], *,
    decision_date: date, valid_until: date, evaluation_as_of: date,
    strategy_version_id: uuid.UUID, lookback: int, slots: int, benchmark_ma: int,
    is_month_end_rebalance: bool,
) -> PortfolioTargetIntent | None:
    if lookback not in (60, 120) or slots not in (10, 20) or benchmark_ma not in (60, 120, 200):
        raise ValueError("medium momentum parameters must be preregistered")
    if not isinstance(is_month_end_rebalance, bool):
        raise TypeError("month-end rebalance flag must be trusted")
    if not is_month_end_rebalance or not benchmark_above_ma(
        benchmark_bars, as_of=evaluation_as_of, window=benchmark_ma,
    ):
        return None
    if len({candidate.ts_code for candidate in candidates}) != len(candidates):
        raise ValueError("duplicate stock candidate")
    ranked = []
    for candidate in candidates:
        if not candidate.eligible(decision_date, allow_held=True, data_as_of=evaluation_as_of):
            continue
        prices = [value for _, value in candidate.adjusted_bars]
        if len(prices) < lookback + 6:
            continue
        momentum = prices[-6] / prices[-lookback - 6] - 1
        if momentum > 0:
            ranked.append((momentum, candidate.ts_code))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]))
    if not ranked:
        return None
    return PortfolioTargetIntent(
        family_id="stock_medium_momentum", strategy_version_id=strategy_version_id,
        evaluation_as_of=evaluation_as_of, decision_date=decision_date,
        valid_until=valid_until, policy_id="STOCK_MEDIUM_TREND_EXIT",
        reason=f"month-end momentum L={lookback}; benchmark MA{benchmark_ma}",
        legs=tuple(TargetLeg(code, Decimal(1) / slots) for _, code in ranked[:slots]),
    )


@dataclass(frozen=True)
class MomentumExitDecision:
    exit_reason: str | None
    benchmark_evidence_missing: bool = False


def medium_momentum_exit(
    *, adjusted_close: Decimal, highest_close_since_entry: Decimal,
    benchmark_trend_ok: bool | None,
) -> MomentumExitDecision:
    """Daily reduction predicate; persistence of peak and cooldown belongs to ledger."""
    if not all(isinstance(value, Decimal) and value.is_finite() and value > 0
               for value in (adjusted_close, highest_close_since_entry)):
        raise ValueError("adjusted close and peak must be positive finite prices")
    if adjusted_close <= highest_close_since_entry * Decimal("0.9"):
        return MomentumExitDecision("PEAK_DRAWDOWN_10PCT", benchmark_trend_ok is None)
    if benchmark_trend_ok is None:
        return MomentumExitDecision(None, True)
    if not isinstance(benchmark_trend_ok, bool):
        raise TypeError("benchmark trend must be bool or None")
    if not benchmark_trend_ok:
        return MomentumExitDecision("BENCHMARK_TREND_LOST")
    return MomentumExitDecision(None)
