# test-catalog-begin
# {
#   "purpose": "量化策略 / stock_medium_momentum",
#   "keywords": [
#     "量化策略",
#     "每日",
#     "个股分析",
#     "stock_medium_momentum",
#     "daily",
#     "stock"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/domain/stock_inputs.py",
#     "backend/modules/quant_strategy/domain/stock_medium_momentum.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pandas as pd

from backend.modules.quant_strategy.domain.stock_inputs import StockCandidate
from backend.modules.quant_strategy.domain.stock_medium_momentum import (
    build_medium_momentum_target,
    medium_momentum_exit,
)

DAY = date(2026, 9, 21)


def _bars(prices):
    dates = (stamp.date() for stamp in pd.bdate_range(end=DAY, periods=len(prices)))
    return tuple(zip(dates, (Decimal(str(price)) for price in prices)))


def _stock(code="000001.SZ", *, held=False, final=111):
    prices = [100] * 250
    prices[-6] = 110
    prices[-1] = final
    return StockCandidate(
        ts_code=code, adjusted_bars=_bars(prices), listed_sessions=300,
        adv20_cny=Decimal(30000000), is_st=False, suspended=False, held=held,
    )


def _target(candidates, *, month_end=True, benchmark=None):
    return build_medium_momentum_target(
        candidates, benchmark or _bars([100] * 199 + [101]),
        decision_date=DAY, valid_until=date(2026, 10, 21),
        evaluation_as_of=DAY, strategy_version_id=uuid4(),
        lookback=120, slots=10, benchmark_ma=200,
        is_month_end_rebalance=month_end,
    )


def test_monthly_momentum_includes_held_slot_and_uses_t_minus_five():
    first = _target((_stock(held=True, final=111),))
    changed_today = _target((_stock(held=True, final=80),))
    assert first is not None and changed_today is not None
    assert first.legs == changed_today.legs
    assert first.legs[0].family_weight == Decimal("0.1")
    assert _target((_stock(),), month_end=False) is None


def test_benchmark_trend_and_daily_exit_predicates():
    assert _target((_stock(),), benchmark=_bars([100] * 200)) is None
    assert medium_momentum_exit(
        adjusted_close=Decimal(89), highest_close_since_entry=Decimal(100),
        benchmark_trend_ok=True,
    ).exit_reason == "PEAK_DRAWDOWN_10PCT"
    assert medium_momentum_exit(
        adjusted_close=Decimal(99), highest_close_since_entry=Decimal(100),
        benchmark_trend_ok=False,
    ).exit_reason == "BENCHMARK_TREND_LOST"
    assert medium_momentum_exit(
        adjusted_close=Decimal(95), highest_close_since_entry=Decimal(100),
        benchmark_trend_ok=True,
    ).exit_reason is None
    missing_benchmark = medium_momentum_exit(
        adjusted_close=Decimal(89), highest_close_since_entry=Decimal(100),
        benchmark_trend_ok=None,
    )
    assert missing_benchmark.exit_reason == "PEAK_DRAWDOWN_10PCT"
    assert missing_benchmark.benchmark_evidence_missing
    no_exit = medium_momentum_exit(
        adjusted_close=Decimal(95), highest_close_since_entry=Decimal(100),
        benchmark_trend_ok=None,
    )
    assert no_exit.exit_reason is None and no_exit.benchmark_evidence_missing
