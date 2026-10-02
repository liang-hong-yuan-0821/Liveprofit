"""Index local facts potentially affected by a proved fill revision.

This is a read-only inventory, not a revised lifecycle or historical as-of
replay. Every returned ID is a current local row or an immutable old write.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select

from backend.modules.investment_workspace.infrastructure.fill_report_models import AccountFillPostingRow
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    FillLifecycleVersionStep, OrderFillEvent, PositionDailyFact,
    PositionDailyFactRevision, PositionIntent, PositionLifecycleState,
)

from .lifecycle_persisted_intent_inputs import load_local_intent_definitions
from .lifecycle_persisted_revision_impact import load_local_lifecycle_revision_impact
from .lifecycle_replay_inventory import inventory_lifecycle_replay
from .lifecycle_version_chain import (
    DailyVersionStep, FillVersionStep, reconcile_local_version_chain,
)


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_UNCERTIFIED = (
    "GLOBAL_TRADING_CALENDAR_UNCERTIFIED",
    "HISTORICAL_SOURCE_AVAILABILITY_UNCERTIFIED",
)


def _old_local_version_bounds_valid(
    facts: list[PositionDailyFact], steps: tuple[OriginalCausalStep, ...],
    current_version: int,
) -> bool:
    """Check old numeric bounds only; infer no revised or event-time chain."""
    if type(current_version) is not int or current_version < 1:
        return False
    if facts:
        checked = reconcile_local_version_chain(
            fills=tuple(FillVersionStep(step.root_fill_event_id,
                                        step.version_before, step.version_after)
                        for step in steps),
            days=tuple(DailyVersionStep(fact.trade_date,
                                        fact.state_version_before,
                                        fact.state_version_after)
                       for fact in facts),
            current_version=current_version,
        )
        return checked.status == "PROVISIONAL"
    cursor = 1  # Initial fill creates version 1 and has no 0048 step.
    for step in steps:
        if step.version_before != cursor:
            return False
        cursor = step.version_after
    return cursor == current_version


@dataclass(frozen=True)
class AffectedDailyFact:
    trade_date: date
    fact_id: UUID
    current_revision_id: UUID


@dataclass(frozen=True)
class AffectedIntent:
    intent_id: UUID
    first_live_revision_id: UUID


@dataclass(frozen=True)
class OriginalCausalStep:
    root_fill_event_id: UUID
    version_before: int
    version_after: int


@dataclass(frozen=True)
class LifecycleRevisionImpactSurface:
    status: Literal["LOCAL_IMPACT", "NO_REVISION", "UNKNOWN"]
    impact_date: date | None
    daily_state: Literal["PRESENT", "LOCAL_EMPTY", "UNKNOWN"]
    daily_facts: tuple[AffectedDailyFact, ...]
    intent_state: Literal["PRESENT", "LOCAL_EMPTY", "UNKNOWN"]
    intents: tuple[AffectedIntent, ...]
    old_step_state: Literal["PRESENT", "LOCAL_EMPTY", "UNKNOWN"]
    original_root_fill_ids: tuple[UUID, ...]
    old_steps: tuple[OriginalCausalStep, ...]
    issues: tuple[str, ...]


def load_local_lifecycle_revision_impact_surface(
    session, *, portfolio_id: UUID, lifecycle_id: UUID,
) -> LifecycleRevisionImpactSurface:
    """Read the whole local impact set under portfolio/lifecycle/order locks.

    The 3am adapter acquires the parent locks and proves the signed revision
    stream first. All downstream lists are discarded if any local chain fails.
    """
    def unknown(*issues: str) -> LifecycleRevisionImpactSurface:
        return LifecycleRevisionImpactSurface(
            "UNKNOWN", None, "UNKNOWN", (), "UNKNOWN", (), "UNKNOWN", (), (),
            (*issues, *_UNCERTIFIED),
        )

    if session.new or session.dirty or session.deleted:
        return unknown("REVISION_SURFACE_DIRTY_SESSION")
    impact = load_local_lifecycle_revision_impact(
        session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
    if impact.status == "UNKNOWN":
        return unknown(*impact.issues)
    if impact.status == "NO_REVISION":
        return LifecycleRevisionImpactSurface(
            "NO_REVISION", None, "LOCAL_EMPTY", (), "LOCAL_EMPTY", (),
            "LOCAL_EMPTY", (), (), (*impact.issues, *_UNCERTIFIED),
        )
    if impact.earliest_impact_at is None or not impact.paths:
        return unknown(*impact.issues, "REVISION_SURFACE_IMPACT_MISSING")
    try:
        impact_date = impact.earliest_impact_at.astimezone(_SHANGHAI).date()
        inventory = inventory_lifecycle_replay(session, portfolio_id, lifecycle_id)
    except (ValueError, OverflowError) as exc:
        return unknown(*impact.issues, f"REVISION_SURFACE_INVENTORY_UNAVAILABLE:{exc}")

    # The 3a inventory already locks all current daily rows and validates
    # every 0044 predecessor/current link plus all pending input proposals.
    daily_blockers = tuple(issue for issue in inventory.issues
                           if issue.startswith(("DAILY_", "PROCESSED_DAY_"))
                           and issue != "DAILY_FACT_CALENDAR_COVERAGE_UNCERTIFIED")
    if daily_blockers or inventory.input_proposal_ids:
        return unknown(*impact.issues, *daily_blockers,
                       "REVISION_SURFACE_DAILY_CHAIN_UNKNOWN")
    facts = list(session.scalars(select(PositionDailyFact).where(
        PositionDailyFact.lifecycle_id == lifecycle_id,
    ).order_by(PositionDailyFact.trade_date, PositionDailyFact.id)
        .with_for_update().execution_options(populate_existing=True)))
    if ({row.id for row in facts} != set(inventory.daily_fact_ids)
            or len(facts) != len(inventory.daily_fact_ids)
            or len({row.trade_date for row in facts}) != len(facts)):
        return unknown(*impact.issues, "REVISION_SURFACE_DAILY_SET_CHANGED")
    revisions = list(session.scalars(select(PositionDailyFactRevision).where(
        PositionDailyFactRevision.daily_fact_id.in_(inventory.daily_fact_ids),
    ).order_by(PositionDailyFactRevision.daily_fact_id,
               PositionDailyFactRevision.revision_no))) if facts else []
    latest_daily = {row.daily_fact_id: row for row in revisions}
    if set(latest_daily) != {row.id for row in facts}:
        return unknown(*impact.issues, "REVISION_SURFACE_DAILY_REVISION_SET_CHANGED")
    affected_daily = tuple(AffectedDailyFact(
        fact.trade_date, fact.id, latest_daily[fact.id].id)
        for fact in facts if fact.trade_date >= impact_date)

    # The parent lifecycle row is still locked. Lock *all* intent current rows,
    # including ones dated before impact: later fill completion can change them.
    intent_rows = list(session.scalars(select(PositionIntent).where(
        PositionIntent.lifecycle_id == lifecycle_id,
    ).order_by(PositionIntent.id).with_for_update()
        .execution_options(populate_existing=True)))
    if intent_rows:
        definitions = load_local_intent_definitions(
            session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
        if (definitions.status != "LOCAL_CANDIDATE"
                or len(definitions.definitions) != len(intent_rows)
                or {row.intent_id for row in definitions.definitions}
                   != {row.id for row in intent_rows}):
            return unknown(*impact.issues, *definitions.issues,
                           "REVISION_SURFACE_INTENT_CHAIN_UNKNOWN")
        affected_intents = tuple(AffectedIntent(
            definition.intent_id, definition.initial_revision_id)
            for definition in definitions.definitions)
    else:
        affected_intents = ()

    # 3am has already independently proved the complete report graph and its
    # original roots. Here we rederive the old root IDs from the entire scoped
    # order stream, removing only frozen replacement CONFIRMs. Changed paths
    # alone are not an independent proof of the full root set. A VOID is never
    # a root.
    replacement_reports = tuple(report_id for path in impact.paths
                                for report_id in path.report_ids[1:])
    replacement_postings = list(session.scalars(select(AccountFillPostingRow).where(
        AccountFillPostingRow.report_id.in_(replacement_reports),
    ).execution_options(populate_existing=True))) if replacement_reports else []
    if (len(replacement_postings) != len(replacement_reports)
            or {row.report_id for row in replacement_postings}
               != set(replacement_reports)):
        return unknown(*impact.issues, "REVISION_SURFACE_REPLACEMENT_SET_CHANGED")
    replacement_ids = {row.fill_event_id for row in replacement_postings}
    events = list(session.scalars(select(OrderFillEvent).where(
        OrderFillEvent.id.in_(inventory.fill_event_ids),
    ).execution_options(populate_existing=True)))
    if len(events) != len(inventory.fill_event_ids) or {
            row.id for row in events} != set(inventory.fill_event_ids):
        return unknown(*impact.issues, "REVISION_SURFACE_EVENT_SET_CHANGED")
    original_ids = {row.id for row in events if row.event_type == "CONFIRM"} - replacement_ids
    if (not original_ids or inventory.initial_fill_id not in original_ids
            or not {path.root_fill_event_id for path in impact.paths} <= original_ids
            or replacement_ids & original_ids):
        return unknown(*impact.issues, "REVISION_SURFACE_ORIGINAL_ROOT_SET_INVALID")
    current_intent_ids = {row.id for row in intent_rows}
    if any(row.id in original_ids and row.id != inventory.initial_fill_id
           and (row.binding_origin != "LIVE"
                or row.lifecycle_id_at_fill != lifecycle_id
                or row.intent_id_at_fill not in current_intent_ids)
           for row in events):
        return unknown(*impact.issues, "REVISION_SURFACE_ROOT_INTENT_UNKNOWN")
    ordered_original_ids = tuple(sorted(original_ids))
    step_rows = list(session.scalars(select(FillLifecycleVersionStep).where(or_(
        FillLifecycleVersionStep.lifecycle_id == lifecycle_id,
        FillLifecycleVersionStep.fill_event_id.in_(inventory.fill_event_ids),
    )).execution_options(populate_existing=True)))
    old_step_rows = {row.fill_event_id: row for row in step_rows
                     if row.fill_event_id in original_ids}
    expected_step_ids = original_ids - {inventory.initial_fill_id}
    if (set(old_step_rows) != expected_step_ids
            or any(row.origin != "LOCAL_CAUSAL" or row.lifecycle_id != lifecycle_id
                   or type(row.version_before) is not int
                   or type(row.version_after) is not int
                   or row.version_before < 1
                   or row.version_after != row.version_before + 1
                   for row in old_step_rows.values())
            or any(row.fill_event_id not in original_ids
                   for row in step_rows)
            or len({row.version_before for row in old_step_rows.values()})
               != len(old_step_rows)):
        return unknown(*impact.issues, "REVISION_SURFACE_ORIGINAL_STEP_SET_UNKNOWN")
    old_steps = tuple(sorted((OriginalCausalStep(
        row.fill_event_id, row.version_before, row.version_after)
        for row in old_step_rows.values()), key=lambda row: row.version_before))
    lifecycle = session.get(PositionLifecycleState, lifecycle_id,
                            populate_existing=True)
    if (lifecycle is None or lifecycle.portfolio_id != portfolio_id
            or not _old_local_version_bounds_valid(
                facts, old_steps, lifecycle.state_version)):
        return unknown(*impact.issues, "REVISION_SURFACE_OLD_VERSION_BOUNDS_UNKNOWN")
    return LifecycleRevisionImpactSurface(
        "LOCAL_IMPACT", impact_date,
        "PRESENT" if affected_daily else "LOCAL_EMPTY", affected_daily,
        "PRESENT" if affected_intents else "LOCAL_EMPTY", affected_intents,
        "PRESENT" if old_steps else "LOCAL_EMPTY", ordered_original_ids,
        old_steps, (*impact.issues, *_UNCERTIFIED),
    )
