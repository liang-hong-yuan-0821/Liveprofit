"""Diagnose a local closing marker from the final bound exit fill.

This only checks the current CLOSED marker. It does not replay daily policy,
certify broker origin, or authorize a projection change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from .lifecycle_accounting_replay import AccountingEventState, AccountingReplay
from .lifecycle_intent_replay import CLOSING_EXIT_REASONS, IntentDefinition


_CN_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class LocalTerminalMarkerDiagnosis:
    status: Literal["PROVISIONAL_MATCH", "PROVISIONAL_DIFFERENCE", "UNKNOWN"]
    closing_fill_event_id: UUID | None
    closing_effective_at: datetime | None
    phase_status: Literal["MATCH", "DIFFERENT", "UNKNOWN"]
    closed_marker_status: Literal["MATCH", "DIFFERENT", "UNKNOWN"]
    issues: tuple[str, ...]


def diagnose_local_terminal_marker(
    *, accounting: AccountingReplay, definitions: tuple[IntentDefinition, ...],
    current_phase: str | None, current_closed_at: datetime | None,
) -> LocalTerminalMarkerDiagnosis:
    """Require one final zero-share SELL bound to a frozen zero target."""
    unknown = LocalTerminalMarkerDiagnosis(
        "UNKNOWN", None, None, "UNKNOWN", "UNKNOWN",
        ("TERMINAL_FILL_OR_INTENT_UNKNOWN",))
    if (not isinstance(accounting, AccountingReplay)
            or accounting.status != "CALCULATED" or accounting.issues
            or accounting.quantity != 0
            or not isinstance(accounting.event_states, tuple)
            or not accounting.event_states
            or not isinstance(accounting.event_ids, tuple)
            or len(accounting.event_states) != len(accounting.event_ids)
            or not isinstance(definitions, tuple)):
        return unknown
    states = accounting.event_states
    if (any(not isinstance(state, AccountingEventState)
            or not isinstance(state.event_id, UUID)
            or state.event_id != accounting.event_ids[index]
            or not isinstance(state.effective_at, datetime)
            or state.effective_at.tzinfo is None
            or state.effective_at.utcoffset() is None
            or not isinstance(state.quantity, Decimal)
            or not state.quantity.is_finite()
            or state.quantity <= 0 and index < len(states) - 1
            for index, state in enumerate(states))
            or any(states[index].effective_at >= states[index + 1].effective_at
                   for index in range(len(states) - 1))):
        return unknown
    final = states[-1]
    if (final.side != "SELL" or final.quantity != 0
            or not isinstance(final.intent_id, UUID)
            or len({state.event_id for state in states}) != len(states)):
        return unknown
    matches = [definition for definition in definitions
               if isinstance(definition, IntentDefinition)
               and definition.intent_id == final.intent_id]
    if len(matches) != 1:
        return unknown
    definition = matches[0]
    if (type(definition.trade_date) is not date
            or definition.trade_date > final.effective_at.astimezone(_CN_TZ).date()
            or not isinstance(definition.target_shares, Decimal)
            or not definition.target_shares.is_finite()
            or definition.target_shares != 0
            or not isinstance(definition.reason_code, str)
            or definition.reason_code not in CLOSING_EXIT_REASONS
            or not isinstance(definition.source_ref, str)
            or not definition.source_ref.strip()):
        return unknown
    phase_status = ("UNKNOWN" if not isinstance(current_phase, str)
                    else "MATCH" if current_phase == "CLOSED" else "DIFFERENT")
    closed_status = ("DIFFERENT" if current_closed_at is None
                     else "MATCH" if isinstance(current_closed_at, datetime)
                     and current_closed_at.tzinfo is not None
                     and current_closed_at.utcoffset() is not None else "UNKNOWN")
    status = ("UNKNOWN" if "UNKNOWN" in (phase_status, closed_status)
              else "PROVISIONAL_DIFFERENCE" if "DIFFERENT" in (phase_status, closed_status)
              else "PROVISIONAL_MATCH")
    return LocalTerminalMarkerDiagnosis(
        status, final.event_id, final.effective_at, phase_status,
        closed_status, ("LOCAL_TERMINAL_SOURCE_UNCERTIFIED",))
