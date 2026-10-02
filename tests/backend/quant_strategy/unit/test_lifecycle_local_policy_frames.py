# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle_local_policy_frames（持仓生命周期）",
#   "keywords": [
#     "量化策略",
#     "每日",
#     "持仓生命周期",
#     "lifecycle_local_policy_frames",
#     "daily",
#     "lifecycle"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/lifecycle_accounting_replay.py",
#     "backend/modules/quant_strategy/application/lifecycle_intent_replay.py",
#     "backend/modules/quant_strategy/application/lifecycle_local_intent_bridge.py",
#     "backend/modules/quant_strategy/application/lifecycle_local_policy_frames.py",
#     "backend/modules/quant_strategy/application/lifecycle_persisted_daily_inputs.py",
#     "backend/modules/quant_strategy/application/lifecycle_version_chain.py"
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

from backend.modules.quant_strategy.application.lifecycle_accounting_replay import DailyQuantityReplay
from backend.modules.quant_strategy.application.lifecycle_intent_replay import IntentCompletion
from backend.modules.quant_strategy.application.lifecycle_local_intent_bridge import LocalIntentCompletionDiagnosis
from backend.modules.quant_strategy.application.lifecycle_local_policy_frames import build_local_policy_frames
from backend.modules.quant_strategy.application.lifecycle_persisted_daily_inputs import (
    LocalDailyFact, LocalDailyFactInputs,
)
from backend.modules.quant_strategy.application.lifecycle_version_chain import (
    DailyVersionStep, FillVersionStep, reconcile_local_version_chain,
)


def test_local_frames_join_daily_holdings_and_completion_by_exact_date():
    first, second = date(2026, 9, 28), date(2026, 9, 29)
    original = {"is_suspended": False, "close": "10", "high": "11"}
    daily = LocalDailyFactInputs("LOCAL_CANDIDATE", (
        LocalDailyFact(first, uuid4(), "raw",
                       datetime(2026, 9, 28, 8, tzinfo=timezone.utc), original, 1, 2),
        LocalDailyFact(second, uuid4(), "raw",
                       datetime(2026, 9, 29, 8, tzinfo=timezone.utc), original, 2, 3),
    ), ("DAILY_FACT_SOURCE_UNCERTIFIED",))
    quantities = DailyQuantityReplay("PROVISIONAL", (),
                                     ((first, Decimal(5)), (second, Decimal(10))))
    completion = IntentCompletion(
        uuid4(), uuid4(), second,
        datetime(2026, 9, 29, 3, tzinfo=timezone.utc), "ADD_AT_R")
    intents = LocalIntentCompletionDiagnosis(
        "PROVISIONAL", (completion,), ("BROKER_FILL_SET_UNCERTIFIED",))

    result = build_local_policy_frames(
        daily=daily, quantities=quantities, intents=intents)
    assert result.status == "PROVISIONAL"
    assert tuple(frame.actual_shares for frame in result.frames) == (Decimal(5), Decimal(10))
    assert result.frames[0].completed_intent_reasons == ()
    assert result.frames[1].completed_intent_reasons == ("ADD_AT_R",)
    assert result.version_advances == (True, True)
    assert result.ending_state_version == 3
    assert result.starting_state_version == 1
    assert result.first_data_as_of == daily.days[0].data_as_of
    original["close"] = "999"
    assert result.frames[0].fact["close"] == "10"
    assert "POLICY_FRAME_SOURCE_UNCERTIFIED" in result.issues

    ambiguous = build_local_policy_frames(
        daily=daily, quantities=quantities,
        intents=LocalIntentCompletionDiagnosis("PROVISIONAL", (
            completion, IntentCompletion(uuid4(), uuid4(), second,
                                         completion.effective_at, "PROFIT_TARGET_TRIM"),
        ), intents.issues))
    assert ambiguous.status == "UNKNOWN" and ambiguous.frames == ()
    assert "INTENT_MULTIPLE_COMPLETIONS_SAME_DAY" in ambiguous.issues

    wrong_day = build_local_policy_frames(
        daily=daily, quantities=quantities,
        intents=LocalIntentCompletionDiagnosis(
            "PROVISIONAL", (replace(completion, completed_on=first),), intents.issues))
    assert wrong_day.status == "UNKNOWN" and wrong_day.frames == ()
    assert "INTENT_COMPLETION_IDENTITY_INVALID" in wrong_day.issues
    bad_reason = build_local_policy_frames(
        daily=daily, quantities=quantities,
        intents=LocalIntentCompletionDiagnosis(
            "PROVISIONAL", (replace(completion, reason_code=[]),), intents.issues))
    assert bad_reason.status == "UNKNOWN" and bad_reason.frames == ()
    assert "INTENT_COMPLETION_IDENTITY_INVALID" in bad_reason.issues

    repeated = build_local_policy_frames(
        daily=LocalDailyFactInputs("LOCAL_CANDIDATE", (
            daily.days[0], replace(daily.days[1], trade_date=first)), daily.issues),
        quantities=DailyQuantityReplay("PROVISIONAL", (),
                                       ((first, Decimal(5)), (first, Decimal(10)))),
        intents=intents)
    assert repeated.status == "UNKNOWN" and repeated.frames == ()
    assert "POLICY_FRAME_DATES_INVALID" in repeated.issues
    no_version = build_local_policy_frames(
        daily=replace(daily, days=(replace(daily.days[0], state_version_after=None),
                                   daily.days[1])),
        quantities=quantities, intents=intents)
    assert no_version.status == "UNKNOWN"
    assert any(issue.startswith("POLICY_FRAME_STATE_VERSION_INVALID:")
               for issue in no_version.issues)
    unattributed_gap = build_local_policy_frames(
        daily=replace(daily, days=(daily.days[0], replace(
            daily.days[1], state_version_before=100, state_version_after=101))),
        quantities=quantities, intents=intents)
    assert unattributed_gap.status == "UNKNOWN" and unattributed_gap.frames == ()
    assert any(issue.startswith("POLICY_FRAME_VERSION_GAP_UNATTRIBUTED:")
               for issue in unattributed_gap.issues)
    # A genuine between-day fill also needs durable version attribution.
    fill_gap = build_local_policy_frames(
        daily=replace(daily, days=(daily.days[0], replace(
            daily.days[1], state_version_before=3, state_version_after=4))),
        quantities=quantities, intents=intents)
    assert fill_gap.status == "UNKNOWN"
    assert any(issue.startswith("POLICY_FRAME_VERSION_GAP_UNATTRIBUTED:")
               for issue in fill_gap.issues)
    frozen_step = FillVersionStep(uuid4(), 2, 3)
    proven_gap = reconcile_local_version_chain(
        fills=(frozen_step,), days=(
            DailyVersionStep(first, 1, 2), DailyVersionStep(second, 3, 4)),
        current_version=4)
    assert proven_gap.status == "PROVISIONAL"
    attributed = build_local_policy_frames(
        daily=replace(daily, days=(daily.days[0], replace(
            daily.days[1], state_version_before=3, state_version_after=4))),
        quantities=quantities, intents=intents, version_chain=proven_gap)
    assert attributed.status == "PROVISIONAL"
    assert attributed.version_advances == (True, True)
    assert attributed.ending_state_version == 4
    forged = build_local_policy_frames(
        daily=replace(daily, days=(daily.days[0], replace(
            daily.days[1], state_version_before=3, state_version_after=4))),
        quantities=quantities, intents=intents,
        version_chain=replace(proven_gap, verified_days=()))
    assert forged.status == "UNKNOWN"
    assert "POLICY_VERSION_CHAIN_UNKNOWN" in forged.issues
