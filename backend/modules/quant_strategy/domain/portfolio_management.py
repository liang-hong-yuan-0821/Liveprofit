"""Deterministic holding transitions for the four trusted portfolio families.

All prices must share the holding's frozen price basis. These are target
reductions; fills, corporate-action rebasing and cooldown clocks belong to the
ledger. No transition here increases an existing position.
"""
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from .stock_inputs import valid_bars

D = Decimal


@dataclass(frozen=True)
class PortfolioHoldingPolicy:
    family: str
    exit_days: int | None = None
    momentum_lookback: int | None = None
    trend_ma: int | None = None

    def __post_init__(self):
        if any(value is not None and type(value) is not int
               for value in (self.exit_days, self.momentum_lookback, self.trend_ma)):
            raise TypeError("holding policy windows must be exact integers")
        valid = {
            "stock_medium_momentum": self.exit_days is None and self.momentum_lookback is None and self.trend_ma in (60, 120, 200),
            "stock_short_reversion": self.exit_days in (3, 5) and self.momentum_lookback is None and self.trend_ma == 120,
            "etf_dual_momentum": self.exit_days is None and self.momentum_lookback in (60, 120, 180) and self.trend_ma is None,
            "etf_defensive_allocation": self.exit_days is None and self.momentum_lookback is None and self.trend_ma in (60, 120),
        }
        if not valid.get(self.family, False):
            raise ValueError("unsupported frozen portfolio holding policy")

    @property
    def cooldown_sessions(self):
        return 5 if self.family.startswith("stock_") else 0


def _positive(value):
    return isinstance(value, Decimal) and value.is_finite() and value > 0


@dataclass(frozen=True)
class PortfolioHoldingState:
    trial_id: str
    definition_hash: str
    price_basis: str
    fill_date: date
    fill_price: Decimal
    initial_stop: Decimal
    active_stop: Decimal
    target_weight: Decimal
    highest_close: Decimal | None = None
    valid_sessions: int = 0
    last_processed_date: date | None = None

    def __post_init__(self):
        if (not all(isinstance(value, str) and value for value in (self.trial_id, self.definition_hash, self.price_basis))
                or type(self.fill_date) is not date
                or not _positive(self.fill_price) or not _positive(self.initial_stop)
                or not _positive(self.active_stop) or self.initial_stop >= self.fill_price
                or self.active_stop < self.initial_stop
                or not isinstance(self.target_weight, Decimal) or not self.target_weight.is_finite()
                or not 0 <= self.target_weight <= 1
                or self.highest_close is not None and not _positive(self.highest_close)
                or type(self.valid_sessions) is not int or self.valid_sessions < 0
                or self.last_processed_date is not None and (
                    type(self.last_processed_date) is not date or self.last_processed_date < self.fill_date)):
            raise ValueError("invalid frozen holding state")


def initialize_holding(policy: PortfolioHoldingPolicy, *, trial_id: str, definition_hash: str,
                       price_basis: str, fill_date: date, fill_price: Decimal,
                       target_weight: Decimal, sigma20: Decimal | None = None,
                       atr: Decimal | None = None) -> PortfolioHoldingState:
    if not _positive(fill_price) or not _positive(target_weight):
        raise ValueError("actual fill price and initial weight must be positive")
    if policy.family == "stock_short_reversion":
        if not isinstance(sigma20, Decimal) or not sigma20.is_finite() or sigma20 < 0:
            raise ValueError("entry requires frozen prior-return sigma20")
        distance = min(D("0.10"), max(D("0.03"), 2 * sigma20 * D(policy.exit_days).sqrt()))
        stop = fill_price * (1 - distance)
    elif policy.family == "stock_medium_momentum":
        stop = fill_price * D("0.9")
    else:
        if not _positive(atr):
            raise ValueError("ETF entry requires positive ATR in the fill price basis")
        stop = fill_price - 3 * atr
    return PortfolioHoldingState(trial_id, definition_hash, price_basis, fill_date,
                                 fill_price, stop, stop, target_weight)


@dataclass(frozen=True)
class PortfolioHoldingDecision:
    state: PortfolioHoldingState
    reason: str
    can_execute: bool
    cooldown_after_exit_fill: int = 0


