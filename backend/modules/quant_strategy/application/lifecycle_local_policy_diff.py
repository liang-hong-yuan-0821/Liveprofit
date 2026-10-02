"""Run provisional daily policy and compare its covered current fields."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from backend.modules.quant_strategy.domain.management_policies import ManagementPolicy
from .lifecycle_accounting_replay import (
    AccountingEventState, AccountingReplay, project_daily_quantities,
)
from .lifecycle_local_policy_frames import LocalPolicyFrames
from .lifecycle_policy_replay import PolicyReplay, replay_lifecycle_policy
from .lifecycle_projection_diff import (
    CurrentLifecycleProjection, LifecycleProjectionDiff, ProjectionFieldDiff,
    compare_lifecycle_projection,
)
from .position_lifecycle_manager import (
    ExpectationStateInput, LifecycleStateInput, TrailingStateInput,
)


_CN_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class LocalPolicyDiffDiagnosis:
    status: Literal["PROVISIONAL_MATCH", "PROVISIONAL_DIFFERENCE", "UNKNOWN"]
    policy: PolicyReplay | None
    diff: LifecycleProjectionDiff | None
    issues: tuple[str, ...]


def compare_local_policy_frames(
    *, accounting: AccountingReplay, prepared: LocalPolicyFrames,
    first_fill_date: date, seed_state: LifecycleStateInput,
    seed_target_shares: Decimal, seed_trailing: TrailingStateInput | None,
    seed_expectation: ExpectationStateInput | None,
    management_policy: ManagementPolicy | None,
    calendar_dates: tuple[date, ...], calendar_source_ref: str,
    corporate_action_dates: tuple[date, ...], corporate_action_source_ref: str,
    current: CurrentLifecycleProjection,
) -> LocalPolicyDiffDiagnosis:
    """Never promote a matched provisional projection to source certification."""
    if (not isinstance(accounting, AccountingReplay)
            or not isinstance(prepared, LocalPolicyFrames)
            or not isinstance(current, CurrentLifecycleProjection)
            or accounting.status != "CALCULATED"
            or prepared.status != "PROVISIONAL" or not prepared.frames):
        return LocalPolicyDiffDiagnosis("UNKNOWN", None, None,
                                        ("LOCAL_POLICY_DIFF_INPUT_UNKNOWN",))
    if (not isinstance(calendar_dates, tuple) or not calendar_dates
            or tuple(frame.trade_date for frame in prepared.frames) != calendar_dates):
        return LocalPolicyDiffDiagnosis(
            "UNKNOWN", None, None,
            (*prepared.issues, "LOCAL_POLICY_ACCOUNTING_CALENDAR_MISMATCH"))
    if (not isinstance(accounting.event_states, tuple)
            or any(not isinstance(state, AccountingEventState)
                   or not isinstance(state.effective_at, datetime)
                   or state.effective_at.tzinfo is None
                   or state.effective_at.utcoffset() is None
                   or state.effective_at.astimezone(_CN_TZ).date() > calendar_dates[-1]
                   for state in accounting.event_states)):
        return LocalPolicyDiffDiagnosis(
            "UNKNOWN", None, None,
            (*prepared.issues, "LOCAL_POLICY_EVENTS_OUTSIDE_CALENDAR"))
    first_as_of = prepared.first_data_as_of
    if (type(prepared.starting_state_version) is not int
            or not isinstance(first_as_of, datetime)
            or first_as_of.tzinfo is None or first_as_of.utcoffset() is None
            or first_as_of.astimezone(_CN_TZ).date() != calendar_dates[0]):
        return LocalPolicyDiffDiagnosis(
            "UNKNOWN", None, None,
            (*prepared.issues, "POLICY_FIRST_VERSION_ANCHOR_UNKNOWN"))
    observed_fills = tuple(
        state for state in accounting.event_states
        if state.side in ("BUY", "SELL") and state.effective_at <= first_as_of)
    # Creation starts at version 1; each later lifecycle-linked fill advances it.
    # Event timing is only a local candidate, so an unexplained count stays unknown.
    if (not observed_fills
            or prepared.starting_state_version != len(observed_fills)):
        return LocalPolicyDiffDiagnosis(
            "UNKNOWN", None, None,
            (*prepared.issues, "POLICY_FIRST_VERSION_UNATTRIBUTED"))
    projected = project_daily_quantities(
        accounting=accounting, calendar_dates=calendar_dates)
    if (projected.status != "PROVISIONAL"
            or projected.quantities != tuple(
                (frame.trade_date, frame.actual_shares) for frame in prepared.frames)):
        return LocalPolicyDiffDiagnosis(
            "UNKNOWN", None, None,
            (*prepared.issues, *projected.issues,
             "LOCAL_POLICY_ACCOUNTING_DAILY_MISMATCH"))
    policy = replay_lifecycle_policy(
        first_fill_date=first_fill_date, seed_state=seed_state,
        seed_target_shares=seed_target_shares, seed_trailing=seed_trailing,
        seed_expectation=seed_expectation, management_policy=management_policy,
        calendar_dates=calendar_dates, calendar_source_ref=calendar_source_ref,
        corporate_action_dates=corporate_action_dates,
        corporate_action_source_ref=corporate_action_source_ref,
        frames=prepared.frames)
    if policy.status != "PROVISIONAL":
        return LocalPolicyDiffDiagnosis(
            "UNKNOWN", policy, None, (*prepared.issues, *policy.issues))
    advances = prepared.version_advances
    if (not isinstance(advances, tuple)
            or len(advances) != len(policy.days)
            or any(type(value) is not bool for value in advances)
            or advances != tuple(day.reason_code != "SUSPENDED_SESSION"
                                 for day in policy.days)):
        return LocalPolicyDiffDiagnosis(
            "UNKNOWN", policy, None,
            (*prepared.issues, "DAILY_POLICY_VERSION_ADVANCE_MISMATCH"))
    if (type(prepared.ending_state_version) is not int
            or type(current.state_version) is not int
            or prepared.ending_state_version != current.state_version):
        return LocalPolicyDiffDiagnosis(
            "UNKNOWN", policy, None,
            (*prepared.issues, "LIFECYCLE_VERSION_AFTER_DAILY_UNATTRIBUTED"))
    diff = compare_lifecycle_projection(
        accounting=accounting, policy=policy, current=current)
    processed_dates = tuple(day.trade_date for day, advanced in zip(
        policy.days, advances) if advanced)
    expected_date = processed_dates[-1] if processed_dates else None
    actual_date = current.last_processed_trade_date
    date_status = (
        "UNKNOWN" if expected_date is None or type(actual_date) is not date
        else "MATCH" if expected_date == actual_date else "DIFFERENT"
    )
    date_field = ProjectionFieldDiff(
        "lifecycle.last_processed_trade_date", expected_date,
        actual_date, date_status)
    date_issues = (("LAST_PROCESSED_DAY_UNKNOWN",)
                   if expected_date is None else
                   ("CURRENT_FIELD_UNKNOWN:lifecycle.last_processed_trade_date",)
                   if type(actual_date) is not date else ())
    fields = (*diff.fields, date_field)
    diff = LifecycleProjectionDiff(
        "UNKNOWN" if any(field.status == "UNKNOWN" for field in fields)
        else "PROVISIONAL_DIFFERENCE" if any(
            field.status == "DIFFERENT" for field in fields)
        else "PROVISIONAL_MATCH",
        fields, (*diff.issues, *date_issues))
    return LocalPolicyDiffDiagnosis(
        diff.status, policy, diff,
        (*prepared.issues, *policy.issues, *diff.issues,
         "LOCAL_POLICY_DIFF_SOURCE_UNCERTIFIED"))
