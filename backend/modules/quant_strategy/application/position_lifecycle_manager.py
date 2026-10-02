"""Pure lifecycle rule engine used by the transactional N6 coordinator.

The evaluator deliberately has no market/provider access.  It consumes one
already-frozen daily fact and returns one final target, so callers can persist
the fact, intent and suggested order in a single transaction.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from typing import Any
import hashlib
import json
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.modules.quant_strategy.application.errors import FillValidationError
from backend.modules.quant_strategy.domain.management_policies import ManagementPolicy
from backend.modules.quant_strategy.domain.family_management import (
    FamilyManagementState, evaluate_family_management,
)

LOT = Decimal("100")
ALLOWED_EXPOSURES = {
    Decimal("0"), Decimal("0.25"), Decimal("0.50"), Decimal("1.00"),
}


def _d(value: Any, name: str) -> Decimal:
    if value is None:
        raise FillValidationError(f"生命周期日事实缺少 {name}")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise FillValidationError(f"生命周期日事实 {name} 必须为有限数") from None
    if not result.is_finite():
        raise FillValidationError(f"生命周期日事实 {name} 必须为有限数")
    return result


def _optional(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _target(capacity: Decimal, exposure: Decimal) -> Decimal:
    if exposure not in ALLOWED_EXPOSURES:
        raise FillValidationError("生命周期目标比例只允许 0/0.25/0.50/1.00")
    return (capacity * exposure / LOT).to_integral_value(rounding=ROUND_FLOOR) * LOT


@dataclass(frozen=True)
class LifecycleStateInput:
    template_id: str
    initial_fill_price: Decimal
    initial_stop_price: Decimal
    risk_capacity_shares: Decimal
    target_exposure_pct: Decimal = Decimal("0.50")
    profit_take_price: Decimal | None = None
    profit_target_reached: bool = False
    confirmation_completed: bool = False
    arc_neckline_price: Decimal | None = None


@dataclass(frozen=True)
class TrailingStateInput:
    high_water_mark: Decimal
    active_stop_price: Decimal
    phase: str = "PROTECT"
    b: Decimal | None = None
    a: Decimal | None = None
    d: Decimal | None = None

    @property
    def enabled(self) -> bool:
        return (
            self.b is not None and self.a is not None and self.d is not None
            and Decimal(0) < self.b <= self.a and Decimal(0) < self.d < Decimal(1)
        )


@dataclass(frozen=True)
class ExpectationStateInput:
    fill_trade_date: date
    observed_trading_days: int = 0
    window_trading_days: int = 3
    status: str = "PENDING"


@dataclass(frozen=True)
class LifecycleDecision:
    target_exposure_pct: Decimal
    target_shares: Decimal
    reason_code: str
    phase: str
    profit_target_reached: bool
    expectation_status: str | None
    expectation_observed_days: int | None
    trailing_phase: str | None
    high_water_mark: Decimal | None
    active_stop_price: Decimal | None
    data_available: bool = True


def evaluate_lifecycle_day(
    state: LifecycleStateInput,
    *,
    trade_date: date,
    fact: dict[str, Any],
    actual_shares: Decimal,
    observation_no: int,
    trailing: TrailingStateInput | None = None,
    expectation: ExpectationStateInput | None = None,
    management_policy: ManagementPolicy | None = None,
) -> LifecycleDecision:
    """Evaluate the frozen N6 priority chain for one complete trading day."""

    if management_policy is not None:
        if not state.risk_capacity_shares.is_finite() or state.risk_capacity_shares <= 0:
            return _unavailable(state, actual_shares, trailing, expectation)
        if fact.get("is_suspended") is True:
            return _unavailable(state, actual_shares, trailing, expectation, "SUSPENDED_SESSION")
        expectation_status = expectation.status if expectation else None
        expectation_days = expectation.observed_trading_days if expectation else None
        if state.template_id == "ma5_pre_cross_v1" and expectation is not None and expectation.status == "PENDING":
            ma5, ma20 = _optional(fact.get("ma5")), _optional(fact.get("ma20"))
            observed_close = _optional(fact.get("close"))
            if (trade_date > expectation.fill_trade_date and observed_close is not None
                    and observed_close > 0 and ma5 is not None and ma5 > 0
                    and ma20 is not None and ma20 > 0):
                expectation_days = max(expectation.observed_trading_days, observation_no)
                expectation_status = "FULFILLED" if ma5 > ma20 else (
                    "EXPIRED" if expectation_days >= expectation.window_trading_days else "PENDING"
                )
        transition = evaluate_family_management(
            management_policy,
            FamilyManagementState(
                state.initial_fill_price, state.initial_stop_price,
                trailing.high_water_mark if trailing and trailing.phase != "PROTECT" else None,
                trailing.active_stop_price if trailing else state.initial_stop_price,
                state.target_exposure_pct, state.confirmation_completed,
                state.target_exposure_pct == 0,
            ),
            close=_optional(fact.get("close")), atr=_optional(fact.get("atr")),
            ma20=_optional(fact.get("ma20")), valid_sessions_after_fill=observation_no,
            new_risk_allowed=fact.get("new_risk_allowed") is not False,
        )
        if expectation_status == "EXPIRED" and transition.target_exposure != 0:
            transition = replace(transition, target_exposure=Decimal(0), reason="EXPECTATION_TIMEOUT", data_available=True)
        elif expectation_status == "PENDING" and transition.reason == "ADD_AT_R":
            transition = replace(transition, target_exposure=state.target_exposure_pct, reason="EXPECTATION_PENDING")
        target = _target(state.risk_capacity_shares, transition.target_exposure)
        if not transition.data_available:
            target = actual_shares
        elif fact.get("new_risk_allowed") is False or transition.target_exposure < state.target_exposure_pct:
            target = min(target, actual_shares)
        return LifecycleDecision(
            transition.target_exposure, target,
            "ADD_AT_R" if transition.target_exposure == 1 and management_policy.max_adds == 1
            and not state.confirmation_completed and transition.data_available else transition.reason,
            "EXIT_PENDING" if transition.target_exposure == 0 else
            "DATA_UNAVAILABLE" if not transition.data_available else
            "CONFIRMATION_PENDING" if transition.reason == "ADD_AT_R" else "INITIALIZED",
            state.profit_target_reached,
            expectation_status,
            expectation_days,
            "TRAILING" if management_policy.trailing_atr_multiple is not None else None,
            transition.highest_close, transition.active_stop, transition.data_available,
        )

    try:
        close = _d(fact.get("close"), "close")
        high = _d(fact.get("high"), "high")
    except FillValidationError:
        return _unavailable(state, actual_shares, trailing, expectation)
    if close <= 0 or high <= 0 or state.risk_capacity_shares <= 0:
        return _unavailable(state, actual_shares, trailing, expectation)

    def decision(exposure: Decimal, reason: str, phase: str, **overrides) -> LifecycleDecision:
        target_shares = _target(state.risk_capacity_shares, exposure)
        if fact.get("new_risk_allowed") is False:
            target_shares = min(target_shares, actual_shares)
        # A profit trim or weakening rule must never buy shares back.
        if exposure < state.target_exposure_pct:
            target_shares = min(target_shares, actual_shares)
        script_target = _optional(fact.get("script_target_shares"))
        if script_target is not None and Decimal(0) <= script_target < target_shares:
            target_shares = script_target
            reason = "SCRIPT_SELL_ALL" if script_target == 0 else "SCRIPT_SELL_PARTIAL"
            phase = "EXIT_PENDING" if script_target == 0 else "SCRIPT_REDUCE_PENDING"
        return LifecycleDecision(
            target_exposure_pct=exposure,
            target_shares=target_shares,
            reason_code=reason,
            phase=phase,
            profit_target_reached=overrides.get("profit_target_reached", state.profit_target_reached),
            expectation_status=overrides.get("expectation_status", expectation.status if expectation else None),
            expectation_observed_days=overrides.get(
                "expectation_observed_days", expectation.observed_trading_days if expectation else None,
            ),
            trailing_phase=overrides.get("trailing_phase", trailing.phase if trailing else None),
            high_water_mark=overrides.get("high_water_mark", trailing.high_water_mark if trailing else None),
            active_stop_price=overrides.get("active_stop_price", trailing.active_stop_price if trailing else None),
        )

    # 1) Cost stop and the previous day's effective trailing stop.
    if close <= state.initial_stop_price:
        return decision(Decimal(0), "INITIAL_STOP_LOSS", "EXIT_PENDING")
    if trailing is not None and trailing.enabled and close <= trailing.active_stop_price:
        return decision(Decimal(0), "TRAILING_STOP_LOSS", "EXIT_PENDING")

    next_trailing = trailing
    if trailing is not None and trailing.enabled:
        risk = state.initial_fill_price - state.initial_stop_price
        if risk <= 0:
            return _unavailable(state, actual_shares, trailing, expectation, "TRAILING_STOP_UNAVAILABLE")
        high_water = max(trailing.high_water_mark, high)
        phase = "PROTECT"
        active = max(trailing.active_stop_price, state.initial_stop_price)
        if high_water >= state.initial_fill_price + trailing.a * risk:
            phase = "TRAILING"
            active = max(active, high_water * (Decimal(1) - trailing.d))
        elif high_water >= state.initial_fill_price + trailing.b * risk:
            phase = "BREAKEVEN"
            active = max(active, state.initial_fill_price)
        next_trailing = replace(trailing, high_water_mark=high_water, active_stop_price=active, phase=phase)

    # 2) Template invalidation and MA5 expectation timeout.
    invalid_reason = _invalid_reason(state, fact)
    if invalid_reason is not None:
        return decision(
            Decimal(0), invalid_reason, "EXIT_PENDING",
            trailing_phase=next_trailing.phase if next_trailing else None,
            high_water_mark=next_trailing.high_water_mark if next_trailing else None,
            active_stop_price=next_trailing.active_stop_price if next_trailing else None,
        )

    expectation_status = expectation.status if expectation else None
    expectation_days = expectation.observed_trading_days if expectation else None
    ma5_fulfilled = False
    if state.template_id == "ma5_pre_cross_v1" and expectation is not None and expectation.status == "PENDING":
        ma5, ma20 = _optional(fact.get("ma5")), _optional(fact.get("ma20"))
        if trade_date > expectation.fill_trade_date and ma5 is not None and ma20 is not None:
            expectation_days = max(expectation.observed_trading_days, observation_no)
            if ma5 > ma20:
                expectation_status = "FULFILLED"
                ma5_fulfilled = True
            elif expectation_days >= expectation.window_trading_days:
                return decision(
                    Decimal(0), "EXPECTATION_TIMEOUT", "EXIT_PENDING",
                    expectation_status="EXPIRED", expectation_observed_days=expectation_days,
                    trailing_phase=next_trailing.phase if next_trailing else None,
                    high_water_mark=next_trailing.high_water_mark if next_trailing else None,
                    active_stop_price=next_trailing.active_stop_price if next_trailing else None,
                )

    # 3) First profit target touch permanently caps the target at 50%.
    profit_reached = state.profit_target_reached
    if not profit_reached and state.profit_take_price is not None and close >= state.profit_take_price:
        return decision(
            min(state.target_exposure_pct, Decimal("0.50")), "PROFIT_TARGET_TRIM", "PROFIT_PROTECTED",
            profit_target_reached=True, expectation_status=expectation_status,
            expectation_observed_days=expectation_days,
            trailing_phase=next_trailing.phase if next_trailing else None,
            high_water_mark=next_trailing.high_water_mark if next_trailing else None,
            active_stop_price=next_trailing.active_stop_price if next_trailing else None,
        )

    # 4) Template weakening.
    if _is_weakened(state, fact, expectation_status):
        exposure = Decimal("0.25") if state.template_id == "ma5_pre_cross_v1" and expectation_status == "PENDING" else Decimal("0.50")
        return decision(
            min(state.target_exposure_pct, exposure), "TEMPLATE_WEAKEN", "WEAKENED",
            expectation_status=expectation_status, expectation_observed_days=expectation_days,
            trailing_phase=next_trailing.phase if next_trailing else None,
            high_water_mark=next_trailing.high_water_mark if next_trailing else None,
            active_stop_price=next_trailing.active_stop_price if next_trailing else None,
        )

    # 5) One-time template confirmation/add. Profit-protected positions never add.
    confirmed = ma5_fulfilled or _is_confirmed(state, fact, observation_no)
    if confirmed and not state.confirmation_completed and not profit_reached:
        return decision(
            Decimal("1.00"), "TEMPLATE_CONFIRM_ADD", "CONFIRMATION_PENDING",
            expectation_status=expectation_status, expectation_observed_days=expectation_days,
            trailing_phase=next_trailing.phase if next_trailing else None,
            high_water_mark=next_trailing.high_water_mark if next_trailing else None,
            active_stop_price=next_trailing.active_stop_price if next_trailing else None,
        )

    return decision(
        state.target_exposure_pct, "LIFECYCLE_HOLD", "INITIALIZED",
        expectation_status=expectation_status, expectation_observed_days=expectation_days,
        trailing_phase=next_trailing.phase if next_trailing else None,
        high_water_mark=next_trailing.high_water_mark if next_trailing else None,
        active_stop_price=next_trailing.active_stop_price if next_trailing else None,
    )


def _unavailable(state, actual_shares, trailing, expectation, reason="LIFECYCLE_DATA_UNAVAILABLE"):
    return LifecycleDecision(
        target_exposure_pct=state.target_exposure_pct,
        target_shares=actual_shares,
        reason_code=reason,
        phase="DATA_UNAVAILABLE",
        profit_target_reached=state.profit_target_reached,
        expectation_status=expectation.status if expectation else None,
        expectation_observed_days=expectation.observed_trading_days if expectation else None,
        trailing_phase=trailing.phase if trailing else None,
        high_water_mark=trailing.high_water_mark if trailing else None,
        active_stop_price=trailing.active_stop_price if trailing else None,
        data_available=False,
    )


def _invalid_reason(state: LifecycleStateInput, f: dict[str, Any]) -> str | None:
    c = _optional(f.get("close")); m5 = _optional(f.get("ma5")); pm5 = _optional(f.get("prev_ma5"))
    m20 = _optional(f.get("ma20")); pm20 = _optional(f.get("prev_ma20")); m60 = _optional(f.get("ma60"))
    macd = _optional(f.get("macd")); dif = _optional(f.get("dif")); dea = _optional(f.get("dea"))
    lower = _optional(f.get("boll_lower")); mid = _optional(f.get("boll_mid"))
    t = state.template_id
    if t in {"ma_trend_cross_v1", "ma5_pre_cross_v1"} and None not in (m5, pm5, m20, pm20):
        return "MA_DEATH_CROSS" if m5 < m20 and pm5 >= pm20 else None
    if t == "trend_pullback_v1" and None not in (c, lower, f.get("rsi"), f.get("prev_rsi")):
        return "PULLBACK_INVALID" if c < lower and _d(f["rsi"], "rsi") < _d(f["prev_rsi"], "prev_rsi") else None
    if t == "boll_volume_breakout_v1" and None not in (c, mid, macd):
        return "BREAKOUT_INVALID" if c < mid and macd < 0 else None
    if t == "macd_rsi_reversal_v1" and None not in (dif, dea, macd):
        return "MOMENTUM_REVERSAL_FAILED" if dif < dea and macd < 0 else None
    if t == "volume_surge_confirm_v1" and None not in (c, m20, macd):
        return "VOLUME_CONFIRM_INVALID" if c < m20 and macd < 0 else None
    if t == "arc_bottom_75a_v1" and c is not None and state.arc_neckline_price is not None:
        if c < state.arc_neckline_price or (None not in (m20, m60, macd) and m20 < m60 and macd < 0):
            return "ARC_BOTTOM_INVALID"
    return None


def _is_weakened(state: LifecycleStateInput, f: dict[str, Any], expectation_status: str | None) -> bool:
    t = state.template_id
    v = {k: _optional(f.get(k)) for k in (
        "close", "ma5", "prev_ma5", "ma20", "ma60", "boll_lower", "boll_upper",
        "macd", "prev_macd", "dif", "prev_dif", "dea", "prev_dea", "rsi", "prev_rsi",
        "volume", "volume_base",
    )}
    if t == "ma_trend_cross_v1" and None not in (v["ma20"], v["ma60"], v["macd"], v["prev_macd"]):
        return v["ma20"] <= v["ma60"] or v["macd"] < v["prev_macd"]
    if t == "trend_pullback_v1" and None not in (v["close"], v["boll_lower"], v["rsi"], v["prev_rsi"], v["macd"], v["prev_macd"]):
        return v["close"] <= v["boll_lower"] or v["rsi"] < v["prev_rsi"] or v["macd"] < v["prev_macd"]
    if t == "boll_volume_breakout_v1" and None not in (v["close"], v["boll_upper"], v["volume"], v["volume_base"], v["macd"], v["prev_macd"]):
        return v["close"] <= v["boll_upper"] or v["volume"] < v["volume_base"] or v["macd"] < v["prev_macd"]
    if t == "macd_rsi_reversal_v1" and None not in (v["dif"], v["dea"], v["prev_dif"], v["prev_dea"], v["rsi"], v["prev_rsi"]):
        return (v["dif"] - v["dea"]) < (v["prev_dif"] - v["prev_dea"]) or v["rsi"] < v["prev_rsi"]
    if t == "ma5_pre_cross_v1" and expectation_status == "PENDING" and None not in (v["ma5"], v["prev_ma5"], v["macd"], v["prev_macd"]):
        return v["ma5"] <= v["prev_ma5"] or v["macd"] < v["prev_macd"]
    if t == "volume_surge_confirm_v1" and None not in (v["close"], v["ma20"], v["volume"], v["volume_base"], v["macd"], v["prev_macd"]):
        return v["close"] <= v["ma20"] or v["volume"] < v["volume_base"] or v["macd"] < v["prev_macd"]
    if t == "arc_bottom_75a_v1" and None not in (v["close"], v["ma20"], v["volume"], v["volume_base"], v["macd"], v["prev_macd"]):
        return v["close"] == state.arc_neckline_price or v["close"] <= v["ma20"] or v["volume"] < v["volume_base"] or v["macd"] < v["prev_macd"]
    return False


def _is_confirmed(state: LifecycleStateInput, f: dict[str, Any], observation_no: int) -> bool:
    if observation_no < 1:
        return False
    t = state.template_id
    v = {k: _optional(f.get(k)) for k in (
        "close", "ma5", "ma20", "ma60", "boll_lower", "boll_upper", "macd", "prev_macd",
        "prev_dif", "dif", "rsi", "prev_rsi", "volume", "volume_base",
    )}
    next_day_only = t in {
        "ma_trend_cross_v1", "trend_pullback_v1", "boll_volume_breakout_v1",
        "volume_surge_confirm_v1", "arc_bottom_75a_v1",
    }
    if next_day_only and observation_no != 1:
        return False
    if t == "ma_trend_cross_v1" and None not in (v["ma5"], v["ma20"], v["ma60"], v["close"]):
        return v["ma5"] > v["ma20"] > v["ma60"] and v["close"] > v["ma5"]
    if t == "trend_pullback_v1" and None not in (v["ma20"], v["ma60"], v["close"], v["boll_lower"], v["rsi"], v["prev_rsi"], v["macd"], v["prev_macd"]):
        return v["ma20"] > v["ma60"] and v["close"] > v["boll_lower"] and v["rsi"] >= v["prev_rsi"] and v["macd"] >= v["prev_macd"] and v["close"] >= state.initial_fill_price
    if t == "boll_volume_breakout_v1" and None not in (v["close"], v["boll_upper"], v["macd"], v["prev_macd"], v["volume"], v["volume_base"]):
        return v["close"] > v["boll_upper"] and v["close"] >= state.initial_fill_price and v["macd"] >= v["prev_macd"] and v["volume"] >= v["volume_base"]
    if t == "macd_rsi_reversal_v1" and None not in (v["prev_dif"], v["dif"]):
        return v["prev_dif"] <= 0 < v["dif"]
    if t == "volume_surge_confirm_v1" and None not in (v["close"], v["ma20"], v["macd"], v["prev_macd"]):
        return v["close"] > v["ma20"] and v["close"] >= state.initial_fill_price and v["macd"] >= v["prev_macd"]
    if t == "arc_bottom_75a_v1" and state.arc_neckline_price is not None and None not in (v["close"], v["volume"], v["volume_base"], v["macd"], v["prev_macd"]):
        return v["close"] > state.arc_neckline_price and v["volume"] >= v["volume_base"] and v["macd"] >= v["prev_macd"]
    return False


class PositionLifecycleManager:
    """Persist one daily lifecycle decision and at most one delta order atomically."""

    ACTIVE_ORDER_STATUSES = ("PROPOSED", "EXECUTING", "PARTIALLY_FILLED", "RECONCILIATION_REQUIRED")

    def __init__(self, session) -> None:
        self._session = session

    def _reject_portfolio_policy_replay(self, lifecycle_id):
        from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
            LifecyclePolicyVersion, PositionLifecycleState,
        )
        config = self._session.scalar(select(LifecyclePolicyVersion.config).join(
            PositionLifecycleState,
            PositionLifecycleState.lifecycle_policy_version_id == LifecyclePolicyVersion.id,
        ).where(PositionLifecycleState.id == lifecycle_id))
        if isinstance(config, dict) and "portfolio_trial" in config:
            raise FillValidationError("组合试验政策尚未接入真实逐日持仓生命周期")

    def process_day(
        self, lifecycle_id: uuid.UUID, *, trade_date: date, fact: dict[str, Any],
        data_as_of: datetime, price_basis: str = "raw", commit: bool = True,
        buy_context: dict | None = None, defer_buy: bool = False,
    ):
        from backend.modules.investment_workspace.infrastructure.models import PortfolioPosition
        from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
            LifecyclePolicyVersion, PositionDailyFact, PositionExpectation,
            PositionIntent, PositionLifecycleState, PositionTrailingStop, SuggestedOrder,
            OrderFillEvent,
        )
        from backend.modules.quant_strategy.infrastructure.models import QuantStrategyVersion

        canonical = json.dumps(fact, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        existing_fact = self._session.execute(select(PositionDailyFact).where(
            PositionDailyFact.lifecycle_id == lifecycle_id,
            PositionDailyFact.trade_date == trade_date,
        )).scalar_one_or_none()
        if existing_fact is not None:
            self._reject_portfolio_policy_replay(lifecycle_id)
            if existing_fact.input_hash != digest or existing_fact.price_basis != price_basis:
                raise FillValidationError("同一生命周期同一交易日事实已冻结且输入不同")
            intent = self._session.execute(select(PositionIntent).where(
                PositionIntent.lifecycle_id == lifecycle_id,
                PositionIntent.trade_date == trade_date,
            ).order_by(PositionIntent.created_at.desc()).limit(1)).scalar_one_or_none()
            order = self._session.execute(select(SuggestedOrder).where(
                SuggestedOrder.intent_id == intent.id
            ).order_by(SuggestedOrder.created_at.desc()).limit(1)).scalar_one_or_none() if intent else None
            if existing_fact.planning_result is not None:
                order_id = existing_fact.planning_result.get("order_id")
                order = self._session.get(SuggestedOrder, uuid.UUID(order_id)) if order_id else None
            return existing_fact, intent, order, None

        from .planning_account import lock_portfolio
        portfolio_id = self._session.scalar(select(PositionLifecycleState.portfolio_id).where(
            PositionLifecycleState.id == lifecycle_id))
        if portfolio_id is None:
            raise FillValidationError("活动持仓生命周期不存在")
        portfolio = lock_portfolio(self._session, portfolio_id)
        from .portfolio_drawdown_actions import PortfolioDrawdownActions
        PortfolioDrawdownActions(self._session).pause_for_planning(
            portfolio_id, valuation_date=trade_date)
        lifecycle = self._session.execute(select(PositionLifecycleState).where(
            PositionLifecycleState.id == lifecycle_id,
            PositionLifecycleState.closed_at.is_(None),
        ).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
        if lifecycle is None:
            raise FillValidationError("活动持仓生命周期不存在")
        # Another worker may have frozen this day while this transaction was
        # waiting for the lifecycle row lock.  Re-read after acquiring the
        # lock so the losing worker replays instead of autoflushing a duplicate.
        existing_fact = self._session.execute(select(PositionDailyFact).where(
            PositionDailyFact.lifecycle_id == lifecycle_id,
            PositionDailyFact.trade_date == trade_date,
        )).scalar_one_or_none()
        if existing_fact is not None:
            self._reject_portfolio_policy_replay(lifecycle_id)
            if existing_fact.input_hash != digest or existing_fact.price_basis != price_basis:
                raise FillValidationError("同一生命周期同一交易日事实已冻结且输入不同")
            intent = self._session.execute(select(PositionIntent).where(
                PositionIntent.lifecycle_id == lifecycle_id,
                PositionIntent.trade_date == trade_date,
            ).order_by(PositionIntent.created_at.desc()).limit(1)).scalar_one_or_none()
            order = self._session.execute(select(SuggestedOrder).where(
                SuggestedOrder.intent_id == intent.id
            ).order_by(SuggestedOrder.created_at.desc()).limit(1)).scalar_one_or_none() if intent else None
            if existing_fact.planning_result is not None:
                order_id = existing_fact.planning_result.get("order_id")
                order = self._session.get(SuggestedOrder, uuid.UUID(order_id)) if order_id else None
            return existing_fact, intent, order, None
        position = self._session.execute(select(PortfolioPosition).where(
            PortfolioPosition.id == lifecycle.position_id
        ).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
        if position is None or _d(position.quantity, "position.quantity") <= 0:
            raise FillValidationError("生命周期缺少实际持仓")
        policy = self._session.get(LifecyclePolicyVersion, lifecycle.lifecycle_policy_version_id)
        version = self._session.get(QuantStrategyVersion, lifecycle.strategy_version_id)
        if policy is None or version is None:
            raise FillValidationError("生命周期冻结版本不存在")
        from .lifecycle_service import LifecyclePolicyService
        config = dict(policy.config or {})
        if "portfolio_trial" in config:
            raise FillValidationError("组合试验政策尚未接入真实逐日持仓生命周期")
        template_id = config.get("template_id") or version.template_id
        if not template_id:
            raise FillValidationError("生命周期策略未声明 template_id")

        management = LifecyclePolicyService.read_family(policy, template_id)

        trailing_row = self._session.execute(select(PositionTrailingStop).where(
            PositionTrailingStop.lifecycle_id == lifecycle.id
        ).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
        trailing_input = None
        if trailing_row is not None and trailing_row.config_snapshot:
            cfg = trailing_row.config_snapshot
            trailing_input = TrailingStateInput(
                high_water_mark=_d(trailing_row.high_water_mark, "high_water_mark"),
                active_stop_price=_d(trailing_row.active_stop_price, "active_stop_price"),
                phase=trailing_row.phase, b=_optional(cfg.get("b")),
                a=_optional(cfg.get("a")), d=_optional(cfg.get("d")),
            )
        expectation_row = self._session.execute(select(PositionExpectation).where(
            PositionExpectation.lifecycle_id == lifecycle.id
        ).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
        expectation_input = ExpectationStateInput(
            fill_trade_date=expectation_row.fill_trade_date,
            observed_trading_days=expectation_row.observed_trading_days,
            window_trading_days=expectation_row.window_trading_days,
            status=expectation_row.status,
        ) if expectation_row is not None else None
        initial_fill = self._session.get(OrderFillEvent, lifecycle.initial_fill_id) if lifecycle.initial_fill_id else None
        fill_trade_date = (
            initial_fill.fill_trade_date if initial_fill is not None
            else expectation_row.fill_trade_date if expectation_row is not None
            else None
        )
        prior_facts = self._session.scalar(select(func.count(PositionDailyFact.id)).where(
            PositionDailyFact.lifecycle_id == lifecycle.id,
            PositionDailyFact.trade_date > (fill_trade_date or date.min),
            PositionDailyFact.trade_date < trade_date,
        )) or 0
        observation_no = (int(prior_facts) + 1) if fill_trade_date is None or trade_date > fill_trade_date else 0
        if management is not None:
            if fill_trade_date is None:
                raise FillValidationError("冻结管理政策缺少真实首笔成交日期")
            def valid_session(payload):
                close = _optional(payload.get("close"))
                valid = close is not None and close > 0 and payload.get("is_suspended") is not True
                if expectation_input is not None and expectation_input.status == "PENDING":
                    ma5, ma20 = _optional(payload.get("ma5")), _optional(payload.get("ma20"))
                    valid = valid and ma5 is not None and ma5 > 0 and ma20 is not None and ma20 > 0
                return valid
            payloads = self._session.scalars(select(PositionDailyFact.input_payload).where(
                PositionDailyFact.lifecycle_id == lifecycle.id,
                PositionDailyFact.trade_date > fill_trade_date,
                PositionDailyFact.trade_date < trade_date,
            ))
            observation_no = sum(valid_session(payload) for payload in payloads)
            observation_no += int(trade_date > fill_trade_date and valid_session(fact))
            if management.trailing_atr_multiple is not None and trailing_input is None:
                raise FillValidationError("冻结ATR政策缺少持久化移动止损状态")
        before_version = lifecycle.state_version
        decision = evaluate_lifecycle_day(
            LifecycleStateInput(
                template_id=template_id,
                initial_fill_price=_d(lifecycle.initial_fill_price, "initial_fill_price"),
                initial_stop_price=_d(lifecycle.initial_stop_price, "initial_stop_price"),
                risk_capacity_shares=_d(lifecycle.risk_capacity_shares, "risk_capacity_shares"),
                target_exposure_pct=_d(lifecycle.target_exposure_pct, "target_exposure_pct"),
                profit_take_price=_optional(lifecycle.profit_take_price),
                profit_target_reached=lifecycle.profit_target_reached,
                confirmation_completed=lifecycle.confirmation_completed,
                arc_neckline_price=_optional(lifecycle.arc_neckline_price),
            ),
            trade_date=trade_date, fact=fact,
            actual_shares=_d(position.quantity, "position.quantity"),
            observation_no=observation_no,
            management_policy=management,
            trailing=trailing_input, expectation=expectation_input,
        )

        if decision.data_available:
            lifecycle.target_exposure_pct = decision.target_exposure_pct
            lifecycle.target_shares = decision.target_shares
            lifecycle.phase = decision.phase
            lifecycle.profit_target_reached = decision.profit_target_reached
            lifecycle.last_processed_trade_date = trade_date
            lifecycle.state_version += 1
            lifecycle.updated_at = datetime.now(timezone.utc)
            if trailing_row is not None and decision.high_water_mark is not None:
                trailing_row.high_water_mark = decision.high_water_mark
                trailing_row.active_stop_price = decision.active_stop_price
                trailing_row.phase = decision.trailing_phase
                trailing_row.last_processed_trade_date = trade_date
                position.active_stop_price = decision.active_stop_price
            if expectation_row is not None and decision.expectation_status is not None:
                expectation_row.status = decision.expectation_status
                expectation_row.observed_trading_days = decision.expectation_observed_days or 0
                expectation_row.last_processed_trade_date = trade_date
                if decision.expectation_status == "FULFILLED":
                    expectation_row.fulfilled_trade_date = trade_date

        daily = PositionDailyFact(
            id=uuid.uuid4(), lifecycle_id=lifecycle.id, trade_date=trade_date,
            price_basis=price_basis, data_as_of=data_as_of,
            input_payload=json.loads(canonical), input_hash=digest, rule_version=policy.content_hash,
            state_version_before=before_version, state_version_after=lifecycle.state_version,
            final_target_shares=decision.target_shares,
        )
        self._session.add(daily)
        intent = order = None
        has_active_execution = self._session.execute(select(SuggestedOrder.id).where(
            SuggestedOrder.lifecycle_id == lifecycle.id,
            SuggestedOrder.status.in_(self.ACTIVE_ORDER_STATUSES),
        ).limit(1)).scalar_one_or_none() is not None
        if decision.data_available and (
            decision.target_shares != _d(position.quantity, "position.quantity") or has_active_execution
        ):
            intent, order = self._materialize_delta(
                lifecycle, position, decision, trade_date, fact,
                PositionIntent=PositionIntent, SuggestedOrder=SuggestedOrder,
                portfolio=portfolio, buy_context=buy_context, daily=daily, defer_buy=defer_buy,
            )
        try:
            if commit:
                self._session.commit()
            else:
                self._session.flush()
        except IntegrityError:
            self._session.rollback()
            replay = self._session.execute(select(PositionDailyFact).where(
                PositionDailyFact.lifecycle_id == lifecycle_id,
                PositionDailyFact.trade_date == trade_date,
            )).scalar_one_or_none()
            if replay is not None and replay.input_hash == digest:
                return replay, None, None, None
            raise
        return daily, intent, order, decision

    def _materialize_delta(self, lifecycle, position, decision, trade_date, fact, *, PositionIntent, SuggestedOrder,
                           portfolio, buy_context, daily, defer_buy=False):
        pending_orders = list(self._session.scalars(select(SuggestedOrder).where(
            SuggestedOrder.lifecycle_id == lifecycle.id,
            SuggestedOrder.status.in_(self.ACTIVE_ORDER_STATUSES),
        ).with_for_update().execution_options(populate_existing=True)))
        actual = _d(position.quantity, "position.quantity")
        active_intent = self._session.execute(select(PositionIntent).where(
            PositionIntent.lifecycle_id == lifecycle.id,
            PositionIntent.status.in_(("ACTIVE", "EXECUTING", "RECONCILIATION_REQUIRED")),
        ).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
        broker_facing = [order for order in pending_orders
                         if order.status != "PROPOSED" or order.filled_quantity != 0]
        if broker_facing:
            target_delta = decision.target_shares - actual
            remaining = sum(
                (Decimal(1) if order.side == "BUY" else Decimal(-1))
                * (_d(order.quantity, "order.quantity") - _d(order.filled_quantity, "order.filled_quantity"))
                for order in pending_orders
            )
            conflicts = (
                any(order.status == "RECONCILIATION_REQUIRED" for order in broker_facing)
                or (active_intent is not None and active_intent.status == "RECONCILIATION_REQUIRED")
                or (active_intent is not None and not (
                    _d(active_intent.target_shares, "intent.target_shares") == decision.target_shares
                    and (active_intent.reason_code == decision.reason_code
                         or decision.target_shares == 0 and decision.reason_code == "EXIT_PENDING")
                ))
                or any(order.intent_id is not None
                       and (active_intent is None or order.intent_id != active_intent.id)
                       for order in broker_facing)
                or any(order.intent_id is None for order in broker_facing)
                or target_delta == 0
                or any((order.side == "BUY") != (target_delta > 0) for order in broker_facing)
                or (remaining > 0) != (target_delta > 0)
                or abs(remaining) > abs(target_delta)
            )
            if conflicts:
                for order in pending_orders:
                    if order.status == "PROPOSED" and order.filled_quantity == 0:
                        order.status = "SUPERSEDED"
                        order.revision += 1
                    elif order.status != "RECONCILIATION_REQUIRED":
                        order.status = "RECONCILIATION_REQUIRED"
                        order.revision += 1
                if active_intent is not None and active_intent.status != "RECONCILIATION_REQUIRED":
                    active_intent.status = "RECONCILIATION_REQUIRED"
                    active_intent.revision += 1
                daily.planning_result = {"stage": "AWAITING_ORDER_RECONCILIATION"}
                return active_intent, None
        if active_intent is not None:
            if (
                _d(active_intent.target_shares, "intent.target_shares") == decision.target_shares
                and (active_intent.reason_code == decision.reason_code
                     or decision.target_shares == 0 and decision.reason_code == "EXIT_PENDING")
            ):
                intent = active_intent
            elif active_intent.status == "ACTIVE":
                active_intent.status = "SUPERSEDED"
                active_intent.revision += 1
                intent = None
            else:
                raise FillValidationError("已有执行中或待对账意图，不能创建冲突目标")
        else:
            intent = None
        if intent is None:
            intent = PositionIntent(
                id=uuid.uuid4(), lifecycle_id=lifecycle.id, trade_date=trade_date,
                target_shares=decision.target_shares, reason_code=decision.reason_code,
                state_version=lifecycle.state_version, status="ACTIVE", revision=1,
            )
            self._session.add(intent)
            self._session.flush()

        def settle_conflicting_order(pending: SuggestedOrder) -> bool:
            """Return true only when an unsubmitted suggestion was safely removed."""
            if pending.status == "PROPOSED" and pending.filled_quantity == 0:
                pending.status = "SUPERSEDED"
                pending.revision += 1
                return True
            if pending.status != "RECONCILIATION_REQUIRED":
                pending.status = "RECONCILIATION_REQUIRED"
                pending.revision += 1
            daily.planning_result = {"stage": "AWAITING_ORDER_RECONCILIATION",
                                     "order_id": str(pending.id)}
            return False

        if decision.target_shares == actual:
            blocked = False
            for pending in pending_orders:
                blocked = not settle_conflicting_order(pending) or blocked
            if blocked:
                return intent, None
            return intent, None
        desired_direction = Decimal(1) if decision.target_shares > actual else Decimal(-1)
        blocked = False
        for pending in pending_orders:
            pending_direction = Decimal(1) if pending.side == "BUY" else Decimal(-1)
            if pending_direction != desired_direction:
                blocked = not settle_conflicting_order(pending) or blocked
            elif (pending_direction == desired_direction and pending.intent_id is None
                  and pending.status == "PROPOSED" and pending.filled_quantity == 0):
                pending.lifecycle_id = lifecycle.id
                pending.intent_id = intent.id
        if blocked:
            return intent, None
        remaining = sum(
            (Decimal(1) if p.side == "BUY" else Decimal(-1))
            * (_d(p.quantity, "order.quantity") - _d(p.filled_quantity, "order.filled_quantity"))
            for p in pending_orders if p.status in self.ACTIVE_ORDER_STATUSES
        )
        target_delta = decision.target_shares - actual
        if (
            remaining != 0 and target_delta != 0
            and (remaining > 0) == (target_delta > 0)
            and abs(remaining) > abs(target_delta)
        ):
            blocked = False
            for pending in pending_orders:
                pending_direction = Decimal(1) if pending.side == "BUY" else Decimal(-1)
                if pending_direction == desired_direction:
                    blocked = not settle_conflicting_order(pending) or blocked
            if blocked:
                return intent, None
            remaining = Decimal(0)
        delta = decision.target_shares - actual - remaining
        if delta == 0:
            return intent, None
        side = "BUY" if delta > 0 else "SELL"
        if side == "BUY" and defer_buy:
            # Reconcile existing orders above first: deferring a new BUY must
            # not leave a cancellable old order larger than today's target.
            daily.planning_result = json.loads(json.dumps({
                "stage": "AWAITING_BATCH", "decision": asdict(decision), "order_id": None,
            }, default=str))
            return intent, None
        quantity = abs(delta)
        if side == "SELL":
            quantity = min(quantity, actual)
        if quantity <= 0:
            return intent, None
        price = _d(fact.get("close"), "close")
        earliest = trade_date
        stop = _optional(decision.active_stop_price) or _optional(lifecycle.initial_stop_price)
        cash = Decimal(0)
        industry = None
        if side == "BUY":
            from .planning_account import planning_account
            from .position_planner import plan_buy_target
            account = planning_account(self._session, portfolio)
            context = buy_context or {}
            from .strategy_admission import StrategyAdmissionService
            admission = StrategyAdmissionService(self._session).gate_new_risk(
                lifecycle.strategy_version_id, asset_scope=context.get("asset_scope"),
                risk_profile=portfolio.risk_profile,
            )
            if (lifecycle.market != "CN" or daily.price_basis != "raw"
                    or any(p["market"] != "CN" for p in account["positions"] + account["pending_orders"])
                    or context.get("valuation_date") != trade_date):
                projection = {"order_status": "BUY_REJECTED_CONTEXT"}
            elif not admission["allowed"]:
                projection = {"order_status": "BUY_REJECTED_ADMISSION"}
            else:
                from .certified_instrument_rules import live_authorizations
                rule_authorization = live_authorizations(
                    self._session, symbols={lifecycle.symbol}, decision_date=trade_date,
                    expected_asset_type=("etf" if context.get("asset_scope") == "CN_ETF" else "stock"),
                ).get(lifecycle.symbol)
                # Reserve external same-symbol buys against the lifecycle target
                # as well as against account exposure. Do not net pending sells.
                reserved = sum((p["remaining_quantity"] for p in account["pending_orders"]
                                if p["side"] == "BUY" and p["symbol"] == lifecycle.symbol), Decimal(0))
                projection = plan_buy_target(
                    symbol=lifecycle.symbol, max_quantity=max(Decimal(0), decision.target_shares - actual - reserved),
                    entry=price, stop=stop, take=_optional(lifecycle.profit_take_price),
                    strategy_version_id=lifecycle.strategy_version_id,
                    market=fact.get("execution_market") or {}, valuation_date=trade_date,
                    max_notional=context.get("max_add_notional"),
                    entry_lower=context.get("entry_lower"), entry_upper=context.get("entry_upper"),
                    closes=context.get("closes", {}), industry_map=context.get("industry_map", {}),
                    industry_bucket_available=context.get("industry_bucket_available") is True,
                    risk_gate="block" if fact.get("new_risk_allowed") is False else None,
                    execution_policy_snapshot=fact.get("execution_policy"), **account,
                    instrument_rules=({lifecycle.symbol: rule_authorization.rule}
                                      if rule_authorization is not None else {}),
                )
            daily.planning_result = json.loads(json.dumps({
                "account": account, "market_context": context, "projection": projection,
                "admission": admission,
            }, default=str))
            if projection["order_status"] != "ELIGIBLE":
                return intent, None
            current_rule = live_authorizations(
                self._session, symbols={lifecycle.symbol}, decision_date=trade_date,
                expected_asset_type=("etf" if context.get("asset_scope") == "CN_ETF" else "stock"),
            ).get(lifecycle.symbol)
            if (rule_authorization is None or current_rule is None
                    or current_rule.certificate_id != rule_authorization.certificate_id
                    or current_rule.rule != rule_authorization.rule):
                daily.planning_result = {
                    **daily.planning_result,
                    "projection": {**daily.planning_result["projection"],
                                   "order_status": "BUY_REJECTED_INSTRUMENT_RULE"},
                }
                return intent, None
            rule_certificate_id = current_rule.certificate_id
            quantity, price, stop = projection["shares"], projection["order_cost_price"], projection["order_stop_price"]
            cash = projection["notional"] + projection["estimated_fees"]
            industry = projection["risk_bucket"]["industry_code"]
            earliest = projection["earliest_execution_trade_date"]
        if side == "SELL":
            from .position_planner import plan_reduction
            from .execution_constraints import ExecutionConstraintEvaluator, ExecutionPolicy
            market = fact.get("execution_market") or {}
            available_sell = _optional(fact.get("available_sell_quantity"))
            if str(market.get("trade_date"))[:10] != trade_date.isoformat():
                daily.planning_result = {"sell_status": "SELL_MARKET_FACTS_UNAVAILABLE"}
                return intent, None
            if available_sell is None or available_sell < 0 or available_sell > actual:
                daily.planning_result = {"sell_status": "SELLABLE_QUANTITY_UNKNOWN"}
                return intent, None
            # Include reservations from manual and signal orders, not only this
            # lifecycle's orders, so independent sources cannot oversell.
            sell_orders = self._session.scalars(select(SuggestedOrder).where(
                SuggestedOrder.portfolio_id == lifecycle.portfolio_id,
                SuggestedOrder.market == lifecycle.market,
                SuggestedOrder.symbol == lifecycle.symbol,
                SuggestedOrder.side == "SELL",
                SuggestedOrder.status.in_(self.ACTIVE_ORDER_STATUSES),
            ).with_for_update().execution_options(populate_existing=True))
            reserved_sell = sum((_d(order.quantity, "order.quantity") - _d(order.filled_quantity, "order.filled_quantity")
                                 for order in sell_orders), Decimal(0))
            reduction = plan_reduction(
                target_quantity=decision.target_shares, actual_quantity=actual,
                available_quantity=available_sell,
                reserved_quantity=reserved_sell, market=market,
                execution=ExecutionConstraintEvaluator(ExecutionPolicy.from_snapshot(fact.get("execution_policy"))),
            )
            if reduction.code is not None:
                return intent, None
            quantity, price = reduction.quantity, reduction.price
            earliest = reduction.earliest_execution_trade_date
        decision_at = datetime.now(timezone.utc)
        if side == "BUY" and decision_at.astimezone(ZoneInfo("Asia/Shanghai")).date() != trade_date:
            daily.planning_result = {
                **daily.planning_result,
                "projection": {**daily.planning_result["projection"],
                               "order_status": "BUY_REJECTED_INSTRUMENT_RULE"},
            }
            return intent, None
        order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=lifecycle.portfolio_id, position_id=position.id,
            lifecycle_id=lifecycle.id, intent_id=intent.id,
            rule_certificate_id=rule_certificate_id if side == "BUY" else None,
            rule_authorized_at=current_rule.authorized_at if side == "BUY" else None,
            decision_at=decision_at,
            market=lifecycle.market, symbol=lifecycle.symbol, side=side,
            quantity=quantity, filled_quantity=Decimal(0), limit_price=price,
            stop_price=stop, reserved_cash=cash, industry_code=industry,
            reserved_risk=max(Decimal(0), price - stop) * quantity if side == "BUY" and stop else Decimal(0),
            reason_code=decision.reason_code, status="PROPOSED", revision=1,
            earliest_execution_trade_date=earliest,
        )
        self._session.add(order)
        if side == "BUY":
            daily.planning_result = {**daily.planning_result, "order_id": str(order.id)}
        return intent, order
