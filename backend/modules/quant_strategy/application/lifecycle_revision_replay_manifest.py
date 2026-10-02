"""Pure local work list for a revised lifecycle fill history.

The mapping names old roots and facts to revisit.  It does not derive a new
causal version, policy day, intent state, position projection, or write right.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from .lifecycle_resolved_accounting import ResolvedAccountingReplay
from .lifecycle_revision_impact import (
    LifecycleRevisionImpact, RevisionDeclaration, RevisionImpactPath,
)
from .lifecycle_revision_impact_surface import (
    AffectedDailyFact, AffectedIntent, LifecycleRevisionImpactSurface,
    OriginalCausalStep,
)


_SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class RevisionReplayRoot:
    root_fill_event_id: UUID
    terminal_fill_event_id: UUID | None
    disposition: Literal["UNCHANGED", "CORRECT", "VOID"]
    old_version_before: int | None
    old_version_after: int | None
    old_step_requires_reattribution: bool


@dataclass(frozen=True)
class RevisionReplayManifest:
    status: Literal["LOCAL_MAPPING", "NO_REVISION", "UNKNOWN"]
    initial_gate: Literal[
        "UNCHANGED_BUY_CANDIDATE", "RESEED_REQUIRED", "INITIAL_VOID",
        "NONBUY_INITIAL", "INITIAL_NOT_FIRST", "UNKNOWN",
    ]
    impact_date: date | None
    roots: tuple[RevisionReplayRoot, ...]
    effective_root_execution_order: tuple[UUID, ...]
    daily_facts_to_recompute: tuple[AffectedDailyFact, ...]
    intents_to_recompute: tuple[AffectedIntent, ...]
    issues: tuple[str, ...]


def build_revision_replay_manifest(
    *, impact: LifecycleRevisionImpact,
    surface: LifecycleRevisionImpactSurface,
    accounting: ResolvedAccountingReplay,
    initial_fill_id: UUID,
) -> RevisionReplayManifest:
    """Join three independently proved local results by immutable event ID.

    In particular, a VOID path has no effective terminal; indexing parallel
    path and accounting tuples would misattribute all following events.
    """
    def unknown(*issues: str) -> RevisionReplayManifest:
        return RevisionReplayManifest(
            "UNKNOWN", "UNKNOWN", None, (), (), (), (), tuple(issues),
        )

    if (not isinstance(impact, LifecycleRevisionImpact)
            or not isinstance(surface, LifecycleRevisionImpactSurface)
            or not isinstance(accounting, ResolvedAccountingReplay)
            or not isinstance(initial_fill_id, UUID)):
        return unknown("REVISION_MANIFEST_INPUT_INVALID")
    inherited_issues = (*impact.issues, *surface.issues, *accounting.issues)
    if "UNKNOWN" in (impact.status, surface.status, accounting.status):
        return unknown(*inherited_issues, "REVISION_MANIFEST_UPSTREAM_UNKNOWN")
    if (accounting.status != "PROVISIONAL_ACCOUNTING"
            or accounting.accounting is None
            or accounting.accounting.status != "CALCULATED"
            or accounting.accounting.issues
            or accounting.accounting.event_ids != accounting.effective_fill_event_ids
            or len(accounting.accounting.event_states) != len(accounting.effective_fill_event_ids)
            or tuple(state.event_id for state in accounting.accounting.event_states)
               != accounting.effective_fill_event_ids
            or len(set(accounting.effective_fill_event_ids))
               != len(accounting.effective_fill_event_ids)):
        return unknown(*inherited_issues, "REVISION_MANIFEST_ACCOUNTING_CHAIN_INVALID")
    states = accounting.accounting.event_states
    if (any(not isinstance(state.event_id, UUID)
            or state.side not in ("BUY", "SELL")
            or not isinstance(state.effective_at, datetime)
            or state.effective_at.tzinfo is None
            or state.effective_at.utcoffset() is None for state in states)
            or any(states[i].effective_at >= states[i + 1].effective_at
                   for i in range(len(states) - 1))):
        return unknown(*inherited_issues, "REVISION_MANIFEST_ACCOUNTING_ORDER_INVALID")

    if impact.status == "NO_REVISION":
        if (surface.status != "NO_REVISION" or impact.paths
                or impact.earliest_impact_at is not None
                or surface.impact_date is not None
                or surface.original_root_fill_ids or surface.old_steps
                or surface.daily_facts or surface.intents
                or accounting.superseded_fill_event_ids):
            return unknown(*inherited_issues, "REVISION_MANIFEST_NO_REVISION_MISMATCH")
        return RevisionReplayManifest(
            "NO_REVISION", "UNKNOWN", None, (), (), (), (), inherited_issues,
        )

    if (impact.status != "LOCAL_IMPACT" or surface.status != "LOCAL_IMPACT"
            or not impact.paths or impact.earliest_impact_at is None
            or not isinstance(impact.earliest_impact_at, datetime)
            or impact.earliest_impact_at.tzinfo is None
            or impact.earliest_impact_at.utcoffset() is None):
        return unknown(*inherited_issues, "REVISION_MANIFEST_IMPACT_MISMATCH")
    try:
        impact_date = impact.earliest_impact_at.astimezone(_SHANGHAI).date()
    except (OverflowError, ValueError):
        return unknown(*inherited_issues, "REVISION_MANIFEST_IMPACT_DATE_INVALID")
    roots = surface.original_root_fill_ids
    if (surface.impact_date != impact_date
            or not isinstance(roots, tuple) or not roots
            or any(not isinstance(root, UUID) for root in roots)
            or len(set(roots)) != len(roots)
            or initial_fill_id not in roots):
        return unknown(*inherited_issues, "REVISION_MANIFEST_ROOT_SET_INVALID")
    root_set = set(roots)
    paths = impact.paths
    if (any(not isinstance(path, RevisionImpactPath)
            or not isinstance(path.root_fill_event_id, UUID)
            or path.root_fill_event_id not in root_set
            or not isinstance(path.root_report_id, UUID)
            or not isinstance(path.report_ids, tuple)
            or not path.report_ids
            or path.report_ids[0] != path.root_report_id
            or any(not isinstance(report_id, UUID)
                   for report_id in path.report_ids)
            or len(set(path.report_ids)) != len(path.report_ids)
            or not isinstance(path.declarations, tuple)
            or not path.declarations
            or any(not isinstance(declaration, RevisionDeclaration)
                   or declaration.report_id not in path.report_ids
                   for declaration in path.declarations)
            or not isinstance(path.earliest_executed_at, datetime)
            or path.earliest_executed_at.tzinfo is None
            or path.earliest_executed_at.utcoffset() is None
            or path.kind not in (
                "INITIAL_ANCHOR_CORRECT", "INITIAL_ANCHOR_VOID",
                "LATER_FILL_CORRECT", "LATER_FILL_VOID")
            or path.kind.startswith("INITIAL_ANCHOR_")
               != (path.root_fill_event_id == initial_fill_id)
            or (path.kind.endswith("_VOID")
                != (path.terminal_fill_event_id is None))
            or (path.kind.endswith("_VOID")
                != (path.terminal_report_id is None))
            or (path.kind.endswith("_CORRECT")
                and (not isinstance(path.terminal_report_id, UUID)
                     or path.terminal_report_id != path.report_ids[-1]
                     or len(path.report_ids) < 2))
            or path.declarations[-1].action != path.kind.rsplit("_", 1)[-1]
            or (path.terminal_fill_event_id is not None
                and (not isinstance(path.terminal_fill_event_id, UUID)
                     or path.terminal_fill_event_id in root_set))
            for path in paths)
            or len({path.root_fill_event_id for path in paths}) != len(paths)):
        return unknown(*inherited_issues, "REVISION_MANIFEST_PATH_SET_INVALID")
    by_path = {path.root_fill_event_id: path for path in paths}
    try:
        earliest = min(path.earliest_executed_at for path in paths)
        earliest.astimezone(_SHANGHAI)
    except (OverflowError, TypeError, ValueError):
        return unknown(*inherited_issues, "REVISION_MANIFEST_IMPACT_TIME_INVALID")
    if earliest != impact.earliest_impact_at:
        return unknown(*inherited_issues, "REVISION_MANIFEST_IMPACT_TIME_MISMATCH")

    steps = surface.old_steps
    if (not isinstance(steps, tuple)
            or any(not isinstance(step, OriginalCausalStep)
                   or not isinstance(step.root_fill_event_id, UUID)
                   or type(step.version_before) is not int
                   or type(step.version_after) is not int
                   or step.version_before < 1
                   or step.version_after != step.version_before + 1
                   for step in steps)
            or len({step.root_fill_event_id for step in steps}) != len(steps)
            or {step.root_fill_event_id for step in steps}
               != root_set - {initial_fill_id}
            or len({step.version_before for step in steps}) != len(steps)
            or surface.old_step_state != ("PRESENT" if steps else "LOCAL_EMPTY")):
        return unknown(*inherited_issues, "REVISION_MANIFEST_OLD_STEP_SET_INVALID")
    by_step = {step.root_fill_event_id: step for step in steps}

    daily = surface.daily_facts
    intents = surface.intents
    if (surface.daily_state != ("PRESENT" if daily else "LOCAL_EMPTY")
            or surface.intent_state != ("PRESENT" if intents else "LOCAL_EMPTY")
            or any(not isinstance(row, AffectedDailyFact)
                   or type(row.trade_date) is not date
                   or row.trade_date < impact_date
                   or not isinstance(row.fact_id, UUID)
                   or not isinstance(row.current_revision_id, UUID)
                   for row in daily)
            or len({row.fact_id for row in daily}) != len(daily)
            or any(not isinstance(row, AffectedIntent)
                   or not isinstance(row.intent_id, UUID)
                   or not isinstance(row.first_live_revision_id, UUID)
                   for row in intents)
            or len({row.intent_id for row in intents}) != len(intents)):
        return unknown(*inherited_issues, "REVISION_MANIFEST_AFFECTED_FACT_SET_INVALID")

    rows: list[RevisionReplayRoot] = []
    terminal_to_root: dict[UUID, UUID] = {}
    superseded: set[UUID] = set()
    for root in roots:
        path = by_path.get(root)
        terminal = root if path is None else path.terminal_fill_event_id
        disposition = "UNCHANGED" if path is None else (
            "VOID" if terminal is None else "CORRECT")
        step = by_step.get(root)
        rows.append(RevisionReplayRoot(
            root, terminal, disposition,
            step.version_before if step else None,
            step.version_after if step else None,
            step is not None,
        ))
        if terminal is not None:
            if terminal in terminal_to_root:
                return unknown(*inherited_issues, "REVISION_MANIFEST_TERMINAL_DUPLICATE")
            terminal_to_root[terminal] = root
        if path is not None:
            superseded.add(root)
    # 3al does not expose intermediate event IDs.  This is a cross-check for
    # old roots, not an independent proof of the whole superseded set; 3ao
    # proves the complete report/posting graph before supplying its result.
    if (set(terminal_to_root) != set(accounting.effective_fill_event_ids)
            or not superseded <= set(accounting.superseded_fill_event_ids)
            or (root_set - superseded) & set(accounting.superseded_fill_event_ids)
            or set(accounting.superseded_fill_event_ids)
               & set(accounting.effective_fill_event_ids)):
        return unknown(*inherited_issues, "REVISION_MANIFEST_EFFECTIVE_SET_MISMATCH")
    effective_root_order = tuple(terminal_to_root[event_id]
                                 for event_id in accounting.effective_fill_event_ids)
    initial_terminal = rows[roots.index(initial_fill_id)].terminal_fill_event_id
    initial_reasons: list[str] = []
    if initial_terminal is None:
        initial_gate = "INITIAL_VOID"
        initial_reasons.append("REPLAY_BLOCKED:INITIAL_VOID")
    else:
        initial_state = states[accounting.effective_fill_event_ids.index(initial_terminal)]
        if initial_state.side != "BUY":
            initial_reasons.append("REPLAY_BLOCKED:NONBUY_INITIAL")
        if (not accounting.effective_fill_event_ids
                or accounting.effective_fill_event_ids[0] != initial_terminal):
            initial_reasons.append("REPLAY_BLOCKED:INITIAL_NOT_FIRST")
        if initial_terminal != initial_fill_id:
            initial_reasons.append("REPLAY_BLOCKED:RESEED_REQUIRED")
        if initial_state.side != "BUY":
            initial_gate = "NONBUY_INITIAL"
        elif not accounting.effective_fill_event_ids or (accounting.effective_fill_event_ids[0]
                                                         != initial_terminal):
            initial_gate = "INITIAL_NOT_FIRST"
        elif initial_terminal != initial_fill_id:
            initial_gate = "RESEED_REQUIRED"
        else:
            initial_gate = "UNCHANGED_BUY_CANDIDATE"
    return RevisionReplayManifest(
        "LOCAL_MAPPING", initial_gate, impact_date, tuple(rows),
        effective_root_order, daily, intents,
        (*inherited_issues, "REPLAY_BLOCKED:CAUSAL_POLICY_INTENT_REPLAY_REQUIRED",
         *initial_reasons),
    )
