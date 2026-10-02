"""Read a complete local report revision graph under the portfolio lock.

This is a diagnostic of local records, not broker or account certification.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, or_, select

from backend.modules.investment_workspace.application.fill_reviews import AccountFillReviewService
from backend.modules.investment_workspace.application.fill_resolution_reviews import AccountFillResolutionReviewService
from backend.modules.investment_workspace.infrastructure.account_ledger_models import AccountLedgerMovementRow
from backend.modules.investment_workspace.infrastructure.fill_report_models import (
    AccountFillPostingCorrectionRow, AccountFillPostingRow, AccountFillPostingVoidRow,
    AccountFillReportResolutionRow, AccountFillReportRow,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_models import OrderFillEvent, SuggestedOrder
from .lifecycle_fill_resolution_chain import (
    AppliedFillResolution, EffectiveFillChain, PostedReportFill,
    resolve_effective_fill_chain,
)
from .planning_account import lock_portfolio


def inventory_effective_fill_chain(session, *, portfolio_id: UUID, market: str,
                                   symbol: str, order_ids: tuple[UUID, ...]
                                   ) -> EffectiveFillChain:
    """Prove local report/posting/revision closure, returning UNKNOWN on gaps."""
    def unknown(issue: str) -> EffectiveFillChain:
        return EffectiveFillChain("UNKNOWN", (), (), (issue,))

    if (not isinstance(portfolio_id, UUID) or not isinstance(market, str)
            or not market or not isinstance(symbol, str) or not symbol
            or not isinstance(order_ids, tuple) or not order_ids
            or any(not isinstance(item, UUID) for item in order_ids)
            or len(set(order_ids)) != len(order_ids)):
        return unknown("FILL_REVISION_SCOPE_INVALID")
    if session.new or session.dirty or session.deleted:
        return unknown("FILL_REVISION_DIRTY_SESSION")
    if lock_portfolio(session, portfolio_id) is None:
        return unknown("FILL_REVISION_PORTFOLIO_MISSING")
    orders = list(session.scalars(select(SuggestedOrder).where(
        SuggestedOrder.id.in_(order_ids)).order_by(SuggestedOrder.id)
        .with_for_update().execution_options(populate_existing=True)))
    if (len(orders) != len(order_ids) or any(
            row.portfolio_id != portfolio_id or row.market != market
            or row.symbol != symbol for row in orders)):
        return unknown("FILL_REVISION_ORDER_SCOPE_MISMATCH")
    by_order = {row.id: row for row in orders}
    reports = list(session.scalars(select(AccountFillReportRow).where(
        AccountFillReportRow.portfolio_id == portfolio_id,
        or_(and_(AccountFillReportRow.market == market,
                 AccountFillReportRow.symbol == symbol),
            AccountFillReportRow.order_id.in_(order_ids)),
    ).order_by(AccountFillReportRow.id).execution_options(populate_existing=True)))
    if not reports:
        return unknown("FILL_REVISION_REPORT_SET_EMPTY")
    report_ids = {row.id for row in reports}
    postings: list[PostedReportFill] = []
    by_report_posting: dict[UUID, AccountFillPostingRow] = {}
    report_reviews = AccountFillReviewService(session)
    for report in reports:
        order = by_order.get(report.order_id)
        if (order is None or report.market != market or report.symbol != symbol
                or report.side != order.side or report.executed_at is None
                or report.fee is None):
            return unknown("FILL_REVISION_REPORT_IDENTITY_MISMATCH")
        try:
            report_reviews.verify_historical_claim(portfolio_id, report.id)
        except ValueError:
            return unknown("FILL_REVISION_REPORT_REVIEW_INVALID")
        posting = session.scalar(select(AccountFillPostingRow).where(
            AccountFillPostingRow.report_id == report.id))
        if posting is None or posting.portfolio_id != portfolio_id:
            return unknown("FILL_REVISION_REPORT_UNPOSTED")
        event = session.get(OrderFillEvent, posting.fill_event_id, populate_existing=True)
        movement = session.get(AccountLedgerMovementRow, posting.ledger_movement_id,
                               populate_existing=True)
        signed_qty = report.quantity if report.side == "BUY" else -report.quantity
        if (event is None or movement is None or event.portfolio_id != portfolio_id
                or event.order_id != report.order_id or event.event_type != "CONFIRM"
                or event.quantity != report.quantity or event.fill_price != report.fill_price
                or event.fill_trade_date != report.fill_trade_date
                or movement.portfolio_id != portfolio_id or movement.kind != "TRADE"
                or movement.effective_at != report.executed_at
                or movement.fill_price != report.fill_price or movement.fee != report.fee
                or movement.cash_delta != -signed_qty * report.fill_price - report.fee
                or movement.holdings_delta != [[market, symbol, str(signed_qty)]]):
            return unknown("FILL_REVISION_POSTING_FACT_MISMATCH")
        postings.append(PostedReportFill(report.id, event.id))
        by_report_posting[report.id] = posting

    resolutions = list(session.scalars(select(AccountFillReportResolutionRow).where(
        AccountFillReportResolutionRow.portfolio_id == portfolio_id,
        or_(AccountFillReportResolutionRow.report_id.in_(report_ids),
            AccountFillReportResolutionRow.replacement_report_id.in_(report_ids)),
    ).order_by(AccountFillReportResolutionRow.id).execution_options(populate_existing=True)))
    applied: list[AppliedFillResolution] = []
    resolution_reviews = AccountFillResolutionReviewService(session)
    for resolution in resolutions:
        if (resolution.report_id not in report_ids
                or (resolution.replacement_report_id is not None
                    and resolution.replacement_report_id not in report_ids)):
            return unknown("FILL_REVISION_RESOLUTION_OUTSIDE_SCOPE")
        try:
            resolution_reviews.verify_signed_claim(portfolio_id, resolution.id)
        except ValueError:
            return unknown("FILL_REVISION_RESOLUTION_REVIEW_INVALID")
        original = by_report_posting[resolution.report_id]
        if resolution.action == "VOID":
            applied_row = session.scalar(select(AccountFillPostingVoidRow).where(
                AccountFillPostingVoidRow.resolution_id == resolution.id))
            if (applied_row is None or applied_row.portfolio_id != portfolio_id
                    or applied_row.posting_id != original.id):
                return unknown("FILL_REVISION_VOID_NOT_APPLIED")
            event_id, movement_id = applied_row.fill_event_id, applied_row.ledger_movement_id
        elif resolution.action == "CORRECT" and resolution.replacement_report_id in report_ids:
            applied_row = session.scalar(select(AccountFillPostingCorrectionRow).where(
                AccountFillPostingCorrectionRow.resolution_id == resolution.id))
            if (applied_row is None or applied_row.portfolio_id != portfolio_id
                    or applied_row.original_posting_id != original.id
                    or applied_row.replacement_posting_id !=
                    by_report_posting[resolution.replacement_report_id].id):
                return unknown("FILL_REVISION_CORRECTION_NOT_APPLIED")
            event_id = applied_row.void_fill_event_id
            movement_id = applied_row.void_ledger_movement_id
        else:
            return unknown("FILL_REVISION_ACTION_INVALID")
        event = session.get(OrderFillEvent, event_id, populate_existing=True)
        movement = session.get(AccountLedgerMovementRow, movement_id,
                               populate_existing=True)
        original_event = session.get(OrderFillEvent, original.fill_event_id,
                                     populate_existing=True)
        original_movement = session.get(AccountLedgerMovementRow,
                                        original.ledger_movement_id,
                                        populate_existing=True)
        if (event is None or movement is None or event.portfolio_id != portfolio_id
                or event.event_type != "VOID" or event.reverses_fill_id != original.fill_event_id
                or event.order_id != session.get(AccountFillReportRow, resolution.report_id).order_id
                or original_event is None or event.quantity != original_event.quantity
                or event.fill_price != original_event.fill_price
                or event.fill_trade_date != original_event.fill_trade_date
                or movement.portfolio_id != portfolio_id or movement.kind != "VOID"
                or movement.supersedes_id != original.ledger_movement_id
                or original_movement is None
                or movement.effective_at != original_movement.effective_at
                or movement.cash_delta != 0 or movement.holdings_delta != []
                or movement.fill_price is not None or movement.fee != 0):
            return unknown("FILL_REVISION_REVERSAL_FACT_MISMATCH")
        applied.append(AppliedFillResolution(
            resolution.id, resolution.report_id, resolution.action,
            resolution.replacement_report_id, True))

    resolution_ids = {row.id for row in resolutions}
    posting_ids = {row.id for row in by_report_posting.values()}
    void_rows = list(session.scalars(select(AccountFillPostingVoidRow).where(
        AccountFillPostingVoidRow.posting_id.in_(posting_ids))))
    correction_rows = list(session.scalars(select(AccountFillPostingCorrectionRow).where(
        AccountFillPostingCorrectionRow.original_posting_id.in_(posting_ids))))
    expected_void_resolutions = {row.id for row in resolutions if row.action == "VOID"}
    expected_correction_resolutions = {row.id for row in resolutions if row.action == "CORRECT"}
    if ({row.resolution_id for row in void_rows} != expected_void_resolutions
            or {row.resolution_id for row in correction_rows}
            != expected_correction_resolutions
            or any(row.resolution_id not in resolution_ids for row in void_rows)
            or any(row.resolution_id not in resolution_ids for row in correction_rows)):
        return unknown("FILL_REVISION_UNDECLARED_POSTING_REVERSAL")
    for posting in by_report_posting.values():
        reversal_events = list(session.scalars(select(OrderFillEvent).where(
            OrderFillEvent.reverses_fill_id == posting.fill_event_id)))
        reversal_movements = list(session.scalars(select(AccountLedgerMovementRow).where(
            AccountLedgerMovementRow.supersedes_id == posting.ledger_movement_id)))
        applied_event_ids = {row.fill_event_id for row in void_rows if row.posting_id == posting.id}
        applied_event_ids.update(row.void_fill_event_id for row in correction_rows
                                 if row.original_posting_id == posting.id)
        applied_movement_ids = {row.ledger_movement_id for row in void_rows
                                if row.posting_id == posting.id}
        applied_movement_ids.update(row.void_ledger_movement_id for row in correction_rows
                                    if row.original_posting_id == posting.id)
        if ({row.id for row in reversal_events} != applied_event_ids
                or {row.id for row in reversal_movements} != applied_movement_ids):
            return unknown("FILL_REVISION_UNMATCHED_REVERSAL")
    # A report graph is not complete if the same orders have fills from an
    # older/manual path that were never bound to a report posting.
    order_events = list(session.scalars(select(OrderFillEvent).where(
        OrderFillEvent.order_id.in_(order_ids))))
    expected_confirm_ids = {row.fill_event_id for row in by_report_posting.values()}
    expected_void_ids = {row.fill_event_id for row in void_rows}
    expected_void_ids.update(row.void_fill_event_id for row in correction_rows)
    if (any(row.portfolio_id != portfolio_id for row in order_events)
            or {row.id for row in order_events if row.event_type == "CONFIRM"}
            != expected_confirm_ids
            or {row.id for row in order_events if row.event_type == "VOID"}
            != expected_void_ids
            or any(row.event_type not in {"CONFIRM", "VOID"} for row in order_events)):
        return unknown("FILL_REVISION_ORDER_EVENT_SET_MISMATCH")
    return resolve_effective_fill_chain(
        postings=tuple(postings), resolutions=tuple(applied))
