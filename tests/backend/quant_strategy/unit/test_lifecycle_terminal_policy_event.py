# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle_terminal_policy_event（持仓生命周期）：A closing fill is an event after real policy days, not another daily fact.",
#   "keywords": [
#     "量化策略",
#     "交易日历",
#     "每日",
#     "策略族",
#     "成交",
#     "交易意图",
#     "持仓生命周期",
#     "订单",
#     "lifecycle_terminal_policy_event",
#     "calendar",
#     "daily",
#     "family",
#     "fill",
#     "intent",
#     "lifecycle",
#     "order"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/lifecycle_accounting_replay.py",
#     "backend/modules/quant_strategy/application/lifecycle_intent_replay.py",
#     "backend/modules/quant_strategy/application/lifecycle_policy_replay.py",
#     "backend/modules/quant_strategy/application/lifecycle_terminal_policy_event.py",
#     "backend/modules/quant_strategy/application/lifecycle_version_chain.py",
#     "backend/modules/quant_strategy/application/position_lifecycle_manager.py",
#     "backend/modules/quant_strategy/domain/family_management.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""A closing fill is an event after real policy days, not another daily fact."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from backend.modules.quant_strategy.application.lifecycle_accounting_replay import (
    ReplayFill, replay_position_accounting,
)
from backend.modules.quant_strategy.application.lifecycle_intent_replay import (
    IntentDefinition, derive_intent_completions,
)
from backend.modules.quant_strategy.application.lifecycle_policy_replay import (
    DailyPolicyFrame, replay_lifecycle_policy,
)
from backend.modules.quant_strategy.application.lifecycle_terminal_policy_event import (
    FrozenClosingIntent, diagnose_terminal_policy_event,
)
from backend.modules.quant_strategy.application.lifecycle_version_chain import (
    DailyVersionStep, FillVersionStep,
)
from backend.modules.quant_strategy.application.position_lifecycle_manager import LifecycleStateInput
from backend.modules.quant_strategy.domain.family_management import MACD_MEAN_REVERSION


CN = ZoneInfo("Asia/Shanghai")
FIRST_FILL_DAY = date(2026, 9, 21)
EXIT_DAY = date(2026, 9, 22)
DELAY_DAY = date(2026, 9, 23)
TERMINAL_DAY = date(2026, 9, 24)


def _fact(*, close="8.8", high="9.0"):
    return {
        "is_suspended": False, "close": close, "high": high,
        "ma5": "10.3", "prev_ma5": "10.0", "ma20": "10.2",
        "prev_ma20": "10.1", "ma60": "9.5", "macd": "0.3",
        "prev_macd": "0.2",
    }


