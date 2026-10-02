# test-catalog-begin
# {
#   "purpose": "量化策略 / buy_target_planner：Target BUY uses the ordinary cash/exposure/risk/fee gates without fake prices.",
#   "keywords": [
#     "量化策略",
#     "执行准入",
#     "现金",
#     "重复请求",
#     "ETF",
#     "执行",
#     "策略族",
#     "费用",
#     "证券数据",
#     "持仓生命周期",
#     "市场分析",
#     "规划器",
#     "投资组合",
#     "仓位管理",
#     "数量",
#     "风险",
#     "品种规则",
#     "复用",
#     "个股分析",
#     "buy_target_planner",
#     "admission",
#     "cash",
#     "duplicate",
#     "etf",
#     "execution",
#     "family",
#     "fee",
#     "instrument",
#     "lifecycle",
#     "market",
#     "planner",
#     "portfolio",
#     "position",
#     "quantity",
#     "risk",
#     "rule",
#     "share",
#     "stock"
#   ],
#   "covers": [
#     "backend/modules/investment_workspace/domain/risk_profiles.py",
#     "backend/modules/quant_strategy/application/ownership.py",
#     "backend/modules/quant_strategy/application/position_planner.py",
#     "backend/modules/quant_strategy/domain/family_allocation.py",
#     "backend/modules/quant_strategy/domain/instrument_rules.py",
#     "backend/modules/quant_strategy/domain/portfolio_targets.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Target BUY uses the ordinary cash/exposure/risk/fee gates without fake prices."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace
from uuid import uuid4

import pytest

from backend.modules.quant_strategy.application.position_planner import PositionPlanner, plan_buy_target
from backend.modules.quant_strategy.domain.instrument_rules import InstrumentTradingRule
from backend.modules.investment_workspace.domain.risk_profiles import PROFILES


DAY = date(2026, 9, 18)
MARKET = {"trade_date": DAY.isoformat(), "raw_close": "10", "qfq_close": "5",
          "raw_amount": "100000", "adv20_amount": "100000", "up_limit": "11", "is_st": False, "is_suspended": False}
ACCOUNT = {
    "portfolio_snapshot": {"total_assets": "100000", "available_cash": "100000",
                           "risk_profile": "AGGRESSIVE", "risk": {
        "risk_per_trade_pct": ".0075", "min_risk_reward_ratio": "2",
        "max_total_position_pct": ".9", "max_single_stock_pct": ".1", "max_sector_pct": ".3",
        "max_portfolio_open_risk_pct": ".06", "max_sector_open_risk_pct": ".03",
        "max_daily_new_risk_pct": ".03", "max_drawdown_pct": ".25", "max_daily_loss_pct": ".1",
        "net_asset_value": "100000", "peak_net_asset_value": "100000",
        "day_start_net_asset_value": "100000", "risk_facts_as_of": DAY.isoformat(),
    }},
    "positions": [], "closes": {},
    "industry_map": {"A": {"industry_code": "I"}}, "industry_bucket_available": True,
    "pending_orders": [],
}


def run(*, market=None, take=D(13), maximum=D(10000), **changes):
    context = deepcopy(ACCOUNT)
    context.update(changes)
    return plan_buy_target(symbol="A", max_quantity=maximum, entry=D(10), stop=D(9),
                           take=take, market=MARKET if market is None else market,
                           valuation_date=DAY, **context)


def pending(symbol="A", quantity="800", industry="I"):
    return {"side": "BUY", "symbol": symbol, "remaining_quantity": quantity,
            "order_entry_price": "10", "order_stop_price": "9", "industry_code": industry,
            "reserved_cash": str(D(quantity) * 10 + 5)}


def test_target_caps_quantity_before_fees_and_does_not_map_raw_price_twice():
    result = run(maximum=D(250))
    assert result["order_status"] == "ELIGIBLE"
    assert result["shares"] == 200
    assert result["order_entry_price"] == D("10.01")
    assert result["order_stop_price"] == 9
    assert result["estimated_fees"] == D("5.03")
    assert result["earliest_execution_trade_date"] == date(2026, 9, 21)
    assert MARKET["qfq_close"] == "5"


