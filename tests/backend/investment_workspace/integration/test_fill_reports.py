# test-catalog-begin
# {
#   "purpose": "投资工作区 / fill_reports（成交、报告）",
#   "keywords": [
#     "投资工作区",
#     "费用",
#     "成交",
#     "历史审计",
#     "订单",
#     "投资组合",
#     "版本修订",
#     "来源证据",
#     "止损",
#     "fill_reports",
#     "fee",
#     "fill",
#     "history",
#     "order",
#     "portfolio",
#     "revision",
#     "source",
#     "stop"
#   ],
#   "covers": [
#     "backend/modules/investment_workspace/application/account_ledger_store.py",
#     "backend/modules/investment_workspace/application/account_observations.py",
#     "backend/modules/investment_workspace/application/fill_reports.py",
#     "backend/modules/investment_workspace/application/reconciliation.py",
#     "backend/modules/investment_workspace/infrastructure/fill_report_models.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_strategy/application/errors.py",
#     "backend/modules/quant_strategy/application/lifecycle_service.py",
#     "backend/modules/quant_strategy/application/order_materialization.py",
#     "backend/modules/quant_strategy/application/planning_account.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

from datetime import date, datetime, timezone
from decimal import Decimal
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from backend.modules.investment_workspace.application.fill_reports import AccountFillReportService
from backend.modules.investment_workspace.application.account_ledger_store import AccountLedgerStore
from backend.modules.investment_workspace.application.account_observations import AccountObservationService
from backend.modules.investment_workspace.application.reconciliation import AccountObservation, ObservedHolding
from backend.modules.investment_workspace.infrastructure.fill_report_models import (
    AccountFillReportAssessmentRow, AccountFillReportResolutionRow, AccountFillReportRow,
)
from backend.modules.investment_workspace.infrastructure.models import Portfolio, PortfolioPosition
from backend.modules.quant_strategy.infrastructure.lifecycle_models import OrderFillEvent, SuggestedOrder
from backend.modules.quant_strategy.application.errors import LifecycleInvalidStateError
from backend.modules.quant_strategy.application.lifecycle_service import LifecycleOrderService
from backend.modules.quant_strategy.application.order_materialization import materialize_signal_orders
from backend.modules.quant_strategy.application.planning_account import lock_portfolio, planning_account


TRADE_DATE = date(2026, 9, 28)
CAPTURED = datetime(2026, 9, 29, 8, tzinfo=timezone.utc)


def _account_and_order(factory):
    with factory() as session:
        portfolio_id, order_id = uuid.uuid4(), uuid.uuid4()
        session.add(Portfolio(id=portfolio_id, name=f"fill-report-{portfolio_id}",
                              version=1, total_assets=Decimal("1000"),
                              available_cash=Decimal("1000")))
        session.add(SuggestedOrder(
            id=order_id, portfolio_id=portfolio_id, market="CN", symbol="000001.SZ",
            side="BUY", quantity=Decimal(10), limit_price=Decimal(10),
            reason_code="TEST_REPORT", status="PROPOSED",
        ))
        session.commit()
        return portfolio_id, order_id


def _record(session, portfolio_id, order_id, *, quantity="20", fee=None, source_ref="report-1",
            market="CN", symbol="000001.SZ", side="BUY"):
    return AccountFillReportService(session).record(
        portfolio_id, order_id=order_id, market=market, symbol=symbol, side=side,
        fill_trade_date=TRADE_DATE,
        captured_at=CAPTURED, quantity=quantity, fill_price="10", fee=fee,
        source_type="MANUAL_REPORT", source_ref=source_ref,
    )


def _establish_baseline(session, portfolio_id, *, cash="1000", quantity="0"):
    observation, _ = AccountObservationService(session).record(AccountObservation(
        portfolio_id=portfolio_id, trade_date=TRADE_DATE, captured_at=CAPTURED,
        cash=cash, holdings=(ObservedHolding("CN", "000001.SZ", quantity, quantity),),
        complete_holdings=True, source_ref="baseline-observation",
    ), source_type="MANUAL_IMPORT")
    AccountLedgerStore(session).establish_baseline(portfolio_id, observation.id)


def test_over_order_report_with_unknown_fee_is_saved_without_projection(env):
    factory = env["session_factory"]
    portfolio_id, order_id = _account_and_order(factory)
    with factory() as session:
        report, created = _record(session, portfolio_id, order_id)
        assert created and report.quantity == Decimal(20) and report.fee is None
        session.commit()
        report_id = report.id
    with factory() as session:
        replay, created = _record(session, portfolio_id, order_id, quantity="20.0000")
        assert not created and replay.id == report_id
        assert session.scalar(select(func.count()).select_from(AccountFillReportRow)) == 1
        portfolio = session.get(Portfolio, portfolio_id)
        order = session.get(SuggestedOrder, order_id)
        assert portfolio.available_cash == Decimal(1000) and portfolio.version == 1
        assert order.filled_quantity == 0 and order.status == "PROPOSED"