def _candidate(*, delayed=False, suspended_delay=False):
    intent_id = uuid4()
    closing_day = TERMINAL_DAY if delayed else DELAY_DAY
    fills = (
        ReplayFill(uuid4(), datetime(2026, 9, 21, 10, tzinfo=CN),
                   FIRST_FILL_DAY, "BUY", Decimal(500), Decimal(10),
                   Decimal(0), "fixture:initial"),
        ReplayFill(uuid4(), datetime(closing_day.year, closing_day.month,
                                    closing_day.day, 10, tzinfo=CN),
                   closing_day, "SELL", Decimal(500), Decimal(9),
                   Decimal(1), "fixture:final-exit", intent_id),
    )
    accounting = replay_position_accounting(
        baseline_as_of=datetime(2026, 9, 20, 12, tzinfo=CN),
        baseline_quantity=Decimal(0), baseline_total_cost=Decimal(0),
        baseline_source_ref="fixture:zero", fills=fills)
    completion = derive_intent_completions(
        accounting=accounting,
        definitions=(IntentDefinition(intent_id, EXIT_DAY, Decimal(0),
                                      "INITIAL_STOP_LOSS", "fixture:frozen"),))
    assert accounting.status == "CALCULATED"
    assert completion.status == "PROVISIONAL"
    policy_days = (EXIT_DAY, DELAY_DAY) if delayed else (EXIT_DAY,)
    frames = (DailyPolicyFrame(EXIT_DAY, uuid4(), _fact(), "raw", Decimal(500)),)
    if delayed:
        later_fact = ({"is_suspended": True} if suspended_delay else
                      _fact(close="10", high="10.1"))
        frames += (DailyPolicyFrame(DELAY_DAY, uuid4(), later_fact,
                                    "raw", Decimal(500)),)
    seed = LifecycleStateInput(
        template_id="macd_rsi_reversal_v1" if delayed else "ma_trend_cross_v1",
        initial_fill_price=Decimal(10), initial_stop_price=Decimal(9),
        risk_capacity_shares=Decimal(1000), target_exposure_pct=Decimal("0.5"),
    )
    policy = replay_lifecycle_policy(
        first_fill_date=FIRST_FILL_DAY, seed_state=seed,
        seed_target_shares=Decimal(500), seed_trailing=None,
        seed_expectation=None,
        management_policy=MACD_MEAN_REVERSION if delayed else None,
        calendar_dates=policy_days, calendar_source_ref="fixture:real-days",
        corporate_action_dates=(), corporate_action_source_ref="fixture:no-actions",
        frames=frames)
    assert policy.status == "PROVISIONAL"
    daily_versions = (DailyVersionStep(EXIT_DAY, 1, 2),)
    if delayed:
        daily_versions += (DailyVersionStep(
            DELAY_DAY, 2, 2 if suspended_delay else 3),)
    last_version = daily_versions[-1].after
    frozen = FrozenClosingIntent(
        intent_id, EXIT_DAY, 2, Decimal(0), "INITIAL_STOP_LOSS",
        "fixture:first-live-revision")
    kwargs = dict(
        policy=policy, completions=completion.completions,
        frozen_closing_intent=frozen, daily_versions=daily_versions,
        final_fill_step=FillVersionStep(fills[-1].event_id,
                                        last_version, last_version + 1),
        current_version=last_version + 1,
        terminal_open_session_date=closing_day,
        calendar_dates=(*policy_days, closing_day),
        calendar_source_ref="fixture:all-open-sessions",
    )
    return kwargs


def _partial_candidate():
    candidate = _candidate()
    intent_id = candidate["frozen_closing_intent"].intent_id
    terminal_day = candidate["terminal_open_session_date"]
    first_sell_id = uuid4()
    final_sell_id = candidate["final_fill_step"].fill_event_id
    fills = (
        ReplayFill(uuid4(), datetime(2026, 9, 21, 10, tzinfo=CN),
                   FIRST_FILL_DAY, "BUY", Decimal(500), Decimal(10),
                   Decimal(0), "fixture:initial"),
        ReplayFill(first_sell_id, datetime(terminal_day.year, terminal_day.month,
                                          terminal_day.day, 10, tzinfo=CN),
                   terminal_day, "SELL", Decimal(200), Decimal(9),
                   Decimal(0), "fixture:partial-exit", intent_id),
        ReplayFill(final_sell_id, datetime(terminal_day.year, terminal_day.month,
                                          terminal_day.day, 11, tzinfo=CN),
                   terminal_day, "SELL", Decimal(300), Decimal(9),
                   Decimal(0), "fixture:final-exit", intent_id),
    )
    accounting = replay_position_accounting(
        baseline_as_of=datetime(2026, 9, 20, 12, tzinfo=CN),
        baseline_quantity=Decimal(0), baseline_total_cost=Decimal(0),
        baseline_source_ref="fixture:zero", fills=fills)
    assert accounting.status == "CALCULATED"
    completion = derive_intent_completions(
        accounting=accounting,
        definitions=(IntentDefinition(intent_id, EXIT_DAY, Decimal(0),
                                      "INITIAL_STOP_LOSS", "fixture:frozen"),))
    assert completion.status == "PROVISIONAL"
    steps = (
        FillVersionStep(first_sell_id, 2, 3),
        FillVersionStep(final_sell_id, 3, 4),
    )
    candidate.update(
        completions=completion.completions,
        final_fill_step=steps[-1],
        current_version=4,
        terminal_fill_steps=steps,
        terminal_event_states=accounting.event_states[1:],
        pre_terminal_quantity=accounting.event_states[0].quantity,
    )
    return candidate


def test_two_same_session_partial_sells_reach_zero_without_daily_fact():
    candidate = _partial_candidate()
    result = diagnose_terminal_policy_event(**candidate)
    assert result.status == "PROVISIONAL"
    assert result.closing_fill_event_id == candidate["final_fill_step"].fill_event_id
    assert result.ending_state_version == 4
    assert result.last_processed_trade_date == EXIT_DAY


