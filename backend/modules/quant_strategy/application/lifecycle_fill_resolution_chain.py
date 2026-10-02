"""Resolve a complete local report correction/void graph without writes.

The caller must prove every review and posting/void/correction identity from
persisted rows. This graph operation never authenticates broker provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID


@dataclass(frozen=True)
class PostedReportFill:
    report_id: UUID
    fill_event_id: UUID


@dataclass(frozen=True)
class AppliedFillResolution:
    resolution_id: UUID
    report_id: UUID
    action: Literal["VOID", "CORRECT"]
    replacement_report_id: UUID | None
    applied: bool


@dataclass(frozen=True)
class EffectiveFillChain:
    status: Literal["PROVISIONAL", "UNKNOWN"]
    effective_fill_event_ids: tuple[UUID, ...]
    report_paths: tuple[tuple[UUID, ...], ...]
    issues: tuple[str, ...]


def resolve_effective_fill_chain(
    *, postings: tuple[PostedReportFill, ...],
    resolutions: tuple[AppliedFillResolution, ...],
) -> EffectiveFillChain:
    """Require one fully applied linear path from each original report."""
    def unknown(issue: str) -> EffectiveFillChain:
        return EffectiveFillChain("UNKNOWN", (), (), (issue,))

    if (not isinstance(postings, tuple) or not postings
            or not isinstance(resolutions, tuple)):
        return unknown("FILL_RESOLUTION_INPUT_INVALID")
    if (any(not isinstance(row, PostedReportFill)
            or not isinstance(row.report_id, UUID)
            or not isinstance(row.fill_event_id, UUID) for row in postings)
            or len({row.report_id for row in postings}) != len(postings)
            or len({row.fill_event_id for row in postings}) != len(postings)):
        return unknown("FILL_RESOLUTION_POSTING_SET_INVALID")
    by_report = {row.report_id: row for row in postings}
    if (any(not isinstance(item, AppliedFillResolution)
            or not isinstance(item.resolution_id, UUID)
            or not isinstance(item.report_id, UUID)
            or item.report_id not in by_report
            or type(item.applied) is not bool or not item.applied
            or item.action not in ("VOID", "CORRECT")
            or (item.action == "VOID" and item.replacement_report_id is not None)
            or (item.action == "CORRECT" and (
                not isinstance(item.replacement_report_id, UUID)
                or item.replacement_report_id == item.report_id
                or item.replacement_report_id not in by_report))
            for item in resolutions)
            or len({item.resolution_id for item in resolutions}) != len(resolutions)
            or len({item.report_id for item in resolutions}) != len(resolutions)):
        return unknown("FILL_RESOLUTION_IDENTITY_INVALID")
    replacements = tuple(item.replacement_report_id for item in resolutions
                         if item.action == "CORRECT")
    if len(set(replacements)) != len(replacements):
        return unknown("FILL_RESOLUTION_REPLACEMENT_AMBIGUOUS")
    incoming = set(replacements)
    resolutions_by_report = {item.report_id: item for item in resolutions}
    roots = tuple(row.report_id for row in postings if row.report_id not in incoming)
    visited: set[UUID] = set()
    paths: list[tuple[UUID, ...]] = []
    effective: list[UUID] = []
    for root in roots:
        path: list[UUID] = []
        report_id = root
        while True:
            if report_id in visited:
                return unknown("FILL_RESOLUTION_CYCLE_OR_OVERLAP")
            visited.add(report_id)
            path.append(report_id)
            resolution = resolutions_by_report.get(report_id)
            if resolution is None:
                effective.append(by_report[report_id].fill_event_id)
                break
            if resolution.action == "VOID":
                break
            report_id = resolution.replacement_report_id
        paths.append(tuple(path))
    if len(visited) != len(postings):
        return unknown("FILL_RESOLUTION_ORPHAN_OR_CYCLE")
    return EffectiveFillChain(
        "PROVISIONAL", tuple(effective), tuple(paths),
        ("FILL_RESOLUTION_SOURCE_UNCERTIFIED",))
