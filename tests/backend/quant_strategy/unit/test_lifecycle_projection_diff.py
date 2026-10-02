# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle_projection_diff（持仓生命周期）：Diagnostic field comparisons never infer missing projections as zero.",
#   "keywords": [
#     "量化策略",
#     "持仓生命周期",
#     "仓位管理",
#     "重放",
#     "lifecycle_projection_diff",
#     "lifecycle",
#     "position",
#     "replay"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/lifecycle_accounting_replay.py",
#     "backend/modules/quant_strategy/application/lifecycle_policy_replay.py",
#     "backend/modules/quant_strategy/application/lifecycle_projection_diff.py",
#     "backend/modules/quant_strategy/application/position_lifecycle_manager.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Diagnostic field comparisons never infer missing projections as zero."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

from backend.modules.quant_strategy.application.lifecycle_accounting_replay import AccountingReplay
from backend.modules.quant_strategy.application.lifecycle_policy_replay import (
    DailyPolicyProjection, PolicyReplay,
)
from backend.modules.quant_strategy.application.lifecycle_projection_diff import (
    CurrentLifecycleProjection, compare_lifecycle_projection,
)
from backend.modules.quant_strategy.application.position_lifecycle_manager import (
    ExpectationStateInput, LifecycleStateInput, TrailingStateInput,
)


def _inputs():
    state = LifecycleStateInput(
        template_id="ma_trend_cross_v1", initial_fill_price=Decimal(10),
        initial_stop_price=Decimal(9), risk_capacity_shares=Decimal(1000),
        target_exposure_pct=Decimal("0.5"),
    )
    day = DailyPolicyProjection(
        date(2026, 9, 22), uuid4(), "LIFECYCLE_HOLD", Decimal(500),
        Decimal("0.5"), None, None, None, None, None, "ACTIVE",
    )
    policy = PolicyReplay("PROVISIONAL", (), (day,), state, None, None, False)
    accounting = AccountingReplay("CALCULATED", (), Decimal(500),
                                   Decimal(5000), Decimal(10), Decimal(0), ())
    current = CurrentLifecycleProjection(
        Decimal(500), Decimal(10), Decimal(10), Decimal(9), Decimal(1000),
        Decimal(500), Decimal("0.5"), False, False,
        None, None, None, None, None, "ACTIVE", False,
        None, None,
    )
    return accounting, policy, current


def test_matching_values_are_only_provisional_and_mismatches_are_named():
    accounting, policy, current = _inputs()
    match = compare_lifecycle_projection(accounting=accounting, policy=policy, current=current)
    assert match.status == "PROVISIONAL_MATCH"
    assert not match.issues
    changed = compare_lifecycle_projection(
        accounting=accounting, policy=policy,
        current=replace(current, position_average_cost=Decimal("10.01"),
                        target_shares=Decimal(400)),
    )
    assert changed.status == "PROVISIONAL_DIFFERENCE"
    assert {item.field for item in changed.fields if item.status == "DIFFERENT"} == {
        "position.average_cost", "lifecycle.target_shares"}
    phase_changed = compare_lifecycle_projection(
        accounting=accounting, policy=policy,
        current=replace(current, phase="EXIT_PENDING"))
    assert phase_changed.status == "PROVISIONAL_DIFFERENCE"
    assert any(item.field == "lifecycle.phase" and item.status == "DIFFERENT"
               for item in phase_changed.fields)
    trim_changed = compare_lifecycle_projection(
        accounting=accounting, policy=replace(policy, profit_trim_completed=True),
        current=current)
    assert trim_changed.status == "PROVISIONAL_DIFFERENCE"
    assert any(item.field == "lifecycle.profit_trim_completed" and item.status == "DIFFERENT"
               for item in trim_changed.fields)
    trim_missing = compare_lifecycle_projection(
        accounting=accounting, policy=policy,
        current=replace(current, profit_trim_completed=None))
    assert trim_missing.status == "UNKNOWN"
    assert "CURRENT_FIELD_UNKNOWN:lifecycle.profit_trim_completed" in trim_missing.issues
    seed_field = compare_lifecycle_projection(
        accounting=accounting, policy=policy,
        current=replace(current, profit_take_price=Decimal(12)))
    assert seed_field.status == "PROVISIONAL_DIFFERENCE"
    assert any(item.field == "lifecycle.profit_take_price" and item.status == "DIFFERENT"
               for item in seed_field.fields)


def test_missing_current_position_and_unknown_replay_are_explicit():
    accounting, policy, current = _inputs()
    missing = compare_lifecycle_projection(
        accounting=accounting, policy=policy,
        current=replace(current, position_quantity=None, position_average_cost=None),
    )
    assert missing.status == "UNKNOWN"
    assert "CURRENT_FIELD_UNKNOWN:position.quantity" in missing.issues
    assert "CURRENT_FIELD_UNKNOWN:position.average_cost" in missing.issues
    unknown = compare_lifecycle_projection(
        accounting=AccountingReplay("UNKNOWN", ("FILL_SOURCE_UNKNOWN",),
                                    None, None, None, None, ()),
        policy=PolicyReplay("UNKNOWN", ("CALENDAR_DATES_INVALID",), (), None, None, None),
        current=current,
    )
    assert unknown.status == "UNKNOWN"
    assert "FILL_SOURCE_UNKNOWN" in unknown.issues
    assert "CALENDAR_DATES_INVALID" in unknown.issues
    assert all(item.status == "UNKNOWN" for item in unknown.fields[:2])


def test_missing_expected_expectation_and_trailing_rows_are_unknown():
    accounting, policy, current = _inputs()
    policy = replace(
        policy,
        final_expectation=ExpectationStateInput(
            fill_trade_date=date(2026, 9, 21), observed_trading_days=1,
            window_trading_days=3, status="PENDING"),
        final_trailing=TrailingStateInput(
            high_water_mark=Decimal("10.5"), active_stop_price=Decimal(9)),
    )
    result = compare_lifecycle_projection(
        accounting=accounting, policy=policy, current=current)
    assert result.status == "UNKNOWN"
    assert "CURRENT_FIELD_UNKNOWN:expectation.status" in result.issues
    assert "CURRENT_FIELD_UNKNOWN:trailing.high_water_mark" in result.issues
