# test-catalog-begin
# {
#   "purpose": "量化策略 / portfolio_drawdown_actions：Only conftest's isolated liveprofit_quant_strategy_test is migrated/written.",
#   "keywords": [
#     "量化策略",
#     "幂等",
#     "证券数据",
#     "市场分析",
#     "投资组合",
#     "仓位管理",
#     "portfolio_drawdown_actions",
#     "idempotent",
#     "instrument",
#     "market",
#     "portfolio",
#     "position"
#   ],
#   "covers": [
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_strategy/application/errors.py",
#     "backend/modules/quant_strategy/application/lifecycle_service.py",
#     "backend/modules/quant_strategy/application/planning_account.py",
#     "backend/modules/quant_strategy/application/portfolio_drawdown_actions.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py",
#     "backend/modules/quant_strategy/infrastructure/portfolio_risk_models.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Only conftest's isolated liveprofit_quant_strategy_test is migrated/written."""

from datetime import date
from decimal import Decimal as D
import base64
import hmac
import json
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from backend.modules.investment_workspace.infrastructure.models import Portfolio, PortfolioPosition
from backend.modules.quant_strategy.application.portfolio_drawdown_actions import PortfolioDrawdownActions
from backend.modules.quant_strategy.application.lifecycle_service import LifecycleOrderService
from backend.modules.quant_strategy.application.errors import FillValidationError
from backend.modules.quant_strategy.infrastructure.lifecycle_models import SuggestedOrder
from backend.modules.quant_strategy.infrastructure.portfolio_risk_models import PortfolioExitTarget, PortfolioRiskEvent


DAY = date(2026, 9, 18)


def _seed(session):
    portfolio = Portfolio(
        id=uuid4(), name=f"drawdown-{uuid4().hex}", risk_profile="AGGRESSIVE",
        total_assets=D(100000), available_cash=D(25000),
        max_drawdown_pct=D("0.25"), net_asset_value=D(75000),
        peak_net_asset_value=D(100000), day_start_net_asset_value=D(75000),
        risk_facts_as_of=DAY,
    )
    session.add(portfolio)
    session.flush()
    for symbol in ("000001.SZ", "000002.SZ"):
        session.add(PortfolioPosition(
            id=uuid4(), portfolio_id=portfolio.id, market="CN", symbol=symbol,
            quantity=D(100), average_cost=D(10),
        ))
    session.commit()
    return portfolio.id


def test_full_drawdown_pause_and_all_position_targets_are_idempotent_and_sticky(env):
    factory = env["session_factory"]
    with factory() as session:
        portfolio_id = _seed(session)
        service = PortfolioDrawdownActions(session)
        event = service.pause_if_full(portfolio_id, valuation_date=DAY)
        assert event.kind == "PAUSE" and event.revision == 1
        session.commit()
        assert service.pause_if_full(portfolio_id, valuation_date=DAY).id == event.id
        session.commit()
        targets = list(session.scalars(select(PortfolioExitTarget).where(
            PortfolioExitTarget.pause_event_id == event.id)))
        assert {t.symbol for t in targets} == {"000001.SZ", "000002.SZ"}
        assert all(t.quantity_at_capture == 100 for t in targets)
        portfolio = session.get(Portfolio, portfolio_id)
        portfolio.net_asset_value = D(90000)
        portfolio.risk_facts_as_of = date(2026, 9, 21)
        session.add(PortfolioPosition(
            id=uuid4(), portfolio_id=portfolio_id, market="CN", symbol="000003.SZ",
            quantity=D(50), average_cost=D(10),
        ))
        session.commit()
        assert service.pause_if_full(portfolio_id, valuation_date=date(2026, 9, 21)).id == event.id
        session.commit()
        assert session.scalar(select(PortfolioRiskEvent).where(
            PortfolioRiskEvent.portfolio_id == portfolio_id).order_by(
                PortfolioRiskEvent.revision.desc())).id == event.id
        assert {t.symbol for t in session.scalars(select(PortfolioExitTarget).where(
            PortfolioExitTarget.pause_event_id == event.id))} == {
            "000001.SZ", "000002.SZ", "000003.SZ",
        }