def test_report_source_drift_and_history_mutation_are_rejected(env):
    factory = env["session_factory"]
    portfolio_id, order_id = _account_and_order(factory)
    with factory() as session:
        report, _ = _record(session, portfolio_id, order_id)
        session.commit()
        report_id = report.id
    with factory() as session:
        with pytest.raises(ValueError, match="replay changed"):
            _record(session, portfolio_id, order_id, fee="1")
        session.rollback()
        with pytest.raises(DBAPIError, match="account fill report history is immutable"):
            session.execute(text("UPDATE account_fill_reports SET quantity=1 WHERE id=:id"),
                            {"id": report_id})
        session.rollback()
        assert session.get(AccountFillReportRow, report_id).quantity == Decimal(20)


def test_report_order_must_belong_to_same_portfolio(env):
    factory = env["session_factory"]
    first_portfolio, first_order = _account_and_order(factory)
    _, other_order = _account_and_order(factory)
    with factory() as session:
        with pytest.raises(ValueError, match="does not belong"):
            _record(session, first_portfolio, other_order)
        mismatch, created = _record(session, first_portfolio, first_order,
                                    side="SELL", source_ref="wrong-side-report")
        assert created and mismatch.side == "SELL"
        with pytest.raises(ValueError, match="instrument or side"):
            _record(session, first_portfolio, None, side="HOLD")
        assert session.scalar(select(func.count()).select_from(AccountFillReportRow)) == 1
        assert session.get(SuggestedOrder, first_order).filled_quantity == 0
        session.commit()


def test_assessment_freezes_local_mismatch_without_accepting_fill(env):
    factory = env["session_factory"]
    portfolio_id, order_id = _account_and_order(factory)
    with factory() as session:
        report, _ = _record(session, portfolio_id, order_id, side="SELL")
        assessment, created = AccountFillReportService(session).assess(
            portfolio_id, report.id, assessment_ref="check-1")
        assert created
        assert assessment.order_revision == 1
        assert assessment.issues == [
            "SOURCE_UNVERIFIED", "FEE_UNKNOWN", "ORDER_INSTRUMENT_OR_SIDE_MISMATCH",
            "ORDER_REMAINING_EXCEEDED",
        ]
        session.commit()
        assessment_id = assessment.id
    with factory() as session:
        replay, created = AccountFillReportService(session).assess(
            portfolio_id, report.id, assessment_ref="check-1")
        assert not created and replay.id == assessment_id
        order = session.get(SuggestedOrder, order_id)
        assert order.filled_quantity == 0 and order.status == "PROPOSED"
        assert session.get(Portfolio, portfolio_id).available_cash == Decimal(1000)
        with pytest.raises(DBAPIError, match="fill assessment history is immutable"):
            session.execute(text("UPDATE account_fill_report_assessments SET issues='[]' WHERE id=:id"),
                            {"id": assessment_id})


def test_assessment_revision_and_reference_drift(env):
    factory = env["session_factory"]
    portfolio_id, order_id = _account_and_order(factory)
    with factory() as session:
        report, _ = _record(session, portfolio_id, order_id, quantity="5", fee="0.1")
        first, _ = AccountFillReportService(session).assess(
            portfolio_id, report.id, assessment_ref="check-1")
        assert first.issues == ["SOURCE_UNVERIFIED"]
        session.commit()
        report_id = report.id
    with factory() as session:
        order = session.get(SuggestedOrder, order_id)
        order.revision = 2
        order.filled_quantity = Decimal(8)
        session.commit()
    with factory() as session:
        replay, created = AccountFillReportService(session).assess(
            portfolio_id, report_id, assessment_ref="check-1")
        assert not created and replay.order_revision == 1
        second, created = AccountFillReportService(session).assess(
            portfolio_id, report_id, assessment_ref="check-2")
        assert created and second.order_revision == 2
        assert second.issues == ["SOURCE_UNVERIFIED", "ORDER_REMAINING_EXCEEDED"]
        other, _ = _record(session, portfolio_id, order_id, quantity="3", source_ref="report-2")
        with pytest.raises(ValueError, match="changed report"):
            AccountFillReportService(session).assess(portfolio_id, other.id,
                                                     assessment_ref="check-1")
        assert session.scalar(select(func.count()).select_from(AccountFillReportAssessmentRow)) == 2
        session.commit()