def test_persistent_portfolio_pause_blocks_buy_after_nav_rebounds():
    account = deepcopy(ACCOUNT)
    account["portfolio_snapshot"]["risk_pause_event_id"] = str(uuid4())
    account["portfolio_snapshot"]["risk"]["net_asset_value"] = "100000"
    assert run(**account)["order_status"] == "BUY_REJECTED_PORTFOLIO_PAUSED"


def test_unverified_account_facts_block_new_buy_even_with_valid_cash():
    account = deepcopy(ACCOUNT)
    account["portfolio_snapshot"]["account_reconciliation_required"] = True
    assert run(**account)["order_status"] == "BUY_REJECTED_ACCOUNT_RECONCILIATION"


def test_entry_interval_reserves_upper_executable_tick_and_rechecks_rr():
    result = plan_buy_target(
        symbol="A", max_quantity=D(10000), entry=D(10), stop=D(9), take=D(14),
        market=MARKET, valuation_date=DAY,
        entry_lower=D("9.5"), entry_upper=D("10.505"), **deepcopy(ACCOUNT),
    )
    assert result["order_status"] == "ELIGIBLE"
    assert result["order_entry_price"] == D("10.01")
    assert result["order_cost_price"] == D("10.50")
    assert result["shares"] == 500
    assert result["notional"] == D("5250.00")
    assert result["notional"] + result["estimated_fees"] <= D("100000")
    assert result["shares"] * (result["order_cost_price"] - result["order_stop_price"]) == D("750")
    assert run(entry_lower=D("9.5"), entry_upper=D("10.505"), take=D("13"))[
        "order_status"] == "BUY_REJECTED_RR"


def test_entry_interval_recomputes_liquidity_and_cash_at_upper_price():
    market = {**MARKET, "adv20_amount": "41", "raw_amount": "100000"}
    result = plan_buy_target(
        symbol="A", max_quantity=D(10000), entry=D(10), stop=D(9), take=D(14),
        market=market, valuation_date=DAY, entry_lower=D("9.5"), entry_upper=D("10.505"),
        **deepcopy(ACCOUNT),
    )
    assert result["order_status"] == "ELIGIBLE"
    assert result["shares"] == 100
    assert D(200) * result["order_entry_price"] <= D("41") * 1000 * D("0.05")
    assert D(200) * result["order_cost_price"] > D("41") * 1000 * D("0.05")
    portfolio = deepcopy(ACCOUNT["portfolio_snapshot"])
    portfolio["available_cash"] = "2090"
    cash_limited = run(portfolio_snapshot=portfolio, entry_lower=D("9.5"),
                       entry_upper=D("10.505"), take=D(14))
    assert cash_limited["order_status"] == "ELIGIBLE"
    assert cash_limited["shares"] == 100
    assert D(200) * D("10.01") <= D("2090")
    assert D(200) * cash_limited["order_cost_price"] > D("2090")


@pytest.mark.parametrize("cap,orders,expected", [
    ("max_single_stock_pct", [pending()], 100),
    ("max_sector_pct", [pending("B")], 100),
    ("max_total_position_pct", [pending("B", industry="J")], 100),
])
def test_pending_notional_consumes_all_exposure_limits(cap, orders, expected):
    portfolio = deepcopy(ACCOUNT["portfolio_snapshot"])
    portfolio["risk"][cap] = ".1"
    result = run(portfolio_snapshot=portfolio, pending_orders=orders)
    assert result["order_status"] == "ELIGIBLE"
    assert result["shares"] == expected


def test_fee_and_pending_cash_both_reduce_affordable_quantity():
    portfolio = deepcopy(ACCOUNT["portfolio_snapshot"])
    portfolio["available_cash"] = "3007"
    result = run(portfolio_snapshot=portfolio, pending_orders=[pending("B", "100", "J")])
    assert result["shares"] == 100
    assert result["notional"] + result["estimated_fees"] + D("1005") <= D("3007")


