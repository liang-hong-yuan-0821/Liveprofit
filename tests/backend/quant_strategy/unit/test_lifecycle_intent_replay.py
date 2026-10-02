# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle_intent_replay（持仓生命周期、重放）：Intent completion dates use fill execution order, not current intent status.",
#   "keywords": [
#     "量化策略",
#     "绑定",
#     "执行",
#     "成交",
#     "交易意图",
#     "持仓生命周期",
#     "重放",
#     "lifecycle_intent_replay",
#     "bound",
#     "execution",
#     "fill",
#     "intent",
#     "lifecycle",
#     "replay"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/lifecycle_accounting_replay.py",
#     "backend/modules/quant_strategy/application/lifecycle_intent_replay.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Intent completion dates use fill execution order, not current intent status."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from backend.modules.quant_strategy.application.lifecycle_accounting_replay import (
    ReplayFill, replay_position_accounting,
)
from backend.modules.quant_strategy.application.lifecycle_intent_replay import (
    CLOSING_EXIT_REASONS, IntentDefinition, derive_intent_completions,
)


CN = ZoneInfo("Asia/Shanghai")


def _facts():
    add_id, trim_id = uuid4(), uuid4()
    fills = (
        ReplayFill(uuid4(), datetime(2026, 9, 21, 9, 30, tzinfo=CN),
                   date(2026, 9, 21), "BUY", Decimal(100), Decimal(10),
                   Decimal(0), "fixture:initial"),
        ReplayFill(uuid4(), datetime(2026, 9, 21, 10, tzinfo=CN),
                   date(2026, 9, 21), "BUY", Decimal(100), Decimal(11),
                   Decimal(1), "fixture:add", add_id),
        ReplayFill(uuid4(), datetime(2026, 9, 22, 14, tzinfo=CN),
                   date(2026, 9, 22), "SELL", Decimal(50), Decimal(12),
                   Decimal(1), "fixture:trim", trim_id),
    )
    accounting = replay_position_accounting(
        baseline_as_of=datetime(2026, 9, 20, 12, tzinfo=CN),
        baseline_quantity=Decimal(0), baseline_total_cost=Decimal(0),
        baseline_source_ref="fixture:zero", fills=fills)
    definitions = (
        IntentDefinition(add_id, date(2026, 9, 21), Decimal(200),
                         "TEMPLATE_CONFIRM_ADD", "fixture:frozen-add"),
        IntentDefinition(trim_id, date(2026, 9, 22), Decimal(150),
                         "PROFIT_TARGET_TRIM", "fixture:frozen-trim"),
    )
    return accounting, definitions, fills


def test_completion_is_bound_to_reaching_fill_and_execution_date():
    accounting, definitions, fills = _facts()
    result = derive_intent_completions(accounting=accounting, definitions=definitions)
    assert result.status == "PROVISIONAL"
    assert [(item.intent_id, item.fill_event_id, item.completed_on) for item in result.completions] == [
        (definitions[0].intent_id, fills[1].event_id, date(2026, 9, 21)),
        (definitions[1].intent_id, fills[2].event_id, date(2026, 9, 22)),
    ]
    assert result.issues == ("INTENT_DEFINITION_SOURCE_UNCERTIFIED",)


def test_missing_wrong_side_and_overshot_intents_stay_unknown():
    accounting, definitions, _ = _facts()
    missing = derive_intent_completions(
        accounting=accounting,
        definitions=(replace(definitions[0], target_shares=Decimal(300)),))
    assert missing.status == "UNKNOWN" and missing.completions == ()
    assert any(item.startswith("INTENT_COMPLETION_NOT_PROVEN:") for item in missing.issues)
    overshot = derive_intent_completions(
        accounting=accounting,
        definitions=(replace(definitions[0], target_shares=Decimal(150)),))
    assert overshot.status == "UNKNOWN" and overshot.completions == ()
    assert any(item.startswith("INTENT_TARGET_OVERSHOT:") for item in overshot.issues)
    wrong_side = derive_intent_completions(
        accounting=accounting,
        definitions=(replace(definitions[0], reason_code="PROFIT_TARGET_TRIM"),))
    assert wrong_side.status == "UNKNOWN" and wrong_side.completions == ()
    assert any(item.startswith("INTENT_FILL_INVALID:") for item in wrong_side.issues)
    malformed_states = (
        accounting.event_states[0],
        replace(accounting.event_states[1], intent_id=[]),
        accounting.event_states[2],
    )
    malformed = derive_intent_completions(
        accounting=replace(accounting, event_states=malformed_states),
        definitions=definitions)
    assert malformed.status == "UNKNOWN" and malformed.completions == ()


def test_same_day_multiple_completions_cannot_be_collapsed_to_one_policy_frame():
    _, definitions, fills = _facts()
    same_day_trim = replace(
        fills[2], executed_at=datetime(2026, 9, 21, 14, tzinfo=CN),
        fill_trade_date=date(2026, 9, 21))
    accounting = replay_position_accounting(
        baseline_as_of=datetime(2026, 9, 20, 12, tzinfo=CN),
        baseline_quantity=Decimal(0), baseline_total_cost=Decimal(0),
        baseline_source_ref="fixture:zero", fills=(fills[0], fills[1], same_day_trim))
    result = derive_intent_completions(
        accounting=accounting,
        definitions=(definitions[0], replace(definitions[1], trade_date=date(2026, 9, 21))))
    assert result.status == "UNKNOWN" and result.completions == ()
    assert "INTENT_MULTIPLE_COMPLETIONS_SAME_DAY" in result.issues