def test_full_drawdown_quarantines_old_buy_suggestions_without_blocking_sell(env):
    with env["session_factory"]() as session:
        portfolio_id = _seed(session)
        orders = []
        for status, filled, side in (("PROPOSED", 0, "BUY"), ("EXECUTING", 0, "BUY"),
                                     ("PARTIALLY_FILLED", 40, "BUY"), ("PROPOSED", 0, "SELL")):
            order = SuggestedOrder(
                id=uuid4(), portfolio_id=portfolio_id, market="CN", symbol="000001.SZ",
                side=side, quantity=D(100), filled_quantity=D(filled), limit_price=D(8),
                reserved_cash=D(800) if side == "BUY" else D(0), reserved_risk=D(0),
                reason_code="OLD_SUGGESTION", status=status, revision=1,
            )
            session.add(order)
            orders.append(order)
        session.commit()
        service = PortfolioDrawdownActions(session)
        service.pause_if_full(portfolio_id, valuation_date=DAY)
        session.commit()
        assert [(o.status, o.revision) for o in orders] == [
            ("SUPERSEDED", 2), ("RECONCILIATION_REQUIRED", 2),
            ("RECONCILIATION_REQUIRED", 2), ("PROPOSED", 1),
        ]
        fill, in_flight = LifecycleOrderService(session).confirm_fill(
            orders[1].id, quantity=D(20), fill_price=D(8), fill_trade_date=DAY,
            idempotency_key=f"paused-broker-fill-{uuid4()}", expected_revision=2,
            source="BROKER", note="broker execution after pause",
        )
        assert fill.quantity == 20 and in_flight.status == "RECONCILIATION_REQUIRED"
        assert in_flight.filled_quantity == 20 and in_flight.revision == 3
        for prohibited in ("EXECUTING", "SUPERSEDED", "CANCELLED", "REJECTED"):
            with pytest.raises(FillValidationError):
                LifecycleOrderService(session).set_order_status(
                    in_flight.id, status=prohibited, expected_revision=3)
        session.rollback()
        assert session.get(SuggestedOrder, in_flight.id).status == "RECONCILIATION_REQUIRED"
        service.pause_for_planning(portfolio_id, valuation_date=DAY)
        session.commit()
        service.pause_if_full(portfolio_id, valuation_date=DAY)
        session.commit()
        assert [(o.status, o.revision) for o in orders] == [
            ("SUPERSEDED", 2), ("RECONCILIATION_REQUIRED", 3),
            ("RECONCILIATION_REQUIRED", 2), ("PROPOSED", 1),
        ]


def test_below_full_does_not_pause_and_same_day_trigger_drift_is_rejected(env):
    factory = env["session_factory"]
    with factory() as session:
        portfolio_id = _seed(session)
        portfolio = session.get(Portfolio, portfolio_id)
        portfolio.net_asset_value = D(75001)
        session.commit()
        service = PortfolioDrawdownActions(session)
        assert service.pause_if_full(portfolio_id, valuation_date=DAY) is None
        portfolio.net_asset_value = D(75000)
        session.commit()
        event = service.pause_if_full(portfolio_id, valuation_date=DAY)
        session.commit()
        portfolio.net_asset_value = D(74000)
        session.commit()
        with pytest.raises(ValueError, match="facts changed"):
            service.pause_if_full(portfolio_id, valuation_date=DAY)
        session.rollback()
        session.add(PortfolioPosition(
            id=uuid4(), portfolio_id=portfolio_id, market="CN", symbol="000004.SZ",
            quantity=D(75), average_cost=D(10),
        ))
        session.commit()
        assert service.pause_for_planning(portfolio_id, valuation_date=DAY).id == event.id
        session.commit()
        assert "000004.SZ" in {target.symbol for target in session.scalars(select(PortfolioExitTarget).where(
            PortfolioExitTarget.pause_event_id == event.id))}
        portfolio = session.get(Portfolio, portfolio_id)
        portfolio.net_asset_value = D(90000)
        session.commit()
        with pytest.raises(ValueError, match="facts changed"):
            service.pause_if_full(portfolio_id, valuation_date=DAY)
        session.rollback()
        assert service.pause_for_planning(portfolio_id, valuation_date=DAY).id == event.id
        portfolio = session.get(Portfolio, portfolio_id)
        portfolio.risk_facts_as_of = None
        session.commit()
        with pytest.raises(ValueError, match="facts changed"):
            service.pause_if_full(portfolio_id, valuation_date=DAY)
        session.rollback()
        assert service.pause_for_planning(portfolio_id, valuation_date=DAY).id == event.id
        assert service.active_pause(portfolio_id).id == event.id


