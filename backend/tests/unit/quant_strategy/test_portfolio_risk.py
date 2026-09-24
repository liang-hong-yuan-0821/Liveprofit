from decimal import Decimal
from datetime import date

from backend.modules.quant_strategy.application.portfolio_risk import (
    DAILY_NEW_RISK_LIMIT,
    PORTFOLIO_CIRCUIT_BREAKER,
    RISK_FACTS_UNAVAILABLE,
    SECTOR_OPEN_RISK_LIMIT,
    PortfolioRiskState,
)


RISK = {
    "max_portfolio_open_risk_pct": "0.06",
    "max_sector_open_risk_pct": "0.03",
    "max_daily_new_risk_pct": "0.02",
    "max_drawdown_pct": "0.10",
    "max_daily_loss_pct": "0.03",
    "net_asset_value": "100000",
    "peak_net_asset_value": "100000",
    "day_start_net_asset_value": "100000",
    "risk_facts_as_of": "2026-09-18",
}


def _state(*, risk=RISK, positions=None, pending=None):
    return PortfolioRiskState.build(
        total_assets="100000",
        risk=risk,
        positions=positions or [],
        closes={"000001.SZ": Decimal("10")},
        industry_map={"000001.SZ": {"industry_code": "I1"}},
        pending_orders=pending or [],
    )


def test_existing_holding_risk_uses_active_stop_not_market_value():
    state = _state(positions=[{
        "symbol": "000001.SZ", "quantity": "1000", "average_cost": "7",
        "active_stop_price": "9",
    }])
    assert state.portfolio_open_risk == Decimal("1000")
    assert state.sector_open_risk == {"I1": Decimal("1000")}


def test_missing_stop_or_equity_facts_fail_closed():
    assert _state(positions=[{
        "symbol": "000001.SZ", "quantity": "100", "average_cost": "10",
        "active_stop_price": None,
    }]).block_code == RISK_FACTS_UNAVAILABLE
    assert _state(risk={**RISK, "net_asset_value": None}).block_code == RISK_FACTS_UNAVAILABLE


def test_pending_buy_reserves_cash_portfolio_sector_and_daily_risk():
    state = _state(pending=[{
        "side": "BUY", "symbol": "000002.SZ", "remaining_quantity": "1000",
        "order_entry_price": "10", "order_stop_price": "9", "industry_code": "I1",
        "reserved_cash": "10005",
    }])
    assert state.reserved_cash == Decimal("10005")
    assert state.portfolio_open_risk == Decimal("1000")
    assert state.daily_new_risk == Decimal("1000")
    capacities = dict((code, amount) for amount, code in state.capacities(
        risk_per_share=Decimal("1"), industry_code="I1"
    ))
    assert capacities[SECTOR_OPEN_RISK_LIMIT] == Decimal("2000")
    assert capacities[DAILY_NEW_RISK_LIMIT] == Decimal("1000")


def test_drawdown_or_daily_loss_circuit_breaker_blocks_only_new_risk_layer():
    state = _state(risk={**RISK, "net_asset_value": "89000"})
    assert state.block_code == PORTFOLIO_CIRCUIT_BREAKER


def test_stale_equity_watermark_fails_closed():
    state = PortfolioRiskState.build(
        total_assets="100000", risk=RISK, positions=[], closes={}, industry_map={},
        pending_orders=[], valuation_date=date(2026, 9, 21),
    )
    assert state.block_code == RISK_FACTS_UNAVAILABLE


def test_persisted_rejection_codes_fit_signal_contract():
    from backend.modules.quant_strategy.application import portfolio_risk

    codes = [
        portfolio_risk.RISK_FACTS_UNAVAILABLE,
        portfolio_risk.PORTFOLIO_OPEN_RISK_LIMIT,
        portfolio_risk.SECTOR_OPEN_RISK_LIMIT,
        portfolio_risk.DAILY_NEW_RISK_LIMIT,
        portfolio_risk.PORTFOLIO_CIRCUIT_BREAKER,
    ]
    assert all(len(code) <= 32 for code in codes)