def test_report_correction_chain_and_void_remain_unverified(env):
    factory = env["session_factory"]
    portfolio_id, order_id = _account_and_order(factory)
    with factory() as session:
        original, _ = _record(session, portfolio_id, order_id, quantity="5")
        replacement, _ = _record(session, portfolio_id, order_id, quantity="4",
                                 fee="0.2", source_ref="report-2")
        service = AccountFillReportService(session)
        corrected, created = service.resolve(
            portfolio_id, original.id, action="CORRECT",
            replacement_report_id=replacement.id, reason="broker correction claim",
            captured_at=CAPTURED, source_type="MANUAL_REPORT", source_ref="resolution-1")
        assert created and corrected.action == "CORRECT"
        replay, created = service.resolve(
            portfolio_id, original.id, action="CORRECT",
            replacement_report_id=replacement.id, reason="broker correction claim",
            captured_at=CAPTURED, source_type="MANUAL_REPORT", source_ref="resolution-1")
        assert not created and replay.id == corrected.id
        with pytest.raises(ValueError, match="already has a resolution"):
            service.resolve(portfolio_id, original.id, action="VOID",
                            replacement_report_id=None, reason="conflicting void",
                            captured_at=CAPTURED, source_type="MANUAL_REPORT",
                            source_ref="resolution-conflict")
        with pytest.raises(ValueError, match="must follow original"):
            service.resolve(portfolio_id, replacement.id, action="CORRECT",
                            replacement_report_id=original.id, reason="cycle",
                            captured_at=CAPTURED, source_type="MANUAL_REPORT",
                            source_ref="resolution-cycle")
        voided, created = service.resolve(
            portfolio_id, replacement.id, action="VOID", replacement_report_id=None,
            reason="trade cancelled claim", captured_at=CAPTURED,
            source_type="MANUAL_REPORT", source_ref="resolution-2")
        assert created and voided.action == "VOID"
        assessment, _ = service.assess(portfolio_id, original.id,
                                       assessment_ref="after-correction")
        assert "REPORT_RESOLVED_BY_DECLARATION" in assessment.issues
        assert "SOURCE_UNVERIFIED" in assessment.issues
        assert session.get(SuggestedOrder, order_id).filled_quantity == 0
        session.commit()
        resolution_id = corrected.id
    with factory() as session:
        with pytest.raises(DBAPIError, match="fill resolution history is immutable"):
            session.execute(text("DELETE FROM account_fill_report_resolutions WHERE id=:id"),
                            {"id": resolution_id})
        session.rollback()
        assert session.scalar(select(func.count()).select_from(AccountFillReportResolutionRow)) == 2


def test_report_resolution_requires_same_portfolio_and_new_source(env):
    factory = env["session_factory"]
    portfolio_id, order_id = _account_and_order(factory)
    other_portfolio, other_order = _account_and_order(factory)
    with factory() as session:
        original, _ = _record(session, portfolio_id, order_id, quantity="5")
        other, _ = _record(session, other_portfolio, other_order, quantity="4")
        service = AccountFillReportService(session)
        with pytest.raises(ValueError, match="does not belong"):
            service.resolve(portfolio_id, original.id, action="CORRECT",
                            replacement_report_id=other.id, reason="wrong account",
                            captured_at=CAPTURED, source_type="MANUAL_REPORT",
                            source_ref="resolution-1")
        first, _ = service.resolve(portfolio_id, original.id, action="VOID",
                                   replacement_report_id=None, reason="cancel claim",
                                   captured_at=CAPTURED, source_type="MANUAL_REPORT",
                                   source_ref="resolution-1")
        assert first.action == "VOID"
        with pytest.raises(ValueError, match="replay changed"):
            service.resolve(portfolio_id, original.id, action="VOID",
                            replacement_report_id=None, reason="different reason",
                            captured_at=CAPTURED, source_type="MANUAL_REPORT",
                            source_ref="resolution-1")
        session.commit()


def test_legacy_fill_mutations_stop_after_audited_baseline(env):
    factory = env["session_factory"]
    portfolio_id, order_id = _account_and_order(factory)
    with factory() as session:
        first, order = LifecycleOrderService(session).confirm_fill(
            order_id, quantity="5", fill_price="10", fill_trade_date=TRADE_DATE,
            idempotency_key="pre-baseline-fill", expected_revision=1)
        assert order.filled_quantity == 5
        fill_id = first.id
    with factory() as session:
        _establish_baseline(session, portfolio_id, cash="950", quantity="5")
        session.commit()
    with factory() as session:
        service = LifecycleOrderService(session)
        with pytest.raises(LifecycleInvalidStateError, match="旧成交入口缺费用"):
            service.confirm_fill(order_id, quantity="1", fill_price="10",
                                 fill_trade_date=TRADE_DATE,
                                 idempotency_key="post-baseline-fill", expected_revision=2)
        with pytest.raises(LifecycleInvalidStateError, match="旧成交入口缺费用"):
            service.correct_fill(fill_id, quantity="4", fill_price="10",
                                 fill_trade_date=TRADE_DATE,
                                 idempotency_key="post-baseline-correct", expected_revision=2)
        with pytest.raises(LifecycleInvalidStateError, match="旧成交入口缺费用"):
            service.void_fill(fill_id, fill_trade_date=TRADE_DATE,
                              idempotency_key="post-baseline-void", expected_revision=2)
        order = session.get(SuggestedOrder, order_id)
        assert order.filled_quantity == 5 and order.revision == 2
        assert session.get(Portfolio, portfolio_id).available_cash == Decimal(950)


