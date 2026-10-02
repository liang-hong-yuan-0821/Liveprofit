# test-catalog-begin
# {
#   "purpose": "量化研究 / opening_execution",
#   "keywords": [
#     "量化研究",
#     "ETF",
#     "执行",
#     "成交",
#     "品种规则",
#     "复用",
#     "opening_execution",
#     "etf",
#     "execution",
#     "fill",
#     "rule",
#     "share"
#   ],
#   "covers": [
#     "backend/modules/quant_research/domain/opening_execution.py",
#     "backend/modules/quant_strategy/domain/instrument_rules.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.modules.quant_research.domain.opening_execution import simulate_opening_buy
from backend.modules.quant_strategy.domain.instrument_rules import InstrumentTradingRule


def _rule(**changes):
    values = dict(symbol="600000.SH", asset_type="stock",
                  effective_from=date(2026, 4, 24), effective_through=None,
                  published_on=date(2026, 4, 24), source_ref="exchange/rule/2026",
                  price_tick=Decimal("0.01"), min_buy_quantity=100,
                  buy_quantity_step=100, max_buy_quantity=1000000,
                  roundtrip_days=1)
    values.update(changes)
    return InstrumentTradingRule(**values)


def _fill(**changes):
    days = (date(2026, 9, 24), date(2026, 9, 28), date(2026, 9, 29))
    schedule = SimpleNamespace(available=True, supported_from=days[0],
                               supported_through=days[-1],
                               sessions=[SimpleNamespace(trade_date=day) for day in days])
    calendar = SimpleNamespace(schedule=lambda market: schedule)
    args = dict(symbol="600000.SH", rules=[_rule()],
                exchange_calendar=calendar,
                decision_date=date(2026, 9, 24), adv20_asof=date(2026, 9, 24),
                adv20_evidence_ref="snapshot/known-at-2026-09-24",
                execution_date=date(2026, 9, 28), requested_quantity=1000,
                entry_lower="9.90", entry_upper="10.03", next_open="10",
                adv20_cny="500000", suspended=False, limit_up_locked=False)
    args.update(changes)
    return simulate_opening_buy(**args)


def test_capacity_uses_t_day_adv20_and_ignores_next_day_turnover():
    low = _fill(next_day_turnover_cny="100")
    high = _fill(next_day_turnover_cny="1000000000")
    assert low == high
    assert low.quantity == 400
    assert low.capacity_quantity == 400
    assert low.price == Decimal("10.01")


def test_etf_slippage_and_partial_lot_capacity():
    result = _fill(symbol="510300.SH", rules=[_rule(symbol="510300.SH", asset_type="etf")],
                   entry_upper="10.01", adv20_cny="1000000")
    assert result.code == "PARTIAL"
    assert result.price == Decimal("10.01")
    assert result.quantity == 900


def test_double_slippage_sensitivity_reprices_without_future_turnover():
    result = _fill(next_open="10", slippage_multiplier=2)
    assert result.price == Decimal("10.02")
    assert result.quantity == 400


def test_gap_outside_entry_interval_and_one_word_limit_do_not_fill():
    assert _fill(next_open="10.04").code == "OUTSIDE_ENTRY_INTERVAL"
    assert _fill(next_open="9.80").code == "OUTSIDE_ENTRY_INTERVAL"
    assert _fill(limit_up_locked=True).code == "LIMIT_UP_LOCKED"
    assert _fill(suspended=True).code == "SUSPENDED"
    assert _fill(suspended=True, next_open=None).code == "SUSPENDED"


@pytest.mark.parametrize("changes", [
    {"adv20_cny": None}, {"adv20_cny": "NaN"},
    {"entry_upper": "10.005"}, {"entry_lower": "10.04"},
    {"requested_quantity": 101}, {"suspended": None},
    {"adv20_asof": date(2026, 9, 28)}, {"execution_date": date(2026, 9, 24)},
    {"decision_date": date(2026, 9, 25), "adv20_asof": date(2026, 9, 25)},
    {"execution_date": date(2026, 9, 29)}, {"exchange_calendar": None},
    {"adv20_evidence_ref": ""}, {"slippage_multiplier": 0},
    {"rules": []}, {"rules": [_rule(published_on=date(2026, 9, 28))]},
    {"rules": [_rule(effective_through=date(2026, 9, 24))]},
    {"rules": [_rule(), _rule()]},
])
def test_missing_or_invalid_dated_input_rejects(changes):
    with pytest.raises(ValueError):
        _fill(**changes)


def test_star_rule_uses_minimum_200_and_one_share_increment():
    star = _rule(symbol="688001.SH", min_buy_quantity=200,
                 buy_quantity_step=1, max_buy_quantity=100000)
    result = _fill(symbol="688001.SH", rules=[star], requested_quantity=1000)
    assert result.quantity == 499
    assert result.capacity_quantity == 499
    assert _fill(symbol="688001.SH", rules=[star], requested_quantity=201).quantity == 201
    with pytest.raises(ValueError):
        _fill(symbol="688001.SH", rules=[star], requested_quantity=100)


def test_rule_category_is_not_inferred_from_symbol_or_etf_label():
    etf = _rule(symbol="510300.SH", asset_type="etf", roundtrip_days=1)
    assert _fill(symbol="510300.SH", rules=[etf], entry_upper="10.01").price == Decimal("10.01")
    assert etf.roundtrip_days == 1
    with pytest.raises(ValueError):
        _fill(symbol="510500.SH", rules=[etf])
