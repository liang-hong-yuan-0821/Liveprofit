"""Pure provisional completion dates for explicitly identified intent fills.

This cannot prove a historical intent definition or a complete broker stream.
Callers must obtain those independently before using completion in policy replay.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from .lifecycle_accounting_replay import AccountingEventState, AccountingReplay


_CN_TZ = ZoneInfo("Asia/Shanghai")
_SIDES = {"TEMPLATE_CONFIRM_ADD": "BUY", "ADD_AT_R": "BUY",
          "PROFIT_TARGET_TRIM": "SELL"}
CLOSING_EXIT_REASONS = frozenset({
    "INITIAL_STOP_LOSS", "TRAILING_STOP_LOSS", "EXPECTATION_TIMEOUT",
    "CLOSE_GE_MA20", "HOLDING_TIMEOUT", "EXIT_PENDING",
    "MA_DEATH_CROSS", "PULLBACK_INVALID", "BREAKOUT_INVALID",
    "MOMENTUM_REVERSAL_FAILED", "VOLUME_CONFIRM_INVALID", "ARC_BOTTOM_INVALID",
})


@dataclass(frozen=True)
class IntentDefinition:
    intent_id: UUID
    trade_date: date
    target_shares: Decimal
    reason_code: str
    source_ref: str


@dataclass(frozen=True)
class IntentCompletion:
    intent_id: UUID
    fill_event_id: UUID
    completed_on: date
    effective_at: datetime
    reason_code: str


@dataclass(frozen=True)
class IntentCompletionReplay:
    status: Literal["PROVISIONAL", "UNKNOWN"]
    completions: tuple[IntentCompletion, ...]
    issues: tuple[str, ...]


def derive_intent_completions(
    *, accounting: AccountingReplay, definitions: tuple[IntentDefinition, ...],
) -> IntentCompletionReplay:
    """Find the single fill that first reaches each declared intent target.

    A closing reason requires a frozen zero-share target and a SELL fill. Its
    completion remains a provisional event-time diagnosis, not proof of a
    terminal lifecycle projection or broker source.
    """
    if (not isinstance(accounting, AccountingReplay)
            or accounting.status != "CALCULATED" or accounting.issues
            or not isinstance(definitions, tuple) or not definitions
            or not isinstance(accounting.event_states, tuple)
            or not isinstance(accounting.event_ids, tuple)
            or len(accounting.event_states) != len(accounting.event_ids)
            or any(not isinstance(state, AccountingEventState)
                   for state in accounting.event_states)):
        return IntentCompletionReplay("UNKNOWN", (), ("INTENT_REPLAY_INPUT_UNKNOWN",))
    states = accounting.event_states
    if (tuple(state.event_id for state in states) != accounting.event_ids
            or any(not isinstance(state.event_id, UUID) for state in states)
            or len({state.event_id for state in states}) != len(states)
            or any(not isinstance(state.effective_at, datetime)
                   or state.effective_at.tzinfo is None
                   or state.effective_at.utcoffset() is None
                   or not isinstance(state.quantity, Decimal)
                   or not state.quantity.is_finite()
                   or (state.intent_id is not None and not isinstance(state.intent_id, UUID))
                   for state in states)
            or any(states[index].effective_at >= states[index + 1].effective_at
                   for index in range(len(states) - 1))
            or (states and states[-1].quantity != accounting.quantity)):
        return IntentCompletionReplay("UNKNOWN", (), ("INTENT_EVENT_CHAIN_INVALID",))
    declared: dict[UUID, IntentDefinition] = {}
    issues: list[str] = []
    for definition in definitions:
        if (not isinstance(definition, IntentDefinition)
                or not isinstance(definition.intent_id, UUID)
                or definition.intent_id in declared
                or type(definition.trade_date) is not date
                or not isinstance(definition.target_shares, Decimal)
                or not definition.target_shares.is_finite()
                or definition.target_shares < 0
                or not isinstance(definition.reason_code, str)
                or (definition.reason_code not in _SIDES
                    and definition.reason_code not in CLOSING_EXIT_REASONS)
                or (definition.reason_code in CLOSING_EXIT_REASONS
                    and definition.target_shares != 0)
                or not isinstance(definition.source_ref, str)
                or not definition.source_ref.strip()):
            issues.append("INTENT_DEFINITION_INVALID")
            continue
        declared[definition.intent_id] = definition
    if issues:
        return IntentCompletionReplay("UNKNOWN", (), tuple(issues))

    completions: dict[UUID, IntentCompletion] = {}
    for state in states:
        definition = declared.get(state.intent_id)
        if definition is None:
            continue
        expected_side = ("SELL" if definition.reason_code in CLOSING_EXIT_REASONS
                         else _SIDES[definition.reason_code])
        if state.side != expected_side:
            issues.append(f"INTENT_FILL_INVALID:{definition.intent_id}")
            continue
        fill_day = state.effective_at.astimezone(_CN_TZ).date()
        if fill_day < definition.trade_date:
            issues.append(f"INTENT_FILL_PRECEDES_INTENT:{definition.intent_id}")
        if state.quantity == definition.target_shares:
            if definition.intent_id in completions:
                issues.append(f"INTENT_COMPLETION_AMBIGUOUS:{definition.intent_id}")
            else:
                completions[definition.intent_id] = IntentCompletion(
                    definition.intent_id, state.event_id, fill_day,
                    state.effective_at, definition.reason_code)
        elif (state.side == "BUY" and state.quantity > definition.target_shares) or (
                state.side == "SELL" and state.quantity < definition.target_shares):
            issues.append(f"INTENT_TARGET_OVERSHOT:{definition.intent_id}")
    for intent_id in declared:
        if intent_id not in completions:
            issues.append(f"INTENT_COMPLETION_NOT_PROVEN:{intent_id}")
    completion_days = [item.completed_on for item in completions.values()]
    if len(completion_days) != len(set(completion_days)):
        issues.append("INTENT_MULTIPLE_COMPLETIONS_SAME_DAY")
    if issues:
        return IntentCompletionReplay("UNKNOWN", (), tuple(issues))
    return IntentCompletionReplay(
        "PROVISIONAL",
        tuple(sorted(completions.values(), key=lambda item: item.effective_at)),
        ("INTENT_DEFINITION_SOURCE_UNCERTIFIED",),
    )
