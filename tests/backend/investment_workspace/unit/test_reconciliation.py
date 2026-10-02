# test-catalog-begin
# {
#   "purpose": "投资工作区 / reconciliation",
#   "keywords": [
#     "投资工作区",
#     "交易日历",
#     "现金",
#     "重复请求",
#     "投资组合",
#     "reconciliation",
#     "calendar",
#     "cash",
#     "duplicate",
#     "portfolio"
#   ],
#   "covers": [
#     "backend/modules/investment_workspace/application/reconciliation.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

from backend.modules.investment_workspace.application.reconciliation import (
    AccountObservation,
    ObservedHolding,
    reconcile_account_observation,
)


DAY = date(2026, 9, 29)
CAPTURED = datetime(2026, 9, 29, 8, tzinfo=timezone.utc)
PORTFOLIO_ID = uuid4()


def _observation(*, holdings, cash="1000", complete=True, captured=CAPTURED):
    return AccountObservation(
        portfolio_id=PORTFOLIO_ID,
        trade_date=DAY, captured_at=captured, cash=cash,
        holdings=tuple(holdings), complete_holdings=complete, source_ref="snapshot-001",
    )


def _compare(observation):
    return reconcile_account_observation(
        expected_portfolio_id=PORTFOLIO_ID,
        expected_trade_date=DAY, local_cash=Decimal("1000"),
        local_holdings=[("CN", "000001.SZ", Decimal(100)),
                        ("HK", "000001.SZ", Decimal(30))],
        observation=observation,
    )


def test_same_symbol_in_two_markets_and_zero_sellable_are_not_mismatches():
    result = _compare(_observation(holdings=[
        ObservedHolding("CN", "000001.SZ", "100", "0"),
        ObservedHolding("HK", "000001.SZ", "30", "20"),
    ]))
    assert result.local_values_match
    assert result.differences == ()


def test_partial_snapshot_cannot_prove_missing_holding_is_zero():
    result = _compare(_observation(holdings=[
        ObservedHolding("CN", "000001.SZ", "100", "100"),
    ], complete=False))
    assert not result.local_values_match
    assert result.observation_issues == ("COVERAGE_PARTIAL",)
    assert result.differences == ()


def test_complete_snapshot_reports_cash_and_missing_or_changed_holdings():
    result = _compare(_observation(cash="900", holdings=[
        ObservedHolding("CN", "000001.SZ", "120", "50"),
        ObservedHolding("CN", "000002.SZ", "10", "10"),
    ]))
    assert [(d.kind, d.instrument, d.local, d.observed) for d in result.differences] == [
        ("CASH", None, Decimal(1000), Decimal(900)),
        ("HOLDING", ("CN", "000001.SZ"), Decimal(100), Decimal(120)),
        ("HOLDING", ("CN", "000002.SZ"), None, Decimal(10)),
        ("HOLDING", ("HK", "000001.SZ"), Decimal(30), None),
    ]


def test_invalid_sellable_and_duplicate_never_yield_clean_comparison():
    result = _compare(_observation(holdings=[
        ObservedHolding("CN", "000001.SZ", "100", "101"),
        ObservedHolding("CN", "000001.SZ", "100", "100"),
        ObservedHolding("HK", "000001.SZ", "30", float("nan")),
    ]))
    assert not result.local_values_match
    assert "SELLABLE_INVALID:CN:000001.SZ" in result.observation_issues
    assert "OBSERVED_HOLDING_INVALID_OR_DUPLICATE" in result.observation_issues
    assert "SELLABLE_INVALID:HK:000001.SZ" in result.observation_issues


def test_china_calendar_date_is_used_for_capture_freshness():
    # UTC is still the previous day, while the account observation is dated in China.
    same_china_day = datetime(2026, 9, 28, 17, tzinfo=timezone.utc)
    result = _compare(_observation(captured=same_china_day, holdings=[
        ObservedHolding("CN", "000001.SZ", "100", "100"),
        ObservedHolding("HK", "000001.SZ", "30", "30"),
    ]))
    assert result.local_values_match
    stale = _compare(_observation(captured=datetime(2026, 9, 28, 12, tzinfo=timezone.utc), holdings=[
        ObservedHolding("CN", "000001.SZ", "100", "100"),
        ObservedHolding("HK", "000001.SZ", "30", "30"),
    ]))
    assert stale.observation_issues == ("CAPTURE_TIME_INVALID",)


def test_missing_sellable_is_unknown_even_when_quantities_match():
    result = _compare(_observation(holdings=[
        ObservedHolding("CN", "000001.SZ", "100", None),
        ObservedHolding("HK", "000001.SZ", "30", "30"),
    ]))
    assert not result.local_values_match
    assert result.observation_issues == ("SELLABLE_INVALID:CN:000001.SZ",)


def test_other_portfolio_snapshot_cannot_reconcile_this_account():
    from dataclasses import replace

    observation = _observation(holdings=[
        ObservedHolding("CN", "000001.SZ", "100", "100"),
        ObservedHolding("HK", "000001.SZ", "30", "30"),
    ])
    result = _compare(replace(observation, portfolio_id=uuid4()))
    assert not result.local_values_match
    assert result.observation_issues == ("PORTFOLIO_MISMATCH",)


def test_malformed_rows_are_diagnostic_and_cannot_match():
    from dataclasses import replace

    observation = _observation(holdings=[
        ObservedHolding("CN", "000001.SZ", "100", "100"),
        ObservedHolding("HK", "000001.SZ", "30", "30"),
    ])
    bad_observation = replace(observation, holdings=(object(),))
    result = reconcile_account_observation(
        expected_portfolio_id=PORTFOLIO_ID,
        expected_trade_date=DAY, local_cash="1000",
        local_holdings=[("CN", "000001.SZ", "100"), ("broken",)],
        observation=bad_observation,
    )
    assert not result.local_values_match
    assert "OBSERVED_HOLDING_INVALID_OR_DUPLICATE" in result.observation_issues
    assert "LOCAL_HOLDING_INVALID_OR_DUPLICATE" in result.local_issues
