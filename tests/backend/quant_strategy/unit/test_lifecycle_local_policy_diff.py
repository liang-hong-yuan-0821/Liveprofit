# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle_local_policy_diff（持仓生命周期）",
#   "keywords": [
#     "量化策略",
#     "持仓生命周期",
#     "重放",
#     "lifecycle_local_policy_diff",
#     "lifecycle",
#     "replay"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/lifecycle_accounting_replay.py",
#     "backend/modules/quant_strategy/application/lifecycle_local_policy_diff.py",
#     "backend/modules/quant_strategy/application/lifecycle_local_policy_frames.py",
#     "backend/modules/quant_strategy/application/lifecycle_policy_replay.py",
#     "backend/modules/quant_strategy/application/lifecycle_projection_diff.py",
#     "backend/modules/quant_strategy/application/position_lifecycle_manager.py"
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

from backend.modules.quant_strategy.application.lifecycle_accounting_replay import (
    ReplayFill, replay_position_accounting,
)
from backend.modules.quant_strategy.application.lifecycle_local_policy_diff import compare_local_policy_frames
from backend.modules.quant_strategy.application.lifecycle_local_policy_frames import LocalPolicyFrames
from backend.modules.quant_strategy.application.lifecycle_policy_replay import (
    DailyPolicyFrame, replay_lifecycle_policy,
)
from backend.modules.quant_strategy.application.lifecycle_projection_diff import CurrentLifecycleProjection
from backend.modules.quant_strategy.application.position_lifecycle_manager import LifecycleStateInput


def test_local_policy_diff_keeps_a_matching_replay_provisional():
    fill_day, session_day = date(2026, 9, 21), date(2026, 9, 22)
    fill = ReplayFill(uuid4(), datetime(2026, 9, 21, 3, tzinfo=timezone.utc),
                      fill_day, "BUY", Decimal(500), Decimal(10), Decimal(0),
                      "local-report:initial")
    accounting = replay_position_accounting(
        baseline_as_of=datetime(2026, 9, 20, tzinfo=timezone.utc),
        baseline_quantity=Decimal(0), baseline_total_cost=Decimal(0),
        baseline_source_ref="declared-zero-position", fills=(fill,))
    state = LifecycleStateInput(
        template_id="ma_trend_cross_v1", initial_fill_price=Decimal(10),
        initial_stop_price=Decimal(9), risk_capacity_shares=Decimal(1000),
        target_exposure_pct=Decimal("0.50"), profit_take_price=Decimal(12))
    frame = DailyPolicyFrame(session_day, uuid4(), {
        "is_suspended": False, "close": "10.5", "high": "10.6",
        "ma5": "10.3", "prev_ma5": "10.0", "ma20": "10.2",
        "prev_ma20": "10.1", "ma60": "9.5", "macd": "0.3",
        "prev_macd": "0.2",
    }, "raw", Decimal(500))
    prepared = LocalPolicyFrames("PROVISIONAL", (frame,),
                                 ("POLICY_FRAME_SOURCE_UNCERTIFIED",), (True,), 2,
                                 1, datetime(2026, 9, 22, 8, tzinfo=timezone.utc))
    common = dict(first_fill_date=fill_day, seed_state=state,
                  seed_target_shares=Decimal(500), seed_trailing=None,
                  seed_expectation=None, management_policy=None,
                  calendar_dates=(session_day,), calendar_source_ref="declared-calendar",
                  corporate_action_dates=(),
                  corporate_action_source_ref="declared-action-coverage")
    expected = replay_lifecycle_policy(frames=(frame,), **common)
    assert expected.status == "PROVISIONAL"
    last = expected.days[-1]
    final = expected.final_state
    current = CurrentLifecycleProjection(
        accounting.quantity, accounting.average_cost,
        final.initial_fill_price, final.initial_stop_price,
        final.risk_capacity_shares, last.target_shares,
        final.target_exposure_pct, final.profit_target_reached,
        final.confirmation_completed, None, None, None, None, None,
        last.phase, False, Decimal(12), None, None, session_day, 2)

    result = compare_local_policy_frames(
        accounting=accounting, prepared=prepared, current=current, **common)
    assert result.status == "PROVISIONAL_MATCH"
    assert "LOCAL_POLICY_DIFF_SOURCE_UNCERTIFIED" in result.issues
    different = compare_local_policy_frames(
        accounting=accounting, prepared=prepared,
        current=replace(current, position_quantity=Decimal(400)), **common)
    assert different.status == "PROVISIONAL_DIFFERENCE"
    assert any(field.field == "position.quantity" and field.status == "DIFFERENT"
               for field in different.diff.fields)
    stale_processed_day = compare_local_policy_frames(
        accounting=accounting, prepared=prepared,
        current=replace(current, last_processed_trade_date=fill_day), **common)
    assert stale_processed_day.status == "PROVISIONAL_DIFFERENCE"
    assert any(field.field == "lifecycle.last_processed_trade_date"
               and field.status == "DIFFERENT" for field in stale_processed_day.diff.fields)
    false_advance = compare_local_policy_frames(
        accounting=accounting,
        prepared=replace(prepared, version_advances=(False,)),
        current=current, **common)
    assert false_advance.status == "UNKNOWN" and false_advance.diff is None
    assert "DAILY_POLICY_VERSION_ADVANCE_MISMATCH" in false_advance.issues
    later_version = compare_local_policy_frames(
        accounting=accounting, prepared=prepared,
        current=replace(current, state_version=3), **common)
    assert later_version.status == "UNKNOWN" and later_version.diff is None
    assert "LIFECYCLE_VERSION_AFTER_DAILY_UNATTRIBUTED" in later_version.issues
    first_version_jump = compare_local_policy_frames(
        accounting=accounting,
        prepared=replace(prepared, starting_state_version=100,
                         ending_state_version=101),
        current=replace(current, state_version=101), **common)
    assert first_version_jump.status == "UNKNOWN" and first_version_jump.diff is None
    assert "POLICY_FIRST_VERSION_UNATTRIBUTED" in first_version_jump.issues
    mismatch = compare_local_policy_frames(
        accounting=accounting,
        prepared=replace(prepared, frames=(replace(frame, actual_shares=Decimal(400)),)),
        current=current, **common)
    assert mismatch.status == "UNKNOWN" and mismatch.diff is None

    later_day = date(2026, 9, 23)
    later_fill = ReplayFill(
        uuid4(), datetime(2026, 9, 23, 3, tzinfo=timezone.utc),
        later_day, "BUY", Decimal(100), Decimal(10), Decimal(0),
        "local-report:later")
    two_fill_accounting = replay_position_accounting(
        baseline_as_of=datetime(2026, 9, 20, tzinfo=timezone.utc),
        baseline_quantity=Decimal(0), baseline_total_cost=Decimal(0),
        baseline_source_ref="declared-zero-position", fills=(fill, later_fill))
    middle_wrong = compare_local_policy_frames(
        accounting=two_fill_accounting,
        prepared=replace(prepared, frames=(
            replace(frame, actual_shares=Decimal(400)),
            replace(frame, trade_date=later_day, revision_id=uuid4(),
                    actual_shares=Decimal(600)))),
        current=current, **{**common, "calendar_dates": (session_day, later_day)})
    assert middle_wrong.status == "UNKNOWN" and middle_wrong.diff is None
    assert "LOCAL_POLICY_ACCOUNTING_DAILY_MISMATCH" in middle_wrong.issues
