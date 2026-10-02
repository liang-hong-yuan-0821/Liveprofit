"""Read one local lifecycle snapshot and diagnose it without projection writes."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from backend.modules.quant_strategy.domain.management_policies import ManagementPolicy
from .lifecycle_accounting_replay import project_daily_quantities, replay_position_accounting
from .lifecycle_intent_replay import CLOSING_EXIT_REASONS
from .lifecycle_local_intent_bridge import combine_local_intent_inputs
from .lifecycle_local_policy_diff import compare_local_policy_frames
from .lifecycle_local_policy_frames import build_local_policy_frames
from .lifecycle_persisted_daily_inputs import load_local_daily_fact_inputs
from .lifecycle_persisted_fill_inputs import load_local_fill_replay_inputs
from .lifecycle_persisted_intent_inputs import load_local_intent_definitions
from .lifecycle_projection_diff import (
    LifecycleProjectionDiff, ProjectionFieldDiff, compare_lifecycle_projection,
    load_current_lifecycle_projection,
)
from .lifecycle_persisted_terminal import diagnose_persisted_terminal_marker
from .lifecycle_terminal_policy_event import (
    FrozenClosingIntent, diagnose_terminal_policy_event,
)
from .position_lifecycle_manager import (
    ExpectationStateInput, LifecycleStateInput, TrailingStateInput,
)
from .lifecycle_version_chain import DailyVersionStep, reconcile_local_version_chain


_CN_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class PersistedLifecycleDiagnosis:
    status: Literal["PROVISIONAL_MATCH", "PROVISIONAL_DIFFERENCE", "UNKNOWN"]
    diff: LifecycleProjectionDiff | None
    issues: tuple[str, ...]


def diagnose_persisted_lifecycle(
    session, *, portfolio_id: UUID, lifecycle_id: UUID,
    baseline_as_of: datetime, baseline_quantity: Decimal,
    baseline_total_cost: Decimal, baseline_source_ref: str,
    seed_state: LifecycleStateInput, seed_target_shares: Decimal,
    seed_trailing: TrailingStateInput | None,
    seed_expectation: ExpectationStateInput | None,
    management_policy: ManagementPolicy | None,
    calendar_dates: tuple[date, ...], calendar_source_ref: str,
    corporate_action_dates: tuple[date, ...], corporate_action_source_ref: str,
    terminal_open_session_date: date | None = None,
    terminal_calendar_dates: tuple[date, ...] | None = None,
    terminal_calendar_source_ref: str | None = None,
) -> PersistedLifecycleDiagnosis:
    """Keep all local reads under the caller's one open transaction.

    Projection rows are locked before intents and daily rows. The supplied
    baseline, seed, calendar and action coverage are declarations, not
    authenticated evidence; even a match is provisional.
    """
    current = load_current_lifecycle_projection(
        session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
    intents = load_local_intent_definitions(
        session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
    fills = load_local_fill_replay_inputs(
        session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
    daily = load_local_daily_fact_inputs(
        session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id,
        calendar_dates=calendar_dates, calendar_source_ref=calendar_source_ref)
    completion = combine_local_intent_inputs(
        intents=intents, fills=fills, baseline_as_of=baseline_as_of,
        baseline_quantity=baseline_quantity, baseline_total_cost=baseline_total_cost,
        baseline_source_ref=baseline_source_ref)
    if completion.status != "PROVISIONAL" or completion.accounting is None:
        return PersistedLifecycleDiagnosis(
            "UNKNOWN", None, (*daily.issues, *completion.issues))
    version_chain = reconcile_local_version_chain(
        fills=fills.version_steps,
        days=tuple(DailyVersionStep(day.trade_date, day.state_version_before,
                                    day.state_version_after) for day in daily.days),
        current_version=current.state_version)
    if version_chain.status != "PROVISIONAL":
        return PersistedLifecycleDiagnosis(
            "UNKNOWN", None, (*completion.issues, *daily.issues, *version_chain.issues))
    step_by_id = {step.fill_event_id: step for step in fills.version_steps}
    for day in daily.days:
        if (not isinstance(day.data_as_of, datetime)
                or day.data_as_of.tzinfo is None
                or day.data_as_of.utcoffset() is None
                or day.data_as_of.astimezone(_CN_TZ).date() != day.trade_date):
            return PersistedLifecycleDiagnosis(
                "UNKNOWN", None, (*daily.issues,
                                  f"DAILY_FACT_CUTOFF_INVALID:{day.trade_date}"))
        for fill in fills.fills:
            if fill.event_id == fills.initial_fill_id:
                if fill.executed_at > day.data_as_of:
                    return PersistedLifecycleDiagnosis(
                        "UNKNOWN", None, (*fills.issues,
                                          f"FILL_AFTER_DAILY_CUTOFF:{fill.event_id}:{day.trade_date}"))
                continue
            step = step_by_id[fill.event_id]
            effective_before_day = fill.fill_trade_date <= day.trade_date
            posted_before_day = step.after <= day.state_version_before
            if effective_before_day != posted_before_day:
                return PersistedLifecycleDiagnosis(
                    "UNKNOWN", None, (*fills.issues,
                                      f"FILL_DAILY_VERSION_ORDER_CONFLICT:{fill.event_id}:{day.trade_date}"))
            if (fill.fill_trade_date == day.trade_date
                    and fill.executed_at > day.data_as_of):
                return PersistedLifecycleDiagnosis(
                    "UNKNOWN", None, (*fills.issues,
                                      f"FILL_AFTER_DAILY_CUTOFF:{fill.event_id}:{day.trade_date}"))
    if (terminal_open_session_date is not None
            or terminal_calendar_dates is not None
            or terminal_calendar_source_ref is not None):
        return _diagnose_persisted_terminal_policy(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id,
            current=current, intents=intents, fills=fills, daily=daily,
            completion=completion, version_chain=version_chain,
            baseline_as_of=baseline_as_of, baseline_quantity=baseline_quantity,
            baseline_total_cost=baseline_total_cost,
            baseline_source_ref=baseline_source_ref,
            seed_state=seed_state, seed_target_shares=seed_target_shares,
            seed_trailing=seed_trailing, seed_expectation=seed_expectation,
            management_policy=management_policy, calendar_dates=calendar_dates,
            calendar_source_ref=calendar_source_ref,
            corporate_action_dates=corporate_action_dates,
            corporate_action_source_ref=corporate_action_source_ref,
            terminal_open_session_date=terminal_open_session_date,
            terminal_calendar_dates=terminal_calendar_dates,
            terminal_calendar_source_ref=terminal_calendar_source_ref)
    quantities = project_daily_quantities(
        accounting=completion.accounting, calendar_dates=calendar_dates)
    prepared = build_local_policy_frames(
        daily=daily, quantities=quantities, intents=completion,
        version_chain=version_chain)
    if prepared.status != "PROVISIONAL":
        return PersistedLifecycleDiagnosis("UNKNOWN", None, prepared.issues)
    first_fill_date = fills.fills[0].fill_trade_date
    compared = compare_local_policy_frames(
        accounting=completion.accounting, prepared=prepared,
        first_fill_date=first_fill_date, seed_state=seed_state,
        seed_target_shares=seed_target_shares, seed_trailing=seed_trailing,
        seed_expectation=seed_expectation, management_policy=management_policy,
        calendar_dates=calendar_dates, calendar_source_ref=calendar_source_ref,
        corporate_action_dates=corporate_action_dates,
        corporate_action_source_ref=corporate_action_source_ref,
        current=current)
    return PersistedLifecycleDiagnosis(
        compared.status, compared.diff,
        (*compared.issues, "POLICY_SEED_SOURCE_UNCERTIFIED"))


def _diagnose_persisted_terminal_policy(
    session, *, portfolio_id, lifecycle_id, current, intents, fills, daily,
    completion, version_chain, baseline_as_of, baseline_quantity,
    baseline_total_cost, baseline_source_ref, seed_state, seed_target_shares,
    seed_trailing, seed_expectation, management_policy, calendar_dates,
    calendar_source_ref, corporate_action_dates, corporate_action_source_ref,
    terminal_open_session_date, terminal_calendar_dates,
    terminal_calendar_source_ref,
) -> PersistedLifecycleDiagnosis:
    """Compare a true final fill separately from the preceding daily facts."""
    issues = (*daily.issues, *completion.issues, *version_chain.issues)
    closing = tuple(item for item in completion.completions
                    if item.reason_code in CLOSING_EXIT_REASONS)
    if (len(closing) != 1 or not fills.fills or not daily.days
            or fills.fills[-1].event_id != closing[0].fill_event_id):
        return PersistedLifecycleDiagnosis(
            "UNKNOWN", None, (*issues, "TERMINAL_COMPLETION_SET_UNKNOWN"))
    final = closing[0]
    last_daily_date = daily.days[-1].trade_date
    segment_start = next((index for index, fill in enumerate(fills.fills)
                          if fill.fill_trade_date > last_daily_date),
                         len(fills.fills))
    prefix_fills = fills.fills[:segment_start]
    terminal_fills = fills.fills[segment_start:]
    terminal_ids = {fill.event_id for fill in terminal_fills}
    step_by_id = {step.fill_event_id: step for step in fills.version_steps}
    terminal_steps = tuple(step_by_id.get(fill.event_id) for fill in terminal_fills)
    definitions = tuple(item for item in intents.definitions
                        if item.intent_id == final.intent_id)
    if (not prefix_fills or not terminal_fills
            or any(step is None for step in terminal_steps)
            or len(terminal_ids) != len(terminal_fills)
            or len(definitions) != 1):
        return PersistedLifecycleDiagnosis(
            "UNKNOWN", None, (*issues, "TERMINAL_FROZEN_IDENTITY_UNKNOWN"))
    final_step = terminal_steps[-1]
    if definitions[0].current_status != "COMPLETED":
        return PersistedLifecycleDiagnosis(
            "UNKNOWN", None, (*issues, "TERMINAL_CLOSING_INTENT_NOT_COMPLETED"))
    prefix_accounting = replay_position_accounting(
        baseline_as_of=baseline_as_of, baseline_quantity=baseline_quantity,
        baseline_total_cost=baseline_total_cost,
        baseline_source_ref=baseline_source_ref, fills=prefix_fills)
    if prefix_accounting.status != "CALCULATED" or prefix_accounting.quantity <= 0:
        return PersistedLifecycleDiagnosis(
            "UNKNOWN", None, (*issues, *prefix_accounting.issues,
                              "TERMINAL_PREFIX_ACCOUNTING_UNKNOWN"))
    prefix_chain = reconcile_local_version_chain(
        fills=tuple(step for step in fills.version_steps
                    if step.fill_event_id not in terminal_ids),
        days=tuple(DailyVersionStep(day.trade_date, day.state_version_before,
                                    day.state_version_after) for day in daily.days),
        current_version=terminal_steps[0].before)
    if prefix_chain.status != "PROVISIONAL":
        return PersistedLifecycleDiagnosis(
            "UNKNOWN", None, (*issues, *prefix_chain.issues))
    prefix_completions = replace(
        completion, accounting=prefix_accounting,
        completions=tuple(item for item in completion.completions
                          if item.intent_id != final.intent_id))
    quantities = project_daily_quantities(
        accounting=prefix_accounting, calendar_dates=calendar_dates)
    prepared = build_local_policy_frames(
        daily=daily, quantities=quantities, intents=prefix_completions,
        version_chain=prefix_chain)
    if prepared.status != "PROVISIONAL":
        return PersistedLifecycleDiagnosis("UNKNOWN", None, (*issues, *prepared.issues))
    # The complete chain already reconciles the locked current version. This
    # prefix comparison uses the version immediately before the final fill so
    # the existing daily policy checks can run over real daily facts only.
    compared_prefix = compare_local_policy_frames(
        accounting=prefix_accounting, prepared=prepared,
        first_fill_date=fills.fills[0].fill_trade_date,
        seed_state=seed_state, seed_target_shares=seed_target_shares,
        seed_trailing=seed_trailing, seed_expectation=seed_expectation,
        management_policy=management_policy, calendar_dates=calendar_dates,
        calendar_source_ref=calendar_source_ref,
        corporate_action_dates=corporate_action_dates,
        corporate_action_source_ref=corporate_action_source_ref,
        current=replace(current, state_version=terminal_steps[0].before))
    if (compared_prefix.status == "UNKNOWN" or compared_prefix.policy is None
            or compared_prefix.policy.status != "PROVISIONAL"):
        return PersistedLifecycleDiagnosis(
            "UNKNOWN", None, (*issues, *compared_prefix.issues,
                              "TERMINAL_PREFIX_POLICY_UNKNOWN"))
    definition = definitions[0]
    event = diagnose_terminal_policy_event(
        policy=compared_prefix.policy, completions=completion.completions,
        frozen_closing_intent=FrozenClosingIntent(
            intent_id=definition.intent_id, trade_date=definition.trade_date,
            state_version=definition.state_version,
            target_shares=definition.target_shares,
            reason_code=definition.reason_code,
            source_ref=f"local-intent-revision:{definition.initial_revision_id}"),
        daily_versions=tuple(DailyVersionStep(
            day.trade_date, day.state_version_before, day.state_version_after)
            for day in daily.days),
        final_fill_step=final_step, current_version=current.state_version,
        terminal_fill_steps=terminal_steps,
        terminal_event_states=tuple(
            state for state in completion.accounting.event_states
            if state.event_id in terminal_ids),
        pre_terminal_quantity=prefix_accounting.quantity,
        terminal_open_session_date=terminal_open_session_date,
        calendar_dates=terminal_calendar_dates,
        calendar_source_ref=terminal_calendar_source_ref)
    if event.status != "PROVISIONAL":
        return PersistedLifecycleDiagnosis("UNKNOWN", None,
                                           (*issues, *event.issues))
    marker = diagnose_persisted_terminal_marker(
        session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id,
        baseline_as_of=baseline_as_of, baseline_quantity=baseline_quantity,
        baseline_total_cost=baseline_total_cost,
        baseline_source_ref=baseline_source_ref,
        corporate_action_dates=corporate_action_dates,
        corporate_action_source_ref=corporate_action_source_ref)
    if (marker.status == "UNKNOWN"
            or marker.closing_fill_event_id != event.closing_fill_event_id):
        return PersistedLifecycleDiagnosis(
            "UNKNOWN", None, (*issues, *event.issues, *marker.issues,
                              "TERMINAL_MARKER_CHAIN_UNKNOWN"))
    base_diff = compare_lifecycle_projection(
        accounting=completion.accounting, policy=compared_prefix.policy,
        current=current)
    # The position row retains the last numeric average cost after a full
    # SELL. With zero shares there is no current per-share cost to compare.
    fields = tuple(
        ProjectionFieldDiff(
            field.field, "CLOSED", field.current,
            "MATCH" if field.current == "CLOSED" else "DIFFERENT")
        if field.field == "lifecycle.phase" else field
        for field in base_diff.fields
        if field.field != "position.average_cost")
    fields += (
        ProjectionFieldDiff(
            "lifecycle.closed_at", "aware closing marker", current.closed_at,
            marker.closed_marker_status),
        ProjectionFieldDiff(
            "lifecycle.last_processed_trade_date",
            event.last_processed_trade_date, current.last_processed_trade_date,
            "MATCH" if current.last_processed_trade_date == event.last_processed_trade_date
            else "UNKNOWN" if current.last_processed_trade_date is None
            else "DIFFERENT"),
    )
    diff = LifecycleProjectionDiff(
        "UNKNOWN" if any(field.status == "UNKNOWN" for field in fields)
        else "PROVISIONAL_DIFFERENCE" if any(
            field.status == "DIFFERENT" for field in fields)
        else "PROVISIONAL_MATCH",
        fields, base_diff.issues)
    return PersistedLifecycleDiagnosis(
        diff.status, diff,
        (*issues, *prepared.issues, *compared_prefix.issues,
         *event.issues, *marker.issues, *diff.issues,
         "POLICY_SEED_SOURCE_UNCERTIFIED"))
