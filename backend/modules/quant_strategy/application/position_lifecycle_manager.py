"""Pure lifecycle rule engine used by the transactional N6 coordinator.

The evaluator deliberately has no market/provider access.  It consumes one
already-frozen daily fact and returns one final target, so callers can persist
the fact, intent and suggested order in a single transaction.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from typing import Any
import hashlib
import json
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.modules.quant_strategy.application.errors import FillValidationError

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
) -> LifecycleDecision:
    """Evaluate the frozen N6 priority chain for one complete trading day."""

    try:
        close = _d(fact.get("close"), "close")
        high = _d(fact.get("high"), "high")
    except FillValidationError:
        return _unavailable(state, actual_shares, trailing, expectation)
    if close <= 0 or high <= 0 or state.risk_capacity_shares <= 0:
        return _unavailable(state, actual_shares, trailing, expectation)

    def decision(exposure: Decimal, reason: str, phase: str, **overrides) -> LifecycleDecision:
        target_shares = _target(state.risk_capacity_shares, exposure)
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

    def process_day(
        self, lifecycle_id: uuid.UUID, *, trade_date: date, fact: dict[str, Any],
        data_as_of: datetime, price_basis: str = "raw", commit: bool = True,
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
            if existing_fact.input_hash != digest or existing_fact.price_basis != price_basis:
                raise FillValidationError("同一生命周期同一交易日事实已冻结且输入不同")
            intent = self._session.execute(select(PositionIntent).where(
                PositionIntent.lifecycle_id == lifecycle_id,
                PositionIntent.trade_date == trade_date,
            ).order_by(PositionIntent.created_at.desc()).limit(1)).scalar_one_or_none()
            order = self._session.execute(select(SuggestedOrder).where(
                SuggestedOrder.intent_id == intent.id
            ).order_by(SuggestedOrder.created_at.desc()).limit(1)).scalar_one_or_none() if intent else None
            return existing_fact, intent, order, None

        lifecycle = self._session.execute(select(PositionLifecycleState).where(
            PositionLifecycleState.id == lifecycle_id,
            PositionLifecycleState.closed_at.is_(None),
        ).with_for_update()).scalar_one_or_none()
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
            if existing_fact.input_hash != digest or existing_fact.price_basis != price_basis:
                raise FillValidationError("同一生命周期同一交易日事实已冻结且输入不同")
            intent = self._session.execute(select(PositionIntent).where(
                PositionIntent.lifecycle_id == lifecycle_id,
                PositionIntent.trade_date == trade_date,
            ).order_by(PositionIntent.created_at.desc()).limit(1)).scalar_one_or_none()
            order = self._session.execute(select(SuggestedOrder).where(
                SuggestedOrder.intent_id == intent.id
            ).order_by(SuggestedOrder.created_at.desc()).limit(1)).scalar_one_or_none() if intent else None
            return existing_fact, intent, order, None
        position = self._session.execute(select(PortfolioPosition).where(
            PortfolioPosition.id == lifecycle.position_id
        ).with_for_update()).scalar_one_or_none()
        if position is None or _d(position.quantity, "position.quantity") <= 0:
            raise FillValidationError("生命周期缺少实际持仓")
        policy = self._session.get(LifecyclePolicyVersion, lifecycle.lifecycle_policy_version_id)
        version = self._session.get(QuantStrategyVersion, lifecycle.strategy_version_id)
        if policy is None or version is None:
            raise FillValidationError("生命周期冻结版本不存在")
        config = dict(policy.config or {})
        template_id = config.get("template_id") or version.template_id
        if not template_id:
            raise FillValidationError("生命周期策略未声明 template_id")

        trailing_row = self._session.execute(select(PositionTrailingStop).where(
            PositionTrailingStop.lifecycle_id == lifecycle.id
        ).with_for_update()).scalar_one_or_none()
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
        ).with_for_update()).scalar_one_or_none()
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
            observation_no=(int(prior_facts) + 1) if fill_trade_date is None or trade_date > fill_trade_date else 0,
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

    def _materialize_delta(self, lifecycle, position, decision, trade_date, fact, *, PositionIntent, SuggestedOrder):
        active_intent = self._session.execute(select(PositionIntent).where(
            PositionIntent.lifecycle_id == lifecycle.id,
            PositionIntent.status.in_(("ACTIVE", "EXECUTING", "RECONCILIATION_REQUIRED")),
        ).with_for_update()).scalar_one_or_none()
        if active_intent is not None:
            if (
                _d(active_intent.target_shares, "intent.target_shares") == decision.target_shares
                and active_intent.reason_code == decision.reason_code
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

        pending_orders = list(self._session.scalars(select(SuggestedOrder).where(
            SuggestedOrder.lifecycle_id == lifecycle.id,
            SuggestedOrder.status.in_(self.ACTIVE_ORDER_STATUSES),
        ).with_for_update()))
        actual = _d(position.quantity, "position.quantity")
        if decision.target_shares == actual:
            for pending in pending_orders:
                if pending.status in ("PROPOSED", "PARTIALLY_FILLED"):
                    pending.status = "SUPERSEDED"
                    pending.revision += 1
                else:
                    raise FillValidationError("执行中或待对账订单阻止目标归位")
            return intent, None
        desired_direction = Decimal(1) if decision.target_shares > actual else Decimal(-1)
        for pending in pending_orders:
            pending_direction = Decimal(1) if pending.side == "BUY" else Decimal(-1)
            if pending_direction != desired_direction and pending.status in ("PROPOSED", "PARTIALLY_FILLED"):
                pending.status = "SUPERSEDED"
                pending.revision += 1
            elif pending_direction == desired_direction and pending.intent_id is None:
                pending.lifecycle_id = lifecycle.id
                pending.intent_id = intent.id
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
            for pending in pending_orders:
                pending_direction = Decimal(1) if pending.side == "BUY" else Decimal(-1)
                if pending_direction == desired_direction and pending.status in ("PROPOSED", "PARTIALLY_FILLED"):
                    pending.status = "SUPERSEDED"
                    pending.revision += 1
                elif pending_direction == desired_direction:
                    raise FillValidationError("执行中或待对账订单超过新目标")
            remaining = Decimal(0)
        delta = decision.target_shares - actual - remaining
        if delta == 0:
            return intent, None
        side = "BUY" if delta > 0 else "SELL"
        quantity = abs(delta)
        if side == "BUY":
            quantity = (quantity / LOT).to_integral_value(rounding=ROUND_FLOOR) * LOT
        else:
            quantity = min(quantity, actual)
        if quantity <= 0:
            return intent, None
        price = _d(fact.get("close"), "close")
        stop = _optional(fact.get("active_stop_price")) or _optional(decision.active_stop_price) or _optional(lifecycle.initial_stop_price)
        order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=lifecycle.portfolio_id, position_id=position.id,
            lifecycle_id=lifecycle.id, intent_id=intent.id,
            market=lifecycle.market, symbol=lifecycle.symbol, side=side,
            quantity=quantity, filled_quantity=Decimal(0), limit_price=price,
            stop_price=stop, reserved_cash=quantity * price if side == "BUY" else Decimal(0),
            reserved_risk=max(Decimal(0), price - stop) * quantity if side == "BUY" and stop else Decimal(0),
            reason_code=decision.reason_code, status="PROPOSED", revision=1,
            earliest_execution_trade_date=trade_date,
        )
        self._session.add(order)
        return intent, order
