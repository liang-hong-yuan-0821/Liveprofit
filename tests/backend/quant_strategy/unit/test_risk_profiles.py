# test-catalog-begin
# {
#   "purpose": "量化策略 / risk_profiles",
#   "keywords": [
#     "量化策略",
#     "现金",
#     "风险",
#     "risk_profiles",
#     "cash",
#     "risk"
#   ],
#   "covers": [
#     "backend/modules/investment_workspace/domain/risk_profiles.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from decimal import Decimal as D

import pytest

from backend.modules.investment_workspace.domain.risk_profiles import PROFILES, profile_budget_violations


@pytest.mark.parametrize("profile,per_trade,open_risk", [
    ("CONSERVATIVE", D("0.0025"), D("0.02")),
    ("BALANCED", D("0.005"), D("0.04")),
    ("AGGRESSIVE", D("0.0075"), D("0.06")),
])
@pytest.mark.parametrize("capital", [D("100000"), D("300000"), D("1000000")])
def test_frozen_profile_cash_risk_budgets(profile, per_trade, open_risk, capital):
    limits = PROFILES[profile]
    assert capital * limits.per_trade == capital * per_trade
    assert capital * limits.open_risk == capital * open_risk
    assert limits.sector_open_risk == limits.daily_new_risk == open_risk / 2


def test_more_conservative_account_limits_pass_but_over_budget_or_unknown_fails():
    limits = PROFILES["BALANCED"]
    risk = {
        "risk_per_trade_pct": limits.per_trade,
        "max_portfolio_open_risk_pct": limits.open_risk,
        "max_total_position_pct": limits.total_exposure,
        "max_single_stock_pct": limits.single_stock,
        "max_drawdown_pct": limits.drawdown,
        "max_sector_open_risk_pct": limits.sector_open_risk,
        "max_daily_new_risk_pct": limits.daily_new_risk,
    }
    assert profile_budget_violations("BALANCED", risk) == ()
    assert profile_budget_violations("BALANCED", {**risk, "risk_per_trade_pct": "0.004"}) == ()
    assert profile_budget_violations("BALANCED", {**risk, "risk_per_trade_pct": "0.01"}) == ("risk_per_trade_pct",)
    assert profile_budget_violations("BALANCED", {**risk, "max_daily_new_risk_pct": "bad"}) == ("max_daily_new_risk_pct",)
    assert profile_budget_violations("UNKNOWN", risk) == ("risk_profile",)