def test_partial_terminal_sequence_requires_each_causal_step_and_final_completion():
    candidate = _partial_candidate()
    missing_step = diagnose_terminal_policy_event(**{
        **candidate,
        "terminal_fill_steps": candidate["terminal_fill_steps"][1:],
        "terminal_event_states": candidate["terminal_event_states"][1:],
    })
    assert missing_step.status == "UNKNOWN"
    assert "TERMINAL_FILL_VERSION_UNATTRIBUTED" in missing_step.issues

    reordered_steps = diagnose_terminal_policy_event(**{
        **candidate,
        "terminal_fill_steps": tuple(reversed(candidate["terminal_fill_steps"])),
    })
    assert reordered_steps.status == "UNKNOWN"

    wrong_completion = diagnose_terminal_policy_event(**{
        **candidate,
        "completions": (replace(candidate["completions"][0],
                                fill_event_id=uuid4()),),
    })
    assert wrong_completion.status == "UNKNOWN"
    assert "TERMINAL_FILL_VERSION_UNATTRIBUTED" in wrong_completion.issues

    wrong_completion_time = diagnose_terminal_policy_event(**{
        **candidate,
        "completions": (replace(candidate["completions"][0],
                                effective_at=datetime(2026, 9, 23, 10,
                                                      tzinfo=CN)),),
    })
    assert wrong_completion_time.status == "UNKNOWN"
    assert "TERMINAL_COMPLETION_BINDING_MISMATCH" in wrong_completion_time.issues


def test_partial_terminal_states_require_one_date_intent_side_and_time_order():
    candidate = _partial_candidate()
    first, final = candidate["terminal_event_states"]
    for changed in (
            replace(first, side="BUY"),
            replace(first, intent_id=uuid4()),
            replace(first, effective_at=datetime(2026, 9, 24, 10, tzinfo=CN)),
            replace(first, effective_at=final.effective_at),
    ):
        diagnosed = diagnose_terminal_policy_event(**{
            **candidate, "terminal_event_states": (changed, final),
        })
        assert diagnosed.status == "UNKNOWN"


def test_partial_terminal_states_must_decrease_and_reach_zero_only_last():
    candidate = _partial_candidate()
    first, final = candidate["terminal_event_states"]
    for states in (
            (replace(first, quantity=Decimal(500)), final),
            (replace(first, quantity=Decimal(0)), final),
            (first, replace(final, quantity=Decimal(1))),
    ):
        diagnosed = diagnose_terminal_policy_event(**{
            **candidate, "terminal_event_states": states,
        })
        assert diagnosed.status == "UNKNOWN"
        assert "TERMINAL_FILL_ACCOUNTING_INVALID" in diagnosed.issues


def test_real_exit_day_and_later_final_sell_close_without_daily_fact():
    candidate = _candidate()
    result = diagnose_terminal_policy_event(**candidate)
    assert candidate["policy"].pending_exit_reason == "INITIAL_STOP_LOSS"
    assert len(candidate["policy"].days) == len(candidate["daily_versions"]) == 1
    assert result.status == "PROVISIONAL"
    assert result.closing_fill_event_id == candidate["final_fill_step"].fill_event_id
    assert result.terminal_trade_date == DELAY_DAY
    assert result.expected_phase == "CLOSED"
    assert (result.expected_target_shares, result.expected_target_exposure_pct) == (
        Decimal(0), Decimal(0))
    assert result.last_processed_trade_date == EXIT_DAY
    assert result.ending_state_version == 3
    assert "TERMINAL_CALENDAR_SOURCE_UNCERTIFIED" in result.issues


def test_delayed_family_generic_exit_retains_frozen_reason():
    candidate = _candidate(delayed=True)
    assert [day.reason_code for day in candidate["policy"].days] == [
        "INITIAL_STOP_LOSS", "EXIT_PENDING"]
    assert candidate["policy"].pending_exit_reason == "INITIAL_STOP_LOSS"
    result = diagnose_terminal_policy_event(**candidate)
    assert result.status == "PROVISIONAL"
    assert result.last_processed_trade_date == DELAY_DAY
    assert result.ending_state_version == 4
    assert len(candidate["policy"].days) == 2


