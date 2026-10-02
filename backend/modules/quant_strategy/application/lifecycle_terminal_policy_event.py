"""Check one closing fill against real daily policy and frozen intent facts.

The terminal session is a fill event, never a PositionDailyFact or a policy
version advance. Callers must first establish the complete local effective
fill/accounting, intent-completion and version chains. Calendar/source claims
remain provisional and this result cannot authorize a projection write.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from .lifecycle_accounting_replay import AccountingEventState
from .lifecycle_intent_replay import CLOSING_EXIT_REASONS, IntentCompletion
from .lifecycle_policy_replay import DailyPolicyProjection, PolicyReplay
from .lifecycle_version_chain import DailyVersionStep, FillVersionStep
from .position_lifecycle_manager import LifecycleStateInput


_CN_TZ = ZoneInfo("Asia/Shanghai")
_ZERO = Decimal(0)


@dataclass(frozen=True)
class FrozenClosingIntent:
    intent_id: UUID
    trade_date: date
    state_version: int
    target_shares: Decimal
    reason_code: str
    source_ref: str


@dataclass(frozen=True)
class TerminalPolicyEventDiagnosis:
    status: Literal["PROVISIONAL", "UNKNOWN"]
    closing_fill_event_id: UUID | None
    terminal_trade_date: date | None
    expected_phase: Literal["CLOSED"] | None
    expected_target_shares: Decimal | None
    expected_target_exposure_pct: Decimal | None
    last_processed_trade_date: date | None
    ending_state_version: int | None
    issues: tuple[str, ...]


def _unknown(issue: str, *prior: str) -> TerminalPolicyEventDiagnosis:
    return TerminalPolicyEventDiagnosis(
        "UNKNOWN", None, None, None, None, None, None, None,
        (*prior, issue),
    )


def diagnose_terminal_policy_event(
    *, policy: PolicyReplay, completions: tuple[IntentCompletion, ...],
    frozen_closing_intent: FrozenClosingIntent,
    daily_versions: tuple[DailyVersionStep, ...],
    final_fill_step: FillVersionStep, current_version: int,
    terminal_open_session_date: date, calendar_dates: tuple[date, ...],
    calendar_source_ref: str,
    terminal_fill_steps: tuple[FillVersionStep, ...] | None = None,
    terminal_event_states: tuple[AccountingEventState, ...] | None = None,
    pre_terminal_quantity: Decimal | None = None,
) -> TerminalPolicyEventDiagnosis:
    """Link later closing fills to a prior real zero-target decision.

    ``calendar_dates`` declares every open session represented by a real
    daily policy result, followed by one closing-fill session. The final date
    has no daily fact or revision ID. The optional terminal sequence describes
    every fill after the last real daily fact. Its states must come from the
    complete effective accounting replay; this function does not authenticate
    that replay or a broker source. The legacy single-fill call remains valid.
    """
    if (not isinstance(policy, PolicyReplay) or policy.status != "PROVISIONAL"
            or not isinstance(policy.days, tuple) or not policy.days
            or not isinstance(policy.final_state, LifecycleStateInput)
            or not isinstance(policy.issues, tuple)
            or any(not isinstance(issue, str) for issue in policy.issues)
            or any(not isinstance(day, DailyPolicyProjection)
                   or type(day.trade_date) is not date
                   or not isinstance(day.reason_code, str)
                   or not isinstance(day.target_shares, Decimal)
                   or not day.target_shares.is_finite()
                   or not isinstance(day.target_exposure_pct, Decimal)
                   or not day.target_exposure_pct.is_finite()
                   for day in policy.days)):
        return _unknown("TERMINAL_POLICY_REPLAY_UNKNOWN")
    prior_issues = policy.issues
    if (not isinstance(daily_versions, tuple)
            or len(daily_versions) != len(policy.days)
            or any(not isinstance(step, DailyVersionStep)
                   or type(step.trade_date) is not date
                   or type(step.before) is not int
                   or type(step.after) is not int
                   or step.before < 1 or step.after - step.before not in (0, 1)
                   for step in daily_versions)
            or tuple(step.trade_date for step in daily_versions)
               != tuple(day.trade_date for day in policy.days)
            or any(daily_versions[index].before < daily_versions[index - 1].after
                   for index in range(1, len(daily_versions)))
            or any((step.after > step.before) !=
                   (day.reason_code != "SUSPENDED_SESSION")
                   for step, day in zip(daily_versions, policy.days))):
        return _unknown("TERMINAL_REAL_DAILY_VERSIONS_INVALID", *prior_issues)
    real_dates = tuple(step.trade_date for step in daily_versions)
    if (type(terminal_open_session_date) is date
            and terminal_open_session_date <= real_dates[-1]):
        return _unknown("TERMINAL_COMPLETION_NOT_AFTER_REAL_DAY", *prior_issues)
    if (real_dates != tuple(sorted(set(real_dates)))
            or type(terminal_open_session_date) is not date
            or not isinstance(calendar_dates, tuple)
            or any(type(day) is not date for day in calendar_dates)
            or calendar_dates != (*real_dates, terminal_open_session_date)
            or calendar_dates != tuple(sorted(set(calendar_dates)))
            or not isinstance(calendar_source_ref, str)
            or not calendar_source_ref.strip()):
        return _unknown("TERMINAL_OPEN_SESSION_CALENDAR_UNKNOWN", *prior_issues)
    if (not isinstance(frozen_closing_intent, FrozenClosingIntent)
            or not isinstance(frozen_closing_intent.intent_id, UUID)
            or type(frozen_closing_intent.trade_date) is not date
            or type(frozen_closing_intent.state_version) is not int
            or frozen_closing_intent.state_version < 1
            or not isinstance(frozen_closing_intent.target_shares, Decimal)
            or not frozen_closing_intent.target_shares.is_finite()
            or frozen_closing_intent.target_shares != _ZERO
            or not isinstance(frozen_closing_intent.reason_code, str)
            or frozen_closing_intent.reason_code not in CLOSING_EXIT_REASONS
            or not isinstance(frozen_closing_intent.source_ref, str)
            or not frozen_closing_intent.source_ref.strip()):
        return _unknown("TERMINAL_FROZEN_EXIT_INVALID", *prior_issues)
    if (not isinstance(completions, tuple)
            or any(not isinstance(item, IntentCompletion)
                   or not isinstance(item.intent_id, UUID)
                   or not isinstance(item.fill_event_id, UUID)
                   or type(item.completed_on) is not date
                   or not isinstance(item.effective_at, datetime)
                   or item.effective_at.tzinfo is None
                   or item.effective_at.utcoffset() is None
                   or not isinstance(item.reason_code, str)
                   for item in completions)):
        return _unknown("TERMINAL_COMPLETION_SET_INVALID", *prior_issues)
    closing = tuple(item for item in completions
                    if item.reason_code in CLOSING_EXIT_REASONS)
    if len(closing) != 1:
        return _unknown("TERMINAL_CLOSING_COMPLETION_NOT_UNIQUE", *prior_issues)
    completion = closing[0]
    if (any(item.completed_on not in real_dates for item in completions
            if item is not completion)
            or completion.intent_id != frozen_closing_intent.intent_id
            or completion.reason_code != frozen_closing_intent.reason_code
            or completion.completed_on != terminal_open_session_date
            or completion.effective_at.astimezone(_CN_TZ).date()
               != completion.completed_on):
        return _unknown("TERMINAL_COMPLETION_BINDING_MISMATCH", *prior_issues)
    if (not isinstance(final_fill_step, FillVersionStep)
            or not isinstance(final_fill_step.fill_event_id, UUID)
            or final_fill_step.fill_event_id != completion.fill_event_id
            or type(final_fill_step.before) is not int
            or type(final_fill_step.after) is not int
            or type(current_version) is not int
            or final_fill_step.after != final_fill_step.before + 1
            or final_fill_step.after != current_version):
        return _unknown("TERMINAL_FILL_VERSION_UNATTRIBUTED", *prior_issues)
    if (terminal_fill_steps is None and terminal_event_states is None
            and pre_terminal_quantity is None):
        if final_fill_step.before != daily_versions[-1].after:
            return _unknown("TERMINAL_FILL_VERSION_UNATTRIBUTED", *prior_issues)
    else:
        if (not isinstance(terminal_fill_steps, tuple)
                or not isinstance(terminal_event_states, tuple)
                or not terminal_fill_steps
                or len(terminal_fill_steps) != len(terminal_event_states)
                or not isinstance(pre_terminal_quantity, Decimal)
                or not pre_terminal_quantity.is_finite()
                or pre_terminal_quantity <= _ZERO
                or terminal_fill_steps[-1] != final_fill_step):
            return _unknown("TERMINAL_FILL_SEQUENCE_INVALID", *prior_issues)
        cursor = daily_versions[-1].after
        remaining = pre_terminal_quantity
        previous_at: datetime | None = None
        seen_ids: set[UUID] = set()
        for index, (step, state) in enumerate(zip(
                terminal_fill_steps, terminal_event_states)):
            if (not isinstance(step, FillVersionStep)
                    or not isinstance(step.fill_event_id, UUID)
                    or step.fill_event_id in seen_ids
                    or type(step.before) is not int
                    or type(step.after) is not int
                    or step.before != cursor or step.after != cursor + 1):
                return _unknown("TERMINAL_FILL_VERSION_UNATTRIBUTED", *prior_issues)
            if (not isinstance(state, AccountingEventState)
                    or state.event_id != step.fill_event_id
                    or state.side != "SELL"
                    or state.intent_id != frozen_closing_intent.intent_id
                    or not isinstance(state.effective_at, datetime)
                    or state.effective_at.tzinfo is None
                    or state.effective_at.utcoffset() is None
                    or state.effective_at.astimezone(_CN_TZ).date()
                       != terminal_open_session_date):
                return _unknown("TERMINAL_FILL_BINDING_MISMATCH", *prior_issues)
            if previous_at is not None and state.effective_at <= previous_at:
                return _unknown("TERMINAL_FILL_CHRONOLOGY_INVALID", *prior_issues)
            if (not isinstance(state.quantity, Decimal)
                    or not state.quantity.is_finite()
                    or state.quantity < _ZERO
                    or state.quantity >= remaining
                    or (state.quantity == _ZERO) !=
                       (index == len(terminal_event_states) - 1)):
                return _unknown("TERMINAL_FILL_ACCOUNTING_INVALID", *prior_issues)
            seen_ids.add(step.fill_event_id)
            cursor = step.after
            remaining = state.quantity
            previous_at = state.effective_at
        if cursor != current_version:
            return _unknown("TERMINAL_FILL_VERSION_UNATTRIBUTED", *prior_issues)
        if completion.effective_at != terminal_event_states[-1].effective_at:
            return _unknown("TERMINAL_COMPLETION_BINDING_MISMATCH", *prior_issues)
    try:
        anchor_index = real_dates.index(frozen_closing_intent.trade_date)
    except ValueError:
        return _unknown("TERMINAL_EXIT_DECISION_DAY_MISSING", *prior_issues)
    anchor_day = policy.days[anchor_index]
    anchor_step = daily_versions[anchor_index]
    if (anchor_step.after != frozen_closing_intent.state_version
            or anchor_step.after != anchor_step.before + 1
            or anchor_day.reason_code != frozen_closing_intent.reason_code
            or anchor_day.target_shares != _ZERO
            or anchor_day.target_exposure_pct != _ZERO
            or anchor_day.phase != "EXIT_PENDING"):
        return _unknown("TERMINAL_EXIT_DECISION_NOT_FROZEN", *prior_issues)
    if (policy.pending_exit_reason != frozen_closing_intent.reason_code
            or policy.days[-1].phase != "EXIT_PENDING"
            or not isinstance(policy.final_state.target_exposure_pct, Decimal)
            or policy.final_state.target_exposure_pct != _ZERO
            or any(day.target_shares != _ZERO
                   or day.target_exposure_pct != _ZERO
                   or day.phase != "EXIT_PENDING"
                   for day in policy.days[anchor_index:])):
        return _unknown("TERMINAL_EXIT_NO_LONGER_PENDING", *prior_issues)
    processed_dates = tuple(step.trade_date for step in daily_versions
                            if step.after > step.before)
    return TerminalPolicyEventDiagnosis(
        "PROVISIONAL", completion.fill_event_id, terminal_open_session_date,
        "CLOSED", _ZERO, _ZERO, processed_dates[-1], current_version,
        (*prior_issues, "TERMINAL_CALENDAR_SOURCE_UNCERTIFIED",
         "TERMINAL_FILL_SOURCE_UNCERTIFIED",
         "TERMINAL_FROZEN_INTENT_SOURCE_UNCERTIFIED"),
    )
