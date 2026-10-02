# test-catalog-begin
# {
#   "purpose": "量化策略 / etf_dual_momentum",
#   "keywords": [
#     "量化策略",
#     "现金",
#     "重复请求",
#     "ETF",
#     "etf_dual_momentum",
#     "cash",
#     "duplicate",
#     "etf"
#   ],
#   "covers": [
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

from backend.modules.quant_strategy.domain.etf_dual_momentum import (
    FundCandidate,
    build_dual_momentum_target,
    select_monthly_representatives,
)

DAY = date(2026, 9, 21)


def _fund(code, category, index, ret, adv=30_000_000, *, day=DAY):
    start = Decimal(100)
    finish = start * (1 + Decimal(str(ret)))
    dates = tuple(stamp.date() for stamp in pd.bdate_range(end=DAY, periods=181))
    return FundCandidate(
        ts_code=code, tracking_index=index, category=category, trade_date=day,
        listed_sessions=300, adv20_cny=Decimal(adv),
        adjusted_bars=tuple(zip(dates, (start,) * 180 + (finish,))), atr20_raw=Decimal("0.5"),
    )


def _target(candidates, reps, *, slots=2):
    return build_dual_momentum_target(
        candidates, monthly_representative_codes=reps, decision_date=DAY,
        valid_until=date(2026, 9, 28), evaluation_as_of=DAY,
        strategy_version_id=uuid4(), lookback=60, slots=slots,
    )


def test_monthly_representative_freezes_highest_adv_not_future_weekly_adv():
    first = _fund("510300.SH", "EQUITY_300", "000300.SH", "0.10", 40_000_000)
    second = _fund("159919.SZ", "EQUITY_300", "000300.SH", "0.20", 30_000_000)
    reps = select_monthly_representatives((first, second), decision_date=DAY, lookback=60)
    assert reps == frozenset({"510300.SH"})
    # A later ADV reversal must not silently switch the representative mid-month.
    later = _fund("159919.SZ", "EQUITY_300", "000300.SH", "0.20", 100_000_000)
    target = _target((first, later), reps, slots=1)
    assert [leg.ts_code for leg in target.legs] == ["510300.SH"]


def test_positive_bond_fills_one_slot_and_unfilled_budget_stays_cash():
    equity = _fund("510300.SH", "EQUITY_300", "000300.SH", "0.10")
    gold = _fund("518880.SH", "GOLD", "Au99.99.SGE", "-0.02")
    bond = _fund("511010.SH", "BOND_MEDIUM", "H00140.CSI", "0.01")
    candidates = (equity, gold, bond)
    reps = select_monthly_representatives(candidates, decision_date=DAY, lookback=60)
    target = _target(candidates, reps)
    assert [leg.ts_code for leg in target.legs] == ["510300.SH", "511010.SH"]
    assert [leg.family_weight for leg in target.legs] == [Decimal("0.5")] * 2
    without_bond_reps = select_monthly_representatives((equity, gold), decision_date=DAY, lookback=60)
    without_bond = _target((equity, gold), without_bond_reps)
    assert len(without_bond.legs) == 1
    assert without_bond.legs[0].family_weight == Decimal("0.5")


def test_no_positive_momentum_or_stale_date_retains_cash():
    negative = _fund("510300.SH", "EQUITY_300", "000300.SH", "-0.01")
    stale = _fund("518880.SH", "GOLD", "Au99.99.SGE", "0.10", day=date(2026, 9, 18))
    reps = frozenset({negative.ts_code, stale.ts_code})
    assert _target((negative, stale), reps) is None
    target = _target((negative,), frozenset({negative.ts_code}))
    assert target.cash_only and target.legs == ()


def test_duplicate_candidate_cannot_change_monthly_owner():
    candidate = _fund("510300.SH", "EQUITY_300", "000300.SH", "0.1")
    with pytest.raises(ValueError, match="duplicate ETF candidate"):
        select_monthly_representatives((candidate, candidate), decision_date=DAY, lookback=60)


def test_previous_session_representative_is_valid_for_next_decision_day():
    as_of = date(2026, 9, 24)
    decision = date(2026, 9, 28)
    source = _fund("510300.SH", "EQUITY_300", "000300.SH", "0.10")
    candidate = replace(source, trade_date=as_of, adjusted_bars=tuple(
        (day + timedelta(days=3), price) for day, price in source.adjusted_bars))
    assert select_monthly_representatives((candidate,), decision_date=decision,
        lookback=60) == frozenset()
    selected = select_monthly_representatives((candidate,), decision_date=decision,
        data_as_of=as_of, lookback=60)
    assert selected == frozenset({candidate.ts_code})
    target = build_dual_momentum_target((candidate,), monthly_representative_codes=selected,
        decision_date=decision, evaluation_as_of=as_of, valid_until=decision,
        strategy_version_id=uuid4(), lookback=60, slots=1)
    assert target is not None and target.legs[0].ts_code == candidate.ts_code