def test_suspended_pending_day_does_not_advance_last_processed_date():
    candidate = _candidate(delayed=True, suspended_delay=True)
    assert candidate["policy"].pending_exit_reason == "INITIAL_STOP_LOSS"
    result = diagnose_terminal_policy_event(**candidate)
    assert result.status == "PROVISIONAL"
    assert result.last_processed_trade_date == EXIT_DAY
    assert result.ending_state_version == 3


def test_wrong_frozen_decision_version_reason_and_stale_pending_state_are_unknown():
    candidate = _candidate(delayed=True)
    frozen = candidate["frozen_closing_intent"]
    wrong_version = diagnose_terminal_policy_event(
        **{**candidate, "frozen_closing_intent": replace(frozen, state_version=99)})
    assert wrong_version.status == "UNKNOWN"
    assert wrong_version.closing_fill_event_id is None
    assert "TERMINAL_EXIT_DECISION_NOT_FROZEN" in wrong_version.issues

    wrong_reason = diagnose_terminal_policy_event(**{
        **candidate,
        "frozen_closing_intent": replace(frozen, reason_code="TRAILING_STOP_LOSS"),
        "completions": (replace(candidate["completions"][0],
                                reason_code="TRAILING_STOP_LOSS"),),
    })
    assert wrong_reason.status == "UNKNOWN"
    assert "TERMINAL_EXIT_DECISION_NOT_FROZEN" in wrong_reason.issues

    stale_pending = diagnose_terminal_policy_event(**{
        **candidate,
        "policy": replace(candidate["policy"], pending_exit_reason="EXIT_PENDING"),
    })
    assert stale_pending.status == "UNKNOWN"
    assert "TERMINAL_EXIT_NO_LONGER_PENDING" in stale_pending.issues


def test_terminal_fill_version_calendar_and_completion_uniqueness_are_gates():
    candidate = _candidate(delayed=True)
    final_step = candidate["final_fill_step"]
    wrong_fill = diagnose_terminal_policy_event(**{
        **candidate, "final_fill_step": replace(final_step, fill_event_id=uuid4()),
    })
    assert wrong_fill.status == "UNKNOWN"
    assert "TERMINAL_FILL_VERSION_UNATTRIBUTED" in wrong_fill.issues
    wrong_before = diagnose_terminal_policy_event(**{
        **candidate, "final_fill_step": replace(final_step, before=2),
    })
    assert wrong_before.status == "UNKNOWN"
    assert "TERMINAL_FILL_VERSION_UNATTRIBUTED" in wrong_before.issues
    wrong_current = diagnose_terminal_policy_event(**{
        **candidate, "current_version": 5,
    })
    assert wrong_current.status == "UNKNOWN"
    assert "TERMINAL_FILL_VERSION_UNATTRIBUTED" in wrong_current.issues
    skipped_session = diagnose_terminal_policy_event(**{
        **candidate, "calendar_dates": (EXIT_DAY, TERMINAL_DAY),
    })
    assert skipped_session.status == "UNKNOWN"
    assert "TERMINAL_OPEN_SESSION_CALENDAR_UNKNOWN" in skipped_session.issues
    two_closings = diagnose_terminal_policy_event(**{
        **candidate,
        "completions": (*candidate["completions"],
                        replace(candidate["completions"][0], fill_event_id=uuid4())),
    })
    assert two_closings.status == "UNKNOWN"
    assert "TERMINAL_CLOSING_COMPLETION_NOT_UNIQUE" in two_closings.issues


def test_same_day_completion_and_missing_real_anchor_stay_unknown():
    candidate = _candidate(delayed=True)
    same_day = diagnose_terminal_policy_event(**{
        **candidate,
        "terminal_open_session_date": DELAY_DAY,
        "calendar_dates": (EXIT_DAY, DELAY_DAY, DELAY_DAY),
        "completions": (replace(candidate["completions"][0],
                                completed_on=DELAY_DAY,
                                effective_at=datetime(2026, 9, 23, 10,
                                                      tzinfo=CN)),),
    })
    assert same_day.status == "UNKNOWN"
    assert "TERMINAL_COMPLETION_NOT_AFTER_REAL_DAY" in same_day.issues
    missing_anchor = diagnose_terminal_policy_event(**{
        **candidate,
        "frozen_closing_intent": replace(candidate["frozen_closing_intent"],
                                         trade_date=FIRST_FILL_DAY),
    })
    assert missing_anchor.status == "UNKNOWN"
    assert "TERMINAL_EXIT_DECISION_DAY_MISSING" in missing_anchor.issues
