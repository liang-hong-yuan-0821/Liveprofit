# test-catalog-begin
# {
#   "purpose": "投资工作区 / account_valuation（账户）",
#   "keywords": [
#     "投资工作区",
#     "现金",
#     "流程",
#     "账户账本",
#     "订单",
#     "个股分析",
#     "account_valuation",
#     "cash",
#     "flow",
#     "ledger",
#     "order",
#     "stock"
#   ],
#   "covers": [
#     "backend/modules/investment_workspace/application/account_ledger.py",
#     "backend/modules/investment_workspace/application/account_valuation.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from datetime import datetime, timedelta, timezone
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from backend.modules.investment_workspace.application.account_valuation import (
    AccountValueSnapshot, ExternalFlowAtValue, unitize_account, value_account,
)
from backend.modules.investment_workspace.application.account_ledger import (
    LedgerBaseline, LedgerMovement, replay_account_balance,
)


PORTFOLIO = uuid4()
T0 = datetime(2026, 9, 28, 8, tzinfo=timezone.utc)


def _snapshot(day, cash, *, quantity="0", price="10", flow_total="0", prices=True):
    return AccountValueSnapshot(
        PORTFOLIO, T0 + timedelta(days=day), Decimal(cash),
        (("CN", "000001.SZ", Decimal(quantity)),) if Decimal(quantity) else (),
        (("CN", "000001.SZ", Decimal(price)),) if prices else (),
        Decimal(flow_total),
    )


def test_stock_gain_then_deposit_does_not_create_return():
    baseline = _snapshot(0, "0", quantity="100", price="10")
    before_deposit = _snapshot(1, "0", quantity="100", price="11")
    deposit = ExternalFlowAtValue(uuid4(), PORTFOLIO, before_deposit.as_of,
                                  Decimal("500"), before_deposit)
    ending = _snapshot(2, "500", quantity="100", price="11", flow_total="500")
    result = unitize_account(baseline, (deposit,), ending,
                             expected_flow_event_ids=(deposit.event_id,))
    assert result.issues == ()
    assert result.units == Decimal("1000") + Decimal("500") / Decimal("1.1")
    assert abs(result.unit_nav - Decimal("1.1")) < Decimal("0.000000000000000000000001")
    assert abs(result.total_return - Decimal("0.1")) < Decimal("0.000000000000000000000001")


def test_withdrawal_uses_pre_flow_value_and_preserves_return():
    baseline = _snapshot(0, "1000")
    before_withdrawal = _snapshot(1, "1100")
    withdrawal = ExternalFlowAtValue(uuid4(), PORTFOLIO, before_withdrawal.as_of,
                                     Decimal("-550"), before_withdrawal)
    ending = _snapshot(2, "550", flow_total="-550")
    result = unitize_account(baseline, (withdrawal,), ending,
                             expected_flow_event_ids=(withdrawal.event_id,))
    assert result.issues == ()
    assert result.units == Decimal("500")
    assert result.unit_nav == Decimal("1.1")


def test_missing_flow_time_price_and_wrong_cumulative_flow_are_unknown():
    baseline = _snapshot(0, "1000")
    before_deposit = _snapshot(1, "1000", quantity="1", prices=False,
                               flow_total="10")
    deposit = ExternalFlowAtValue(uuid4(), PORTFOLIO, T0 + timedelta(days=2),
                                  Decimal("100"), before_deposit)
    ending = _snapshot(3, "1100", flow_total="0")
    result = unitize_account(baseline, (deposit,), ending,
                             expected_flow_event_ids=(deposit.event_id,))
    assert result.unit_nav is None and result.units is None
    assert any(issue.startswith("PRE_FLOW:") and "MISSING_PRICE" in issue
               for issue in result.issues)
    assert any(issue.startswith("FLOW_TIME_OR_PORTFOLIO:") for issue in result.issues)
    assert any(issue.startswith("FLOW_TOTAL_MISMATCH:") for issue in result.issues)
    assert "ENDING_FLOW_TOTAL_MISMATCH" in result.issues


def test_negative_diagnostic_balance_and_nonpositive_value_are_unknown():
    assert value_account(_snapshot(0, "-1")).issues == ("NEGATIVE_CASH",)
    assert value_account(_snapshot(0, "0")).issues == ("NONPOSITIVE_VALUE",)
    assert value_account(_snapshot(0, "10", quantity="-1")).issues == (
        "NEGATIVE_HOLDING:CN:000001.SZ",
    )
    assert value_account(replace(_snapshot(0, "10"), cash=None)).issues == ("INVALID_CASH",)
    assert value_account(_snapshot(0, "10", quantity="1", price="NaN")).issues == (
        "INVALID_PRICE:CN:000001.SZ",
    )
    bad_quantity = replace(_snapshot(0, "10"),
                           holdings=(("CN", "000001.SZ", None),))
    assert value_account(bad_quantity).issues == (
        "INVALID_HOLDING_QUANTITY:CN:000001.SZ",
    )
    with pytest.raises(ValueError, match="valuation portfolio or time mismatch"):
        unitize_account(_snapshot(0, "1000"), (),
                        replace(_snapshot(1, "1000"), as_of=datetime(2026, 9, 29)))


def test_overwithdrawal_is_unknown_even_when_ending_account_value_is_positive():
    baseline = _snapshot(0, "1000")
    before = _snapshot(1, "1000")
    flow = ExternalFlowAtValue(uuid4(), PORTFOLIO, before.as_of, Decimal("-1100"), before)
    ending = _snapshot(2, "100", flow_total="-1100")
    result = unitize_account(baseline, (flow,), ending,
                             expected_flow_event_ids=(flow.event_id,))
    assert result.unit_nav is None
    assert any(issue.startswith("NONPOSITIVE_UNITS:") for issue in result.issues)