def test_explicit_empty_reservations_do_not_restore_obsolete_snapshot_orders():
    portfolio = deepcopy(ACCOUNT["portfolio_snapshot"])
    portfolio["pending_orders"] = [pending(quantity="1000")]
    assert run(portfolio_snapshot=portfolio, pending_orders=[])["shares"] == 700


@pytest.mark.parametrize("change", [
    {"is_st": None}, {"is_suspended": None}, {"adv20_amount": None},
    {"adv20_amount": "NaN"}, {"adv20_amount": "bad"}, {"raw_close": 0}, {"up_limit": None},
    {"trade_date": "2026-09-17"},
])
def test_missing_or_stale_market_facts_never_use_planner_fallback(change):
    assert run(market={**MARKET, **change})["order_status"] == "BUY_MARKET_FACTS_UNAVAILABLE"


def test_old_market_payload_with_only_single_day_amount_is_rejected():
    legacy = dict(MARKET)
    del legacy["adv20_amount"]
    assert run(market=legacy)["order_status"] == "BUY_MARKET_FACTS_UNAVAILABLE"


@pytest.mark.parametrize("change,code", [
    ({"is_st": True}, "BUY_REJECTED_ST"),
    ({"is_suspended": True}, "BUY_REJECTED_SUSPENDED"),
    ({"up_limit": "10"}, "BUY_REJECTED_LIMIT_UP"),
])
def test_real_execution_gates_still_apply(change, code):
    assert run(market={**MARKET, **change})["order_status"] == code


def test_null_profit_target_and_missing_nav_do_not_bypass_account_admission():
    assert run(take=None)["order_status"] == "BUY_REJECTED_RR"
    portfolio = deepcopy(ACCOUNT["portfolio_snapshot"])
    portfolio["risk"]["risk_facts_as_of"] = "2026-09-17"
    assert run(portfolio_snapshot=portfolio)["order_status"] == "BUY_REJECTED_RISK_FACTS"


@pytest.mark.parametrize("change", [
    {"order_stop_price": None}, {"order_entry_price": "NaN"},
    {"remaining_quantity": "-100"}, {"industry_code": None}, {"reserved_cash": "1"},
])
def test_invalid_reservation_blocks_instead_of_crashing_or_releasing_capacity(change):
    assert run(pending_orders=[{**pending(), **change}])["order_status"] == "BUY_REJECTED_RISK_FACTS"


def test_sub_lot_target_is_not_rounded_up():
    assert run(maximum=D(99))["order_status"] == "BUY_REJECTED_LOT_SIZE"


def test_direct_planner_missing_execution_market_cannot_invent_liquidity():
    signal = SimpleNamespace(
        id=uuid4(), signal_kind="BUY", ts_code="A", entry_price=D(10),
        stop_loss=D(9), take_profit=D(13), valuation_price=D(10),
        execution_market=None,
    )

    class Signals:
        fields = None

        def list_actionable(self, task_id, attempt_no):
            return [signal]

        def update_order_fields(self, signal_id, fields):
            self.fields = fields

    repo = Signals()
    PositionPlanner(repo).plan(task_id=uuid4(), attempt_no=1,
                               valuation_date=DAY, **deepcopy(ACCOUNT))
    assert repo.fields["order_status"] == "BUY_REJECTED_LIQUIDITY"


def test_balanced_profile_blocks_old_aggressive_numeric_account_at_buy_gate():
    portfolio = deepcopy(ACCOUNT["portfolio_snapshot"])
    portfolio["risk_profile"] = "BALANCED"
    assert run(portfolio_snapshot=portfolio)["order_status"] == "BUY_REJECTED_PROFILE_BUDGET"
    portfolio["risk"].update({
        "risk_per_trade_pct": ".005", "max_portfolio_open_risk_pct": ".04",
        "max_total_position_pct": ".75", "max_single_stock_pct": ".08",
        "max_sector_open_risk_pct": ".02", "max_daily_new_risk_pct": ".02",
        "max_drawdown_pct": ".15",
    })
    assert run(portfolio_snapshot=portfolio)["order_status"] == "ELIGIBLE"


