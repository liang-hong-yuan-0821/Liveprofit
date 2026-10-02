# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle_policy_replay（持仓生命周期、重放）：Pure policy replay with explicit calendar and intent completion evidence.",
#   "keywords": [
#     "量化策略",
#     "绑定",
#     "交易日历",
#     "策略族",
#     "成交",
#     "持仓生命周期",
#     "重放",
#     "版本修订",
#     "来源证据",
#     "止损",
#     "停牌",
#     "lifecycle_policy_replay",
#     "bound",
#     "calendar",
#     "family",
#     "fill",
#     "lifecycle",
#     "replay",
#     "revision",
#     "source",
#     "stop",
#     "suspension"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/lifecycle_policy_replay.py",
#     "backend/modules/quant_strategy/application/position_lifecycle_manager.py",
#     "backend/modules/quant_strategy/domain/family_management.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Pure policy replay with explicit calendar and intent completion evidence."""

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from backend.modules.quant_strategy.application.lifecycle_policy_replay import (
    DailyPolicyFrame, replay_lifecycle_policy,
)
from backend.modules.quant_strategy.application.position_lifecycle_manager import (
    ExpectationStateInput, LifecycleStateInput, TrailingStateInput,
)
from backend.modules.quant_strategy.domain.family_management import (
    MACD_MEAN_REVERSION, TREND_3ATR,
)


FILL_DAY = date(2026, 9, 21)
DAY1 = date(2026, 9, 22)
DAY2 = date(2026, 9, 23)
DAY3 = date(2026, 9, 24)


def _state():
    return LifecycleStateInput(
        template_id="ma_trend_cross_v1", initial_fill_price=Decimal(10),
        initial_stop_price=Decimal(9), risk_capacity_shares=Decimal(1000),
        target_exposure_pct=Decimal("0.50"), profit_take_price=Decimal(12),
    )


def _fact(**updates):
    result = dict(is_suspended=False, close="10.5", high="10.6",
                  ma5="10.3", prev_ma5="10.0", ma20="10.2",
                  prev_ma20="10.1", ma60="9.5", macd="0.3",
                  prev_macd="0.2")
    result.update(updates)
    return result


def _frame(day, fact, **kwargs):
    return DailyPolicyFrame(day, uuid4(), fact, "raw", Decimal(500), **kwargs)


def _replay(days, frames):
    return replay_lifecycle_policy(
        first_fill_date=FILL_DAY, seed_state=_state(), seed_target_shares=Decimal(500), seed_trailing=None,
        seed_expectation=None, management_policy=None,
        calendar_dates=tuple(days), calendar_source_ref="fixture:exchange-calendar",
        corporate_action_dates=(), corporate_action_source_ref="fixture:action-coverage",
        frames=tuple(frames),
    )


def test_suspension_does_not_count_as_observed_session_or_trigger_exit():
    suspended = DailyPolicyFrame(DAY1, uuid4(), {"is_suspended": True},
                                 "raw", Decimal(400))
    traded = _frame(DAY2, _fact())
    result = _replay((DAY1, DAY2), (suspended, traded))
    assert result.status == "PROVISIONAL"
    assert [day.reason_code for day in result.days] == [
        "SUSPENDED_SESSION", "TEMPLATE_CONFIRM_ADD"]
    assert result.days[-1].target_shares == Decimal(1000)
    assert result.days[0].target_shares == Decimal(500)
    assert result.final_state.target_exposure_pct == Decimal("1.00")
    assert result.final_state.confirmation_completed is False


def test_completion_requires_explicit_binding_and_changes_later_decisions():
    first = _frame(DAY1, _fact())
    second = _frame(DAY2, _fact(), completed_intent_reasons=("TEMPLATE_CONFIRM_ADD",),
                    completion_source_ref="fixture:intent-fill-binding")
    result = _replay((DAY1, DAY2), (first, second))
    assert result.status == "PROVISIONAL"
    assert result.final_state.confirmation_completed is True
    assert result.days[-1].reason_code == "LIFECYCLE_HOLD"
    missing = _replay((DAY1, DAY2), (first, _frame(
        DAY2, _fact(), completed_intent_reasons=("TEMPLATE_CONFIRM_ADD",))))
    assert missing.status == "UNKNOWN"
    assert f"INTENT_COMPLETION_EVIDENCE_UNKNOWN:{DAY2}" in missing.issues


