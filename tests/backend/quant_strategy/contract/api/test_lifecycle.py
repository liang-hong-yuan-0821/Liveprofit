# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle（持仓生命周期）",
#   "keywords": [
#     "量化策略",
#     "接口",
#     "成交",
#     "幂等",
#     "持仓生命周期",
#     "仓位管理",
#     "lifecycle",
#     "api",
#     "fill",
#     "idempotent",
#     "position"
#   ],
#   "covers": [
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py"
#   ],
#   "environment": [
#     "db",
#     "redis"
#   ]
# }
# test-catalog-end

from __future__ import annotations

import uuid
from decimal import Decimal

from backend.modules.investment_workspace.infrastructure.models import Portfolio
from backend.modules.quant_strategy.infrastructure.lifecycle_models import SuggestedOrder


def test_policy_publish_is_content_idempotent_and_listed(client):
    payload = {
        "policy_key": f"policy_{uuid.uuid4().hex[:8]}",
        "required_fields": ["ma20", "ma60"],
        "config": {"trailing_stop": {"b": "1", "a": "2", "d": "0.1"}},
    }
    first = client.http.post("/api/v1/lifecycle-policy-versions", json=payload)
    assert first.status_code == 201, first.text
    replay = client.http.post("/api/v1/lifecycle-policy-versions", json=payload)
    assert replay.status_code == 201
    assert replay.json()["data"]["id"] == first.json()["data"]["id"]
    listed = client.http.get("/api/v1/lifecycle-policy-versions")
    assert listed.status_code == 200
    assert any(x["id"] == first.json()["data"]["id"] for x in listed.json()["data"]["items"])


def test_manual_partial_fill_api_updates_real_position_and_replays(client):
    services = client.http.app.state.analysis_services
    with services._container.sync_session_factory() as session:
        portfolio = Portfolio(
            id=uuid.uuid4(), name=f"contract-{uuid.uuid4().hex[:8]}", version=1,
            total_assets=Decimal("10000"), available_cash=Decimal("10000"),
            risk_per_trade_pct=Decimal("0.01"), min_risk_reward_ratio=Decimal("2"),
            max_total_position_pct=Decimal("0.8"), max_single_stock_pct=Decimal("0.1"),
            max_sector_pct=Decimal("0.3"), max_portfolio_open_risk_pct=Decimal("0.06"),
            max_sector_open_risk_pct=Decimal("0.03"), max_daily_new_risk_pct=Decimal("0.02"),
            max_drawdown_pct=Decimal("0.1"), max_daily_loss_pct=Decimal("0.03"),
        )
        order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio.id, market="CN", symbol="000001.SZ",
            industry_code="801010", side="BUY", quantity=Decimal("100"),
            filled_quantity=Decimal(0), limit_price=Decimal("10"), stop_price=Decimal("8"),
            reserved_cash=Decimal("1000"), reserved_risk=Decimal("200"),
            reason_code="CONTRACT", status="PROPOSED", revision=1,
        )
        session.add_all([portfolio, order])
        session.commit()
        portfolio_id, order_id = portfolio.id, order.id

    payload = {
        "quantity": "40", "fill_price": "9", "fill_trade_date": "2026-09-21",
        "idempotency_key": f"contract-fill-{uuid.uuid4()}", "expected_revision": 1,
        "source": "MANUAL",
    }
    response = client.http.post(f"/api/v1/suggested-orders/{order_id}/fills", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["data"]["order"]["status"] == "PARTIALLY_FILLED"
    replay = client.http.post(f"/api/v1/suggested-orders/{order_id}/fills", json=payload)
    assert replay.status_code == 201
    assert replay.json()["data"]["fill"]["id"] == response.json()["data"]["fill"]["id"]
    orders = client.http.get(f"/api/v1/portfolios/{portfolio_id}/suggested-orders")
    assert orders.status_code == 200
    assert orders.json()["data"]["items"][0]["filled_quantity"] == "40.0000"