def test_full_drawdown_is_exposed_as_exit_required_and_blocks_new_buy():
    portfolio = deepcopy(ACCOUNT["portfolio_snapshot"])
    portfolio["risk"]["net_asset_value"] = "75000"
    portfolio["risk"]["day_start_net_asset_value"] = "75000"
    signal = SimpleNamespace(
        id=uuid4(), signal_kind="BUY", ts_code="A", entry_price=D(10),
        stop_loss=D(9), take_profit=D(13), valuation_price=D(10),
        execution_market=MARKET,
    )

    class Signals:
        fields = None

        def list_actionable(self, task_id, attempt_no):
            return [signal]

        def update_order_fields(self, signal_id, fields):
            self.fields = fields

    repo = Signals()
    summary = PositionPlanner(repo).plan(
        task_id=uuid4(), attempt_no=1, valuation_date=DAY,
        **{**deepcopy(ACCOUNT), "portfolio_snapshot": portfolio},
    )
    assert repo.fields["order_status"] == "BUY_REJECTED_RISK_CIRCUIT"
    assert summary.full_drawdown_exit_required is True
    assert "PORTFOLIO_FULL_DRAWDOWN_EXIT_REQUIRED" in summary.warnings


def _dated_rule(*, published_on=date(2026, 9, 17), tick=D("0.05")):
    return InstrumentTradingRule(
        symbol="A", asset_type="stock", effective_from=date(2026, 9, 21),
        effective_through=None, published_on=published_on, source_ref="source#sha256=test",
        price_tick=tick, min_buy_quantity=200, buy_quantity_step=1,
        max_buy_quantity=100000, roundtrip_days=1,
    )


def test_explicit_dated_rule_uses_tick_and_200_then_single_share_quantities():
    rule = _dated_rule()
    result = run(maximum=D(251), instrument_rules={"A": rule})
    assert result["order_status"] == "ELIGIBLE"
    assert result["shares"] == 251
    assert result["order_entry_price"] == D("10.05")
    assert result["order_stop_price"] == D("9")
    assert result["order_take_price"] == D("13")
    assert result["earliest_execution_trade_date"] == date(2026, 9, 21)
    assert run(maximum=D(199), instrument_rules={"A": rule})["order_status"] == "BUY_REJECTED_LOT_SIZE"


def test_explicit_rule_collection_rejects_missing_or_late_rule():
    assert run(instrument_rules={})["order_status"] == "BUY_REJECTED_INSTRUMENT_RULE"
    assert run(instrument_rules={"A": _dated_rule(published_on=DAY)})[
        "order_status"] == "BUY_REJECTED_INSTRUMENT_RULE"
    assert run(instrument_rules={"A": replace(_dated_rule(), symbol="B")})[
        "order_status"] == "BUY_REJECTED_INSTRUMENT_RULE"


def test_direct_planner_explicit_rule_rejects_stale_market_day():
    signal = SimpleNamespace(
        id=uuid4(), signal_kind="BUY", ts_code="A", entry_price=D(10),
        stop_loss=D(9), take_profit=D(13), valuation_price=D(10),
        execution_market={**MARKET, "trade_date": "2026-09-17"},
    )

    class Signals:
        fields = None

        def list_actionable(self, task_id, attempt_no):
            return [signal]

        def update_order_fields(self, signal_id, fields):
            self.fields = fields

    repo = Signals()
    PositionPlanner(repo).plan(task_id=uuid4(), attempt_no=1,
                               valuation_date=DAY, instrument_rules={"A": _dated_rule()},
                               **deepcopy(ACCOUNT))
    assert repo.fields["order_status"] == "BUY_REJECTED_INSTRUMENT_RULE"


def test_explicit_rule_caps_liquidity_and_cash_on_valid_quantity_grid():
    rule = replace(_dated_rule(), max_buy_quantity=235)
    assert run(instrument_rules={"A": rule})["shares"] == 235
    portfolio = deepcopy(ACCOUNT["portfolio_snapshot"])
    portfolio["available_cash"] = "2217"
    result = run(instrument_rules={"A": rule}, portfolio_snapshot=portfolio)
    assert result["order_status"] == "ELIGIBLE"
    assert result["shares"] == 220
    assert result["notional"] + result["estimated_fees"] <= D("2217")
    assert run(market={**MARKET, "adv20_amount": "39"}, instrument_rules={"A": rule})[
        "order_status"] == "BUY_REJECTED_LIQUIDITY"