def test_profit_trim_completion_is_derived_from_fill_binding_not_price_touch():
    touched = _replay((DAY1,), (_frame(DAY1, _fact(close="12", high="12")),))
    assert touched.status == "PROVISIONAL"
    assert touched.final_state.profit_target_reached is True
    assert touched.profit_trim_completed is False
    completed = _replay((DAY1, DAY2), (
        _frame(DAY1, _fact(close="12", high="12")),
        _frame(DAY2, _fact(close="11", high="11.1"),
               completed_intent_reasons=("PROFIT_TARGET_TRIM",),
               completion_source_ref="fixture:bound-trim-fill"),
    ))
    assert completed.status == "PROVISIONAL"
    assert completed.profit_trim_completed is True


def test_calendar_gap_and_missing_traded_price_remain_unknown():
    result = _replay((DAY1, DAY2), (_frame(DAY2, _fact()),))
    assert result.status == "UNKNOWN"
    assert "DAILY_FACT_CALENDAR_MISMATCH" in result.issues
    missing_price = _replay((DAY1,), (_frame(DAY1, _fact(close=None)),))
    assert missing_price.status == "UNKNOWN"
    assert f"DAILY_POLICY_MARKET_FACTS_UNKNOWN:{DAY1}" in missing_price.issues


def test_stop_loss_is_replayed_from_seed_not_current_target():
    result = _replay((DAY1,), (_frame(DAY1, _fact(close="8.8", high="9.0")),))
    assert result.status == "PROVISIONAL"
    assert result.days[0].reason_code == "INITIAL_STOP_LOSS"
    assert result.days[0].target_shares == 0


def test_atr_policy_without_trailing_seed_is_unknown():
    result = replay_lifecycle_policy(
        first_fill_date=FILL_DAY, seed_state=_state(), seed_target_shares=Decimal(500), seed_trailing=None,
        seed_expectation=None, management_policy=TREND_3ATR,
        calendar_dates=(DAY1,), calendar_source_ref="fixture:exchange-calendar",
        corporate_action_dates=(), corporate_action_source_ref="fixture:action-coverage",
        frames=(_frame(DAY1, _fact(atr="1.0")),),
    )
    assert result.status == "UNKNOWN"
    assert "ATR_TRAILING_SEED_MISSING" in result.issues


def test_malformed_dates_and_integer_suspension_never_advance_state():
    bad_seed = replay_lifecycle_policy(
        first_fill_date=None, seed_state=_state(), seed_target_shares=Decimal(500), seed_trailing=None,
        seed_expectation=None, management_policy=None,
        calendar_dates=(DAY1,), calendar_source_ref="fixture:calendar",
        corporate_action_dates=(), corporate_action_source_ref="fixture:action-coverage",
        frames=(_frame(DAY1, _fact()),),
    )
    assert bad_seed.status == "UNKNOWN"
    assert "POLICY_SEED_OR_CALENDAR_SOURCE_UNKNOWN" in bad_seed.issues
    mixed_calendar = _replay((DAY1, datetime(2026, 9, 23)),
                             (_frame(DAY1, _fact()), _frame(DAY2, _fact())))
    assert mixed_calendar.status == "UNKNOWN"
    assert "CALENDAR_DATES_INVALID" in mixed_calendar.issues
    for false_bool in (0, 1):
        result = _replay((DAY1,), (_frame(DAY1, {"is_suspended": false_bool}),))
        assert result.status == "UNKNOWN"
        assert f"DAILY_POLICY_INPUT_INVALID:{DAY1}" in result.issues


def test_action_basis_reset_and_unproved_zero_holding_remain_unknown():
    action = replay_lifecycle_policy(
        first_fill_date=FILL_DAY, seed_state=_state(), seed_target_shares=Decimal(500), seed_trailing=None,
        seed_expectation=None, management_policy=None,
        calendar_dates=(DAY1,), calendar_source_ref="fixture:calendar",
        corporate_action_dates=(DAY1,), corporate_action_source_ref="fixture:action-coverage",
        frames=(_frame(DAY1, _fact(close="5.25", high="5.3")),),
    )
    assert action.status == "UNKNOWN"
    assert "CORPORATE_ACTION_BASIS_RESET_UNSUPPORTED" in action.issues
    zero = _replay((DAY1,), (DailyPolicyFrame(
        DAY1, uuid4(), _fact(), "raw", Decimal(0)),))
    assert zero.status == "UNKNOWN"
    assert f"ZERO_HOLDING_TERMINAL_EVIDENCE_UNKNOWN:{DAY1}" in zero.issues


