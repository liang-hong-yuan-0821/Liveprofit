# test-catalog-begin
# {
#   "purpose": "量化策略 / family_allocation",
#   "keywords": [
#     "量化策略",
#     "执行准入",
#     "现金",
#     "策略族",
#     "交易意图",
#     "订单",
#     "family_allocation",
#     "admission",
#     "cash",
#     "family",
#     "intent",
#     "order"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/domain/family_allocation.py",
#     "backend/modules/quant_strategy/domain/portfolio_targets.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from itertools import permutations
from uuid import UUID

import pytest

from backend.modules.quant_strategy.domain.family_allocation import (
    FamilyAdmission, OwnedExposure, allocate_families,
)
from backend.modules.quant_strategy.domain.portfolio_targets import PortfolioTargetIntent, TargetLeg

AT = datetime(2026, 9, 24, 15, tzinfo=timezone.utc)
DAY = AT.date()
A, B, C = "000001.SZ", "000002.SZ", "000003.SZ"


def admission(n, bound=".02", *, when=AT-timedelta(days=1), rank=None):
    return FamilyAdmission(f"f{n}", UUID(int=n), UUID(int=n+10),
                           D(bound) if bound is not None else None, when,
                           n if rank is None else rank)


def intent(n, *legs):
    return PortfolioTargetIntent(f"f{n}", UUID(int=n+10), DAY, DAY, DAY,
                                 "policy", "rebalance", tuple(TargetLeg(s, D(w)) for s, w in legs),
                                 cash_only=not legs)


def exposure(n, symbol=A, held="1000", pending="0"):
    return OwnedExposure(symbol, f"f{n}", UUID(int=n), UUID(int=n+10), D(held), D(pending))


def allocate(admissions, intents, **kw):
    return allocate_families(capital_budget=D(10000), decision_at=AT,
                             admissions=tuple(admissions), intents=tuple(intents), **kw)


def test_equal_family_budget_conflict_loser_and_missing_candidate_stay_cash():
    result = allocate([admission(1), admission(2, ".03")],
                      [intent(1, (A, ".5"), (B, ".25")), intent(2, (A, ".5"))])
    assert dict(result.family_budgets) == {"f1": D(5000), "f2": D(5000)}
    targets = {t.symbol: t for t in result.targets}
    assert targets[A].family_id == "f2" and targets[A].max_add_notional == 2500
    assert targets[B].max_add_notional == 1250  # not scaled up after f1 loses A
    assert result.uncommitted_capacity == 6250
    assert result.conflicts == ((A, "f1", "RANKING_CONFLICT"),)


def test_input_order_does_not_change_result_and_ties_use_stable_strategy_id():
    admissions = [admission(1), admission(2), admission(3)]
    intents = [intent(n, (A, "1")) for n in (1, 2, 3)]
    expected = allocate(admissions, intents)
    assert expected.targets[0].strategy_id == UUID(int=1)
    for a in permutations(admissions):
        for i in permutations(intents):
            assert allocate(a, i) == expected


def test_owner_retained_even_without_admission_and_during_pending_exit():
    result = allocate([admission(2)], [intent(2, (A, "1"))],
                      exposures=(exposure(1, held="4000", pending="1000"),),
                      protective_targets={A: D(0)})
    assert result.targets[0].strategy_id == UUID(int=1)
    assert result.targets[0].target_notional == 0
    assert result.targets[0].max_add_notional == 0
    assert result.uncommitted_capacity == 5000  # unfilled exit releases nothing
    assert result.conflicts == ((A, "f2", "FROZEN_OWNER_CONFLICT"),)


def test_new_version_of_same_strategy_cannot_take_over_frozen_owner():
    old = OwnedExposure(A, "f1", UUID(int=1), UUID(int=99), D(1000))
    result = allocate([admission(1)], [intent(1, (A, "1"))], exposures=(old,))
    assert result.targets[0].strategy_version_id == UUID(int=99)
    assert result.targets[0].max_add_notional == 0


def test_unmanaged_holding_cannot_be_implicitly_adopted():
    result = allocate([admission(1)], [intent(1, (A, "1"))],
                      exposures=(OwnedExposure(A, None, None, None, D(1000)),))
    assert result.targets[0].family_id is None
    assert result.targets[0].max_add_notional == 0