def _closing_facts(reason):
    exit_id = uuid4()
    fills = (
        ReplayFill(uuid4(), datetime(2026, 9, 21, 9, 30, tzinfo=CN),
                   date(2026, 9, 21), "BUY", Decimal(100), Decimal(10),
                   Decimal(0), "fixture:initial"),
        ReplayFill(uuid4(), datetime(2026, 9, 21, 14, tzinfo=CN),
                   date(2026, 9, 21), "SELL", Decimal(40), Decimal(9),
                   Decimal(0), "fixture:partial-exit", exit_id),
        ReplayFill(uuid4(), datetime(2026, 9, 22, 10, tzinfo=CN),
                   date(2026, 9, 22), "SELL", Decimal(60), Decimal(9),
                   Decimal(0), "fixture:final-exit", exit_id),
    )
    definition = IntentDefinition(
        exit_id, date(2026, 9, 21), Decimal(0), reason,
        "fixture:frozen-zero-target-exit")
    return fills, definition


def _closing_accounting(fills):
    return replay_position_accounting(
        baseline_as_of=datetime(2026, 9, 20, 12, tzinfo=CN),
        baseline_quantity=Decimal(0), baseline_total_cost=Decimal(0),
        baseline_source_ref="fixture:zero", fills=fills)


@pytest.mark.parametrize("reason", sorted(CLOSING_EXIT_REASONS))
def test_zero_target_closing_reason_completes_only_on_final_sell(reason):
    fills, definition = _closing_facts(reason)
    accounting = _closing_accounting(fills)
    result = derive_intent_completions(
        accounting=accounting, definitions=(definition,))
    assert result.status == "PROVISIONAL"
    assert len(result.completions) == 1
    assert (result.completions[0].intent_id, result.completions[0].fill_event_id,
            result.completions[0].completed_on, result.completions[0].reason_code) == (
        definition.intent_id, fills[-1].event_id, date(2026, 9, 22), reason)
    assert result.issues == ("INTENT_DEFINITION_SOURCE_UNCERTIFIED",)


def test_closing_reason_rejects_nonzero_target_wrong_side_and_partial_exit():
    fills, definition = _closing_facts("INITIAL_STOP_LOSS")
    accounting = _closing_accounting(fills)
    nonzero_target = derive_intent_completions(
        accounting=accounting,
        definitions=(replace(definition, target_shares=Decimal(60)),))
    assert nonzero_target.status == "UNKNOWN" and nonzero_target.completions == ()
    assert "INTENT_DEFINITION_INVALID" in nonzero_target.issues

    wrong_side = derive_intent_completions(
        accounting=_closing_accounting((replace(
            fills[0], intent_id=definition.intent_id),
            replace(fills[1], intent_id=None),
            replace(fills[2], intent_id=None))),
        definitions=(definition,))
    assert wrong_side.status == "UNKNOWN" and wrong_side.completions == ()
    assert f"INTENT_FILL_INVALID:{definition.intent_id}" in wrong_side.issues

    incomplete = derive_intent_completions(
        accounting=_closing_accounting(fills[:2]), definitions=(definition,))
    assert incomplete.status == "UNKNOWN" and incomplete.completions == ()
    assert f"INTENT_COMPLETION_NOT_PROVEN:{definition.intent_id}" in incomplete.issues


def test_closing_completion_rejects_ambiguous_or_early_event_chain():
    fills, definition = _closing_facts("INITIAL_STOP_LOSS")
    buy_again = ReplayFill(
        uuid4(), datetime(2026, 9, 23, 9, 30, tzinfo=CN),
        date(2026, 9, 23), "BUY", Decimal(100), Decimal(10),
        Decimal(0), "fixture:reopened")
    sell_again = ReplayFill(
        uuid4(), datetime(2026, 9, 23, 10, tzinfo=CN),
        date(2026, 9, 23), "SELL", Decimal(100), Decimal(9),
        Decimal(0), "fixture:second-completion", definition.intent_id)
    ambiguous = derive_intent_completions(
        accounting=_closing_accounting((*fills, buy_again, sell_again)),
        definitions=(definition,))
    assert ambiguous.status == "UNKNOWN" and ambiguous.completions == ()
    assert f"INTENT_COMPLETION_AMBIGUOUS:{definition.intent_id}" in ambiguous.issues

    early = derive_intent_completions(
        accounting=_closing_accounting(fills),
        definitions=(replace(definition, trade_date=date(2026, 9, 23)),))
    assert early.status == "UNKNOWN" and early.completions == ()
    assert f"INTENT_FILL_PRECEDES_INTENT:{definition.intent_id}" in early.issues

    accounting = _closing_accounting(fills)
    states = (*accounting.event_states[:-1], replace(
        accounting.event_states[-1],
        effective_at=accounting.event_states[-2].effective_at))
    tied = derive_intent_completions(
        accounting=replace(accounting, event_states=states),
        definitions=(definition,))
    assert tied.status == "UNKNOWN" and tied.completions == ()
    assert "INTENT_EVENT_CHAIN_INVALID" in tied.issues
