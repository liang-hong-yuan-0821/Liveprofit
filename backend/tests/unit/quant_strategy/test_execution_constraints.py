from datetime import date
from decimal import Decimal

from backend.modules.quant_strategy.application.execution_constraints import (
    BUY_REJECTED_LIMIT_UP,
    BUY_REJECTED_LIQUIDITY,
    BUY_REJECTED_ST,
    BUY_REJECTED_SUSPENDED,
    SELL_REJECTED_LIMIT_DOWN,
    SELL_REJECTED_SUSPENDED,
    SELL_REJECTED_T1,
    ExecutionConstraintEvaluator,
    ExecutionPolicy,
    PriceBasisMapper,
)


def _market(**overrides):
    value = {
        "trade_date": "2026-09-18",
        "qfq_close": 10,
        "raw_close": 20,
        "raw_amount": 1000,
        "is_suspended": False,
        "is_st": False,
        "up_limit": 22,
        "down_limit": 18,
    }
    value.update(overrides)
    return value


def test_qfq_to_raw_and_buy_tick_slippage_are_separate_prices():
    evaluator = ExecutionConstraintEvaluator(ExecutionPolicy())
    decision = evaluator.evaluate_buy(entry=10, stop=9, take=13, market=_market())
    assert PriceBasisMapper.qfq_to_raw(9, qfq_close=10, raw_close=20) == Decimal("18")
    assert decision.prices.entry == Decimal("20.02")
    assert decision.prices.stop == Decimal("18")
    assert decision.prices.take == Decimal("26")
    assert decision.earliest_execution_trade_date == date(2026, 9, 21)


def test_buy_trading_status_and_liquidity_fail_closed():
    evaluator = ExecutionConstraintEvaluator(ExecutionPolicy())
    assert evaluator.evaluate_buy(entry=10, stop=9, take=13, market=_market(is_suspended=True)).code == BUY_REJECTED_SUSPENDED
    assert evaluator.evaluate_buy(entry=10, stop=9, take=13, market=_market(is_st=True)).code == BUY_REJECTED_ST
    assert evaluator.evaluate_buy(entry=10, stop=9, take=13, market=_market(raw_close=22)).code == BUY_REJECTED_LIMIT_UP
    assert evaluator.evaluate_buy(entry=10, stop=9, take=13, market=_market(raw_amount=0)).code == BUY_REJECTED_LIQUIDITY


def test_liquidity_participation_and_fees():
    evaluator = ExecutionConstraintEvaluator(ExecutionPolicy())
    decision = evaluator.evaluate_buy(entry=10, stop=9, take=13, market=_market(raw_amount=100))
    # 成交额 10 万元 × 5% / 20.02，按 100 股整手向下。
    assert decision.max_liquidity_shares == Decimal("200")
    assert evaluator.fees(Decimal("1000"), side="BUY") == Decimal("5.01")
    assert evaluator.fees(Decimal("10000"), side="SELL") == Decimal("10.10")


def test_sell_constraints_and_t1_available_quantity():
    evaluator = ExecutionConstraintEvaluator(ExecutionPolicy())
    assert evaluator.evaluate_sell(market=_market(is_suspended=True), available_quantity=100)[0] == SELL_REJECTED_SUSPENDED
    assert evaluator.evaluate_sell(market=_market(raw_close=18), available_quantity=100)[0] == SELL_REJECTED_LIMIT_DOWN
    assert evaluator.evaluate_sell(market=_market(), available_quantity=0)[0] == SELL_REJECTED_T1
    code, price, slippage, earliest = evaluator.evaluate_sell(market=_market(), available_quantity=100)
    assert code is None
    assert price == Decimal("19.98")
    assert slippage == Decimal("0.02")
    assert earliest == date(2026, 9, 21)


def test_low_price_tick_and_board_specific_limits_use_market_facts():
    evaluator = ExecutionConstraintEvaluator(ExecutionPolicy())
    low = _market(qfq_close=Decimal("0.51"), raw_close=Decimal("0.51"), up_limit=Decimal("0.56"))
    decision = evaluator.evaluate_buy(entry=Decimal("0.51"), stop=Decimal("0.49"), take=Decimal("0.60"), market=low)
    assert decision.prices.entry == Decimal("0.52")
    # 创业板等差异不硬编码百分比，而以当日交易状态表给出的板块限价为事实。
    chinext_limit = _market(raw_close=24, qfq_close=20, up_limit=24, market_board="CHINEXT")
    assert evaluator.evaluate_buy(entry=20, stop=18, take=26, market=chinext_limit).code == BUY_REJECTED_LIMIT_UP
