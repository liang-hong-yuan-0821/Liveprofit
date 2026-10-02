"""Read locally frozen intent definitions from immutable write revisions.

The first LIVE revision proves only what this database recorded at insertion.
It does not prove broker fills, historical publication, or human authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import select

from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    PositionIntent, PositionIntentRevision, PositionLifecycleState,
)
from .planning_account import lock_portfolio


@dataclass(frozen=True)
class LocalIntentDefinition:
    intent_id: UUID
    trade_date: date
    target_shares: Decimal
    reason_code: str
    initial_revision_id: UUID
    state_version: int | None = None
    current_status: str | None = None


@dataclass(frozen=True)
class LocalIntentInputs:
    status: Literal["LOCAL_CANDIDATE", "UNKNOWN"]
    definitions: tuple[LocalIntentDefinition, ...]
    issues: tuple[str, ...]


def load_local_intent_definitions(session, *, portfolio_id: UUID,
                                  lifecycle_id: UUID) -> LocalIntentInputs:
    """Return all locally stable definitions while holding their parent locks."""
    if session.new or session.dirty or session.deleted:
        raise ValueError("intent definition read requires a clean session")
    if lock_portfolio(session, portfolio_id) is None:
        raise ValueError("portfolio does not exist")
    lifecycle = session.scalar(select(PositionLifecycleState).where(
        PositionLifecycleState.id == lifecycle_id,
        PositionLifecycleState.portfolio_id == portfolio_id,
    ).with_for_update().execution_options(populate_existing=True))
    if lifecycle is None:
        raise ValueError("lifecycle does not belong to portfolio")
    intents = list(session.scalars(select(PositionIntent).where(
        PositionIntent.lifecycle_id == lifecycle_id,
    ).order_by(PositionIntent.id).with_for_update()
        .execution_options(populate_existing=True)))
    if not intents:
        return LocalIntentInputs("UNKNOWN", (), ("INTENT_DEFINITION_SET_EMPTY",))
    rows = list(session.scalars(select(PositionIntentRevision).where(
        PositionIntentRevision.intent_id.in_(intent.id for intent in intents),
    ).order_by(PositionIntentRevision.intent_id,
               PositionIntentRevision.revision_no)))
    chains: dict[UUID, list[PositionIntentRevision]] = {}
    for row in rows:
        chains.setdefault(row.intent_id, []).append(row)

    issues: list[str] = []
    definitions: list[LocalIntentDefinition] = []
    for intent in intents:
        chain = chains.get(intent.id, ())
        if not chain:
            issues.append(f"INTENT_REVISION_HISTORY_MISSING:{intent.id}")
            continue
        first, latest = chain[0], chain[-1]
        if first.baseline_origin != "LIVE":
            issues.append(f"INTENT_PREMIGRATION_HISTORY_UNKNOWN:{intent.id}")
        if first.status != "ACTIVE" or first.intent_revision != 1:
            issues.append(f"INTENT_INITIAL_DEFINITION_INVALID:{intent.id}")
        for index, row in enumerate(chain):
            predecessor = chain[index - 1] if index else None
            if (row.revision_no != index + 1
                    or row.previous_revision_id != (predecessor.id if predecessor else None)
                    or row.intent_id != intent.id
                    or row.lifecycle_id != lifecycle_id
                    or row.baseline_origin != first.baseline_origin
                    or (predecessor is not None
                        and row.intent_revision != predecessor.intent_revision + 1)):
                issues.append(f"INTENT_REVISION_CHAIN_INVALID:{intent.id}")
                break
            if predecessor is not None and any(getattr(row, field) != getattr(first, field)
                    for field in ("source_signal_id", "trade_date", "target_shares",
                                  "reason_code", "state_version")):
                issues.append(f"INTENT_DEFINITION_CHANGED:{intent.id}")
                break
        if any(getattr(latest, field) != getattr(intent, field) for field in (
                "lifecycle_id", "source_signal_id", "trade_date", "target_shares",
                "reason_code", "state_version", "status")) or latest.intent_revision != intent.revision:
            issues.append(f"INTENT_CURRENT_REVISION_MISMATCH:{intent.id}")
        definitions.append(LocalIntentDefinition(
            intent.id, first.trade_date, first.target_shares,
            first.reason_code, first.id, first.state_version, intent.status))
    if issues:
        return LocalIntentInputs("UNKNOWN", (), tuple(issues))
    return LocalIntentInputs(
        "LOCAL_CANDIDATE", tuple(definitions),
        tuple(f"INTENT_DEFINITION_SOURCE_UNCERTIFIED:{definition.intent_id}"
              for definition in definitions),
    )
