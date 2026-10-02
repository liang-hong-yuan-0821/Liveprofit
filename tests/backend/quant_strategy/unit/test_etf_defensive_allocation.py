# test-catalog-begin
# {
#   "purpose": "量化策略 / etf_defensive_allocation",
#   "keywords": [
#     "量化策略",
#     "现金",
#     "每日",
#     "ETF",
#     "风险",
#     "etf_defensive_allocation",
#     "cash",
#     "daily",
#     "etf",
#     "risk"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/domain/etf_defensive_allocation.py",
#     "backend/modules/quant_strategy/domain/etf_dual_momentum.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pandas as pd
import pytest

from backend.modules.quant_strategy.domain.etf_defensive_allocation import (
    build_defensive_target,
    select_defensive_representatives,
)
from backend.modules.quant_strategy.domain.etf_dual_momentum import FundCandidate

DAY = date(2026, 9, 21)


def _fund(code, category, prices=None, adv=30_000_000):
    prices = prices or [100 + index * 0.01 for index in range(250)]
    dates = (stamp.date() for stamp in pd.bdate_range(end=DAY, periods=len(prices)))
    return FundCandidate(
        ts_code=code, tracking_index=category, category=category, trade_date=DAY,
        listed_sessions=300, adv20_cny=Decimal(adv),
        adjusted_bars=tuple(zip(dates, (Decimal(str(price)) for price in prices))),
        atr20_raw=Decimal("0.5"),
    )


def _target(candidates, frozen, *, weekly=True, previous=None, vol="0.04"):
    return build_defensive_target(
        candidates, frozen_symbols=frozen, decision_date=DAY,
        valid_until=date(2026, 9, 28), evaluation_as_of=DAY,
        strategy_version_id=uuid4(), cov_window=20, vol_target=Decimal(vol),
        trend_ma=60, is_weekly_rebalance=weekly, previous_weights=previous,
    )


def test_base_weights_and_monthly_representatives():
    equity = _fund("510300.SH", "EQUITY_300")
    duplicate = _fund("159919.SZ", "EQUITY_300", adv=20_000_000)
    bond = _fund("511010.SH", "BOND_MEDIUM")
    gold = _fund("518880.SH", "GOLD")
    candidates = (equity, duplicate, bond, gold)
    frozen = select_defensive_representatives(candidates, decision_date=DAY)
    assert ("EQUITY_300", "510300.SH") in frozen
    target = _target(candidates, frozen)
    assert {leg.ts_code: leg.family_weight for leg in target.legs} == {
        "510300.SH": Decimal("0.4"), "511010.SH": Decimal("0.4"),
        "518880.SH": Decimal("0.2"),
    }


def test_previous_session_defensive_representative_survives_weekend():
    as_of = date(2026, 9, 24)
    decision = date(2026, 9, 28)
    source = _fund("510300.SH", "EQUITY_300")
    candidate = replace(source, trade_date=as_of, adjusted_bars=tuple(
        (day + timedelta(days=3), price) for day, price in source.adjusted_bars))
    assert select_defensive_representatives((candidate,), decision_date=decision) == ()
    selected = select_defensive_representatives((candidate,), decision_date=decision,
        data_as_of=as_of)
    assert selected == (("EQUITY_300", candidate.ts_code),)
    target = build_defensive_target((candidate,), frozen_symbols=selected,
        decision_date=decision, evaluation_as_of=as_of, valid_until=decision,
        strategy_version_id=uuid4(), cov_window=20, vol_target=Decimal("0.04"),
        trend_ma=60, is_weekly_rebalance=True)
    assert target is not None and target.legs[0].ts_code == candidate.ts_code


def test_high_covariance_scales_risk_and_daily_run_cannot_increase():
    volatile = [100, 110] * 124 + [100, 111]
    equity = _fund("510300.SH", "EQUITY_300", prices=volatile)
    frozen = (("EQUITY_300", equity.ts_code),)
    weekly = _target((equity,), frozen)
    assert weekly is not None and weekly.legs[0].family_weight < Decimal("0.4")
    daily = _target((equity,), frozen, weekly=False,
                    previous={equity.ts_code: Decimal("0.01")})
    assert daily.legs[0].family_weight == Decimal("0.01")
    with pytest.raises(ValueError, match="previous target weights"):
        _target((equity,), frozen, weekly=False)


def test_mismatched_covariance_dates_fail_closed():
    equity = _fund("510300.SH", "EQUITY_300")
    bond = _fund("511010.SH", "BOND_MEDIUM")
    bars = list(bond.adjusted_bars)
    friday = next(index for index in range(len(bars) - 20, len(bars) - 1)
                  if bars[index][0].weekday() == 4)
    day, value = bars[friday]
    bars[friday] = (day + timedelta(days=1), value)
    bond = FundCandidate(**{**bond.__dict__, "adjusted_bars": tuple(bars)})
    frozen = (("EQUITY_300", equity.ts_code), ("BOND_MEDIUM", bond.ts_code))
    assert _target((equity, bond), frozen) is None


def test_all_trend_sleeves_screen_to_explicit_cash_target():
    falling = _fund("510300.SH", "EQUITY_300", prices=list(reversed(range(100, 350))))
    target = _target((falling,), (("EQUITY_300", falling.ts_code),))
    assert target.cash_only and target.legs == ()