def test_pending_buys_consume_family_and_account_capacity():
    result = allocate([admission(1), admission(2)],
                      [intent(1, (A, ".8"), (B, ".2")), intent(2, (C, "1"))],
                      exposures=(exposure(1, held="2000", pending="2500"),))
    targets = {t.symbol: t for t in result.targets}
    assert targets[A].max_add_notional == 0
    assert targets[B].max_add_notional == 500
    assert targets[C].max_add_notional == 5000
    assert result.uncommitted_capacity == 0


def test_protective_minimum_overrides_owner_add_and_sell_does_not_fund_other_buy():
    result = allocate([admission(1), admission(2)],
                      [intent(1, (A, "1")), intent(2, (B, "1"))],
                      exposures=(exposure(1, held="9000"),), protective_targets={A: D(500)})
    targets = {t.symbol: t for t in result.targets}
    assert targets[A].target_notional == 500 and targets[A].max_add_notional == 0
    assert targets[B].max_add_notional == 1000


def test_cash_only_exits_but_missing_intent_holds():
    held = (exposure(1),)
    assert allocate([admission(1)], [], exposures=held).targets[0].target_notional == 1000
    assert allocate([admission(1)], [intent(1)], exposures=held).targets[0].target_notional == 0


def test_no_admissions_still_allows_protection():
    result = allocate([], [], exposures=(exposure(1),), protective_targets={A: D(0)})
    assert result.targets[0].target_notional == 0
    assert result.family_budgets == ()


@pytest.mark.parametrize("when", [AT, AT+timedelta(days=1)])
def test_future_or_not_completed_ranking_evidence_is_rejected(when):
    with pytest.raises(ValueError, match="before"):
        allocate([admission(1, when=when)], [])


def test_cold_start_is_explicit_and_only_uses_preregistered_order():
    families = [admission(1, None, when=None, rank=2), admission(2, None, when=None, rank=1)]
    intents = [intent(1, (A, "1")), intent(2, (A, "1"))]
    with pytest.raises(ValueError, match="validation"):
        allocate(families, intents)
    result = allocate(families, intents, research_cold_start=True)
    assert result.targets[0].family_id == "f2"
    with pytest.raises(ValueError, match="mixed"):
        allocate([admission(1), families[1]], intents, research_cold_start=True)


@pytest.mark.parametrize("case", ["family_duplicate", "version", "unadmitted", "duplicate_intent",
                                  "duplicate_owner", "protect_buy", "protect_unknown"])
def test_inconsistent_identity_or_protection_fails_closed(case):
    families, intents, extras = [admission(1)], [intent(1, (A, "1"))], {}
    if case == "family_duplicate": families *= 2
    if case == "version":
        families = [FamilyAdmission("f1", UUID(int=1), UUID(int=99), D(".02"), AT-timedelta(days=1), 1)]
    if case == "unadmitted": families = []
    if case == "duplicate_intent": intents *= 2
    if case == "duplicate_owner": extras["exposures"] = (exposure(1), exposure(2))
    if case == "protect_buy": extras.update(exposures=(exposure(1),), protective_targets={A: D(1001)})
    if case == "protect_unknown": extras["protective_targets"] = {B: D(0)}
    with pytest.raises(ValueError):
        allocate(families, intents, **extras)


@pytest.mark.parametrize("amount", [D(-1), D("NaN"), D("Infinity")])
def test_invalid_budget(amount):
    with pytest.raises(ValueError):
        allocate_families(capital_budget=amount, decision_at=AT, admissions=(), intents=())


def test_equal_budgets_round_down_and_remainder_is_not_redistributed():
    result = allocate([admission(1), admission(2), admission(3)],
                      [intent(1, (A, "1")), intent(2, (B, "1")), intent(3, (C, "1"))])
    assert all(budget == D("3333.33") for _, budget in result.family_budgets)
    assert result.uncommitted_capacity == D("0.01")
    assert sum((target.max_add_notional for target in result.targets), D(0)) == D("9999.99")


def test_same_frozen_version_cannot_impersonate_two_family_admissions():
    second = FamilyAdmission("f2", UUID(int=2), UUID(int=11), D(".02"), AT-timedelta(days=1), 2)
    with pytest.raises(ValueError, match="frozen version"):
        allocate([admission(1), second], [])
