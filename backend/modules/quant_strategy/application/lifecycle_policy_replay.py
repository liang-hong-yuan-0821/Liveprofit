"""Offline daily policy replay from explicit snapshots and calendar dates.

Inputs must be independently linked to effective fills, frozen intent
definitions and completions, corporate-action coverage and an exchange calendar
by the caller. A zero holding only closes a previously replayed zero-target
exit with matching completion evidence; same-day decisions and fills remain
UNKNOWN. Action basis resets remain UNKNOWN. It never authorizes state or
infers a closing timestamp.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, DecimalException, InvalidOperation
from uuid import UUID

from backend.modules.quant_strategy.application.errors import FillValidationError
from backend.modules.quant_strategy.application.lifecycle_intent_replay import CLOSING_EXIT_REASONS
from backend.modules.quant_strategy.application.position_lifecycle_manager import (
    ALLOWED_EXPOSURES, ExpectationStateInput, LifecycleDecision, LifecycleStateInput,
    TrailingStateInput, evaluate_lifecycle_day,
)
from backend.modules.quant_strategy.domain.management_policies import ManagementPolicy


@dataclass(frozen=True)
class DailyPolicyFrame:
    trade_date: date
    revision_id: UUID
    fact: dict
    price_basis: str
    actual_shares: Decimal
    completed_intent_reasons: tuple[str, ...] = ()
    completion_source_ref: str | None = None


@dataclass(frozen=True)
class DailyPolicyProjection:
    trade_date: date
    revision_id: UUID
    reason_code: str
    target_shares: Decimal
    target_exposure_pct: Decimal
    expectation_status: str | None
    expectation_observed_days: int | None
    trailing_phase: str | None
    high_water_mark: Decimal | None
    active_stop_price: Decimal | None
    phase: str | None = None


@dataclass(frozen=True)
class PolicyReplay:
    status: str
    issues: tuple[str, ...]
    days: tuple[DailyPolicyProjection, ...]
    final_state: LifecycleStateInput | None
    final_trailing: TrailingStateInput | None
    final_expectation: ExpectationStateInput | None
    profit_trim_completed: bool | None = None
    # Frozen reason of an exit still awaiting its final bound SELL. Family
    # policies may emit generic EXIT_PENDING on later real daily facts.
    pending_exit_reason: str | None = None


def _positive(value: object) -> bool:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return number.is_finite() and number > 0


def replay_lifecycle_policy(
    *, first_fill_date: date, seed_state: LifecycleStateInput,
    seed_target_shares: Decimal,
    seed_trailing: TrailingStateInput | None,
    seed_expectation: ExpectationStateInput | None,
    management_policy: ManagementPolicy | None,
    calendar_dates: tuple[date, ...], calendar_source_ref: str,
    corporate_action_dates: tuple[date, ...], corporate_action_source_ref: str,
    frames: tuple[DailyPolicyFrame, ...],
) -> PolicyReplay:
    """Run the existing pure rule evaluator once per proved exchange session.

    A suspended session has no observed price and cannot advance expectations,
    trailing stops or one-day confirmation counters. A missing completion
    binding cannot be replaced with a target or holding-quantity guess. The
    terminal frame has no policy evaluation and must follow an earlier exit
    decision; its completion source does not establish a closing timestamp.
    """
    issues: list[str] = []
    if (type(first_fill_date) is not date or not isinstance(seed_state, LifecycleStateInput)
            or not isinstance(calendar_source_ref, str) or not calendar_source_ref.strip()):
        issues.append("POLICY_SEED_OR_CALENDAR_SOURCE_UNKNOWN")
    if (not isinstance(seed_target_shares, Decimal)
            or not seed_target_shares.is_finite() or seed_target_shares <= 0):
        issues.append("POLICY_SEED_TARGET_INVALID")
    if (isinstance(seed_state, LifecycleStateInput)
            and (seed_state.confirmation_completed or seed_state.profit_target_reached)):
        issues.append("POLICY_SEED_NOT_INITIAL_FILL")
    if (isinstance(seed_state, LifecycleStateInput)
            and (not isinstance(seed_state.target_exposure_pct, Decimal)
                 or not seed_state.target_exposure_pct.is_finite()
                 or seed_state.target_exposure_pct == 0
                 or seed_state.target_exposure_pct not in ALLOWED_EXPOSURES
                 or any(not isinstance(value, Decimal) or not value.is_finite()
                        for value in (seed_state.initial_fill_price,
                                      seed_state.initial_stop_price,
                                      seed_state.risk_capacity_shares))
                 or not (Decimal(0) < seed_state.initial_stop_price
                         < seed_state.initial_fill_price)
                 or seed_state.risk_capacity_shares <= 0)):
        issues.append("POLICY_SEED_VALUES_INVALID")
    if (seed_trailing is not None and not isinstance(seed_trailing, TrailingStateInput)):
        issues.append("POLICY_TRAILING_SEED_INVALID")
    elif (isinstance(seed_trailing, TrailingStateInput)
          and isinstance(seed_state, LifecycleStateInput)
          and (seed_trailing.phase != "PROTECT"
               or seed_trailing.high_water_mark != seed_state.initial_fill_price
               or seed_trailing.active_stop_price != seed_state.initial_stop_price)):
        issues.append("POLICY_TRAILING_SEED_NOT_INITIAL")
    if management_policy is not None and not isinstance(management_policy, ManagementPolicy):
        issues.append("MANAGEMENT_POLICY_INVALID")
    elif (management_policy is not None
          and management_policy.trailing_atr_multiple is not None
          and seed_trailing is None):
        issues.append("ATR_TRAILING_SEED_MISSING")
    if (seed_expectation is not None and
            (not isinstance(seed_expectation, ExpectationStateInput)
             or seed_expectation.fill_trade_date != first_fill_date
             or seed_expectation.status != "PENDING"
             or seed_expectation.observed_trading_days != 0)):
        issues.append("POLICY_EXPECTATION_SEED_INVALID")
    calendar_valid = (isinstance(calendar_dates, tuple) and bool(calendar_dates)
                      and all(type(day) is date for day in calendar_dates))
    if (not calendar_valid or tuple(sorted(set(calendar_dates))) != calendar_dates
            or (type(first_fill_date) is date
                and any(day < first_fill_date for day in calendar_dates))):
        issues.append("CALENDAR_DATES_INVALID")
    if (not isinstance(corporate_action_dates, tuple)
            or any(type(day) is not date for day in corporate_action_dates)
            or not isinstance(corporate_action_source_ref, str)
            or not corporate_action_source_ref.strip()):
        issues.append("CORPORATE_ACTION_COVERAGE_UNKNOWN")
    elif corporate_action_dates:
        issues.append("CORPORATE_ACTION_BASIS_RESET_UNSUPPORTED")
    if (not isinstance(frames, tuple)
            or any(not isinstance(frame, DailyPolicyFrame) for frame in frames)
            or tuple(frame.trade_date for frame in frames) != calendar_dates):
        issues.append("DAILY_FACT_CALENDAR_MISMATCH")
    allowed_completions = {"TEMPLATE_CONFIRM_ADD", "ADD_AT_R", "PROFIT_TARGET_TRIM"}
    allowed_completions.update(CLOSING_EXIT_REASONS)
    seen_revisions: set[UUID] = set()
    for frame in frames if isinstance(frames, tuple) else ():
        if not isinstance(frame, DailyPolicyFrame):
            continue
        if (not isinstance(frame.revision_id, UUID)
                or frame.revision_id in seen_revisions
                or not isinstance(frame.fact, dict)
                or frame.price_basis != "raw"
                or type(frame.fact.get("is_suspended")) is not bool
                or not isinstance(frame.actual_shares, Decimal)
                or not frame.actual_shares.is_finite() or frame.actual_shares < 0):
            issues.append(f"DAILY_POLICY_INPUT_INVALID:{frame.trade_date}")
        if isinstance(frame.revision_id, UUID):
            seen_revisions.add(frame.revision_id)
        if (not isinstance(frame.completed_intent_reasons, tuple)
                or len(frame.completed_intent_reasons) > 1
                or any(not isinstance(reason, str) or reason not in allowed_completions
                       for reason in frame.completed_intent_reasons)
                or (frame.completed_intent_reasons and
                    (not isinstance(frame.completion_source_ref, str)
                     or not frame.completion_source_ref.strip()))):
            issues.append(f"INTENT_COMPLETION_EVIDENCE_UNKNOWN:{frame.trade_date}")
        elif (isinstance(frame.actual_shares, Decimal)
              and frame.actual_shares.is_finite()
              and frame.actual_shares > 0
              and any(reason in CLOSING_EXIT_REASONS
                      for reason in frame.completed_intent_reasons)):
            issues.append(f"CLOSING_COMPLETION_WITH_HOLDING:{frame.trade_date}")
        if (isinstance(frame.fact, dict)
                and frame.fact.get("is_suspended") is False
                and isinstance(frame.actual_shares, Decimal)
                and frame.actual_shares != 0 and (
                not _positive(frame.fact.get("close"))
                or not _positive(frame.fact.get("high")))):
            issues.append(f"DAILY_POLICY_MARKET_FACTS_UNKNOWN:{frame.trade_date}")
    if issues:
        return PolicyReplay("UNKNOWN", tuple(issues), (), None, None, None)

    state = seed_state
    trailing = seed_trailing
    expectation = seed_expectation
    target_shares = seed_target_shares
    observed = 0
    phase: str | None = None
    pending_exit_reason: str | None = None
    profit_trim_completed = False
    projections: list[DailyPolicyProjection] = []
    for index, frame in enumerate(frames):
        if frame.actual_shares == 0:
            closing_reasons = frame.completed_intent_reasons
            if (index != len(frames) - 1 or frame.fact["is_suspended"]
                    or len(closing_reasons) != 1
                    or closing_reasons[0] not in CLOSING_EXIT_REASONS
                    or closing_reasons[0] != pending_exit_reason):
                return PolicyReplay(
                    "UNKNOWN", (f"ZERO_HOLDING_TERMINAL_EVIDENCE_UNKNOWN:{frame.trade_date}",),
                    tuple(projections), None, None, None)
            state = replace(state, target_exposure_pct=Decimal(0))
            projections.append(DailyPolicyProjection(
                frame.trade_date, frame.revision_id, closing_reasons[0],
                Decimal(0), Decimal(0),
                expectation.status if expectation else None,
                expectation.observed_trading_days if expectation else None,
                trailing.phase if trailing else None,
                trailing.high_water_mark if trailing else None,
                trailing.active_stop_price if trailing else None,
                "CLOSED",
            ))
            return PolicyReplay("PROVISIONAL", (), tuple(projections), state,
                                trailing, expectation, profit_trim_completed)
        if "TEMPLATE_CONFIRM_ADD" in frame.completed_intent_reasons or "ADD_AT_R" in frame.completed_intent_reasons:
            state = replace(state, confirmation_completed=True)
        if "PROFIT_TARGET_TRIM" in frame.completed_intent_reasons:
            profit_trim_completed = True
        if frame.fact["is_suspended"] is True:
            projections.append(DailyPolicyProjection(
                frame.trade_date, frame.revision_id, "SUSPENDED_SESSION",
                target_shares, state.target_exposure_pct,
                expectation.status if expectation else None,
                expectation.observed_trading_days if expectation else None,
                trailing.phase if trailing else None,
                trailing.high_water_mark if trailing else None,
                trailing.active_stop_price if trailing else None,
                phase,
            ))
            continue
        if frame.trade_date > first_fill_date:
            if management_policy is None or expectation is None or expectation.status != "PENDING":
                observed += 1
            elif _positive(frame.fact.get("ma5")) and _positive(frame.fact.get("ma20")):
                observed += 1
        try:
            decision: LifecycleDecision = evaluate_lifecycle_day(
                state, trade_date=frame.trade_date, fact=frame.fact,
                actual_shares=frame.actual_shares, observation_no=observed,
                trailing=trailing, expectation=expectation,
                management_policy=management_policy,
            )
        except (FillValidationError, DecimalException, TypeError, ValueError):
            return PolicyReplay("UNKNOWN", (f"POLICY_EVALUATION_FAILED:{frame.trade_date}",),
                                tuple(projections), None, None, None)
        if not decision.data_available:
            return PolicyReplay("UNKNOWN", (f"POLICY_DAY_UNAVAILABLE:{frame.trade_date}",),
                                tuple(projections), None, None, None)
        state = replace(state, target_exposure_pct=decision.target_exposure_pct,
                        profit_target_reached=decision.profit_target_reached)
        phase = decision.phase
        target_shares = decision.target_shares
        if target_shares != 0:
            pending_exit_reason = None
        elif (decision.target_exposure_pct == 0
              and decision.reason_code in CLOSING_EXIT_REASONS):
            # Family management emits a generic EXIT_PENDING on later days
            # while the original zero-target intent remains active. Keep the
            # specific frozen reason until a distinct decision replaces it.
            if decision.reason_code != "EXIT_PENDING" or pending_exit_reason is None:
                pending_exit_reason = decision.reason_code
        elif decision.reason_code != "LIFECYCLE_HOLD":
            pending_exit_reason = None
        if trailing is not None and decision.high_water_mark is not None:
            trailing = replace(trailing, high_water_mark=decision.high_water_mark,
                               active_stop_price=decision.active_stop_price,
                               phase=decision.trailing_phase)
        if expectation is not None and decision.expectation_status is not None:
            expectation = replace(expectation, status=decision.expectation_status,
                                  observed_trading_days=decision.expectation_observed_days)
        projections.append(DailyPolicyProjection(
            frame.trade_date, frame.revision_id, decision.reason_code,
            decision.target_shares, decision.target_exposure_pct,
            decision.expectation_status, decision.expectation_observed_days,
            decision.trailing_phase, decision.high_water_mark,
            decision.active_stop_price,
            phase,
        ))
    return PolicyReplay("PROVISIONAL", (), tuple(projections), state, trailing,
                        expectation, profit_trim_completed, pending_exit_reason)
