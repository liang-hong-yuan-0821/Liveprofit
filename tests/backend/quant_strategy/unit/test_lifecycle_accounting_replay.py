# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle_accounting_replay（持仓生命周期、重放）：Pure arithmetic checks; no broker or database access.",
#   "keywords": [
#     "量化策略",
#     "每日",
#     "执行",
#     "费用",
#     "持仓生命周期",
#     "订单",
#     "仓位管理",
#     "数量",
#     "重放",
#     "lifecycle_accounting_replay",
#     "daily",
#     "execution",
#     "fee",
#     "lifecycle",
#     "order",
#     "position",
#     "quantity",
#     "replay"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/lifecycle_accounting_replay.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Pure arithmetic checks; no broker or database access."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal, Inexact, ROUND_DOWN, localcontext
from uuid import uuid4
from zoneinfo import ZoneInfo

from backend.modules.quant_strategy.application.lifecycle_accounting_replay import (
    ReplayFill, ReplaySplit, project_daily_quantities, replay_position_accounting,
)


CN = ZoneInfo("Asia/Shanghai")
BASELINE = datetime(2026, 9, 20, 12, tzinfo=CN)
DAY = date(2026, 9, 21)


def _fill(hour, minute, side, quantity, price, fee):
    return ReplayFill(uuid4(), datetime(2026, 9, 21, hour, minute, tzinfo=CN),
                      DAY, side, Decimal(quantity), Decimal(price),
                      Decimal(fee) if fee is not None else None, "fixture:reported-fill")


def _replay(*fills, splits=()):
    return replay_position_accounting(
        baseline_as_of=BASELINE, baseline_quantity=Decimal(0),
        baseline_total_cost=Decimal(0), baseline_source_ref="fixture:empty-baseline",
        fills=tuple(fills), splits=tuple(splits),
    )


def test_late_arriving_correction_replays_by_execution_time_and_fee():
    first = _fill(9, 30, "BUY", "100", "10", "2")
    second = _fill(10, 0, "BUY", "100", "12", "2")
    sell = _fill(14, 0, "SELL", "50", "15", "1")
    original = _replay(sell, second, first)  # arrival order is irrelevant
    assert original.status == "CALCULATED"
    assert original.event_ids == (first.event_id, second.event_id, sell.event_id)
    assert (original.quantity, original.total_cost, original.average_cost,
            original.realized_pnl) == (Decimal(150), Decimal(1653),
                                       Decimal("11.02"), Decimal(198))

    corrected_second = ReplayFill(second.event_id, second.executed_at, DAY, "BUY",
                                  Decimal(100), Decimal(11), Decimal(2), second.source_ref)
    corrected = _replay(sell, corrected_second, first)
    assert corrected.event_ids == original.event_ids
    assert (corrected.total_cost, corrected.average_cost, corrected.realized_pnl) == (
        Decimal(1578), Decimal("10.52"), Decimal(223))
    first_voided = _replay(sell, corrected_second)
    assert first_voided.status == "CALCULATED"
    assert first_voided.quantity == Decimal(50)
    assert first_voided.total_cost == Decimal(551)


def test_split_keeps_total_cost_and_fractional_rights_remain_unknown():
    buy = _fill(9, 30, "BUY", "100", "10", "2")
    split = ReplaySplit(uuid4(), datetime(2026, 9, 22, 0, tzinfo=CN),
                        Decimal(2), "fixture:exchange-action")
    result = _replay(buy, splits=(split,))
    assert (result.quantity, result.total_cost, result.average_cost) == (
        Decimal(200), Decimal(1002), Decimal("5.01"))
    fractional = ReplaySplit(uuid4(), split.effective_at, Decimal("1.005"), split.source_ref)
    unknown = _replay(buy, splits=(fractional,))
    assert unknown.status == "UNKNOWN"
    assert unknown.issues == (f"SPLIT_FRACTIONAL_RIGHTS:{fractional.event_id}",)


def test_unknown_fee_order_and_oversell_never_produce_a_partial_balance():
    buy = _fill(9, 30, "BUY", "100", "10", "2")
    missing_fee = _fill(10, 0, "BUY", "100", "11", None)
    assert f"FILL_VALUES_UNKNOWN:{missing_fee.event_id}" in _replay(buy, missing_fee).issues
    same_time = ReplayFill(uuid4(), buy.executed_at, DAY, "BUY",
                           Decimal(1), Decimal(10), Decimal(0), "fixture:fill")
    assert f"EVENT_ORDER_AMBIGUOUS:{same_time.event_id}" in _replay(buy, same_time).issues
    oversell = _fill(14, 0, "SELL", "101", "15", "1")
    result = _replay(buy, oversell)
    assert result.status == "UNKNOWN" and result.quantity is None
    assert result.issues == (f"SELL_EXCEEDS_REPLAY_HOLDING:{oversell.event_id}",)