def test_drawdown_events_and_targets_reject_mutation(env):
    with env["session_factory"]() as session:
        portfolio_id = _seed(session)
        event = PortfolioDrawdownActions(session).pause_if_full(portfolio_id, valuation_date=DAY)
        session.commit()
        with pytest.raises(DBAPIError):
            session.execute(text("DELETE FROM quant_portfolio_exit_targets WHERE pause_event_id=:id"),
                            {"id": event.id})
        session.rollback()
        with pytest.raises(DBAPIError):
            session.execute(text("UPDATE quant_portfolio_risk_events SET reason='changed' WHERE id=:id"),
                            {"id": event.id})
        session.rollback()


def test_exit_target_rejects_cross_portfolio_position(env):
    with env["session_factory"]() as session:
        first = _seed(session)
        second = _seed(session)
        event = PortfolioDrawdownActions(session).pause_if_full(first, valuation_date=DAY)
        session.commit()
        other_position = session.scalar(select(PortfolioPosition).where(
            PortfolioPosition.portfolio_id == second))
        with pytest.raises(DBAPIError, match="portfolio exit target owner mismatch"):
            session.add(PortfolioExitTarget(
                id=uuid4(), pause_event_id=event.id, position_id=other_position.id,
                market=other_position.market, symbol=other_position.symbol,
                quantity_at_capture=other_position.quantity,
            ))
            session.flush()
        session.rollback()


def test_exit_target_rejects_instrument_identity_mismatch(env):
    with env["session_factory"]() as session:
        portfolio_id = _seed(session)
        event = PortfolioDrawdownActions(session).pause_if_full(portfolio_id, valuation_date=DAY)
        session.commit()
        position = session.scalar(select(PortfolioPosition).where(
            PortfolioPosition.portfolio_id == portfolio_id))
        with pytest.raises(DBAPIError, match="portfolio exit target instrument mismatch"):
            session.add(PortfolioExitTarget(
                id=uuid4(), pause_event_id=event.id, position_id=position.id,
                market="HK", symbol=position.symbol, quantity_at_capture=D(100),
            ))
            session.flush()
        session.rollback()


def test_exit_projection_requires_settlement_availability_and_same_day_market(env):
    with env["session_factory"]() as session:
        portfolio_id = _seed(session)
        service = PortfolioDrawdownActions(session)
        service.pause_if_full(portfolio_id, valuation_date=DAY)
        session.commit()
        positions = list(session.scalars(select(PortfolioPosition).where(
            PortfolioPosition.portfolio_id == portfolio_id)))
        known = {position.id: D(100) for position in positions}
        market = {("CN", symbol): {"trade_date": DAY.isoformat(), "raw_close": "8",
                                   "down_limit": "7", "is_suspended": False}
                  for symbol in ("000001.SZ", "000002.SZ")}
        missing = service.project_exit_targets(
            portfolio_id, valuation_date=DAY, market_by_instrument=market,
            available_by_position={})
        assert {row.status for row in missing} == {"SELLABLE_UNKNOWN"}
        stale = service.project_exit_targets(
            portfolio_id, valuation_date=DAY,
            market_by_instrument={key: {**facts, "trade_date": "2026-09-17"}
                                  for key, facts in market.items()},
            available_by_position=known)
        assert {row.status for row in stale} == {"SELL_MARKET_FACTS_UNAVAILABLE"}
        blocked = service.project_exit_targets(
            portfolio_id, valuation_date=DAY, market_by_instrument=market,
            available_by_position={position.id: D(0) for position in positions})
        assert {row.status for row in blocked} == {"SELL_REJECTED_T1"}
        ready = service.project_exit_targets(
            portfolio_id, valuation_date=DAY, market_by_instrument=market,
            available_by_position=known)
        assert {row.status for row in ready} == {"READY"}
        assert all(row.suggested_quantity == 100 and row.suggested_price > 0 for row in ready)
        session.add(SuggestedOrder(
            id=uuid4(), portfolio_id=portfolio_id, position_id=positions[0].id,
            market=positions[0].market, symbol=positions[0].symbol, side="SELL",
            quantity=D(100), filled_quantity=D(0), limit_price=D(8),
            reserved_cash=D(0), reserved_risk=D(0),
            reason_code="TEST_EXIT_RESERVATION", status="PROPOSED", revision=1,
        ))
        session.flush()
        reserved = service.project_exit_targets(
            portfolio_id, valuation_date=DAY, market_by_instrument=market,
            available_by_position=known)
        by_symbol = {row.symbol: row for row in reserved}
        assert by_symbol[positions[0].symbol].status == "SELL_ALREADY_RESERVED"
        assert by_symbol[positions[1].symbol].status == "READY"