def test_unverified_reports_and_baseline_block_new_buy_at_live_materialization(env):
    factory = env["session_factory"]
    reported_id, reported_order = _account_and_order(factory)
    baseline_id, _ = _account_and_order(factory)
    with factory() as session:
        _record(session, reported_id, reported_order, quantity="2", fee="0")
        _establish_baseline(session, baseline_id)
        session.commit()
    for portfolio_id in (reported_id, baseline_id):
        with factory() as session:
            portfolio = lock_portfolio(session, portfolio_id)
            assert planning_account(session, portfolio)["portfolio_snapshot"][
                "account_reconciliation_required"] is True
            signal = SimpleNamespace(id=999, signal_kind="BUY", ts_code="000001.SZ")
            orders = materialize_signal_orders(
                session, portfolio_id=portfolio_id, rows=[signal],
                strategy_snapshots={}, industry_map={})
            assert orders == []
            assert signal.order_status == "BUY_REJECTED_ACCOUNT_RECONCILIATION"
            assert session.get(Portfolio, portfolio_id).available_cash == Decimal(1000)


def test_unverified_report_blocks_existing_buy_fill_even_after_void_claim(env):
    factory = env["session_factory"]
    portfolio_id, order_id = _account_and_order(factory)
    with factory() as session:
        report, _ = _record(session, portfolio_id, order_id, quantity="2", fee="0")
        session.commit()
        report_id = report.id
    for void_claim in (False, True):
        with factory() as session:
            if void_claim:
                AccountFillReportService(session).resolve(
                    portfolio_id, report_id, action="VOID", replacement_report_id=None,
                    reason="unverified cancellation", captured_at=CAPTURED,
                    source_type="MANUAL_REPORT", source_ref="void-before-fill")
                session.commit()
            with pytest.raises(LifecycleInvalidStateError, match="未认证成交报告"):
                LifecycleOrderService(session).confirm_fill(
                    order_id, quantity="1", fill_price="10", fill_trade_date=TRADE_DATE,
                    idempotency_key=f"unverified-{void_claim}", expected_revision=1)
            order = session.get(SuggestedOrder, order_id)
            assert order.filled_quantity == 0 and order.status == "PROPOSED"
            assert session.get(Portfolio, portfolio_id).available_cash == Decimal(1000)
            assert session.scalar(select(func.count()).select_from(PortfolioPosition)) == 0
            assert session.scalar(select(func.count()).select_from(OrderFillEvent)) == 0


def test_report_gate_preserves_protective_sell_but_blocks_old_fill_revisions(env):
    factory = env["session_factory"]
    portfolio_id, buy_order_id = _account_and_order(factory)
    with factory() as session:
        first, _ = LifecycleOrderService(session).confirm_fill(
            buy_order_id, quantity="5", fill_price="10", fill_trade_date=TRADE_DATE,
            idempotency_key="before-report", expected_revision=1)
        fill_id = first.id
    with factory() as session:
        _record(session, portfolio_id, buy_order_id, quantity="1", fee="0")
        sell_id = uuid.uuid4()
        session.add(SuggestedOrder(
            id=sell_id, portfolio_id=portfolio_id, market="CN", symbol="000001.SZ",
            side="SELL", quantity=Decimal(5), limit_price=Decimal(10),
            reason_code="PROTECTIVE", status="PROPOSED"))
        session.commit()
    with factory() as session:
        service = LifecycleOrderService(session)
        with pytest.raises(LifecycleInvalidStateError, match="未认证成交报告"):
            service.correct_fill(fill_id, quantity="6", fill_price="10",
                                 fill_trade_date=TRADE_DATE,
                                 idempotency_key="blocked-correct", expected_revision=2)
        with pytest.raises(LifecycleInvalidStateError, match="未认证成交报告"):
            service.void_fill(fill_id, fill_trade_date=TRADE_DATE,
                              idempotency_key="blocked-void", expected_revision=2)
        sell_fill, sell_order = service.confirm_fill(
            sell_id, quantity="1", fill_price="10", fill_trade_date=TRADE_DATE,
            idempotency_key="protective-sell", expected_revision=1)
        assert sell_fill.event_type == "CONFIRM" and sell_order.filled_quantity == 1
        assert session.scalar(select(PortfolioPosition.quantity).where(
            PortfolioPosition.portfolio_id == portfolio_id)) == Decimal(4)
