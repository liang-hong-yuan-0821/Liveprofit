"""Pure local event order for a revised lifecycle history.

This is an identity and timestamp work list. ``data_as_of`` is a market-input
cutoff, not a policy processing or transaction commit time. The old daily
version bounds, policy decisions, intent completions, and current projection
are never reused as revised facts. Local timestamps do not prove visibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from .lifecycle_accounting_replay import AccountingEventState, AccountingReplay
from .lifecycle_persisted_daily_inputs import LocalDailyFact, LocalDailyFactInputs
from .lifecycle_resolved_accounting import ResolvedAccountingReplay
from .lifecycle_revision_impact_surface import AffectedDailyFact
from .lifecycle_revision_replay_manifest import (
    RevisionReplayManifest, RevisionReplayRoot,
)


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_UNRESOLVED = (
    "CHRONOLOGY_LOCAL_ONLY",
    "HISTORICAL_VISIBILITY_UNCERTIFIED",
    "REPLAY_BLOCKED:CAUSAL_POLICY_INTENT_REPLAY_REQUIRED",
)


@dataclass(frozen=True)
class LocalEffectiveFillSlot:
    root_fill_event_id: UUID
    terminal_fill_event_id: UUID
    effective_at: datetime
    fill_trade_date: date


@dataclass(frozen=True)
class LocalDailyInputSlot:
    trade_date: date
    revision_id: UUID
    data_as_of: datetime
    effective_fill_event_ids_before_cutoff: tuple[UUID, ...]


@dataclass(frozen=True)
class RevisionChronology:
    status: Literal["LOCAL_ORDER", "UNKNOWN"]
    slots: tuple[LocalEffectiveFillSlot | LocalDailyInputSlot, ...]
    issues: tuple[str, ...]


def _shanghai_date(value: object) -> date | None:
    try:
        if (not isinstance(value, datetime) or value.tzinfo is None
                or value.utcoffset() is None):
            return None
        return value.astimezone(_SHANGHAI).date()
    except (OverflowError, TypeError, ValueError):
        return None


def build_revision_chronology(
    *, manifest: RevisionReplayManifest,
    accounting: ResolvedAccountingReplay,
    daily: LocalDailyFactInputs,
) -> RevisionChronology:
    """Interleave verified effective fills with complete local daily cutoffs.

    The caller must read the three inputs in one transaction. In particular,
    ``daily`` contains every declared daily fact, not only the affected suffix
    in ``manifest``. The cutoff only orders local market inputs against fill
    effective times; it cannot establish policy processing or commit order.
    Same-day fills at or after a cutoff make the entire work list unknown.
    """
    def unknown(*issues: str) -> RevisionChronology:
        return RevisionChronology("UNKNOWN", (), tuple(issues))

    if (not isinstance(manifest, RevisionReplayManifest)
            or not isinstance(accounting, ResolvedAccountingReplay)
            or not isinstance(daily, LocalDailyFactInputs)):
        return unknown("CHRONOLOGY_INPUT_INVALID")
    if (any(not isinstance(issues, tuple)
            or any(type(issue) is not str for issue in issues)
            for issues in (manifest.issues, accounting.issues, daily.issues))):
        return unknown("CHRONOLOGY_ISSUES_INVALID")
    inherited = (*manifest.issues, *accounting.issues, *daily.issues)
    if (manifest.status != "LOCAL_MAPPING"
            or manifest.initial_gate != "UNCHANGED_BUY_CANDIDATE"
            or type(manifest.impact_date) is not date):
        return unknown(*inherited, "CHRONOLOGY_INITIAL_OR_REVISION_UNKNOWN")
    if (accounting.status != "PROVISIONAL_ACCOUNTING"
            or not isinstance(accounting.accounting, AccountingReplay)
            or accounting.accounting.status != "CALCULATED"
            or not isinstance(accounting.accounting.issues, tuple)
            or any(type(issue) is not str for issue in accounting.accounting.issues)
            or accounting.accounting.issues
            or not isinstance(accounting.accounting.event_ids, tuple)
            or accounting.accounting.event_ids != accounting.effective_fill_event_ids):
        return unknown(*inherited, "CHRONOLOGY_ACCOUNTING_UNKNOWN")
    if (daily.status != "LOCAL_CANDIDATE"
            or not isinstance(daily.days, tuple) or not daily.days):
        return unknown(*inherited, "CHRONOLOGY_FULL_DAILY_UNKNOWN")

    roots = manifest.roots
    states = accounting.accounting.event_states
    effective_ids = accounting.effective_fill_event_ids
    if (not isinstance(roots, tuple) or not roots
            or any(not isinstance(row, RevisionReplayRoot)
                   or not isinstance(row.root_fill_event_id, UUID)
                   or (row.terminal_fill_event_id is not None
                       and not isinstance(row.terminal_fill_event_id, UUID))
                   or row.disposition not in ("UNCHANGED", "CORRECT", "VOID")
                   or (row.disposition == "VOID")
                      != (row.terminal_fill_event_id is None)
                   or (row.disposition == "UNCHANGED"
                       and row.terminal_fill_event_id != row.root_fill_event_id)
                   or (row.disposition == "CORRECT"
                       and row.terminal_fill_event_id == row.root_fill_event_id)
                   for row in roots)
            or len({row.root_fill_event_id for row in roots}) != len(roots)
            or len({row.terminal_fill_event_id for row in roots
                    if row.terminal_fill_event_id is not None})
               != sum(row.terminal_fill_event_id is not None for row in roots)
            or not isinstance(effective_ids, tuple)
            or any(not isinstance(event_id, UUID) for event_id in effective_ids)
            or not isinstance(states, tuple)
            or any(not isinstance(state, AccountingEventState) for state in states)
            or len(states) != len(effective_ids)
            or tuple(state.event_id for state in states) != effective_ids
            or len(set(effective_ids)) != len(effective_ids)):
        return unknown(*inherited, "CHRONOLOGY_ROOT_OR_EVENT_SET_INVALID")
    terminal_to_root = {
        row.terminal_fill_event_id: row.root_fill_event_id
        for row in roots if row.terminal_fill_event_id is not None
    }
    if (set(terminal_to_root) != set(effective_ids)
            or manifest.effective_root_execution_order != tuple(
                terminal_to_root[event_id] for event_id in effective_ids)
            or not states or states[0].side != "BUY"
            or terminal_to_root[effective_ids[0]] != effective_ids[0]):
        return unknown(*inherited, "CHRONOLOGY_EFFECTIVE_ORDER_MISMATCH")

    fill_slots: list[LocalEffectiveFillSlot] = []
    previous_at: datetime | None = None
    for state in states:
        at = state.effective_at
        trade_date = _shanghai_date(at)
        if (not isinstance(state.event_id, UUID)
                or state.side not in ("BUY", "SELL")
                or trade_date is None
                or (previous_at is not None and at <= previous_at)):
            return unknown(*inherited, "CHRONOLOGY_EFFECTIVE_TIME_INVALID")
        fill_slots.append(LocalEffectiveFillSlot(
            terminal_to_root[state.event_id], state.event_id, at, trade_date))
        previous_at = at

    days = daily.days
    dates: list[date] = []
    revisions: set[UUID] = set()
    for row in days:
        if (not isinstance(row, LocalDailyFact)
                or type(row.trade_date) is not date
                or not isinstance(row.revision_id, UUID)
                or row.revision_id in revisions
                or _shanghai_date(row.data_as_of) != row.trade_date
                or (dates and row.trade_date <= dates[-1])):
            return unknown(*inherited, "CHRONOLOGY_DAILY_IDENTITY_OR_CUTOFF_INVALID")
        dates.append(row.trade_date)
        revisions.add(row.revision_id)
    if fill_slots[0].effective_at >= days[0].data_as_of:
        return unknown(*inherited, "CHRONOLOGY_INITIAL_AFTER_FIRST_DAILY_CUTOFF")

    # 3e carries the current revision ID and date, but not the daily fact ID.
    # The persisted adapters must prove fact identity in the same transaction.
    affected = manifest.daily_facts_to_recompute
    expected = tuple(row for row in days
                     if row.trade_date >= manifest.impact_date)
    if (not isinstance(affected, tuple)
            or len(affected) != len(expected)
            or any(not isinstance(row, AffectedDailyFact)
                   or not isinstance(row.fact_id, UUID)
                   or not isinstance(row.current_revision_id, UUID)
                   or type(row.trade_date) is not date
                   or row.trade_date != day.trade_date
                   or row.current_revision_id != day.revision_id
                   for row, day in zip(affected, expected))
            or len({row.fact_id for row in affected}) != len(affected)):
        return unknown(*inherited, "CHRONOLOGY_AFFECTED_DAILY_MISMATCH")

    slots: list[LocalEffectiveFillSlot | LocalDailyInputSlot] = []
    cursor = 0
    for day in days:
        cutoff = day.data_as_of
        while cursor < len(fill_slots) and fill_slots[cursor].fill_trade_date <= day.trade_date:
            fill = fill_slots[cursor]
            if fill.effective_at >= cutoff:
                reason = ("CHRONOLOGY_FILL_CUTOFF_TIE" if fill.effective_at == cutoff
                          else "CHRONOLOGY_FILL_AFTER_DAILY_CUTOFF")
                return unknown(*inherited, f"{reason}:{fill.terminal_fill_event_id}:{day.trade_date}")
            slots.append(fill)
            cursor += 1
        slots.append(LocalDailyInputSlot(
            day.trade_date, day.revision_id, cutoff,
            tuple(fill.terminal_fill_event_id for fill in fill_slots[:cursor])))
    slots.extend(fill_slots[cursor:])
    return RevisionChronology("LOCAL_ORDER", tuple(slots), (*inherited, *_UNRESOLVED))
