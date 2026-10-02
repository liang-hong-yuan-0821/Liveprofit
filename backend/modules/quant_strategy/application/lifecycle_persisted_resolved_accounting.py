"""Read-only accounting candidate from locally verified report revisions.

The result proves consistency of this database's signed reports and postings,
not broker provenance, opening cost, causal versions, or a production balance.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select

from backend.modules.investment_workspace.infrastructure.account_ledger_models import (
    AccountLedgerMovementRow,
)
from backend.modules.investment_workspace.infrastructure.fill_report_models import (
    AccountFillPostingRow, AccountFillReportResolutionRow, AccountFillReportRow,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    OrderFillEvent, PositionLifecycleState, SuggestedOrder,
)

from .lifecycle_accounting_replay import ReplayFill
from .lifecycle_fill_resolution_chain import (
    AppliedFillResolution, PostedReportFill, resolve_effective_fill_chain,
)
from .lifecycle_fill_resolution_inventory import inventory_effective_fill_chain
from .lifecycle_persisted_revision_impact import load_local_lifecycle_revision_impact
from .lifecycle_replay_inventory import inventory_lifecycle_replay
from .lifecycle_resolved_accounting import (
    PostedReplayFill, ResolvedAccountingReplay, replay_resolved_accounting,
)


_CN_TZ = ZoneInfo("Asia/Shanghai")


def load_local_resolved_accounting(
    session, *, portfolio_id: UUID, lifecycle_id: UUID,
    baseline_as_of: datetime, baseline_quantity: Decimal,
    baseline_total_cost: Decimal, baseline_source_ref: str,
) -> ResolvedAccountingReplay:
    """Recompute local effective fills without authorizing any state change.

    The caller supplies its own cost baseline. No current position, initial
    fill anchor, or posting snapshot is promoted into an opening-cost fact.
    """
    def unknown(*issues: str) -> ResolvedAccountingReplay:
        return ResolvedAccountingReplay("UNKNOWN", None, (), (), tuple(issues))

    if not isinstance(portfolio_id, UUID) or not isinstance(lifecycle_id, UUID):
        return unknown("RESOLVED_ACCOUNTING_SCOPE_INVALID")
    if session.new or session.dirty or session.deleted:
        return unknown("RESOLVED_ACCOUNTING_DIRTY_SESSION")
    # Extremely early/late aware datetimes can overflow on time-zone conversion.
    try:
        if (not isinstance(baseline_as_of, datetime)
                or baseline_as_of.tzinfo is None
                or baseline_as_of.utcoffset() is None):
            return unknown("RESOLVED_ACCOUNTING_BASELINE_TIME_INVALID")
        baseline_as_of.astimezone(_CN_TZ)
    except (OverflowError, ValueError):
        return unknown("RESOLVED_ACCOUNTING_BASELINE_TIME_INVALID")

    impact = load_local_lifecycle_revision_impact(
        session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
    if impact.status == "UNKNOWN":
        return unknown(*impact.issues)
    try:
        inventory = inventory_lifecycle_replay(session, portfolio_id, lifecycle_id)
    except ValueError as exc:
        return unknown(*impact.issues, f"RESOLVED_ACCOUNTING_INVENTORY_UNAVAILABLE:{exc}")
    if (inventory.ambiguous_fill_event_ids or not inventory.owned_order_ids
            or inventory.initial_fill_id is None):
        return unknown(*impact.issues, "RESOLVED_ACCOUNTING_ORDER_SCOPE_UNKNOWN")
    lifecycle = session.get(PositionLifecycleState, lifecycle_id,
                            populate_existing=True)
    if lifecycle is None or lifecycle.portfolio_id != portfolio_id:
        return unknown(*impact.issues, "RESOLVED_ACCOUNTING_LIFECYCLE_CHANGED")
    effective = inventory_effective_fill_chain(
        session, portfolio_id=portfolio_id, market=lifecycle.market,
        symbol=lifecycle.symbol, order_ids=inventory.owned_order_ids)
    if effective.status != "PROVISIONAL":
        return unknown(*impact.issues, *effective.issues)

    path_ids = tuple(report_id for path in effective.report_paths
                     for report_id in path)
    report_ids = set(path_ids)
    if not path_ids or len(report_ids) != len(path_ids):
        return unknown(*impact.issues, "RESOLVED_ACCOUNTING_PATH_SET_INVALID")
    # Use the whole same-symbol scope, including unposted or foreign-order
    # reports. Looking up only the graph's IDs would silently omit additions.
    reports = list(session.scalars(select(AccountFillReportRow).where(
        AccountFillReportRow.portfolio_id == portfolio_id,
        or_(and_(AccountFillReportRow.market == lifecycle.market,
                 AccountFillReportRow.symbol == lifecycle.symbol),
            AccountFillReportRow.order_id.in_(inventory.owned_order_ids)),
    ).execution_options(populate_existing=True)))
    if len(reports) != len(report_ids) or {row.id for row in reports} != report_ids:
        return unknown(*impact.issues, "RESOLVED_ACCOUNTING_REPORT_SET_CHANGED")
    by_report = {row.id: row for row in reports}
    postings = list(session.scalars(select(AccountFillPostingRow).where(
        AccountFillPostingRow.report_id.in_(report_ids),
    ).execution_options(populate_existing=True)))
    by_posting = {row.report_id: row for row in postings}
    if (len(postings) != len(report_ids) or set(by_posting) != report_ids
            or len({row.fill_event_id for row in postings}) != len(postings)
            or len({row.ledger_movement_id for row in postings}) != len(postings)
            or any(row.portfolio_id != portfolio_id for row in postings)):
        return unknown(*impact.issues, "RESOLVED_ACCOUNTING_POSTING_SET_CHANGED")
    orders = list(session.scalars(select(SuggestedOrder).where(
        SuggestedOrder.id.in_(inventory.owned_order_ids),
    ).order_by(SuggestedOrder.id).with_for_update()
        .execution_options(populate_existing=True)))
    by_order = {row.id: row for row in orders}
    if (len(orders) != len(inventory.owned_order_ids)
            or set(by_order) != set(inventory.owned_order_ids)):
        return unknown(*impact.issues, "RESOLVED_ACCOUNTING_ORDER_SET_CHANGED")
    fill_ids = {row.fill_event_id for row in postings}
    movement_ids = {row.ledger_movement_id for row in postings}
    events = list(session.scalars(select(OrderFillEvent).where(
        OrderFillEvent.order_id.in_(inventory.owned_order_ids),
    ).execution_options(populate_existing=True)))
    by_event = {row.id: row for row in events if row.event_type == "CONFIRM"}
    if (len(by_event) != len(fill_ids) or set(by_event) != fill_ids
            or any(row.portfolio_id != portfolio_id
                   or row.event_type not in ("CONFIRM", "VOID") for row in events)):
        return unknown(*impact.issues, "RESOLVED_ACCOUNTING_EVENT_SET_CHANGED")
    movements = list(session.scalars(select(AccountLedgerMovementRow).where(
        AccountLedgerMovementRow.id.in_(movement_ids),
    ).execution_options(populate_existing=True)))
    by_movement = {row.id: row for row in movements}
    if len(movements) != len(movement_ids) or set(by_movement) != movement_ids:
        return unknown(*impact.issues, "RESOLVED_ACCOUNTING_MOVEMENT_SET_CHANGED")

    resolutions = list(session.scalars(select(AccountFillReportResolutionRow).where(
        AccountFillReportResolutionRow.portfolio_id == portfolio_id,
        or_(AccountFillReportResolutionRow.report_id.in_(report_ids),
            AccountFillReportResolutionRow.replacement_report_id.in_(report_ids)),
    ).execution_options(populate_existing=True)))
    if any(row.report_id not in report_ids or
           (row.replacement_report_id is not None
            and row.replacement_report_id not in report_ids)
           for row in resolutions):
        return unknown(*impact.issues, "RESOLVED_ACCOUNTING_RESOLUTION_SET_CHANGED")
    applied = tuple(AppliedFillResolution(
        row.id, row.report_id, row.action, row.replacement_report_id, True,
    ) for row in resolutions)
    posted = tuple(PostedReportFill(
        report_id, by_posting[report_id].fill_event_id,
    ) for report_id in path_ids)
    graph = resolve_effective_fill_chain(postings=posted, resolutions=applied)
    revised_paths = {path for path in graph.report_paths
                     if any(report_id == row.report_id for report_id in path
                            for row in resolutions)}
    if (graph.status != "PROVISIONAL"
            or graph.report_paths != effective.report_paths
            or graph.effective_fill_event_ids != effective.effective_fill_event_ids
            or revised_paths != {path.report_ids for path in impact.paths}
            or (impact.status == "NO_REVISION") != (not resolutions)):
        return unknown(*impact.issues, "RESOLVED_ACCOUNTING_GRAPH_CHANGED", *graph.issues)

    payloads = []
    for report_id in path_ids:
        report = by_report[report_id]
        posting = by_posting[report_id]
        event = by_event[posting.fill_event_id]
        movement = by_movement[posting.ledger_movement_id]
        order = by_order.get(report.order_id)
        signed_qty = report.quantity if report.side == "BUY" else -report.quantity
        if (order is None or order.portfolio_id != portfolio_id
                or order.market != lifecycle.market or order.symbol != lifecycle.symbol
                or order.side != report.side or report.side not in ("BUY", "SELL")
                or report.market != lifecycle.market or report.symbol != lifecycle.symbol
                or report.executed_at is None or report.fee is None
                or not report.source_type or not report.source_ref
                or event.portfolio_id != portfolio_id or event.order_id != report.order_id
                or event.event_type != "CONFIRM" or event.reverses_fill_id is not None
                or event.quantity != report.quantity
                or event.fill_price != report.fill_price
                or event.fill_trade_date != report.fill_trade_date
                or movement.portfolio_id != portfolio_id or movement.kind != "TRADE"
                or movement.supersedes_id is not None
                or movement.effective_at != report.executed_at
                or movement.fill_price != report.fill_price
                or movement.fee != report.fee
                or movement.cash_delta != -signed_qty * report.fill_price - report.fee
                or movement.holdings_delta != [[lifecycle.market, lifecycle.symbol,
                                                str(signed_qty)]]):
            return unknown(*impact.issues, "RESOLVED_ACCOUNTING_ECONOMIC_FACT_CHANGED")
        payloads.append(PostedReplayFill(report_id, ReplayFill(
            event_id=event.id, executed_at=report.executed_at,
            fill_trade_date=report.fill_trade_date, side=report.side,
            quantity=report.quantity, price=report.fill_price, fee=report.fee,
            source_ref=f"{report.source_type}:{report.source_ref}",
            intent_id=(event.intent_id_at_fill
                       if event.binding_origin == "LIVE"
                       and event.lifecycle_id_at_fill == lifecycle_id else None),
        )))
    # An opening balance after a superseded report may already contain that
    # obsolete fill. Require it to predate the complete local report history,
    # even though only effective terminals enter the accounting arithmetic.
    if any(row.executed_at <= baseline_as_of for row in reports):
        return unknown(*impact.issues,
                       "RESOLVED_ACCOUNTING_BASELINE_OVERLAPS_REPORT_HISTORY")
    try:
        replay = replay_resolved_accounting(
            postings=posted, resolutions=applied, payloads=tuple(payloads),
            baseline_as_of=baseline_as_of, baseline_quantity=baseline_quantity,
            baseline_total_cost=baseline_total_cost,
            baseline_source_ref=baseline_source_ref)
    except (OverflowError, ValueError):
        return unknown(*impact.issues, "RESOLVED_ACCOUNTING_TIME_OUT_OF_RANGE")
    if replay.status != "PROVISIONAL_ACCOUNTING":
        return unknown(*impact.issues, *replay.issues)
    return ResolvedAccountingReplay(
        replay.status, replay.accounting, replay.effective_fill_event_ids,
        replay.superseded_fill_event_ids,
        (*impact.issues, *effective.issues, *replay.issues),
    )
