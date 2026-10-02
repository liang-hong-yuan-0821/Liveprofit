# test-catalog-begin
# {
#   "purpose": "量化策略 / instrument_rules",
#   "keywords": [
#     "量化策略",
#     "证券数据",
#     "品种规则",
#     "来源证据",
#     "instrument_rules",
#     "instrument",
#     "rule",
#     "source"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/domain/instrument_rules.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from datetime import date
from decimal import Decimal

import pytest

from backend.modules.quant_strategy.domain.instrument_rules import (
    InstrumentRuleUnavailable, InstrumentTradingRule, resolve_instrument_rule,
)


def _rule(**changes):
    values = dict(symbol="688001.SH", asset_type="stock",
                  effective_from=date(2026, 4, 24), effective_through=None,
                  published_on=date(2026, 4, 24), source_ref="exchange/rule/2026",
                  price_tick=Decimal("0.01"), min_buy_quantity=200,
                  buy_quantity_step=1, max_buy_quantity=100000, roundtrip_days=1)
    values.update(changes)
    return InstrumentTradingRule(**values)


def test_star_step_and_capacity_floor():
    rule = _rule()
    assert not rule.permits_buy_quantity(100)
    assert rule.permits_buy_quantity(201)
    assert rule.floor_buy_quantity(199) == 0
    assert rule.floor_buy_quantity(499) == 499
    assert rule.floor_buy_quantity(200000) == 100000


def test_date_source_and_identity_must_identify_one_rule():
    day = date(2026, 9, 29)
    assert resolve_instrument_rule([_rule()], symbol="688001.SH",
                                   decision_date=date(2026, 9, 28), execution_date=day).symbol == "688001.SH"
    assert resolve_instrument_rule([_rule(), _rule(symbol="688002.SH", price_tick=Decimal("NaN"))],
                                   symbol="688001.SH", decision_date=date(2026, 9, 28),
                                   execution_date=day).symbol == "688001.SH"
    for rules, symbol in [([], "688001.SH"), ([_rule()], "688002.SH"),
                          ([_rule(), _rule()], "688001.SH"),
                          ([_rule(published_on=day)], "688001.SH"),
                          ([_rule(published_on=date(2026, 9, 28))], "688001.SH"),
                          ([_rule(effective_through=date(2026, 9, 28))], "688001.SH")]:
        with pytest.raises(InstrumentRuleUnavailable):
            resolve_instrument_rule(rules, symbol=symbol,
                                    decision_date=date(2026, 9, 28), execution_date=day)


@pytest.mark.parametrize("changes", [
    {"price_tick": Decimal("NaN")}, {"source_ref": ""},
    {"price_tick": "0.01"},
    {"min_buy_quantity": 0}, {"buy_quantity_step": 0},
    {"max_buy_quantity": 100}, {"roundtrip_days": 2},
    {"effective_through": date(2026, 4, 23)},
])
def test_malformed_rule_rejected(changes):
    with pytest.raises(InstrumentRuleUnavailable):
        resolve_instrument_rule([_rule(**changes)], symbol="688001.SH",
                                decision_date=date(2026, 9, 28),
                                execution_date=date(2026, 9, 29))
