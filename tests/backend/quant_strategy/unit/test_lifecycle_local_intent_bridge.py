# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle_local_intent_bridge（持仓生命周期）",
#   "keywords": [
#     "量化策略",
#     "交易意图",
#     "持仓生命周期",
#     "lifecycle_local_intent_bridge",
#     "intent",
#     "lifecycle"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/lifecycle_accounting_replay.py",
#     "backend/modules/quant_strategy/application/lifecycle_local_intent_bridge.py",
#     "backend/modules/quant_strategy/application/lifecycle_persisted_fill_inputs.py",
#     "backend/modules/quant_strategy/application/lifecycle_persisted_intent_inputs.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from backend.modules.quant_strategy.application.lifecycle_accounting_replay import ReplayFill
from backend.modules.quant_strategy.application.lifecycle_local_intent_bridge import (
    combine_local_intent_inputs,
)
from backend.modules.quant_strategy.application.lifecycle_persisted_fill_inputs import LocalFillReplayInputs
from backend.modules.quant_strategy.application.lifecycle_persisted_intent_inputs import (
    LocalIntentDefinition, LocalIntentInputs,
)


def test_local_bridge_reaches_explicit_intent_target_without_certifying_sources():
    intent_id, initial_id, event_id = uuid4(), uuid4(), uuid4()
    intents = LocalIntentInputs("LOCAL_CANDIDATE", (
        LocalIntentDefinition(intent_id, date(2026, 9, 28), Decimal(10),
                              "TEMPLATE_CONFIRM_ADD", uuid4()),
    ), ("INTENT_DEFINITION_SOURCE_UNCERTIFIED",))
    initial = ReplayFill(initial_id, datetime(2026, 9, 28, 2, tzinfo=timezone.utc),
                         date(2026, 9, 28), "BUY", Decimal(5), Decimal(10),
                         Decimal(1), "local-report:initial")
    fill = ReplayFill(event_id, datetime(2026, 9, 28, 3, tzinfo=timezone.utc),
                      date(2026, 9, 28), "BUY", Decimal(5), Decimal(10),
                      Decimal(1), "local-report:1", intent_id)
    fills = LocalFillReplayInputs("LOCAL_CANDIDATE", (initial, fill),
                                  ("FILL_SOURCE_UNCERTIFIED",), initial_id)
    kwargs = dict(intents=intents, fills=fills,
                  baseline_as_of=datetime(2026, 9, 27, tzinfo=timezone.utc),
                  baseline_quantity=Decimal(0), baseline_total_cost=Decimal(0),
                  baseline_source_ref="declared-zero-position")

    result = combine_local_intent_inputs(**kwargs)
    assert result.status == "PROVISIONAL"
    assert len(result.completions) == 1
    assert result.completions[0].intent_id == intent_id
    assert result.completions[0].fill_event_id == event_id
    assert result.accounting is not None and result.accounting.quantity == Decimal(10)
    assert "BASELINE_SOURCE_UNCERTIFIED" in result.issues
    assert "BROKER_FILL_SET_UNCERTIFIED" in result.issues

    unbound = combine_local_intent_inputs(
        **{**kwargs, "fills": LocalFillReplayInputs(
            "LOCAL_CANDIDATE", (initial, ReplayFill(
                fill.event_id, fill.executed_at, fill.fill_trade_date,
                fill.side, fill.quantity, fill.price, fill.fee, fill.source_ref),),
            fills.issues, initial_id)})
    assert unbound.status == "UNKNOWN" and unbound.completions == ()
    assert "FILL_INTENT_BINDING_UNKNOWN" in unbound.issues

    malformed = combine_local_intent_inputs(**{
        **kwargs, "intents": LocalIntentInputs(
            "LOCAL_CANDIDATE", (replace(intents.definitions[0], intent_id=[]),),
            intents.issues)})
    assert malformed.status == "UNKNOWN" and malformed.completions == ()
    assert "LOCAL_REPLAY_SET_INVALID" in malformed.issues
