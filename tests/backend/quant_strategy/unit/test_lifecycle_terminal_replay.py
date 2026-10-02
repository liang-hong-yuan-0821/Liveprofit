# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle_terminal_replay（持仓生命周期、重放）：The terminal marker is a provisional local check, not closure authority.",
#   "keywords": [
#     "量化策略",
#     "绑定",
#     "持仓生命周期",
#     "重放",
#     "来源证据",
#     "lifecycle_terminal_replay",
#     "bound",
#     "lifecycle",
#     "replay",
#     "source"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/lifecycle_accounting_replay.py",
#     "backend/modules/quant_strategy/application/lifecycle_intent_replay.py",
#     "backend/modules/quant_strategy/application/lifecycle_terminal_replay.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""The terminal marker is a provisional local check, not closure authority."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from backend.modules.quant_strategy.application.lifecycle_accounting_replay import (
    ReplayFill, replay_position_accounting,
)
from backend.modules.quant_strategy.application.lifecycle_intent_replay import IntentDefinition
from backend.modules.quant_strategy.application.lifecycle_terminal_replay import diagnose_local_terminal_marker


CN = ZoneInfo("Asia/Shanghai")


def _case():
    intent_id = uuid4()
    fills = (
        ReplayFill(uuid4(), datetime(2026, 9, 21, 10, tzinfo=CN),
                   date(2026, 9, 21), "BUY", Decimal(100), Decimal(10),
                   Decimal(1), "fixture:entry"),
        ReplayFill(uuid4(), datetime(2026, 9, 22, 10, tzinfo=CN),
                   date(2026, 9, 22), "SELL", Decimal(100), Decimal(11),
                   Decimal(1), "fixture:exit", intent_id),
    )
    accounting = replay_position_accounting(
        baseline_as_of=datetime(2026, 9, 20, 10, tzinfo=CN),
        baseline_quantity=Decimal(0), baseline_total_cost=Decimal(0),
        baseline_source_ref="fixture:zero", fills=fills)
    definition = IntentDefinition(intent_id, date(2026, 9, 22), Decimal(0),
                                  "INITIAL_STOP_LOSS", "fixture:frozen-intent")
    return accounting, (definition,), fills


def test_final_bound_exit_checks_local_closed_marker_without_claiming_source():
    accounting, definitions, fills = _case()
    result = diagnose_local_terminal_marker(
        accounting=accounting, definitions=definitions,
        current_phase="CLOSED",
        current_closed_at=datetime(2026, 9, 22, 11, tzinfo=CN))
    assert result.status == "PROVISIONAL_MATCH"
    assert result.closing_fill_event_id == fills[-1].event_id
    assert result.closing_effective_at == fills[-1].executed_at
    assert result.issues == ("LOCAL_TERMINAL_SOURCE_UNCERTIFIED",)
    missing_marker = diagnose_local_terminal_marker(
        accounting=accounting, definitions=definitions,
        current_phase="ACTIVE", current_closed_at=None)
    assert missing_marker.status == "PROVISIONAL_DIFFERENCE"
    assert (missing_marker.phase_status, missing_marker.closed_marker_status) == (
        "DIFFERENT", "DIFFERENT")


def test_zero_shares_without_frozen_exit_identity_remains_unknown():
    accounting, definitions, _fills = _case()
    for bad_definitions in (
        (), (replace(definitions[0], reason_code="PROFIT_TARGET_TRIM"),),
        (replace(definitions[0], target_shares=Decimal(1)),),
        (replace(definitions[0], source_ref=""),),
        (replace(definitions[0], reason_code=[]),),
        definitions * 2,
    ):
        result = diagnose_local_terminal_marker(
            accounting=accounting, definitions=bad_definitions,
            current_phase="CLOSED", current_closed_at=datetime(2026, 9, 22, 11, tzinfo=CN))
        assert result.status == "UNKNOWN"
    unbound = replace(accounting, event_states=(
        accounting.event_states[0], replace(accounting.event_states[1], intent_id=None)))
    assert diagnose_local_terminal_marker(
        accounting=unbound, definitions=definitions,
        current_phase="CLOSED", current_closed_at=None).status == "UNKNOWN"


def test_later_reentry_or_prior_zero_event_cannot_be_called_terminal():
    accounting, definitions, fills = _case()
    reentry = ReplayFill(uuid4(), datetime(2026, 9, 23, 10, tzinfo=CN),
                         date(2026, 9, 23), "BUY", Decimal(10), Decimal(12),
                         Decimal(0), "fixture:later-entry")
    replayed = replay_position_accounting(
        baseline_as_of=datetime(2026, 9, 20, 10, tzinfo=CN),
        baseline_quantity=Decimal(0), baseline_total_cost=Decimal(0),
        baseline_source_ref="fixture:zero", fills=(*fills, reentry))
    assert diagnose_local_terminal_marker(
        accounting=replayed, definitions=definitions,
        current_phase="CLOSED", current_closed_at=datetime(2026, 9, 22, 11, tzinfo=CN)).status == "UNKNOWN"
    earlier_zero = replace(accounting, event_states=(
        replace(accounting.event_states[0], quantity=Decimal(0)),
        accounting.event_states[1]))
    assert diagnose_local_terminal_marker(
        accounting=earlier_zero, definitions=definitions,
        current_phase="CLOSED", current_closed_at=datetime(2026, 9, 22, 11, tzinfo=CN)).status == "UNKNOWN"