def test_exit_projection_keeps_unsupported_market_pending_with_same_symbol(env):
    with env["session_factory"]() as session:
        portfolio_id = _seed(session)
        service = PortfolioDrawdownActions(session)
        service.pause_if_full(portfolio_id, valuation_date=DAY)
        hk = PortfolioPosition(id=uuid4(), portfolio_id=portfolio_id, market="HK",
                               symbol="000001.SZ", quantity=D(50), average_cost=D(10))
        session.add(hk)
        session.flush()
        service.pause_if_full(portfolio_id, valuation_date=DAY)
        session.commit()
        cn_positions = list(session.scalars(select(PortfolioPosition).where(
            PortfolioPosition.portfolio_id == portfolio_id,
            PortfolioPosition.market == "CN")))
        result = service.project_exit_targets(
            portfolio_id, valuation_date=DAY,
            market_by_instrument={("CN", p.symbol): {
                "trade_date": DAY.isoformat(), "raw_close": "8", "down_limit": "7",
                "is_suspended": False} for p in cn_positions},
            available_by_position={p.id: p.quantity for p in cn_positions},
        )
        assert {(row.position_id, row.status) for row in result} == {
            *((p.id, "READY") for p in cn_positions),
            (hk.id, "UNSUPPORTED_MARKET"),
        }