def test_rule_step_above_one_survives_fee_driven_cash_reduction():
    rule = replace(_dated_rule(), buy_quantity_step=10, max_buy_quantity=235)
    assert run(instrument_rules={"A": rule})["shares"] == 230
    portfolio = deepcopy(ACCOUNT["portfolio_snapshot"])
    portfolio["available_cash"] = "2214"
    result = run(instrument_rules={"A": rule}, portfolio_snapshot=portfolio)
    assert result["order_status"] == "ELIGIBLE"
    assert result["shares"] == 210
    assert D(220) * result["order_entry_price"] <= D("2214")
    assert D("2214") < D(220) * result["order_entry_price"] + D("5.03")
    assert result["notional"] + result["estimated_fees"] <= D("2214")


@pytest.mark.parametrize("profile,expected", [
    ("CONSERVATIVE", 1900), ("BALANCED", 2400), ("AGGRESSIVE", 2900),
])
def test_etf_uses_profile_specific_single_instrument_cap(profile, expected):
    portfolio = deepcopy(ACCOUNT["portfolio_snapshot"])
    portfolio["risk_profile"] = profile
    portfolio["risk"].update(PROFILES[profile].account_caps())
    portfolio["risk"]["max_sector_pct"] = D("0.8")
    rule = replace(_dated_rule(), asset_type="etf", min_buy_quantity=100,
                   buy_quantity_step=100)
    result = plan_buy_target(
        symbol="A", max_quantity=D(10000), entry=D(10), stop=D("9.95"),
        take=D(13), market=MARKET, valuation_date=DAY,
        **{**deepcopy(ACCOUNT), "portfolio_snapshot": portfolio,
           "instrument_rules": {"A": rule}},
    )
    assert result["order_status"] == "ELIGIBLE"
    assert result["shares"] == expected
    assert result["notional"] <= D("100000") * PROFILES[profile].single_etf
    assert result["notional"] > D("100000") * PROFILES[profile].single_stock


def test_etf_cap_includes_same_symbol_pending_buy_exposure():
    portfolio = deepcopy(ACCOUNT["portfolio_snapshot"])
    portfolio["risk"]["max_sector_pct"] = D("0.8")
    rule = replace(_dated_rule(), asset_type="etf", min_buy_quantity=100,
                   buy_quantity_step=100)
    result = plan_buy_target(
        symbol="A", max_quantity=D(10000), entry=D(10), stop=D("9.95"),
        take=D(13), market=MARKET, valuation_date=DAY,
        **{**deepcopy(ACCOUNT), "portfolio_snapshot": portfolio,
           "pending_orders": [pending(quantity="500")],
           "instrument_rules": {"A": rule}},
    )
    assert result["order_status"] == "ELIGIBLE"
    assert result["shares"] == 2400
    assert result["notional"] + D(5000) <= D("30000")


def test_etf_cap_combines_valued_holding_and_pending_buy():
    portfolio = deepcopy(ACCOUNT["portfolio_snapshot"])
    portfolio["available_cash"] = "95000"
    portfolio["risk"]["max_sector_pct"] = D("0.8")
    rule = replace(_dated_rule(), asset_type="etf", min_buy_quantity=100,
                   buy_quantity_step=100)
    result = plan_buy_target(
        symbol="A", max_quantity=D(10000), entry=D(10), stop=D("9.95"),
        take=D(13), market=MARKET, valuation_date=DAY,
        **{**deepcopy(ACCOUNT), "portfolio_snapshot": portfolio,
           "positions": [{"symbol": "A", "quantity": D(500),
                          "average_cost": D(10), "active_stop_price": D("9.95")}],
           "closes": {"A": D(10)},
           "pending_orders": [pending(quantity="500")],
           "instrument_rules": {"A": rule}},
    )
    assert result["order_status"] == "ELIGIBLE"
    assert result["shares"] == 1900
    assert result["notional"] + D(5000) + D(5000) <= D("30000")


