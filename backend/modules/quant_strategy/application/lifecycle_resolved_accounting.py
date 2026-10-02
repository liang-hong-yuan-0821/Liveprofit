"""Pure accounting candidate from a complete local report revision graph.

The caller must independently prove the report/posting set and each applied
resolution. Replacing a reported fill changes the effective accounting input;
it does not rewrite the original local lifecycle version or daily policy
history. This result cannot authorize a projection write or certify a broker.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from .lifecycle_accounting_replay import (
    AccountingReplay, ReplayFill, replay_position_accounting,
)
from .lifecycle_fill_resolution_chain import (
    AppliedFillResolution, PostedReportFill, resolve_effective_fill_chain,
)


@dataclass(frozen=True)
class PostedReplayFill:
    report_id: UUID
    fill: ReplayFill


@dataclass(frozen=True)
class ResolvedAccountingReplay:
    status: Literal["PROVISIONAL_ACCOUNTING", "UNKNOWN"]
    accounting: AccountingReplay | None
    effective_fill_event_ids: tuple[UUID, ...]  # actual execution order
    superseded_fill_event_ids: tuple[UUID, ...]
    issues: tuple[str, ...]


def replay_resolved_accounting(
    *, postings: tuple[PostedReportFill, ...],
    resolutions: tuple[AppliedFillResolution, ...],
    payloads: tuple[PostedReplayFill, ...],
    baseline_as_of: datetime, baseline_quantity: Decimal,
    baseline_total_cost: Decimal, baseline_source_ref: str,
) -> ResolvedAccountingReplay:
    """Calculate effective quantities and cost without inferring causal state.

    Every posted report must have exactly one immutable fill payload. A VOID
    removes its original from this calculation; a CORRECT selects the newest
    replacement fill, which must have its own event ID. Neither operation is
    represented by a negative or in-place modified ReplayFill.
    """
    def unknown(*issues: str) -> ResolvedAccountingReplay:
        return ResolvedAccountingReplay("UNKNOWN", None, (), (), tuple(issues))

    graph = resolve_effective_fill_chain(postings=postings, resolutions=resolutions)
    if graph.status != "PROVISIONAL":
        return unknown(*graph.issues)
    if (not isinstance(payloads, tuple)
            or any(not isinstance(row, PostedReplayFill)
                   or not isinstance(row.report_id, UUID)
                   or not isinstance(row.fill, ReplayFill)
                   or not isinstance(row.fill.event_id, UUID)
                   for row in payloads)):
        return unknown(*graph.issues, "RESOLVED_FILL_PAYLOAD_INVALID")

    by_report = {row.report_id: row.fill for row in payloads}
    posted_by_report = {row.report_id: row.fill_event_id for row in postings}
    if (len(by_report) != len(payloads)
            or set(by_report) != set(posted_by_report)
            or any(fill.event_id != posted_by_report[report_id]
                   for report_id, fill in by_report.items())):
        return unknown(*graph.issues, "RESOLVED_FILL_PAYLOAD_SET_MISMATCH")

    by_event = {fill.event_id: fill for fill in by_report.values()}
    if len(by_event) != len(by_report):
        return unknown(*graph.issues, "RESOLVED_FILL_EVENT_REUSED")
    effective_ids = graph.effective_fill_event_ids
    if (len(set(effective_ids)) != len(effective_ids)
            or any(event_id not in by_event for event_id in effective_ids)):
        return unknown(*graph.issues, "RESOLVED_FILL_EFFECTIVE_SET_INVALID")

    accounting = replay_position_accounting(
        baseline_as_of=baseline_as_of,
        baseline_quantity=baseline_quantity,
        baseline_total_cost=baseline_total_cost,
        baseline_source_ref=baseline_source_ref,
        fills=tuple(by_event[event_id] for event_id in effective_ids),
    )
    if accounting.status != "CALCULATED":
        return unknown(*graph.issues, *accounting.issues)

    effective_set = set(effective_ids)
    return ResolvedAccountingReplay(
        "PROVISIONAL_ACCOUNTING", accounting, accounting.event_ids,
        tuple(row.fill_event_id for row in postings
              if row.fill_event_id not in effective_set),
        (*graph.issues, "BASELINE_SOURCE_UNCERTIFIED",
         "BROKER_FILL_SET_UNCERTIFIED", "LOCAL_CAUSAL_HISTORY_NOT_REPLAYED",
         "HISTORICAL_VISIBILITY_UNCERTIFIED"),
    )