def test_bound_exit_completion_closes_previous_zero_target_without_new_policy_evaluation():
    exit_day = _frame(DAY1, _fact(close="8.8", high="9.0"))
    completed = DailyPolicyFrame(
        DAY2, uuid4(), {"is_suspended": False}, "raw", Decimal(0),
        completed_intent_reasons=("INITIAL_STOP_LOSS",),
        completion_source_ref="fixture:frozen-zero-target-intent-fill",
    )
    result = _replay((DAY1, DAY2), (exit_day, completed))
    assert result.status == "PROVISIONAL"
    assert [day.phase for day in result.days] == ["EXIT_PENDING", "CLOSED"]
    assert result.days[-1].reason_code == "INITIAL_STOP_LOSS"
    assert result.days[-1].target_shares == Decimal(0)
    assert result.days[-1].target_exposure_pct == Decimal(0)
    assert result.final_state.target_exposure_pct == Decimal(0)


def test_terminal_completion_must_match_prior_exit_and_have_source():
    exit_day = _frame(DAY1, _fact(close="8.8", high="9.0"))
    for reasons, source in (
        ((), None),
        (("INITIAL_STOP_LOSS",), None),
        (("TRAILING_STOP_LOSS",), "fixture:wrong-frozen-reason"),
        (("INITIAL_STOP_LOSS", "TRAILING_STOP_LOSS"), "fixture:two-intents"),
    ):
        terminal = DailyPolicyFrame(
            DAY2, uuid4(), {"is_suspended": False}, "raw", Decimal(0),
            completed_intent_reasons=reasons, completion_source_ref=source,
        )
        assert _replay((DAY1, DAY2), (exit_day, terminal)).status == "UNKNOWN"
    same_day = DailyPolicyFrame(
        DAY1, uuid4(), {"is_suspended": False}, "raw", Decimal(0),
        completed_intent_reasons=("INITIAL_STOP_LOSS",),
        completion_source_ref="fixture:same-day-exit",
    )
    result = _replay((DAY1,), (same_day,))
    assert result.status == "UNKNOWN"
    assert f"ZERO_HOLDING_TERMINAL_EVIDENCE_UNKNOWN:{DAY1}" in result.issues


def test_delayed_family_exit_keeps_original_frozen_reason():
    state = LifecycleStateInput(
        template_id="macd_rsi_reversal_v1", initial_fill_price=Decimal(10),
        initial_stop_price=Decimal(9), risk_capacity_shares=Decimal(1000),
        target_exposure_pct=Decimal("0.5"),
    )
    decision = _frame(DAY1, _fact(close="8.8", high="9.0"))
    still_pending = _frame(DAY2, _fact(close="10", high="10.1"))
    completed = DailyPolicyFrame(
        DAY3, uuid4(), {"is_suspended": False}, "raw", Decimal(0),
        completed_intent_reasons=("INITIAL_STOP_LOSS",),
        completion_source_ref="fixture:original-frozen-exit-intent",
    )
    def replay(terminal):
        return replay_lifecycle_policy(
            first_fill_date=FILL_DAY, seed_state=state,
            seed_target_shares=Decimal(500), seed_trailing=None,
            seed_expectation=None, management_policy=MACD_MEAN_REVERSION,
            calendar_dates=(DAY1, DAY2, DAY3),
            calendar_source_ref="fixture:calendar", corporate_action_dates=(),
            corporate_action_source_ref="fixture:action-coverage",
            frames=(decision, still_pending, terminal),
        )

    result = replay(completed)
    assert result.status == "PROVISIONAL"
    assert [day.reason_code for day in result.days] == [
        "INITIAL_STOP_LOSS", "EXIT_PENDING", "INITIAL_STOP_LOSS"]
    assert result.days[-1].phase == "CLOSED"
    wrong_reason = DailyPolicyFrame(
        DAY3, uuid4(), {"is_suspended": False}, "raw", Decimal(0),
        completed_intent_reasons=("EXIT_PENDING",),
        completion_source_ref="fixture:wrong-frozen-intent",
    )
    assert replay(wrong_reason).status == "UNKNOWN"


def test_zero_initial_target_cannot_masquerade_as_a_completed_exit():
    seed = LifecycleStateInput(
        template_id="macd_rsi_reversal_v1", initial_fill_price=Decimal(10),
        initial_stop_price=Decimal(9), risk_capacity_shares=Decimal(1000),
        target_exposure_pct=Decimal(0),
    )
    terminal = DailyPolicyFrame(
        DAY2, uuid4(), {"is_suspended": False}, "raw", Decimal(0),
        completed_intent_reasons=("EXIT_PENDING",),
        completion_source_ref="fixture:unproven-exit",
    )
    result = replay_lifecycle_policy(
        first_fill_date=FILL_DAY, seed_state=seed,
        seed_target_shares=Decimal(0), seed_trailing=None,
        seed_expectation=None, management_policy=MACD_MEAN_REVERSION,
        calendar_dates=(DAY1, DAY2), calendar_source_ref="fixture:calendar",
        corporate_action_dates=(),
        corporate_action_source_ref="fixture:action-coverage",
        frames=(_frame(DAY1, _fact()), terminal),
    )
    assert result.status == "UNKNOWN" and result.days == ()
    assert "POLICY_SEED_TARGET_INVALID" in result.issues
    assert "POLICY_SEED_VALUES_INVALID" in result.issues