def test_stock_rule_keeps_account_single_stock_cap():
    portfolio = deepcopy(ACCOUNT["portfolio_snapshot"])
    portfolio["risk"]["max_sector_pct"] = D("0.8")
    result = plan_buy_target(
        symbol="A", max_quantity=D(10000), entry=D(10), stop=D("9.95"),
        take=D(13), market=MARKET, valuation_date=DAY,
        **{**deepcopy(ACCOUNT), "portfolio_snapshot": portfolio,
           "instrument_rules": {"A": _dated_rule()}},
    )
    assert result["order_status"] == "ELIGIBLE"
    assert result["shares"] == 995
    assert result["notional"] <= D("10000")


def test_missing_risk_profile_is_not_an_eligible_buy():
    portfolio = deepcopy(ACCOUNT["portfolio_snapshot"])
    portfolio["risk_profile"] = None
    assert run(portfolio_snapshot=portfolio)["order_status"] == "BUY_REJECTED_PROFILE_BUDGET"


@pytest.mark.parametrize("value", [D(-1), D("NaN"), D("Infinity")])
def test_invalid_target_is_rejected(value):
    with pytest.raises(ValueError):
        run(maximum=value)


def test_family_arbitration_budget_is_applied_after_execution_price_normalization():
    from datetime import datetime, timezone
    from uuid import UUID
    from backend.modules.quant_strategy.domain.family_allocation import FamilyAdmission, allocate_families
    from backend.modules.quant_strategy.domain.portfolio_targets import PortfolioTargetIntent, TargetLeg
    allocation = allocate_families(
        capital_budget=D(2000), decision_at=datetime(2026, 9, 18, tzinfo=timezone.utc),
        admissions=(FamilyAdmission("family", UUID(int=1), UUID(int=2), D(".01"),
                                    datetime(2026, 9, 17, tzinfo=timezone.utc), 0),),
        intents=(PortfolioTargetIntent("family", UUID(int=2), DAY, DAY, DAY, "policy", "rebalance",
                                       (TargetLeg("000001.SZ", D(1)),)),),
    )
    result = run(max_notional=allocation.targets[0].max_add_notional)
    assert result["shares"] == 100  # 200 shares at slipped 10.01 would exceed the 2000 budget
    assert result["notional"] <= allocation.targets[0].max_add_notional
    assert run(max_notional=D(1000))["order_status"] == "BUY_REJECTED_FAMILY_BUDGET"


@pytest.mark.parametrize("value", [D(-1), D("NaN"), D("Infinity")])
def test_invalid_family_capacity_is_rejected(value):
    with pytest.raises(ValueError):
        run(max_notional=value)


@pytest.mark.parametrize("owner,source,expected", [
    ("00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000001", "ELIGIBLE"),
    ("00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002", "BUY_REJECTED_OWNER"),
    (None, "00000000-0000-0000-0000-000000000001", "BUY_REJECTED_OWNER"),
    ("00000000-0000-0000-0000-000000000001", None, "BUY_REJECTED_OWNER"),
])
def test_owner_gate_requires_exact_frozen_version(owner, source, expected):
    from uuid import UUID
    result = run(owner_versions={"A": owner}, strategy_version_id=UUID(source) if source else None)
    assert result["order_status"] == expected


def test_regular_buy_on_managed_position_cannot_duplicate_lifecycle_add():
    assert run(lifecycle_managed_symbols={"A"})["order_status"] == "BUY_REJECTED_MANAGED"


def test_foreign_scan_script_does_not_override_frozen_owner():
    from uuid import UUID
    from backend.modules.quant_strategy.application.ownership import owned_script_output
    sell = {"action": "SELL_ALL"}
    assert owned_script_output(sell, owner_version_id=UUID(int=1), scanner_version_id=UUID(int=2)) is None
    assert owned_script_output(sell, owner_version_id=UUID(int=1), scanner_version_id=None) is None
    assert owned_script_output(sell, owner_version_id=UUID(int=1), scanner_version_id=UUID(int=1)) == sell
