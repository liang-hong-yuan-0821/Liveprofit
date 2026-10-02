"""Read locally proved fill revisions into an impact-only lifecycle diagnosis.

This adapter never replays daily policy, causal versions, or account projection.
Local signed records do not authenticate the broker or the account owner.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, or_, select

from backend.modules.investment_workspace.infrastructure.fill_report_models import (
    AccountFillPostingRow, AccountFillReportResolutionRow, AccountFillReportRow,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    OrderFillEvent, PositionLifecycleState,
)

from .lifecycle_fill_resolution_chain import (
    AppliedFillResolution, PostedReportFill, resolve_effective_fill_chain,
)
from .lifecycle_fill_resolution_inventory import inventory_effective_fill_chain
from .lifecycle_replay_inventory import inventory_lifecycle_replay
from .lifecycle_revision_impact import (
    FrozenReportFill, LifecycleRevisionImpact, classify_lifecycle_revision_impact,
)


_UNCERTIFIED_PREFIXES = (
    "FILL_SOURCE_UNCERTIFIED:",
    "FILL_EXECUTION_ORDER_UNCERTIFIED:",
    "INITIAL_COST_EVIDENCE_MISSING:",
    "INITIAL_COST_SOURCE_UNCERTIFIED:",
)


def load_local_lifecycle_revision_impact(
    session, *, portfolio_id: UUID, lifecycle_id: UUID,
) -> LifecycleRevisionImpact:
    """Classify only a complete, locally verified revision path under locks."""
    def unknown(*issues: str) -> LifecycleRevisionImpact:
        return LifecycleRevisionImpact("UNKNOWN", (), None, tuple(issues))

    if not isinstance(portfolio_id, UUID) or not isinstance(lifecycle_id, UUID):
        return unknown("REVISION_SCOPE_INVALID")
    if session.new or session.dirty or session.deleted:
        return unknown("REVISION_DIRTY_SESSION")
    try:
        inventory = inventory_lifecycle_replay(session, portfolio_id, lifecycle_id)
    except ValueError as exc:
        return unknown(f"REVISION_INVENTORY_UNAVAILABLE:{exc}")
    if (inventory.ambiguous_fill_event_ids or not inventory.fill_event_ids
            or not inventory.owned_order_ids or inventory.initial_fill_id is None):
        return unknown(*inventory.issues, "REVISION_ORIGINAL_SCOPE_UNKNOWN")

    lifecycle = session.get(PositionLifecycleState, lifecycle_id,
                            populate_existing=True)
    if lifecycle is None or lifecycle.portfolio_id != portfolio_id:
        return unknown(*inventory.issues, "REVISION_LIFECYCLE_SCOPE_CHANGED")

    # This inventory proves every scoped report/review/evidence byte, posting,
    # resolution and its order/ledger reversal before any revision hint from
    # the older unrevised inventory may be treated as expected.
    effective = inventory_effective_fill_chain(
        session, portfolio_id=portfolio_id, market=lifecycle.market,
        symbol=lifecycle.symbol, order_ids=inventory.owned_order_ids,
    )
    if effective.status != "PROVISIONAL":
        return unknown(*inventory.issues, *effective.issues)

    path_report_ids = tuple(report_id for path in effective.report_paths
                            for report_id in path)
    path_set = set(path_report_ids)
    if not path_report_ids or len(path_set) != len(path_report_ids):
        return unknown(*inventory.issues, *effective.issues,
                       "REVISION_PATH_SET_INVALID")

    # Read the entire same-symbol/order report scope, rather than trusting a
    # path-selected subset. A newly visible unposted or foreign report closes
    # the diagnostic result, never yielding a partial list of paths.
    reports = list(session.scalars(select(AccountFillReportRow).where(
        AccountFillReportRow.portfolio_id == portfolio_id,
        or_(and_(AccountFillReportRow.market == lifecycle.market,
                 AccountFillReportRow.symbol == lifecycle.symbol),
            AccountFillReportRow.order_id.in_(inventory.owned_order_ids)),
    ).execution_options(populate_existing=True)))
    if len(reports) != len(path_set) or {row.id for row in reports} != path_set:
        return unknown(*inventory.issues, *effective.issues,
                       "REVISION_REPORT_SET_CHANGED")
    reports_by_id = {row.id: row for row in reports}
    posting_rows = list(session.scalars(select(AccountFillPostingRow).where(
        AccountFillPostingRow.report_id.in_(path_set),
    ).execution_options(populate_existing=True)))
    postings_by_report = {row.report_id: row for row in posting_rows}
    if (len(posting_rows) != len(path_set)
            or set(postings_by_report) != path_set
            or any(row.portfolio_id != portfolio_id for row in posting_rows)):
        return unknown(*inventory.issues, *effective.issues,
                       "REVISION_POSTING_SET_CHANGED")

    fill_ids = {row.fill_event_id for row in posting_rows}
    fill_rows = list(session.scalars(select(OrderFillEvent).where(
        OrderFillEvent.id.in_(fill_ids),
    ).execution_options(populate_existing=True)))
    fills_by_id = {row.id: row for row in fill_rows}
    if len(fill_rows) != len(path_set) or set(fills_by_id) != fill_ids:
        return unknown(*inventory.issues, *effective.issues,
                       "REVISION_FROZEN_FILL_SET_CHANGED")

    resolutions = list(session.scalars(select(AccountFillReportResolutionRow).where(
        AccountFillReportResolutionRow.portfolio_id == portfolio_id,
        or_(AccountFillReportResolutionRow.report_id.in_(path_set),
            AccountFillReportResolutionRow.replacement_report_id.in_(path_set)),
    ).execution_options(populate_existing=True)))
    if any(row.report_id not in path_set or
           (row.replacement_report_id is not None
            and row.replacement_report_id not in path_set)
           for row in resolutions):
        return unknown(*inventory.issues, *effective.issues,
                       "REVISION_RESOLUTION_SET_CHANGED")

    # A correction cannot move its replacement to a different order, even if
    # a malformed historical row otherwise passes graph shape checks.
    if any(row.action == "CORRECT" and
           reports_by_id[row.report_id].order_id !=
           reports_by_id[row.replacement_report_id].order_id
           for row in resolutions if row.replacement_report_id in path_set):
        return unknown(*inventory.issues, *effective.issues,
                       "REVISION_ORDER_MIGRATION")

    postings = tuple(PostedReportFill(
        report_id, postings_by_report[report_id].fill_event_id,
    ) for report_id in path_report_ids)
    applied = tuple(AppliedFillResolution(
        row.id, row.report_id, row.action, row.replacement_report_id, True,
    ) for row in resolutions)
    graph = resolve_effective_fill_chain(postings=postings, resolutions=applied)
    if (graph.status != "PROVISIONAL"
            or graph.report_paths != effective.report_paths
            or graph.effective_fill_event_ids != effective.effective_fill_event_ids):
        return unknown(*inventory.issues, *effective.issues,
                       "REVISION_GRAPH_SET_CHANGED", *graph.issues)

    frozen = []
    for report_id in path_report_ids:
        report = reports_by_id[report_id]
        event = fills_by_id[postings_by_report[report_id].fill_event_id]
        if (report.portfolio_id != portfolio_id
                or report.order_id not in inventory.owned_order_ids
                or event.portfolio_id != portfolio_id
                or event.order_id != report.order_id
                or event.event_type != "CONFIRM"
                or event.fill_trade_date != report.fill_trade_date):
            return unknown(*inventory.issues, *effective.issues,
                           "REVISION_FROZEN_FILL_IDENTITY_CHANGED")
        frozen.append(FrozenReportFill(
            report_id, event.id, report.executed_at, report.fill_trade_date,
            event.order_id, event.lifecycle_id_at_fill,
            event.intent_id_at_fill, event.binding_origin,
        ))

    # Derive the old CONFIRM set from the independently scoped order stream.
    # In particular, a CORRECT replacement must never be fed back as a root.
    order_events = list(session.scalars(select(OrderFillEvent).where(
        OrderFillEvent.order_id.in_(inventory.owned_order_ids),
    ).execution_options(populate_existing=True)))
    confirm_ids = {row.id for row in order_events if row.event_type == "CONFIRM"}
    void_ids = {row.id for row in order_events if row.event_type == "VOID"}
    if (any(row.portfolio_id != portfolio_id or row.event_type not in ("CONFIRM", "VOID")
            for row in order_events) or confirm_ids != fill_ids
            or {row.id for row in order_events} != set(inventory.fill_event_ids)):
        return unknown(*inventory.issues, *effective.issues,
                       "REVISION_ORDER_EVENT_SET_CHANGED")
    replacement_ids = {
        postings_by_report[row.replacement_report_id].fill_event_id
        for row in resolutions if row.action == "CORRECT"
        and row.replacement_report_id in postings_by_report
    }
    original_ids = tuple(sorted(confirm_ids - replacement_ids))
    anchors = list(session.scalars(select(PositionLifecycleState.id).where(
        PositionLifecycleState.initial_fill_id == inventory.initial_fill_id,
    )))
    if (anchors != [lifecycle_id] or inventory.initial_fill_id not in original_ids):
        return unknown(*inventory.issues, *effective.issues,
                       "REVISION_INITIAL_ANCHOR_NOT_UNIQUE_OR_ORIGINAL")

    # 3a's unrevised replay warnings become expected only when they refer to
    # the exact revision facts independently proved by 3ad above.
    revised_original_ids = {
        postings_by_report[row.report_id].fill_event_id for row in resolutions
    }
    revision_prefix_ids = {
        "FILL_REVISION_REPLAY_UNSUPPORTED": void_ids,
        "FILL_REPORT_REVISION_PRESENT": revised_original_ids,
        "FILL_POSTING_REVISION_PRESENT": revised_original_ids,
    }
    for issue in inventory.issues:
        if issue.startswith(_UNCERTIFIED_PREFIXES):
            continue
        prefix, separator, value = issue.partition(":")
        if (separator and prefix in revision_prefix_ids
                and value in {str(item) for item in revision_prefix_ids[prefix]}):
            continue
        if issue.startswith(("DAILY_", "PROCESSED_DAY_")):
            continue  # Report impact does not replay the existing daily stream.
        return unknown(*inventory.issues, *effective.issues,
                       "REVISION_INVENTORY_UNRESOLVED")

    impact = classify_lifecycle_revision_impact(
        lifecycle_id=lifecycle_id, initial_fill_id=inventory.initial_fill_id,
        original_fill_event_ids=original_ids, postings=postings,
        resolutions=applied, frozen_fills=tuple(frozen),
    )
    if impact.status == "UNKNOWN":
        return unknown(*inventory.issues, *effective.issues, *impact.issues)
    return LifecycleRevisionImpact(
        impact.status, impact.paths, impact.earliest_impact_at,
        (*inventory.issues, *effective.issues, *impact.issues),
    )
