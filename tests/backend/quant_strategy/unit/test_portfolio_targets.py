# test-catalog-begin
# {
#   "purpose": "量化策略 / portfolio_targets",
#   "keywords": [
#     "量化策略",
#     "现金",
#     "重复请求",
#     "投资组合",
#     "portfolio_targets",
#     "cash",
#     "duplicate",
#     "portfolio"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/domain/portfolio_targets.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from dataclasses import FrozenInstanceError, replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from backend.modules.quant_strategy.domain.portfolio_targets import (
    PortfolioTargetIntent,
    TargetLeg,
)


def _intent(*legs):
    return PortfolioTargetIntent(
        family_id="etf_dual_momentum", strategy_version_id=uuid4(),
        evaluation_as_of=date(2026, 9, 18), decision_date=date(2026, 9, 21),
        valid_until=date(2026, 9, 25), policy_id="TRAILING_3ATR",
        reason="positive momentum", legs=legs,
    )


def test_portfolio_target_is_frozen_and_leaves_unused_budget_in_cash():
    target = _intent(
        TargetLeg("510300.SH", Decimal("0.4"), Decimal("4.1"), Decimal("4.3")),
        TargetLeg("518880.SH", Decimal("0.2")),
    )
    assert sum((leg.family_weight for leg in target.legs), Decimal(0)) == Decimal("0.6")
    assert target.legs[0].entry_upper == Decimal("4.3")
    with pytest.raises(FrozenInstanceError):
        target.policy_id = "FIXED_TARGET"


@pytest.mark.parametrize("legs", [
    (TargetLeg("510300.SH", Decimal("0.6")), TargetLeg("518880.SH", Decimal("0.5"))),
    (TargetLeg("510300.SH", Decimal("0.5")), TargetLeg("510300.SH", Decimal("0.2"))),
    (),
])
def test_invalid_portfolio_weights_or_duplicate_owners_fail(legs):
    with pytest.raises(ValueError):
        _intent(*legs)


def test_price_interval_and_version_validity_are_enforced():
    with pytest.raises(ValueError, match="entry interval"):
        TargetLeg("510300.SH", Decimal("0.5"), Decimal("4.1"))
    with pytest.raises(ValueError, match="invalid entry interval"):
        TargetLeg("510300.SH", Decimal("0.5"), Decimal("4.3"), Decimal("4.1"))
    with pytest.raises(TypeError, match="frozen strategy version"):
        PortfolioTargetIntent(
            family_id="etf_dual_momentum", strategy_version_id=None,
            evaluation_as_of=date(2026, 9, 18), decision_date=date(2026, 9, 21),
            valid_until=date(2026, 9, 25), policy_id="TRAILING_3ATR", reason="signal",
            legs=(TargetLeg("510300.SH", Decimal("0.5")),),
        )


def test_trial_identity_is_atomic_and_well_formed():
    target = _intent(TargetLeg("510300.SH", Decimal("0.5")))
    digest = "a" * 64
    assert replace(target, trial_id="etf_dual_momentum:60:1:WEEKLY",
                   definition_hash=digest).definition_hash == digest
    with pytest.raises(ValueError, match="travel together"):
        replace(target, trial_id="etf_dual_momentum:60:1:WEEKLY")
    with pytest.raises(ValueError, match="invalid frozen"):
        replace(target, trial_id="etf_dual_momentum:60:1:WEEKLY", definition_hash="ABC")
