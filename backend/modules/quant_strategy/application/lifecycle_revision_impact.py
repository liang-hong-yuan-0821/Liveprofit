"""Classify local lifecycle fill revisions without replaying causal history.

The caller must independently prove that the supplied rows form the complete
posted report, applied resolution, and original lifecycle fill sets. This
pure classifier does not certify broker provenance or authorize a write.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from .lifecycle_fill_resolution_chain import (
    AppliedFillResolution, PostedReportFill, resolve_effective_fill_chain,
)


_CN_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class FrozenReportFill:
    """One posted report and its immutable 0047 fill-time ownership."""

    report_id: UUID
    fill_event_id: UUID
    executed_at: datetime
    fill_trade_date: date
    order_id: UUID
    lifecycle_id_at_fill: UUID | None
    intent_id_at_fill: UUID | None
    binding_origin: str


@dataclass(frozen=True)
class RevisionDeclaration:
    resolution_id: UUID
    report_id: UUID
    action: Literal["CORRECT", "VOID"]
    replacement_report_id: UUID | None


@dataclass(frozen=True)
class RevisionImpactPath:
    root_report_id: UUID
    root_fill_event_id: UUID
    report_ids: tuple[UUID, ...]
    declarations: tuple[RevisionDeclaration, ...]
    terminal_report_id: UUID | None
    terminal_fill_event_id: UUID | None
    kind: Literal[
        "INITIAL_ANCHOR_CORRECT", "INITIAL_ANCHOR_VOID",
        "LATER_FILL_CORRECT", "LATER_FILL_VOID",
    ]
    earliest_executed_at: datetime


@dataclass(frozen=True)
class LifecycleRevisionImpact:
    status: Literal["NO_REVISION", "LOCAL_IMPACT", "UNKNOWN"]
    paths: tuple[RevisionImpactPath, ...]
    earliest_impact_at: datetime | None
    issues: tuple[str, ...]


def classify_lifecycle_revision_impact(
    *, lifecycle_id: UUID, initial_fill_id: UUID,
    original_fill_event_ids: tuple[UUID, ...],
    postings: tuple[PostedReportFill, ...],
    resolutions: tuple[AppliedFillResolution, ...],
    frozen_fills: tuple[FrozenReportFill, ...],
) -> LifecycleRevisionImpact:
    """Map each changed report path to its original lifecycle fill.

    The effective event tuple is intentionally not paired by index with graph
    paths: a VOID path contributes no effective event. An initial anchor
    change remains only a classified impact; no replacement seed is inferred.
    """
    def unknown(*issues: str) -> LifecycleRevisionImpact:
        return LifecycleRevisionImpact("UNKNOWN", (), None, tuple(issues))

    def valid_frozen_time(row: FrozenReportFill) -> bool:
        if (not isinstance(row.executed_at, datetime)
                or row.executed_at.tzinfo is None
                or type(row.fill_trade_date) is not date):
            return False
        try:
            return (row.executed_at.utcoffset() is not None
                    and row.executed_at.astimezone(_CN_TZ).date()
                    == row.fill_trade_date)
        except (OverflowError, ValueError):
            return False

    graph = resolve_effective_fill_chain(postings=postings, resolutions=resolutions)
    if graph.status != "PROVISIONAL":
        return unknown(*graph.issues)
    if (not isinstance(lifecycle_id, UUID)
            or not isinstance(initial_fill_id, UUID)
            or not isinstance(original_fill_event_ids, tuple)
            or not original_fill_event_ids
            or any(not isinstance(item, UUID) for item in original_fill_event_ids)
            or len(set(original_fill_event_ids)) != len(original_fill_event_ids)
            or initial_fill_id not in original_fill_event_ids):
        return unknown(*graph.issues, "REVISION_ORIGINAL_FILL_SET_INVALID")
    if (not isinstance(frozen_fills, tuple)
            or any(not isinstance(row, FrozenReportFill)
                   or not isinstance(row.report_id, UUID)
                   or not isinstance(row.fill_event_id, UUID)
                   or not isinstance(row.order_id, UUID)
                   or (row.lifecycle_id_at_fill is not None
                       and not isinstance(row.lifecycle_id_at_fill, UUID))
                   or (row.intent_id_at_fill is not None
                       and not isinstance(row.intent_id_at_fill, UUID))
                   or row.binding_origin != "LIVE"
                   or not valid_frozen_time(row)
                   for row in frozen_fills)):
        return unknown(*graph.issues, "REVISION_FROZEN_FILL_INVALID")

    by_posting = {row.report_id: row.fill_event_id for row in postings}
    by_report = {row.report_id: row for row in frozen_fills}
    if (len(by_report) != len(frozen_fills)
            or set(by_report) != set(by_posting)
            or any(row.fill_event_id != by_posting[row.report_id]
                   for row in frozen_fills)
            or len({row.fill_event_id for row in frozen_fills}) != len(frozen_fills)):
        return unknown(*graph.issues, "REVISION_FROZEN_FILL_SET_MISMATCH")

    roots = tuple(by_report[path[0]].fill_event_id for path in graph.report_paths)
    original_set = set(original_fill_event_ids)
    if (len(roots) != len(original_fill_event_ids)
            or set(roots) != original_set
            or any(by_report[report_id].fill_event_id in original_set
                   for path in graph.report_paths for report_id in path[1:])):
        return unknown(*graph.issues, "REVISION_ROOT_SET_MISMATCH")

    by_resolution = {row.report_id: row for row in resolutions}
    changed: list[RevisionImpactPath] = []
    for path in graph.report_paths:
        root = by_report[path[0]]
        initial = root.fill_event_id == initial_fill_id
        if initial:
            # The initial order can be unbound at its first fill. A later
            # replacement may be bound to this lifecycle, but never another.
            if root.lifecycle_id_at_fill not in (None, lifecycle_id):
                return unknown(*graph.issues, "REVISION_INITIAL_ANCHOR_MISBOUND")
        elif (root.lifecycle_id_at_fill != lifecycle_id
              or not isinstance(root.intent_id_at_fill, UUID)):
            return unknown(*graph.issues, "REVISION_LATER_ROOT_BINDING_INVALID")

        for report_id in path[1:]:
            replacement = by_report[report_id]
            if replacement.order_id != root.order_id:
                return unknown(*graph.issues, "REVISION_ORDER_MIGRATION")
            if initial:
                if replacement.lifecycle_id_at_fill not in (None, lifecycle_id):
                    return unknown(*graph.issues, "REVISION_INITIAL_ANCHOR_MISBOUND")
            elif (replacement.lifecycle_id_at_fill != lifecycle_id
                  or replacement.intent_id_at_fill != root.intent_id_at_fill):
                return unknown(*graph.issues, "REVISION_LATER_BINDING_MIGRATION")

        declarations = tuple(RevisionDeclaration(
            by_resolution[report_id].resolution_id,
            by_resolution[report_id].report_id,
            by_resolution[report_id].action,
            by_resolution[report_id].replacement_report_id,
        ) for report_id in path if report_id in by_resolution)
        if not declarations:
            continue
        final_action = declarations[-1].action
        terminal = by_report[path[-1]] if final_action == "CORRECT" else None
        changed.append(RevisionImpactPath(
            root_report_id=root.report_id,
            root_fill_event_id=root.fill_event_id,
            report_ids=path,
            declarations=declarations,
            terminal_report_id=terminal.report_id if terminal else None,
            terminal_fill_event_id=terminal.fill_event_id if terminal else None,
            kind=("INITIAL_ANCHOR_" if initial else "LATER_FILL_") + final_action,
            earliest_executed_at=min(by_report[report_id].executed_at
                                     for report_id in path),
        ))

    if not changed:
        return LifecycleRevisionImpact(
            "NO_REVISION", (), None,
            (*graph.issues, "BROKER_FILL_SET_UNCERTIFIED",
             "LOCAL_CAUSAL_HISTORY_NOT_REPLAYED"))
    return LifecycleRevisionImpact(
        "LOCAL_IMPACT", tuple(changed),
        min(path.earliest_executed_at for path in changed),
        (*graph.issues, "BROKER_FILL_SET_UNCERTIFIED",
         "LOCAL_CAUSAL_HISTORY_NOT_REPLAYED"),
    )