def test_resume_requires_review_fresh_facts_flat_positions_and_no_active_buy(env, monkeypatch):
    from backend.modules.quant_strategy.application.planning_account import planning_account

    review_key = b"isolated-test-review-key-32bytes-minimum"
    configured = json.dumps({
        "reviewer-1": base64.b64encode(review_key).decode(),
        "another": base64.b64encode(b"second-isolated-review-key-32bytes-minimum").decode(),
    })
    monkeypatch.setenv("LIVEPROFIT_RISK_REVIEW_KEYS", configured)
    with env["session_factory"]() as session:
        portfolio_id = _seed(session)
        service = PortfolioDrawdownActions(session)
        pause = service.pause_if_full(portfolio_id, valuation_date=DAY)
        session.commit()
        later = date(2026, 9, 21)
        portfolio = session.get(Portfolio, portfolio_id)
        portfolio.risk_facts_as_of = later
        portfolio.net_asset_value = D(99000)
        session.commit()
        facts_sha256 = service._digest_facts(
            portfolio_id, later, portfolio.risk_profile,
            D(portfolio.net_asset_value), D(portfolio.peak_net_asset_value),
            D(portfolio.max_drawdown_pct))
        review_payload = json.dumps({
            "portfolio_id": str(portfolio_id), "pause_event_id": str(pause.id),
            "valuation_date": later.isoformat(), "reviewed_by": "reviewer-1",
            "reason": "reconciled", "facts_sha256": facts_sha256,
        }, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(review_key, review_payload, "sha256").hexdigest()
        with pytest.raises(ValueError, match="positions must exit"):
            service.resume_after_review(portfolio_id, pause_event_id=pause.id,
                                        valuation_date=later, reviewed_by="reviewer-1",
                                        reason="reconciled", review_signature=signature)
        session.rollback()
        positions = list(session.scalars(select(PortfolioPosition).where(
            PortfolioPosition.portfolio_id == portfolio_id)))
        for position in positions:
            position.quantity = D(0)
        positions[0].quantity = D(-1)
        session.commit()
        with pytest.raises(ValueError, match="positions must exit"):
            service.resume_after_review(portfolio_id, pause_event_id=pause.id,
                                        valuation_date=later, reviewed_by="reviewer-1",
                                        reason="reconciled", review_signature=signature)
        session.rollback()
        session.get(PortfolioPosition, positions[0].id).quantity = D(0)
        session.add(SuggestedOrder(
            id=uuid4(), portfolio_id=portfolio_id, market="CN", symbol="000001.SZ",
            side="BUY", quantity=D(100), filled_quantity=D(0), limit_price=D(8),
            reserved_cash=D(805), reserved_risk=D(100),
            reason_code="OLD_BUY", status="PROPOSED", revision=1,
        ))
        session.commit()
        with pytest.raises(ValueError, match="active BUY"):
            service.resume_after_review(portfolio_id, pause_event_id=pause.id,
                                        valuation_date=later, reviewed_by="reviewer-1",
                                        reason="reconciled", review_signature=signature)
        session.rollback()
        order = session.scalar(select(SuggestedOrder).where(
            SuggestedOrder.portfolio_id == portfolio_id, SuggestedOrder.side == "BUY"))
        order.status = "CANCELLED"
        session.commit()
        monkeypatch.delenv("LIVEPROFIT_RISK_REVIEW_KEYS")
        with pytest.raises(ValueError, match="not configured"):
            service.resume_after_review(portfolio_id, pause_event_id=pause.id,
                                        valuation_date=later, reviewed_by="reviewer-1",
                                        reason="reconciled", review_signature=signature)
        monkeypatch.setenv("LIVEPROFIT_RISK_REVIEW_KEYS", configured)
        duplicate = json.dumps({"reviewer-1": base64.b64encode(review_key).decode(),
                                "another": base64.b64encode(review_key).decode()})
        monkeypatch.setenv("LIVEPROFIT_RISK_REVIEW_KEYS", duplicate)
        with pytest.raises(ValueError, match="uniquely"):
            service.resume_after_review(portfolio_id, pause_event_id=pause.id,
                                        valuation_date=later, reviewed_by="reviewer-1",
                                        reason="reconciled", review_signature=signature)
        monkeypatch.setenv("LIVEPROFIT_RISK_REVIEW_KEYS", configured)
        portfolio = session.get(Portfolio, portfolio_id)
        portfolio.net_asset_value = D(98000)
        session.commit()
        with pytest.raises(ValueError, match="signature"):
            service.resume_after_review(portfolio_id, pause_event_id=pause.id,
                                        valuation_date=later, reviewed_by="reviewer-1",
                                        reason="reconciled", review_signature=signature)
        session.rollback()
        session.get(Portfolio, portfolio_id).net_asset_value = D(99000)
        session.commit()
        resumed = service.resume_after_review(
            portfolio_id, pause_event_id=pause.id, valuation_date=later,
            reviewed_by="reviewer-1", reason="reconciled", review_signature=signature)
        session.commit()
        assert resumed.kind == "RESUME" and resumed.revision == 2
        assert service.active_pause(portfolio_id) is None
        assert planning_account(session, session.get(Portfolio, portfolio_id))[
            "portfolio_snapshot"]["risk_pause_event_id"] is None
        assert service.resume_after_review(
            portfolio_id, pause_event_id=pause.id, valuation_date=later,
            reviewed_by="reviewer-1", reason="reconciled",
            review_signature=signature).id == resumed.id
        with pytest.raises(ValueError, match="signature"):
            service.resume_after_review(portfolio_id, pause_event_id=pause.id,
                                        valuation_date=later, reviewed_by="another",
                                        reason="reconciled", review_signature=signature)