def test_offsetting_omitted_flows_are_caught_by_ledger_inventory():
    baseline = _snapshot(0, "1000")
    ending = _snapshot(2, "1200")
    deposit_id, withdrawal_id = uuid4(), uuid4()
    ledger = replay_account_balance(
        LedgerBaseline(PORTFOLIO, T0, Decimal("1000"), ()),
        (LedgerMovement(PORTFOLIO, deposit_id, T0 + timedelta(days=1),
                        T0 + timedelta(hours=12), "CASH_FLOW", Decimal("1000")),
         LedgerMovement(PORTFOLIO, withdrawal_id, T0 + timedelta(days=2),
                        T0 + timedelta(days=1, hours=12), "CASH_FLOW", Decimal("-1000"))),
        as_of=ending.as_of,
    )
    assert ledger.external_flow_total == 0
    assert ledger.external_flow_event_ids == (deposit_id, withdrawal_id)
    result = unitize_account(baseline, (), ending,
                             expected_flow_event_ids=ledger.external_flow_event_ids)
    assert result.unit_nav is None
    assert "FLOW_INVENTORY_MISMATCH" in result.issues
    assert "MISSING_FLOW_INVENTORY" in unitize_account(baseline, (), ending).issues


def test_same_time_flow_requires_previous_cash_to_be_applied():
    baseline = _snapshot(0, "1000")
    first_pre = _snapshot(1, "1000")
    first = ExternalFlowAtValue(uuid4(), PORTFOLIO, first_pre.as_of,
                                Decimal("1000"), first_pre)
    second_pre = _snapshot(1, "1000", flow_total="1000")
    second = ExternalFlowAtValue(uuid4(), PORTFOLIO, first_pre.as_of,
                                 Decimal("1000"), second_pre)
    ending = _snapshot(2, "3000", flow_total="2000")
    invalid = unitize_account(baseline, (first, second), ending,
                              expected_flow_event_ids=(first.event_id, second.event_id))
    assert invalid.unit_nav is None
    assert any(issue.startswith("SAME_TIME_FLOW_DISCONTINUITY:") for issue in invalid.issues)
    valid_second_pre = _snapshot(1, "2000", flow_total="1000")
    valid_second = ExternalFlowAtValue(second.event_id, PORTFOLIO, first_pre.as_of,
                                       Decimal("1000"), valid_second_pre)
    valid = unitize_account(baseline, (first, valid_second), ending,
                            expected_flow_event_ids=(first.event_id, second.event_id))
    assert valid.issues == ()
    assert valid.units == Decimal("3000") and valid.unit_nav == Decimal("1")
    prices = (("CN", "000001.SZ", Decimal("10")),
              ("CN", "000002.SZ", Decimal("20")))
    ordered_first = replace(first, pre_flow=replace(first_pre, prices=prices))
    reordered_second = replace(valid_second, pre_flow=replace(valid_second_pre,
                                                             prices=tuple(reversed(prices))))
    reordered = unitize_account(baseline, (ordered_first, reordered_second), ending,
                                expected_flow_event_ids=(first.event_id, second.event_id))
    assert reordered.issues == () and reordered.unit_nav == Decimal("1")


def test_invalid_flow_time_followed_by_valid_flow_stays_unknown():
    baseline = _snapshot(0, "1000")
    ending = _snapshot(2, "1200", flow_total="200")
    bad = ExternalFlowAtValue(uuid4(), PORTFOLIO, None, Decimal("100"),
                              _snapshot(1, "1000"))
    good_pre = _snapshot(1, "1100", flow_total="100")
    good = ExternalFlowAtValue(uuid4(), PORTFOLIO, good_pre.as_of,
                               Decimal("100"), good_pre)
    result = unitize_account(baseline, (bad, good), ending,
                             expected_flow_event_ids=(bad.event_id, good.event_id))
    assert result.unit_nav is None
    assert any(issue.startswith("FLOW_TIME_OR_PORTFOLIO:") for issue in result.issues)


def test_same_time_flows_must_follow_effective_ledger_inventory_order():
    baseline = _snapshot(0, "1000")
    at_flow = _snapshot(1, "1000")
    first_id, second_id = uuid4(), uuid4()
    first = ExternalFlowAtValue(first_id, PORTFOLIO, at_flow.as_of,
                                Decimal("100"), at_flow)
    second = ExternalFlowAtValue(second_id, PORTFOLIO, at_flow.as_of,
                                 Decimal("200"), _snapshot(1, "1100", flow_total="100"))
    ending = _snapshot(2, "1300", flow_total="300")
    valid = unitize_account(baseline, (first, second), ending,
                            expected_flow_event_ids=(first_id, second_id))
    assert valid.issues == () and valid.unit_nav == 1

    reversed_second = ExternalFlowAtValue(second_id, PORTFOLIO, at_flow.as_of,
                                          Decimal("200"), at_flow)
    reversed_first = ExternalFlowAtValue(first_id, PORTFOLIO, at_flow.as_of,
                                         Decimal("100"), _snapshot(1, "1200", flow_total="200"))
    wrong_order = unitize_account(baseline, (reversed_second, reversed_first), ending,
                                  expected_flow_event_ids=(first_id, second_id))
    assert wrong_order.unit_nav is None
    assert "FLOW_INVENTORY_MISMATCH" in wrong_order.issues
