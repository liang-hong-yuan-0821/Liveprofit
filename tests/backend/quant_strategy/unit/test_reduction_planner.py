# test-catalog-begin
# {
#   "purpose": "量化策略 / reduction_planner",
#   "keywords": [
#     "量化策略",
#     "交易日历",
#     "数据完整性",
#     "规划器",
#     "收益",
#     "停牌",
#     "reduction_planner",
#     "calendar",
#     "coverage",
#     "planner",
#     "returns",
#     "suspension"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/execution_constraints.py",
#     "backend/modules/quant_strategy/application/position_planner.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from backend.modules.quant_strategy.application.execution_constraints import (
    ExecutionConstraintEvaluator, ExecutionPolicy, EXECUTION_CALENDAR_UNAVAILABLE,
)
from backend.modules.quant_strategy.application.position_planner import plan_reduction


def plan(**kwargs):
    args = dict(target_quantity=D(0), actual_quantity=D(1000), available_quantity=D(1000),
                reserved_quantity=D(0), market={"trade_date": "2026-09-24", "raw_close": "10",
                                                "down_limit": "9", "is_suspended": False},
                execution=ExecutionConstraintEvaluator(ExecutionPolicy()))
    args.update(kwargs)
    return plan_reduction(**args)


def test_real_exchange_holiday_is_not_a_weekday():
    result = plan()
    assert result.earliest_execution_trade_date == date(2026, 9, 28)
    assert result.price == D("9.99") and result.fees > 0


def test_pending_sell_reservations_and_partial_fills_cannot_oversell():
    assert plan(reserved_quantity=D(600)).quantity == 400
    assert plan(actual_quantity=D(800), available_quantity=D(800), reserved_quantity=D(400)).quantity == 400
    assert plan(reserved_quantity=D(1000)).quantity == 0
    assert plan(target_quantity=D(600), reserved_quantity=D(300)).quantity == 100
    assert plan(available_quantity=D(0)).code == "SELL_REJECTED_T1"


def test_liquidation_keeps_odd_lots_partial_reduction_rounds_down():
    assert plan(actual_quantity=D(1050), available_quantity=D(1050)).quantity == 1050
    assert plan(actual_quantity=D(1050), available_quantity=D(1050), target_quantity=D(600)).quantity == 400


@pytest.mark.parametrize("override", [{"trade_date": None}, {"trade_date": "bad"},
                                       {"raw_close": "bad"}, {"raw_close": "NaN"},
                                       {"down_limit": None}, {"is_suspended": None}])
def test_missing_or_invalid_evidence_returns_explicit_rejection(override):
    market = {"trade_date": "2026-09-24", "raw_close": "10", "down_limit": "9", "is_suspended": False}
    assert plan(market={**market, **override}).code == "SELL_MARKET_FACTS_UNAVAILABLE"


def test_calendar_unavailable_or_outside_coverage_cannot_guess_weekdays():
    schedule = SimpleNamespace(available=False, supported_from=date(2026, 1, 1),
                               supported_through=date(2026, 12, 31), sessions=())
    calendar = SimpleNamespace(schedule=lambda market: schedule)
    execution = ExecutionConstraintEvaluator(ExecutionPolicy(), calendar=calendar)
    assert plan(execution=execution).code == EXECUTION_CALENDAR_UNAVAILABLE
    schedule.available = True
    assert plan(execution=execution).earliest_execution_trade_date is None


def test_limits_and_suspension_apply_to_every_reduction():
    market = {"trade_date": "2026-09-24", "raw_close": "10", "down_limit": "10", "is_suspended": False}
    assert plan(market=market).code == "SELL_REJECTED_LIMIT_DOWN"
    assert plan(market={**market, "is_suspended": True}).code == "SELL_REJECTED_SUSPENDED"
