"""Read a local terminal marker in one transaction without changing it."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select

from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    PositionIntent, PositionLifecycleState, SuggestedOrder,
)
from .lifecycle_accounting_replay import replay_position_accounting
from .lifecycle_intent_replay import IntentDefinition
from .lifecycle_persisted_fill_inputs import load_local_fill_replay_inputs
from .lifecycle_persisted_intent_inputs import load_local_intent_definitions
from .lifecycle_projection_diff import load_current_lifecycle_projection
from .lifecycle_service import ACTIVE_ORDER_STATUSES
from .lifecycle_terminal_replay import (
    LocalTerminalMarkerDiagnosis, diagnose_local_terminal_marker,
)


def diagnose_persisted_terminal_marker(
    session, *, portfolio_id: UUID, lifecycle_id: UUID,
    baseline_as_of: datetime, baseline_quantity: Decimal,
    baseline_total_cost: Decimal, baseline_source_ref: str,
    corporate_action_dates: tuple[date, ...], corporate_action_source_ref: str,
) -> LocalTerminalMarkerDiagnosis:
    """Use the same lock order as active replay; never certify broker closure."""
    current = load_current_lifecycle_projection(
        session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
    intents = load_local_intent_definitions(
        session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
    lifecycle = session.get(PositionLifecycleState, lifecycle_id,
                            populate_existing=True)
    active_intents = list(session.scalars(select(PositionIntent).where(
        PositionIntent.lifecycle_id == lifecycle_id,
        PositionIntent.status.in_(("ACTIVE", "EXECUTING", "RECONCILIATION_REQUIRED")),
    ).with_for_update().execution_options(populate_existing=True)))
    # Lock every existing portfolio order before filtering: a currently
    # cancelled or differently bound row can otherwise change into an active
    # candidate through a direct UPDATE while this diagnosis is running.
    portfolio_orders = list(session.scalars(select(SuggestedOrder).where(
        SuggestedOrder.portfolio_id == portfolio_id,
    ).order_by(SuggestedOrder.id).with_for_update()
        .execution_options(populate_existing=True)))
    intent_ids = {definition.intent_id for definition in intents.definitions}
    active_orders = [order for order in portfolio_orders
                     if order.status in ACTIVE_ORDER_STATUSES
                     and (order.lifecycle_id == lifecycle_id
                          or order.intent_id in intent_ids
                          or lifecycle.position_id is not None
                          and order.position_id == lifecycle.position_id
                          or (order.market, order.symbol) ==
                          (lifecycle.market, lifecycle.symbol))]
    fills = load_local_fill_replay_inputs(
        session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
    source_issues = (
        "BASELINE_SOURCE_UNCERTIFIED", "CORPORATE_ACTION_SOURCE_UNCERTIFIED",
    )
    conflicts = (
        ("CURRENT_TERMINAL_HOLDING_UNKNOWN",)
        if current.position_quantity is None else
        ("CURRENT_TERMINAL_HOLDING_NONZERO",)
        if current.position_quantity != 0 else ()
    )
    conflicts += tuple(f"TERMINAL_ACTIVE_INTENT:{intent.id}" for intent in active_intents)
    conflicts += tuple(f"TERMINAL_ACTIVE_ORDER:{order.id}" for order in active_orders)
    if conflicts:
        return LocalTerminalMarkerDiagnosis(
            "UNKNOWN", None, None, "UNKNOWN", "UNKNOWN",
            (*intents.issues, *fills.issues, *source_issues, *conflicts))
    if (not isinstance(corporate_action_dates, tuple)
            or any(type(day) is not date for day in corporate_action_dates)
            or corporate_action_dates
            or not isinstance(corporate_action_source_ref, str)
            or not corporate_action_source_ref.strip()):
        return LocalTerminalMarkerDiagnosis(
            "UNKNOWN", None, None, "UNKNOWN", "UNKNOWN",
            (*intents.issues, *fills.issues, *source_issues,
             "TERMINAL_CORPORATE_ACTION_COVERAGE_UNKNOWN"))
    if intents.status != "LOCAL_CANDIDATE" or fills.status != "LOCAL_CANDIDATE":
        return LocalTerminalMarkerDiagnosis(
            "UNKNOWN", None, None, "UNKNOWN", "UNKNOWN",
            (*intents.issues, *fills.issues, *source_issues,
             "TERMINAL_LOCAL_FACTS_UNKNOWN"))
    accounting = replay_position_accounting(
        baseline_as_of=baseline_as_of, baseline_quantity=baseline_quantity,
        baseline_total_cost=baseline_total_cost,
        baseline_source_ref=baseline_source_ref, fills=fills.fills)
    definitions = tuple(IntentDefinition(
        intent_id=definition.intent_id, trade_date=definition.trade_date,
        target_shares=definition.target_shares, reason_code=definition.reason_code,
        source_ref=f"local-intent-revision:{definition.initial_revision_id}",
    ) for definition in intents.definitions)
    diagnosed = diagnose_local_terminal_marker(
        accounting=accounting, definitions=definitions,
        current_phase=current.phase, current_closed_at=current.closed_at)
    return replace(diagnosed, issues=(
        *intents.issues, *fills.issues, *accounting.issues,
        *diagnosed.issues, *source_issues,
    ))
