# test-catalog-begin
# {
#   "purpose": "量化策略 / stock_short_reversion",
#   "keywords": [
#     "量化策略",
#     "风险",
#     "品种规则",
#     "个股分析",
#     "stock_short_reversion",
#     "risk",
#     "rule",
#     "stock"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/domain/stock_inputs.py",
#     "backend/modules/quant_strategy/domain/stock_short_reversion.py"
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
from backend.modules.quant_strategy.domain.stock_short_reversion import (
    _z_score,
    build_short_reversion_target,
)

DAY = date(2026, 9, 21)


def _bars(prices):
    dates = (stamp.date() for stamp in pd.bdate_range(end=DAY, periods=len(prices)))
    return tuple(zip(dates, (Decimal(str(price)) for price in prices)))


def _stock(code="000001.SZ", *, held=False, is_st=False, cooldown=None, prices=None):
    return StockCandidate(
        ts_code=code, adjusted_bars=_bars(prices or [80] * 225 + [100] * 24 + [95]),
        listed_sessions=300, adv20_cny=Decimal(30000000),
        is_st=is_st, suspended=False, held=held, cooldown_until=cooldown,
    )


def _target(candidates, benchmark=None):
    return build_short_reversion_target(
        candidates, benchmark or _bars([100] * 119 + [101]),
        decision_date=DAY, valid_until=date(2026, 9, 24),
        evaluation_as_of=DAY, strategy_version_id=uuid4(),
        reversal_days=2, z_threshold=Decimal("1.5"), exit_days=3,
    )


def test_oversold_stock_gets_one_of_ten_slots():
    target = _target((_stock(),))
    assert target is not None
    assert [leg.ts_code for leg in target.legs] == ["000001.SZ"]
    assert target.legs[0].family_weight == Decimal("0.1")
    assert target.policy_id == "STOCK_REVERSION_RULE_BASED"


def test_benchmark_and_current_state_block_new_risk():
    stock = _stock()
    assert _target((stock,), _bars([100] * 120)) is None
    assert _target((_stock(held=True),)) is None
    assert _target((_stock(is_st=True),)) is None
    assert _target((_stock(cooldown=DAY),)) is None
    assert _target((_stock(prices=[100] * 249 + [95]),)) is None


def test_entry_below_ma5_prevents_immediate_rule_exit():
    prices = [100] * 245 + [70, 70, 120, 70, 100]
    stock = _stock(prices=prices)
    assert _z_score(stock.adjusted_bars, 2) is None
    assert _target((stock,)) is None


def test_today_move_is_excluded_from_prior_volatility_window():
    before = _stock(prices=[100] * 249 + [95])
    # A large decision-day move changes the numerator, but previous 20-return sigma
    # is still zero and therefore uses the declared 0.005 floor.
    assert _z_score(before.adjusted_bars, 2) > 1.5
    volatile = _stock(prices=[100] * 228 + [90, 110] * 10 + [100, 95])
    assert _z_score(volatile.adjusted_bars, 2) < 1.5


def test_price_equal_to_own_ma120_is_not_an_entry_even_with_large_reversal():
    stock = _stock(prices=[100] * 130 + [95] * 97 + [165] + [120] * 21 + [100])
    tail = [price for _, price in stock.adjusted_bars[-120:]]
    assert sum(tail, Decimal(0)) / 120 == tail[-1]
    assert _z_score(stock.adjusted_bars, 2) > 1.5
    assert _target((stock,)) is None