def test_terminal_day_cannot_be_suspended_or_followed_by_active_replay():
    exit_day = _frame(DAY1, _fact(close="8.8", high="9.0"))
    terminal = DailyPolicyFrame(
        DAY2, uuid4(), {"is_suspended": False}, "raw", Decimal(0),
        completed_intent_reasons=("INITIAL_STOP_LOSS",),
        completion_source_ref="fixture:terminal-fill",
    )
    later = _frame(DAY3, _fact())
    assert _replay((DAY1, DAY2, DAY3), (exit_day, terminal, later)).status == "UNKNOWN"
    suspended_terminal = DailyPolicyFrame(
        DAY2, uuid4(), {"is_suspended": True}, "raw", Decimal(0),
        completed_intent_reasons=("INITIAL_STOP_LOSS",),
        completion_source_ref="fixture:suspended-terminal-fill",
    )
    assert _replay((DAY1, DAY2), (exit_day, suspended_terminal)).status == "UNKNOWN"
    positive_with_closing = _frame(
        DAY2, _fact(), completed_intent_reasons=("INITIAL_STOP_LOSS",),
        completion_source_ref="fixture:cannot-be-complete-with-shares",
    )
    result = _replay((DAY1, DAY2), (exit_day, positive_with_closing))
    assert result.status == "UNKNOWN"
    assert f"CLOSING_COMPLETION_WITH_HOLDING:{DAY2}" in result.issues


def test_invalid_revision_and_payload_return_unknown():
    malformed = DailyPolicyFrame(DAY1, [], None, "raw", Decimal(500))
    result = _replay((DAY1,), (malformed,))
    assert result.status == "UNKNOWN"
    assert f"DAILY_POLICY_INPUT_INVALID:{DAY1}" in result.issues


def test_family_expectation_and_trailing_skip_suspension_then_advance():
    state = LifecycleStateInput(
        template_id="ma5_pre_cross_v1", initial_fill_price=Decimal(10),
        initial_stop_price=Decimal(9), risk_capacity_shares=Decimal(1000),
        target_exposure_pct=Decimal("0.50"),
    )
    trailing = TrailingStateInput(high_water_mark=Decimal(10),
                                  active_stop_price=Decimal(9))
    expectation = ExpectationStateInput(fill_trade_date=FILL_DAY,
        observed_trading_days=0, window_trading_days=2, status="PENDING")
    frames = (
        _frame(DAY1, {"is_suspended": True}),
        _frame(DAY2, _fact(close="10.5", high="10.6", atr="1", ma5="10.0", ma20="10.2")),
        _frame(DAY3, _fact(close="10.6", high="10.7", atr="1", ma5="10.0", ma20="10.2")),
    )
    result = replay_lifecycle_policy(
        first_fill_date=FILL_DAY, seed_state=state, seed_target_shares=Decimal(500), seed_trailing=trailing,
        seed_expectation=expectation, management_policy=TREND_3ATR,
        calendar_dates=(DAY1, DAY2, DAY3), calendar_source_ref="fixture:calendar",
        corporate_action_dates=(), corporate_action_source_ref="fixture:action-coverage",
        frames=frames,
    )
    assert result.status == "PROVISIONAL"
    assert result.days[0].reason_code == "SUSPENDED_SESSION"
    assert result.days[0].expectation_observed_days == 0
    assert result.days[1].expectation_observed_days == 1
    assert result.days[2].reason_code == "EXPECTATION_TIMEOUT"
    assert result.final_expectation.status == "EXPIRED"
    assert result.final_trailing.high_water_mark >= Decimal("10.5")


def test_mid_lifecycle_expectation_snapshot_cannot_be_used_as_initial_seed():
    result = replay_lifecycle_policy(
        first_fill_date=FILL_DAY, seed_state=_state(),
        seed_target_shares=Decimal(500), seed_trailing=None,
        seed_expectation=ExpectationStateInput(
            fill_trade_date=FILL_DAY, observed_trading_days=2,
            window_trading_days=3, status="PENDING"),
        management_policy=None, calendar_dates=(DAY1,),
        calendar_source_ref="fixture:calendar", corporate_action_dates=(),
        corporate_action_source_ref="fixture:action-coverage",
        frames=(_frame(DAY1, _fact()),),
    )
    assert result.status == "UNKNOWN"
    assert "POLICY_EXPECTATION_SEED_INVALID" in result.issues
