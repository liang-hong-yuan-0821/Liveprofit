"""Frozen family policies and their deterministic daily transition.

Prices and ATR must share one adjustment basis. The caller supplies the number
of valid sessions after the actual fill; calendar days are never counted here.
This transition does not place orders or authorize account risk.
"""
from dataclasses import dataclass
from decimal import Decimal

from .management_policies import ExitPolicyKind, InitialStopRule, ManagementPolicy, StopMode

D = Decimal
TREND_3ATR = ManagementPolicy(
    "TREND_3ATR", ExitPolicyKind.TRAILING, InitialStopRule(StopMode.ENTRY_SIGNAL, None),
    trailing_atr_multiple=D(3), initial_exposure=D("0.5"), add_at_r=D(1), max_adds=1,
)
MACD_MEAN_REVERSION = ManagementPolicy(
    "MACD_MEAN_REVERSION", ExitPolicyKind.RULE_BASED,
    InitialStopRule(StopMode.ENTRY_SIGNAL, None),
    rule_exit_conditions=("CLOSE_GE_MA20", "INITIAL_STOP", "TIMEOUT"),
    max_holding_sessions=10,
)


def validate_family_policy(policy: ManagementPolicy, template_id: str) -> None:
    from .templates import TEMPLATES
    expected = MACD_MEAN_REVERSION if template_id == "macd_rsi_reversal_v1" else TREND_3ATR
    if template_id not in TEMPLATES or policy != expected:
        raise ValueError("frozen management policy is not supported by this template")


@dataclass(frozen=True)
class FamilyManagementState:
    fill_price: Decimal
    initial_stop: Decimal
    highest_close: Decimal | None
    active_stop: Decimal
    target_exposure: Decimal
    add_filled: bool = False
    exit_pending: bool = False


@dataclass(frozen=True)
class FamilyManagementDecision:
    target_exposure: Decimal
    highest_close: Decimal | None
    active_stop: Decimal
    reason: str
    data_available: bool = True


def _positive(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def evaluate_family_management(
    policy: ManagementPolicy, state: FamilyManagementState, *, close: Decimal | None,
    valid_sessions_after_fill: int, atr: Decimal | None = None,
    ma20: Decimal | None = None, new_risk_allowed: bool = True,
) -> FamilyManagementDecision:
    """Exit first; then tighten the close-derived stop before considering adds.

An add is a target request, not a fill. Only a confirmed fill sets add_filled.
Incomplete indicators prohibit adds but do not disable known protective stops.
"""
    if policy not in (TREND_3ATR, MACD_MEAN_REVERSION):
        raise ValueError("unsupported frozen family policy")
    if type(valid_sessions_after_fill) is not int or valid_sessions_after_fill < 0:
        raise ValueError("valid session count must be a nonnegative integer")
    if (not all(_positive(v) for v in (state.fill_price, state.initial_stop, state.active_stop))
            or state.highest_close is not None and not _positive(state.highest_close)
            or state.initial_stop >= state.fill_price
            or state.active_stop < state.initial_stop
            or not isinstance(state.target_exposure, Decimal)
            or not state.target_exposure.is_finite()
            or state.target_exposure not in (D(0), D("0.5"), D(1))):
        raise ValueError("invalid family management state")

    def result(exposure, reason, *, high=None, stop=None, available=True):
        return FamilyManagementDecision(exposure, state.highest_close if high is None else high,
                                        state.active_stop if stop is None else stop, reason, available)

    if state.exit_pending or state.target_exposure == 0:
        return result(D(0), "EXIT_PENDING", available=_positive(close))
    if not _positive(close):
        return result(state.target_exposure, "DATA_UNAVAILABLE", available=False)
    if close <= state.initial_stop:
        return result(D(0), "INITIAL_STOP_LOSS")
    if close <= state.active_stop:
        return result(D(0), "TRAILING_STOP_LOSS")
    if policy == MACD_MEAN_REVERSION:
        if valid_sessions_after_fill >= policy.max_holding_sessions:
            return result(D(0), "HOLDING_TIMEOUT")
        if not _positive(ma20):
            return result(state.target_exposure, "DATA_UNAVAILABLE", available=False)
        if close >= ma20:
            return result(D(0), "CLOSE_GE_MA20")
        return result(state.target_exposure, "HOLD")
    if not _positive(atr):
        return result(state.target_exposure, "DATA_UNAVAILABLE", available=False)
    highest = max(state.highest_close, close) if state.highest_close is not None else close
    stop = max(state.active_stop, highest - policy.trailing_atr_multiple * atr)
    # If ATR contracts enough to place today's derived stop above the close,
    # request exit immediately, rather than adding against a breached stop.
    if close <= stop:
        return result(D(0), "TRAILING_STOP_LOSS", high=highest, stop=stop)
    risk = state.fill_price - state.initial_stop
    if (valid_sessions_after_fill > 0 and new_risk_allowed and not state.add_filled
            and state.target_exposure < 1 and close >= state.fill_price + risk):
        return result(D(1), "ADD_AT_R", high=highest, stop=stop)
    return result(state.target_exposure, "HOLD", high=highest, stop=stop)