def test_execution_date_and_baseline_must_be_explicit():
    wrong_day = ReplayFill(uuid4(), datetime(2026, 9, 22, 9, 30, tzinfo=CN),
                           DAY, "BUY", Decimal(100), Decimal(10), Decimal(0), "fixture:fill")
    assert f"FILL_TRADE_DATE_MISMATCH:{wrong_day.event_id}" in _replay(wrong_day).issues
    no_baseline = replay_position_accounting(
        baseline_as_of=BASELINE, baseline_quantity=Decimal(10),
        baseline_total_cost=Decimal(0), baseline_source_ref="",
        fills=(),
    )
    assert no_baseline.status == "UNKNOWN"
    assert "BASELINE_SOURCE_UNKNOWN" in no_baseline.issues
    assert "BASELINE_COST_INVALID" in no_baseline.issues


def test_external_decimal_context_cannot_change_split_or_cost_result():
    buy = _fill(9, 30, "BUY", "100", "10", "2")
    sell = _fill(14, 0, "SELL", "33", "11", "1")
    expected = _replay(buy, sell)
    with localcontext() as external:
        external.prec = 6
        external.rounding = ROUND_DOWN
        external.traps[Inexact] = True
        result = _replay(buy, sell)
        tiny_fraction = ReplaySplit(
            uuid4(), datetime(2026, 9, 22, 0, tzinfo=CN),
            Decimal("1.00000000001"), "fixture:exchange-action")
        fractional = _replay(buy, splits=(tiny_fraction,))
    assert result == expected
    assert fractional.status == "UNKNOWN"
    assert fractional.issues == (f"SPLIT_FRACTIONAL_RIGHTS:{tiny_fraction.event_id}",)


def test_extreme_finite_decimal_is_unknown_without_raising():
    extreme = _fill(9, 30, "BUY", "1e999999", "10", "0")
    result = _replay(extreme)
    assert result.status == "UNKNOWN"
    assert result.issues == (f"FILL_VALUES_UNKNOWN:{extreme.event_id}",)


def test_event_states_supply_daily_holdings_without_current_position_guess():
    buy = _fill(9, 30, "BUY", "100", "10", "2")
    split = ReplaySplit(uuid4(), datetime(2026, 9, 22, 0, tzinfo=CN),
                        Decimal(2), "fixture:exchange-action")
    sell = ReplayFill(uuid4(), datetime(2026, 9, 23, 14, tzinfo=CN),
                      date(2026, 9, 23), "SELL", Decimal(50), Decimal(12),
                      Decimal(1), "fixture:reported-fill")
    replay = _replay(sell, buy, splits=(split,))
    assert replay.status == "CALCULATED"
    assert tuple(state.quantity for state in replay.event_states) == (
        Decimal(100), Decimal(200), Decimal(150))
    daily = project_daily_quantities(
        accounting=replay,
        calendar_dates=(date(2026, 9, 21), date(2026, 9, 22),
                        date(2026, 9, 23), date(2026, 9, 24)))
    assert daily.status == "PROVISIONAL"
    assert daily.quantities == tuple(zip(
        (date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 24)),
        (Decimal(100), Decimal(200), Decimal(150), Decimal(150))))
    assert project_daily_quantities(
        accounting=replay,
        calendar_dates=(date(2026, 9, 21), date(2026, 9, 23), date(2026, 9, 22))
    ).status == "UNKNOWN"
    assert project_daily_quantities(
        accounting=_replay(_fill(10, 0, "BUY", "1", "10", None)),
        calendar_dates=(date(2026, 9, 21),)
    ).status == "UNKNOWN"
    assert project_daily_quantities(
        accounting=None, calendar_dates=(date(2026, 9, 21),)
    ).status == "UNKNOWN"
    assert project_daily_quantities(
        accounting=replace(replay, event_states=(object(),)),
        calendar_dates=(date(2026, 9, 21),)
    ).status == "UNKNOWN"


def test_daily_quantity_uses_the_accounting_replays_nonzero_baseline():
    baseline = datetime(2026, 9, 20, 12, tzinfo=CN)
    later_buy = ReplayFill(uuid4(), datetime(2026, 9, 22, 9, 30, tzinfo=CN),
                           date(2026, 9, 22), "BUY", Decimal(10), Decimal(10),
                           Decimal(0), "fixture:reported-fill")
    replay = replay_position_accounting(
        baseline_as_of=baseline, baseline_quantity=Decimal(100),
        baseline_total_cost=Decimal(1000), baseline_source_ref="fixture:opening-cost",
        fills=(later_buy,))
    daily = project_daily_quantities(
        accounting=replay,
        calendar_dates=(date(2026, 9, 21), date(2026, 9, 22)))
    assert daily.status == "PROVISIONAL"
    assert daily.quantities == ((date(2026, 9, 21), Decimal(100)),
                                (date(2026, 9, 22), Decimal(110)))
