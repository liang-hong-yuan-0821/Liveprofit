# test-catalog-begin
# {
#   "purpose": "量化策略 / order_materialization_quantity：The persisted first entry must stay on the instrument quantity grid.",
#   "keywords": [
#     "量化策略",
#     "认证证书",
#     "ETF",
#     "持仓生命周期",
#     "订单",
#     "数量",
#     "品种规则",
#     "复用",
#     "order_materialization_quantity",
#     "certificate",
#     "etf",
#     "lifecycle",
#     "order",
#     "quantity",
#     "rule",
#     "share"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/order_materialization.py",
#     "backend/modules/quant_strategy/domain/instrument_rules.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""The persisted first entry must stay on the instrument quantity grid."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.modules.quant_strategy.application.order_materialization import (
    initial_exposure_quantity, materialize_signal_orders,
)
from backend.modules.quant_strategy.domain.instrument_rules import InstrumentTradingRule


def _rule(*, minimum=200, step=1, maximum=100000):
    return InstrumentTradingRule(
        symbol="688001.SH", asset_type="stock", effective_from=date(2026, 7, 6),
        effective_through=None, published_on=date(2026, 4, 24),
        source_ref="diagnostic-source", price_tick=Decimal("0.01"),
        min_buy_quantity=minimum, buy_quantity_step=step,
        max_buy_quantity=maximum, roundtrip_days=1,
    )


def test_rule_grid_preserves_200_minimum_then_single_share_increment():
    assert initial_exposure_quantity(
        planned_shares=Decimal(500), initial_exposure=Decimal("0.5"), rule=_rule(),
    ) == 250
    assert initial_exposure_quantity(
        planned_shares=Decimal(399), initial_exposure=Decimal("0.5"), rule=_rule(),
    ) == 0
    assert initial_exposure_quantity(
        planned_shares=Decimal(430), initial_exposure=Decimal("0.5"), rule=_rule(step=10),
    ) == 210


def test_legacy_and_etf_grid_never_round_partial_entry_up():
    assert initial_exposure_quantity(
        planned_shares=Decimal(251), initial_exposure=Decimal("0.5"),
    ) == 100
    assert initial_exposure_quantity(
        planned_shares=Decimal(300), initial_exposure=Decimal("0.5"),
        rule=_rule(minimum=100, step=100),
    ) == 100


@pytest.mark.parametrize("planned,exposure", [
    (Decimal("NaN"), Decimal("0.5")),
    (Decimal(-1), Decimal("0.5")),
    (Decimal(100), Decimal(0)),
    (Decimal(100), Decimal("1.01")),
])
def test_invalid_initial_exposure_is_rejected(planned, exposure):
    with pytest.raises(ValueError):
        initial_exposure_quantity(planned_shares=planned, initial_exposure=exposure)


class _Session:
    def __init__(self):
        self.added = []

    def scalars(self, _query):
        return iter(())

    def scalar(self, _query):
        return None

    def add(self, row):
        self.added.append(row)


def _signal():
    return SimpleNamespace(
        id=1, ts_code="688001.SH", signal_kind="BUY", shares=Decimal(500),
        strategy_version_id=uuid4(), execution_market={"trade_date": "2026-09-18"},
        earliest_execution_trade_date=date(2026, 9, 21),
        order_cost_price=Decimal(10), order_entry_price=Decimal(10),
        valuation_price=Decimal(10), order_stop_price=Decimal(9),
        estimated_fees=Decimal(5), reason="entry", action="BUY",
    )


def test_materialization_rejects_supplied_rule_grid_without_certificate():
    session, signal = _Session(), _signal()
    policy = {"lifecycle_policy": {"config": {"initial_exposure_pct": "0.50"}}}
    orders = materialize_signal_orders(
        session, portfolio_id=uuid4(), rows=[signal],
        strategy_snapshots={str(signal.strategy_version_id): policy}, industry_map={},
        instrument_rules={signal.ts_code: _rule(step=10)},
    )
    assert orders == []
    assert session.added == []
    assert signal.order_status == "BUY_REJECTED_INSTRUMENT_RULE"


def test_explicit_rule_path_rejects_missing_or_late_rule_even_without_lifecycle_policy():
    for rules in ({}, {"688001.SH": _rule()}):
        session, signal = _Session(), _signal()
        if rules:
            signal.execution_market = {"trade_date": "2026-04-24"}
        orders = materialize_signal_orders(
            session, portfolio_id=uuid4(), rows=[signal],
            strategy_snapshots={}, industry_map={}, instrument_rules=rules,
        )
        assert orders == []
        assert session.added == []
        assert signal.order_status == "BUY_REJECTED_INSTRUMENT_RULE"


@pytest.mark.parametrize("decision,execution", [
    ("2026-09-18", date(2026, 9, 18)),
    ("2026-09-18", date(2026, 9, 22)),
    ("2026-09-25", date(2026, 9, 28)),
])
def test_explicit_rule_path_requires_open_decision_day_and_actual_next_session(decision, execution):
    session, signal = _Session(), _signal()
    signal.execution_market = {"trade_date": decision}
    signal.earliest_execution_trade_date = execution
    assert materialize_signal_orders(
        session, portfolio_id=uuid4(), rows=[signal], strategy_snapshots={},
        industry_map={}, instrument_rules={signal.ts_code: _rule()},
    ) == []
    assert signal.order_status == "BUY_REJECTED_INSTRUMENT_RULE"
