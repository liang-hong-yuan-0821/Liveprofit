"""Join local fill and intent candidates into a read-only completion diagnosis.

The caller must keep the portfolio transaction open while loading both sets.
An explicit cost baseline remains a declaration, never a broker certificate.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from .lifecycle_accounting_replay import AccountingReplay, ReplayFill, replay_position_accounting
from .lifecycle_intent_replay import IntentCompletion, IntentDefinition, derive_intent_completions
from .lifecycle_persisted_fill_inputs import LocalFillReplayInputs, load_local_fill_replay_inputs
from .lifecycle_persisted_intent_inputs import (
    LocalIntentDefinition, LocalIntentInputs, load_local_intent_definitions,
)


@dataclass(frozen=True)
class LocalIntentCompletionDiagnosis:
    status: Literal["PROVISIONAL", "UNKNOWN"]
    completions: tuple[IntentCompletion, ...]
    issues: tuple[str, ...]
    accounting: AccountingReplay | None = None


def combine_local_intent_inputs(
    *, intents: LocalIntentInputs, fills: LocalFillReplayInputs,
    baseline_as_of: datetime, baseline_quantity: Decimal,
    baseline_total_cost: Decimal, baseline_source_ref: str,
) -> LocalIntentCompletionDiagnosis:
    """Calculate only when both complete local sets and every fill identity agree."""
    if not isinstance(intents, LocalIntentInputs) or not isinstance(fills, LocalFillReplayInputs):
        return LocalIntentCompletionDiagnosis("UNKNOWN", (), ("LOCAL_REPLAY_INPUT_INVALID",))
    if intents.status != "LOCAL_CANDIDATE" or fills.status != "LOCAL_CANDIDATE":
        return LocalIntentCompletionDiagnosis(
            "UNKNOWN", (), (*intents.issues, *fills.issues, "LOCAL_REPLAY_INPUT_UNKNOWN"))
    if (not isinstance(intents.definitions, tuple) or not intents.definitions
            or not isinstance(fills.fills, tuple) or not fills.fills
            or any(not isinstance(item, LocalIntentDefinition)
                   for item in intents.definitions)
            or any(not isinstance(item.intent_id, UUID)
                   for item in intents.definitions)
            or any(not isinstance(item, ReplayFill)
                   or not isinstance(item.event_id, UUID)
                   or (item.intent_id is not None
                       and not isinstance(item.intent_id, UUID))
                   for item in fills.fills)):
        return LocalIntentCompletionDiagnosis(
            "UNKNOWN", (), (*intents.issues, *fills.issues, "LOCAL_REPLAY_SET_INVALID"))
    definitions = {item.intent_id: item for item in intents.definitions}
    if (len(definitions) != len(intents.definitions)
            or not isinstance(fills.initial_fill_id, UUID)
            or fills.fills[0].event_id != fills.initial_fill_id
            or any((fill.intent_id is None and
                    (fill.event_id != fills.initial_fill_id or fill.side != "BUY"))
                   or (fill.intent_id is not None and fill.intent_id not in definitions)
                   for fill in fills.fills)):
        return LocalIntentCompletionDiagnosis(
            "UNKNOWN", (), (*intents.issues, *fills.issues,
                            "FILL_INTENT_BINDING_UNKNOWN"))
    accounting = replay_position_accounting(
        baseline_as_of=baseline_as_of, baseline_quantity=baseline_quantity,
        baseline_total_cost=baseline_total_cost,
        baseline_source_ref=baseline_source_ref, fills=fills.fills)
    if accounting.status != "CALCULATED":
        return LocalIntentCompletionDiagnosis(
            "UNKNOWN", (), (*intents.issues, *fills.issues, *accounting.issues))
    declared = tuple(IntentDefinition(
        intent_id=item.intent_id, trade_date=item.trade_date,
        target_shares=item.target_shares, reason_code=item.reason_code,
        source_ref=f"local-intent-revision:{item.initial_revision_id}",
    ) for item in intents.definitions)
    completion = derive_intent_completions(
        accounting=accounting, definitions=declared)
    if completion.status != "PROVISIONAL":
        return LocalIntentCompletionDiagnosis(
            "UNKNOWN", (), (*intents.issues, *fills.issues, *completion.issues))
    return LocalIntentCompletionDiagnosis(
        "PROVISIONAL", completion.completions,
        (*intents.issues, *fills.issues, *completion.issues,
         "BASELINE_SOURCE_UNCERTIFIED", "BROKER_FILL_SET_UNCERTIFIED"),
        accounting)


def diagnose_local_intent_completions(
    session, *, portfolio_id: UUID, lifecycle_id: UUID,
    baseline_as_of: datetime, baseline_quantity: Decimal,
    baseline_total_cost: Decimal, baseline_source_ref: str,
) -> LocalIntentCompletionDiagnosis:
    """Read under one transaction in portfolio→lifecycle→intent→daily lock order."""
    intents = load_local_intent_definitions(
        session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
    fills = load_local_fill_replay_inputs(
        session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id)
    return combine_local_intent_inputs(
        intents=intents, fills=fills, baseline_as_of=baseline_as_of,
        baseline_quantity=baseline_quantity, baseline_total_cost=baseline_total_cost,
        baseline_source_ref=baseline_source_ref)
