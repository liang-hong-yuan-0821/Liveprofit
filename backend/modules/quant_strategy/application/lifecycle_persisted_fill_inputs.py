"""Read local lifecycle fill facts into diagnostic replay inputs.

These rows establish local consistency only. Broker origin, account ownership,
execution order outside this database and opening cost remain separate gates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select

from backend.modules.investment_workspace.infrastructure.fill_report_models import (
    AccountFillPostingRow, AccountFillReportRow,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    FillLifecycleVersionStep, OrderFillEvent, PositionLifecycleState, SuggestedOrder,
)
from .lifecycle_accounting_replay import ReplayFill
from .lifecycle_fill_resolution_inventory import inventory_effective_fill_chain
from .lifecycle_replay_inventory import inventory_lifecycle_replay
from .lifecycle_version_chain import FillVersionStep


@dataclass(frozen=True)
class LocalFillReplayInputs:
    status: Literal["LOCAL_CANDIDATE", "UNKNOWN"]
    fills: tuple[ReplayFill, ...]
    issues: tuple[str, ...]
    initial_fill_id: UUID | None = None
    version_steps: tuple[FillVersionStep, ...] = ()


def load_local_fill_replay_inputs(session, *, portfolio_id: UUID,
                                  lifecycle_id: UUID) -> LocalFillReplayInputs:
    """Map a complete local unrevised stream; keep revisions unknown for replay."""
    inventory = inventory_lifecycle_replay(session, portfolio_id, lifecycle_id)
    fill_issues = tuple(issue for issue in inventory.issues if issue.startswith((
        "FILL_", "INITIAL_", "ORDER_",
    )))
    baseline_only = ("INITIAL_COST_EVIDENCE_MISSING:",
                     "INITIAL_COST_SOURCE_UNCERTIFIED:")
    blockers = tuple(issue for issue in fill_issues
                     if not issue.startswith(("FILL_SOURCE_UNCERTIFIED:", *baseline_only)))
    if inventory.ambiguous_fill_event_ids or not inventory.fill_event_ids:
        return LocalFillReplayInputs("UNKNOWN", (), fill_issues or ("FILL_STREAM_EMPTY",))

    lifecycle = session.get(PositionLifecycleState, lifecycle_id,
                            populate_existing=True)
    if (lifecycle is None or lifecycle.portfolio_id != portfolio_id
            or not inventory.owned_order_ids):
        return LocalFillReplayInputs(
            "UNKNOWN", (), (*fill_issues, "FILL_REVISION_SCOPE_UNKNOWN"))
    # The report graph checks all reports, postings and reversal events for
    # these locked orders. A raw CONFIRM stream alone cannot prove closure.
    effective = inventory_effective_fill_chain(
        session, portfolio_id=portfolio_id, market=lifecycle.market,
        symbol=lifecycle.symbol, order_ids=inventory.owned_order_ids)
    if effective.status != "PROVISIONAL":
        return LocalFillReplayInputs(
            "UNKNOWN", (), (*fill_issues, *effective.issues))
    if (inventory.initial_fill_id not in effective.effective_fill_event_ids
            or set(effective.effective_fill_event_ids) != set(inventory.fill_event_ids)):
        return LocalFillReplayInputs(
            "UNKNOWN", (), (*fill_issues, *effective.issues,
                            "FILL_EFFECTIVE_SET_REQUIRES_LIFECYCLE_REPLAY"))
    if blockers:
        return LocalFillReplayInputs("UNKNOWN", (), (*fill_issues, *effective.issues))

    rows = list(session.execute(select(
        OrderFillEvent, SuggestedOrder, AccountFillPostingRow, AccountFillReportRow,
    ).join(SuggestedOrder, SuggestedOrder.id == OrderFillEvent.order_id)
        .join(AccountFillPostingRow, AccountFillPostingRow.fill_event_id == OrderFillEvent.id)
        .join(AccountFillReportRow, AccountFillReportRow.id == AccountFillPostingRow.report_id)
        .where(OrderFillEvent.id.in_(inventory.fill_event_ids),
               OrderFillEvent.portfolio_id == portfolio_id,
               SuggestedOrder.portfolio_id == portfolio_id,
               AccountFillPostingRow.portfolio_id == portfolio_id,
               AccountFillReportRow.portfolio_id == portfolio_id)
        .execution_options(populate_existing=True)))
    if len(rows) != len(inventory.fill_event_ids):
        return LocalFillReplayInputs("UNKNOWN", (), (*fill_issues, "FILL_BINDING_SET_CHANGED"))
    fills = tuple(ReplayFill(
        event_id=event.id, executed_at=report.executed_at,
        fill_trade_date=event.fill_trade_date, side=order.side,
        quantity=event.quantity, price=event.fill_price,
        fee=report.fee, source_ref=f"{report.source_type}:{report.source_ref}",
        intent_id=(event.intent_id_at_fill
                   if event.binding_origin == "LIVE"
                   and event.lifecycle_id_at_fill == lifecycle_id else None),
    ) for event, order, _posting, report in rows)
    if any(fill.executed_at is None or fill.fee is None for fill in fills):
        return LocalFillReplayInputs("UNKNOWN", (), (*fill_issues, "FILL_BINDING_SET_CHANGED"))
    version_rows = list(session.scalars(select(FillLifecycleVersionStep).where(
        FillLifecycleVersionStep.fill_event_id.in_(inventory.fill_event_ids))
        .execution_options(populate_existing=True)))
    steps = tuple(FillVersionStep(row.fill_event_id, row.version_before,
                                  row.version_after)
                  for row in version_rows if row.origin == "LOCAL_CAUSAL")
    expected_step_ids = set(inventory.fill_event_ids) - {inventory.initial_fill_id}
    if (set(row.fill_event_id for row in version_rows) != expected_step_ids
            or any(row.origin != "LOCAL_CAUSAL" or row.lifecycle_id != lifecycle_id
                   for row in version_rows)):
        return LocalFillReplayInputs(
            "UNKNOWN", (), (*fill_issues, "FILL_VERSION_STEP_SET_UNKNOWN"))
    return LocalFillReplayInputs(
        "LOCAL_CANDIDATE", tuple(sorted(fills, key=lambda fill: fill.executed_at)),
        (*fill_issues, *effective.issues), inventory.initial_fill_id, steps,
    )
