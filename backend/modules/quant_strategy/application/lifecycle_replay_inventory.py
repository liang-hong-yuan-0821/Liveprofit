"""Read-only inventory of facts needed before a lifecycle can be replayed.

The inventory is diagnostic. A report binding and a fee do not certify broker
origin, account ownership, cost provenance, or historical data availability.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import and_, or_, select

from backend.modules.investment_workspace.infrastructure.account_ledger_models import AccountLedgerMovementRow
from backend.modules.investment_workspace.infrastructure.fill_report_models import (
    AccountFillPostingCorrectionRow, AccountFillPostingRow,
    AccountFillPostingVoidRow, AccountFillReportResolutionRow, AccountFillReportRow,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    OrderFillEvent, PositionDailyFact, PositionDailyFactInputProposal,
    PositionDailyFactRevision,
    PositionLifecycleState, SuggestedOrder,
)
from .planning_account import lock_portfolio


@dataclass(frozen=True)
class LifecycleReplayInventory:
    portfolio_id: UUID
    lifecycle_id: UUID
    fill_event_ids: tuple[UUID, ...]
    ambiguous_fill_event_ids: tuple[UUID, ...]
    daily_fact_ids: tuple[UUID, ...]
    input_proposal_ids: tuple[UUID, ...]
    issues: tuple[str, ...]
    initial_fill_id: UUID | None = None
    owned_order_ids: tuple[UUID, ...] = ()


def inventory_lifecycle_replay(session, portfolio_id: UUID,
                               lifecycle_id: UUID) -> LifecycleReplayInventory:
    """List local replay facts under the portfolio lock without changing them."""
    if not isinstance(portfolio_id, UUID) or not isinstance(lifecycle_id, UUID):
        raise ValueError("portfolio and lifecycle IDs must be UUIDs")
    if session.new or session.dirty or session.deleted:
        raise ValueError("replay inventory requires a clean read-only session")
    if lock_portfolio(session, portfolio_id) is None:
        raise ValueError("portfolio does not exist")
    lifecycle = session.scalar(select(PositionLifecycleState).where(
        PositionLifecycleState.id == lifecycle_id,
        PositionLifecycleState.portfolio_id == portfolio_id,
    ).with_for_update().execution_options(populate_existing=True))
    if lifecycle is None:
        raise ValueError("lifecycle does not belong to portfolio")

    issues: list[str] = []
    initial = session.get(OrderFillEvent, lifecycle.initial_fill_id) if lifecycle.initial_fill_id else None
    order_rows = list(session.scalars(select(SuggestedOrder).where(
        or_(SuggestedOrder.lifecycle_id == lifecycle_id,
            and_(SuggestedOrder.portfolio_id == portfolio_id,
                 SuggestedOrder.market == lifecycle.market,
                 SuggestedOrder.symbol == lifecycle.symbol)),
    ).order_by(SuggestedOrder.id).with_for_update()
        .execution_options(populate_existing=True)))
    order_ids = {row.id for row in order_rows}
    orders_by_id = {row.id: row for row in order_rows}
    owned_order_ids = {order.id for order in order_rows
                       if (order.lifecycle_id == lifecycle_id
                           and order.portfolio_id == portfolio_id
                           and order.market == lifecycle.market
                           and order.symbol == lifecycle.symbol
                           and (lifecycle.position_id is None
                                or order.position_id == lifecycle.position_id))}
    for order in order_rows:
        if order.lifecycle_id == lifecycle_id and order.id not in owned_order_ids:
            issues.append(f"ORDER_IDENTITY_MISMATCH:{order.id}")
    if initial is None:
        issues.append("INITIAL_FILL_MISSING")
    elif initial.portfolio_id != portfolio_id:
        issues.append(f"INITIAL_FILL_PORTFOLIO_MISMATCH:{initial.id}")
    else:
        order_ids.add(initial.order_id)
        initial_order = session.get(SuggestedOrder, initial.order_id, populate_existing=True)
        if (initial_order is None or initial_order.portfolio_id != portfolio_id
                or initial_order.market != lifecycle.market
                or initial_order.symbol != lifecycle.symbol
                or initial_order.lifecycle_id not in (None, lifecycle_id)
                or (lifecycle.position_id is not None
                    and initial_order.position_id not in (None, lifecycle.position_id))):
            issues.append(f"INITIAL_ORDER_IDENTITY_MISMATCH:{initial.id}")
        else:
            owned_order_ids.add(initial.order_id)
            orders_by_id[initial.order_id] = initial_order
        competing_anchor = session.scalar(select(PositionLifecycleState.id).where(
            PositionLifecycleState.initial_fill_id == initial.id,
            PositionLifecycleState.id != lifecycle_id).limit(1))
        if competing_anchor is not None:
            issues.append(f"INITIAL_FILL_ANCHOR_REUSED:{initial.id}:{competing_anchor}")
    candidate_events = list(session.scalars(select(OrderFillEvent).where(or_(
        OrderFillEvent.order_id.in_(order_ids),
        and_(OrderFillEvent.portfolio_id == portfolio_id,
             OrderFillEvent.lifecycle_id_at_fill == lifecycle_id),
    )).order_by(OrderFillEvent.fill_trade_date, OrderFillEvent.created_at,
                OrderFillEvent.id)))
    ambiguous_events = [event for event in candidate_events
                        if event.order_id not in owned_order_ids
                        or event.portfolio_id != portfolio_id]
    for event in ambiguous_events:
        issues.append(f"FILL_OWNERSHIP_AMBIGUOUS:{event.id}")
    events = [event for event in candidate_events
              if event.order_id in owned_order_ids and event.portfolio_id == portfolio_id]
    if initial is not None and initial.id not in {event.id for event in events}:
        issues.append(f"INITIAL_FILL_OUTSIDE_ORDER_STREAM:{initial.id}")
    event_ids = tuple(event.id for event in events)
    # A locally reported trade cannot disappear from replay merely because it
    # has not yet passed the separate posting gate. Same-symbol reports without
    # an owned order remain ambiguous rather than being silently excluded.
    reported = list(session.scalars(select(AccountFillReportRow).where(
        AccountFillReportRow.portfolio_id == portfolio_id,
        or_(and_(AccountFillReportRow.market == lifecycle.market,
                 AccountFillReportRow.symbol == lifecycle.symbol),
            AccountFillReportRow.order_id.in_(order_ids)),
    ).order_by(AccountFillReportRow.id)))
    report_postings = {row.report_id: row for row in session.scalars(select(
        AccountFillPostingRow).where(
            AccountFillPostingRow.report_id.in_(report.id for report in reported),
        ))} if reported else {}
    for report in reported:
        if report.order_id not in owned_order_ids:
            issues.append(f"FILL_REPORT_OWNERSHIP_AMBIGUOUS:{report.id}")
            continue
        order = orders_by_id.get(report.order_id)
        if (report.market != lifecycle.market or report.symbol != lifecycle.symbol
                or order is None or report.side != order.side):
            issues.append(f"FILL_REPORT_IDENTITY_MISMATCH:{report.id}")
        if report.id not in report_postings:
            issues.append(f"FILL_REPORT_UNPOSTED:{report.id}")
        elif report_postings[report.id].fill_event_id not in event_ids:
            issues.append(f"FILL_REPORT_POSTING_OUTSIDE_STREAM:{report.id}")
    postings = {row.fill_event_id: row for row in session.scalars(select(AccountFillPostingRow).where(
        AccountFillPostingRow.portfolio_id == portfolio_id,
        AccountFillPostingRow.fill_event_id.in_(event_ids),
    ))} if event_ids else {}
    execution_times: dict[object, UUID] = {}
    for event in events:
        order = session.get(SuggestedOrder, event.order_id, populate_existing=True)
        if (order is None or order.portfolio_id != portfolio_id
                or order.market != lifecycle.market or order.symbol != lifecycle.symbol
                or (lifecycle.position_id is not None and event.position_id is not None
                    and event.position_id != lifecycle.position_id)):
            issues.append(f"FILL_ORDER_IDENTITY_MISMATCH:{event.id}")
            continue
        if event.event_type != "CONFIRM":
            issues.append(f"FILL_REVISION_REPLAY_UNSUPPORTED:{event.id}")
            continue
        posting = postings.get(event.id)
        if posting is None:
            issues.append(f"FILL_REPORT_BINDING_MISSING:{event.id}")
            continue
        report = session.get(AccountFillReportRow, posting.report_id)
        movement = session.get(AccountLedgerMovementRow, posting.ledger_movement_id)
        if report is None or movement is None:
            issues.append(f"FILL_REPORT_LEDGER_MISMATCH:{event.id}")
            continue
        if report.executed_at is None or report.fee is None:
            issues.append(f"FILL_TIME_OR_FEE_MISSING:{event.id}")
            continue
        try:
            signed_quantity = event.quantity if order.side == "BUY" else -event.quantity
            expected_holdings = ((order.market, order.symbol, signed_quantity),)
            actual_holdings = tuple((market, symbol, Decimal(quantity))
                                    for market, symbol, quantity in movement.holdings_delta)
        except (InvalidOperation, TypeError, ValueError):
            actual_holdings = None
        if (report.portfolio_id != portfolio_id
                or movement.portfolio_id != portfolio_id or report.order_id != event.order_id
                or report.market != order.market or report.symbol != order.symbol
                or report.side != order.side or report.fill_trade_date != event.fill_trade_date
                or report.quantity != event.quantity or report.fill_price != event.fill_price
                or movement.kind != "TRADE" or movement.fee != report.fee
                or movement.effective_at != report.executed_at
                or actual_holdings != expected_holdings
                or movement.cash_delta != -signed_quantity * report.fill_price - movement.fee):
            issues.append(f"FILL_REPORT_LEDGER_MISMATCH:{event.id}")
            continue
        if session.scalar(select(AccountFillReportResolutionRow.id).where(
                AccountFillReportResolutionRow.report_id == report.id).limit(1)) is not None:
            issues.append(f"FILL_REPORT_REVISION_PRESENT:{event.id}")
        if (session.scalar(select(AccountFillPostingVoidRow.id).where(
                AccountFillPostingVoidRow.posting_id == posting.id).limit(1)) is not None
                or session.scalar(select(AccountFillPostingCorrectionRow.id).where(
                    AccountFillPostingCorrectionRow.original_posting_id == posting.id,
                ).limit(1)) is not None
                or session.scalar(select(AccountLedgerMovementRow.id).where(
                    AccountLedgerMovementRow.supersedes_id == movement.id,
                ).limit(1)) is not None):
            issues.append(f"FILL_POSTING_REVISION_PRESENT:{event.id}")
        if report.executed_at in execution_times:
            issues.append(f"FILL_EXECUTION_ORDER_UNCERTIFIED:{execution_times[report.executed_at]}:{event.id}")
        else:
            execution_times[report.executed_at] = event.id
        if event.id == lifecycle.initial_fill_id and (
                posting.position_quantity_before is None
                or posting.position_average_cost_before is None):
            issues.append(f"INITIAL_COST_EVIDENCE_MISSING:{event.id}")
        elif (event.id == lifecycle.initial_fill_id
              and posting.position_quantity_before != 0):
            issues.append(f"INITIAL_COST_SOURCE_UNCERTIFIED:{event.id}")
        issues.append(f"FILL_SOURCE_UNCERTIFIED:{event.id}")

    facts = list(session.scalars(select(PositionDailyFact).where(
        PositionDailyFact.lifecycle_id == lifecycle_id,
    ).order_by(PositionDailyFact.trade_date, PositionDailyFact.id)
        .with_for_update().execution_options(populate_existing=True)))
    revisions = list(session.scalars(select(PositionDailyFactRevision).where(
        PositionDailyFactRevision.daily_fact_id.in_([fact.id for fact in facts]),
    ).order_by(PositionDailyFactRevision.daily_fact_id,
               PositionDailyFactRevision.revision_no))) if facts else []
    revisions_by_fact: dict[UUID, list[PositionDailyFactRevision]] = {}
    for revision in revisions:
        revisions_by_fact.setdefault(revision.daily_fact_id, []).append(revision)
    proposals = list(session.scalars(select(PositionDailyFactInputProposal).where(
        PositionDailyFactInputProposal.daily_fact_id.in_([fact.id for fact in facts]),
    ).order_by(PositionDailyFactInputProposal.daily_fact_id,
               PositionDailyFactInputProposal.recorded_at,
               PositionDailyFactInputProposal.id))) if facts else []
    proposals_by_fact: dict[UUID, list[PositionDailyFactInputProposal]] = {}
    for proposal in proposals:
        proposals_by_fact.setdefault(proposal.daily_fact_id, []).append(proposal)
    previous_version: int | None = None
    for fact in facts:
        chain = revisions_by_fact.get(fact.id, [])
        for proposal in proposals_by_fact.get(fact.id, []):
            issues.append(f"DAILY_FACT_INPUT_PROPOSAL_PENDING:{proposal.id}:{fact.trade_date}")
            issues.append(f"DAILY_FACT_INPUT_PROPOSAL_SOURCE_UNCERTIFIED:{proposal.id}:{fact.trade_date}")
            if not chain or proposal.base_revision_id != chain[-1].id:
                issues.append(f"DAILY_FACT_INPUT_PROPOSAL_STALE:{proposal.id}:{fact.trade_date}")
            try:
                proposed_canonical = json.dumps(proposal.proposed_input_payload,
                    sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                    allow_nan=False)
                if (proposed_canonical.encode("utf-8") != proposal.canonical_input_bytes
                        or hashlib.sha256(proposal.canonical_input_bytes).hexdigest()
                        != proposal.proposed_input_hash):
                    issues.append(f"DAILY_FACT_INPUT_PROPOSAL_HASH_MISMATCH:{proposal.id}:{fact.trade_date}")
            except (TypeError, ValueError):
                issues.append(f"DAILY_FACT_INPUT_PROPOSAL_INVALID:{proposal.id}:{fact.trade_date}")
        if not chain:
            issues.append(f"DAILY_FACT_REVISION_HISTORY_MISSING:{fact.id}:{fact.trade_date}")
        else:
            if chain[0].baseline_origin == "MIGRATED":
                issues.append(f"DAILY_FACT_PREMIGRATION_HISTORY_UNKNOWN:{fact.id}:{fact.trade_date}")
            for index, revision in enumerate(chain):
                predecessor = chain[index - 1] if index else None
                if (revision.revision_no != index + 1
                        or revision.previous_revision_id != (predecessor.id if predecessor else None)
                        or revision.lifecycle_id != fact.lifecycle_id
                        or revision.trade_date != fact.trade_date
                        or revision.baseline_origin != chain[0].baseline_origin):
                    issues.append(f"DAILY_FACT_REVISION_CHAIN_INVALID:{fact.id}:{fact.trade_date}")
                    break
                if predecessor and (revision.input_payload != predecessor.input_payload
                                    or revision.input_hash != predecessor.input_hash
                                    or revision.data_as_of != predecessor.data_as_of
                                    or revision.price_basis != predecessor.price_basis):
                    issues.append(f"DAILY_FACT_INPUT_CHANGE_UNREVIEWED:{revision.id}:{fact.trade_date}")
            current = chain[-1]
            if any(getattr(current, field) != getattr(fact, field) for field in (
                    "price_basis", "data_as_of", "input_payload", "input_hash",
                    "planning_result", "rule_version", "state_version_before",
                    "state_version_after", "final_target_shares")):
                issues.append(f"DAILY_FACT_CURRENT_REVISION_MISMATCH:{fact.id}:{fact.trade_date}")
        if (fact.state_version_before < 1
                or fact.state_version_after < fact.state_version_before
                or (previous_version is not None
                    and fact.state_version_before < previous_version)):
            issues.append(f"DAILY_FACT_VERSION_INVALID:{fact.id}:{fact.trade_date}")
        if not isinstance(fact.input_payload, dict):
            issues.append(f"DAILY_FACT_INPUT_INVALID:{fact.id}:{fact.trade_date}")
        else:
            canonical = json.dumps(fact.input_payload, sort_keys=True,
                                   separators=(",", ":"), ensure_ascii=False, default=str)
            if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != fact.input_hash:
                issues.append(f"DAILY_FACT_INPUT_HASH_MISMATCH:{fact.id}:{fact.trade_date}")
        previous_version = fact.state_version_after
    if (lifecycle.last_processed_trade_date is not None
            and lifecycle.last_processed_trade_date not in {fact.trade_date for fact in facts}):
        issues.append(f"PROCESSED_DAY_FACT_MISSING:{lifecycle.last_processed_trade_date}")
    if facts:
        issues.append("DAILY_FACT_CALENDAR_COVERAGE_UNCERTIFIED")
    return LifecycleReplayInventory(
        portfolio_id=portfolio_id, lifecycle_id=lifecycle_id,
        fill_event_ids=event_ids,
        ambiguous_fill_event_ids=tuple(event.id for event in ambiguous_events),
        daily_fact_ids=tuple(fact.id for fact in facts),
        input_proposal_ids=tuple(proposal.id for proposal in proposals),
        issues=tuple(issues),
        initial_fill_id=lifecycle.initial_fill_id,
        owned_order_ids=tuple(sorted(owned_order_ids)),
    )