def evaluate_holding(policy: PortfolioHoldingPolicy, state: PortfolioHoldingState, *,
                     trade_date: date, price_basis: str,
                     bars: tuple[tuple[date, Decimal], ...],
                     benchmark_bars: tuple[tuple[date, Decimal], ...] = (),
                     suspended: bool = False, atr: Decimal | None = None,
                     defensive_target_weight: Decimal | None = None) -> PortfolioHoldingDecision:
    if type(trade_date) is not date or trade_date < state.fill_date or (
        state.last_processed_date is not None and trade_date <= state.last_processed_date
    ):
        raise ValueError("holding observations must advance in date order; replay duplicates in the ledger")
    if price_basis != state.price_basis:
        raise ValueError("corporate-action rebasing must precede holding evaluation")
    if type(suspended) is not bool:
        raise TypeError("suspension state must be a trusted boolean")
    observed = replace(state, last_processed_date=trade_date)
    if suspended or not valid_bars(bars, trade_date, 1):
        return PortfolioHoldingDecision(observed, "SUSPENDED" if suspended else "DATA_UNAVAILABLE", False)
    close = bars[-1][1]
    highest = max(state.highest_close, close) if state.highest_close is not None else close
    # A trusted close remains evidence of the peak even when other inputs are
    # missing. Losing it would weaken protection after the missing data returns.
    observed = replace(observed, highest_close=highest)
    if defensive_target_weight is not None and (
        not isinstance(defensive_target_weight, Decimal) or not defensive_target_weight.is_finite()
        or not 0 <= defensive_target_weight <= 1
    ):
        raise ValueError("invalid trusted defensive target weight")
    if policy.family.startswith("stock_"):
        complete = valid_bars(benchmark_bars, trade_date, policy.trend_ma)
        if policy.family == "stock_short_reversion":
            complete = complete and valid_bars(bars, trade_date, 5)
    elif policy.family == "etf_dual_momentum":
        complete = _positive(atr) and valid_bars(bars, trade_date, policy.momentum_lookback + 1)
    else:
        complete = (_positive(atr) and valid_bars(bars, trade_date, policy.trend_ma)
                    and defensive_target_weight is not None)
    if complete:
        observed = replace(observed, valid_sessions=state.valid_sessions + int(trade_date > state.fill_date))

    def exit_with(reason):
        return PortfolioHoldingDecision(replace(observed, target_weight=D(0)), reason, True,
                                         policy.cooldown_sessions)

    if state.target_weight == 0:
        return exit_with("EXIT_PENDING")
    if close <= state.initial_stop:
        return exit_with("INITIAL_STOP_LOSS")
    if close <= state.active_stop:
        return exit_with("TRAILING_STOP_LOSS")
    if policy.family == "stock_medium_momentum":
        observed = replace(observed, active_stop=max(state.active_stop, highest * D("0.9")))
        if close <= observed.active_stop:
            return exit_with("PEAK_DRAWDOWN_10PCT")
    elif policy.family == "stock_short_reversion":
        if complete and observed.valid_sessions >= policy.exit_days:
            return exit_with("HOLDING_TIMEOUT")
        if valid_bars(bars, trade_date, 5) and close >= sum((p for _, p in bars[-5:]), D(0)) / 5:
            return exit_with("CLOSE_GE_MA5")
    else:
        if _positive(atr):
            observed = replace(observed, active_stop=max(state.active_stop, highest - 3 * atr))
            if close <= observed.active_stop:
                return exit_with("TRAILING_STOP_LOSS")
        if policy.family == "etf_dual_momentum":
            window = policy.momentum_lookback
            if valid_bars(bars, trade_date, window + 1) and close <= bars[-window - 1][1]:
                return exit_with("MOMENTUM_NONPOSITIVE")
        else:
            if valid_bars(bars, trade_date, policy.trend_ma):
                if close <= sum((p for _, p in bars[-policy.trend_ma:]), D(0)) / policy.trend_ma:
                    return exit_with("ASSET_TREND_LOST")
            if defensive_target_weight is not None:
                if defensive_target_weight < state.target_weight:
                    observed = replace(observed, target_weight=defensive_target_weight)
                    return PortfolioHoldingDecision(observed, "DEFENSIVE_RISK_REDUCTION", True)
    if policy.family.startswith("stock_"):
        window = policy.trend_ma
        if valid_bars(benchmark_bars, trade_date, window):
            if benchmark_bars[-1][1] <= sum((p for _, p in benchmark_bars[-window:]), D(0)) / window:
                return exit_with("BENCHMARK_TREND_LOST")
        else:
            return PortfolioHoldingDecision(observed, "BENCHMARK_UNAVAILABLE", False)
    # HOLD is never an instruction to top up. Missing indicators do not clear
    # known protection or invalidate separately proven exit conditions above.
    return PortfolioHoldingDecision(observed, "HOLD" if complete else "MANAGEMENT_INPUTS_UNAVAILABLE", False)
