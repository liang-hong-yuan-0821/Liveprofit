# test-catalog-begin
# {
#   "purpose": "投资工作区 / fill_postings（成交）：0037 identity constraints only; no production acceptance service is invoked.",
#   "keywords": [
#     "投资工作区",
#     "执行",
#     "成交",
#     "fill_postings",
#     "execution",
#     "fill"
#   ],
#   "covers": [
#     "backend/modules/investment_workspace/application/account_ledger_store.py",
#     "backend/modules/investment_workspace/application/account_observations.py",
#     "backend/modules/investment_workspace/application/fill_reports.py",
#     "backend/modules/investment_workspace/application/reconciliation.py",
#     "backend/modules/investment_workspace/infrastructure/account_ledger_models.py",
#     "backend/modules/investment_workspace/infrastructure/fill_report_models.py",
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""0037 identity constraints only; no production acceptance service is invoked."""

from datetime import date, datetime, timezone
from decimal import Decimal
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from backend.modules.investment_workspace.application.account_ledger_store import AccountLedgerStore
from backend.modules.investment_workspace.application.account_observations import AccountObservationService
from backend.modules.investment_workspace.application.fill_reports import AccountFillReportService
from backend.modules.investment_workspace.application.reconciliation import AccountObservation
from backend.modules.investment_workspace.infrastructure.account_ledger_models import AccountLedgerMovementRow
from backend.modules.investment_workspace.infrastructure.fill_report_models import (
    AccountFillPostingRow, AccountFillReportRow,
)
from backend.modules.investment_workspace.infrastructure.models import Portfolio, PortfolioPosition
from backend.modules.quant_strategy.infrastructure.lifecycle_models import OrderFillEvent, SuggestedOrder


TRADE_DATE = date(2026, 9, 28)
EXECUTED = datetime(2026, 9, 28, 3, 30, tzinfo=timezone.utc)
CAPTURED = datetime(2026, 9, 29, 8, tzinfo=timezone.utc)
BASELINE_AT = datetime(2026, 9, 27, 8, tzinfo=timezone.utc)


def _facts(factory):
    portfolio_id, order_id = uuid.uuid4(), uuid.uuid4()
    with factory() as session:
        session.add(Portfolio(id=portfolio_id, name=f"posting-{portfolio_id}",
                              version=1, total_assets=Decimal(1000),
                              available_cash=Decimal(1000)))
        session.add(SuggestedOrder(
            id=order_id, portfolio_id=portfolio_id, market="CN", symbol="000001.SZ",
            side="BUY", quantity=Decimal(10), limit_price=Decimal(10),
            reason_code="TEST_POSTING", status="PROPOSED"))
        session.commit()
    with factory() as session:
        observation, _ = AccountObservationService(session).record(AccountObservation(
            portfolio_id=portfolio_id, trade_date=BASELINE_AT.date(),
            captured_at=BASELINE_AT, cash="1000", holdings=(),
            complete_holdings=True, source_ref="opening-account"),
            source_type="MANUAL_IMPORT")
        baseline, _ = AccountLedgerStore(session).establish_baseline(portfolio_id, observation.id)
        report, _ = AccountFillReportService(session).record(
            portfolio_id, order_id=order_id, market="CN", symbol="000001.SZ",
            side="BUY", fill_trade_date=TRADE_DATE, executed_at=EXECUTED,
            captured_at=CAPTURED, quantity="5", fill_price="10", fee="1",
            source_type="MANUAL_REPORT", source_ref="broker-row-1")
        store = AccountLedgerStore(session)
        movement, _ = store.append_trade(
            portfolio_id, effective_at=EXECUTED, market="CN", symbol="000001.SZ",
            quantity_delta="5", fill_price="10", fee="1",
            source_type="MANUAL_ENTRY", source_ref="broker-row-1")
        wrong_fee, _ = store.append_trade(
            portfolio_id, effective_at=EXECUTED, market="CN", symbol="000001.SZ",
            quantity_delta="5", fill_price="10", fee="2",
            source_type="MANUAL_ENTRY", source_ref="wrong-fee")
        null_quantity = AccountLedgerMovementRow(
            id=uuid.uuid4(), portfolio_id=portfolio_id, baseline_id=baseline.id,
            recorded_at=datetime.now(timezone.utc), effective_at=EXECUTED,
            kind="TRADE", cash_delta=Decimal(-51),
            holdings_delta=[["CN", "000001.SZ", None]],
            fill_price=Decimal(10), fee=Decimal(1),
            source_type="MANUAL_ENTRY", source_ref="malformed-null-quantity",
            payload_sha256="0" * 64)
        session.add(null_quantity)
        event = OrderFillEvent(
            order_id=order_id, portfolio_id=portfolio_id, event_type="CONFIRM",
            quantity=Decimal(5), fill_price=Decimal(10), fill_trade_date=TRADE_DATE,
            source="MANUAL", idempotency_key=f"posting-{order_id}")
        session.add(event)
        # Structure test simulates the projection that the later acceptance
        # service must commit together with the fill, ledger and binding.
        order = session.get(SuggestedOrder, order_id)
        order.filled_quantity = Decimal(5)
        order.status = "PARTIALLY_FILLED"
        session.add(PortfolioPosition(
            portfolio_id=portfolio_id, market="CN", symbol="000001.SZ",
            quantity=Decimal(5), average_cost=Decimal(10)))
        session.get(Portfolio, portfolio_id).available_cash = Decimal(949)
        session.commit()
        return portfolio_id, report.id, event.id, movement.id, wrong_fee.id, null_quantity.id


def test_posting_requires_matching_trade_and_one_to_one_identity(env):
    factory = env["session_factory"]
    portfolio_id, report_id, event_id, movement_id, wrong_fee_id, null_quantity_id = _facts(factory)
    with factory() as session:
        session.add(AccountFillPostingRow(
            id=uuid.uuid4(), portfolio_id=portfolio_id, report_id=report_id,
            fill_event_id=event_id, ledger_movement_id=wrong_fee_id))
        with pytest.raises(DBAPIError, match="fill posting values disagree"):
            session.flush()
        session.rollback()
    with factory() as session:
        session.add(AccountFillPostingRow(
            id=uuid.uuid4(), portfolio_id=portfolio_id, report_id=report_id,
            fill_event_id=event_id, ledger_movement_id=null_quantity_id))
        with pytest.raises(DBAPIError, match="fill posting trade cash or quantity disagree"):
            session.flush()
        session.rollback()
    with factory() as session:
        posted = AccountFillPostingRow(
            id=uuid.uuid4(), portfolio_id=portfolio_id, report_id=report_id,
            fill_event_id=event_id, ledger_movement_id=movement_id)
        session.add(posted)
        session.commit()
        posted_id = posted.id
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AccountFillPostingRow)) == 1
        session.add(AccountFillPostingRow(
            id=uuid.uuid4(), portfolio_id=portfolio_id, report_id=report_id,
            fill_event_id=event_id, ledger_movement_id=movement_id))
        with pytest.raises(DBAPIError, match="uq_fill_posting"):
            session.flush()
        session.rollback()
        with pytest.raises(DBAPIError, match="account fill posting history is immutable"):
            session.execute(text("UPDATE account_fill_postings SET report_id=:id WHERE id=:id"),
                            {"id": posted_id})
        session.rollback()


def test_report_execution_time_is_explicit_and_cn_date_matched(env):
    factory = env["session_factory"]
    portfolio_id, report_id, _, _, _, _ = _facts(factory)
    with factory() as session:
        report = session.get(AccountFillReportRow, report_id)
        assert report.executed_at == EXECUTED
        with pytest.raises(ValueError, match="executed_at"):
            AccountFillReportService(session).record(
                portfolio_id, order_id=None, market="CN", symbol="000001.SZ",
                side="BUY", fill_trade_date=TRADE_DATE,
                executed_at=datetime(2026, 9, 27, 3, 30, tzinfo=timezone.utc),
                captured_at=CAPTURED, quantity="1", fill_price="10", fee="0",
                source_type="MANUAL_REPORT", source_ref="wrong-day")


def test_posted_report_later_resolution_and_reversal_remain_visible(env):
    factory = env["session_factory"]
    portfolio_id, report_id, event_id, movement_id, _, _ = _facts(factory)
    with factory() as session:
        service = AccountFillReportService(session)
        assert service.diagnose_posting(portfolio_id, report_id).issues == (
            "SOURCE_UNVERIFIED", "NOT_POSTED")
        posting = AccountFillPostingRow(
            id=uuid.uuid4(), portfolio_id=portfolio_id, report_id=report_id,
            fill_event_id=event_id, ledger_movement_id=movement_id)
        session.add(posting)
        session.flush()
        assert service.diagnose_posting(portfolio_id, report_id).issues == ("SOURCE_UNVERIFIED",)
        service.resolve(
            portfolio_id, report_id, action="VOID", replacement_report_id=None,
            reason="broker declaration corrected", captured_at=CAPTURED,
            source_type="MANUAL_REPORT", source_ref="void-broker-row-1")
        original = session.get(OrderFillEvent, event_id)
        session.add(OrderFillEvent(
            order_id=original.order_id, portfolio_id=portfolio_id,
            event_type="VOID", reverses_fill_id=event_id,
            quantity=original.quantity, fill_price=original.fill_price,
            fill_trade_date=TRADE_DATE, source="MANUAL",
            idempotency_key=f"void-{event_id}"))
        old_movement = session.get(AccountLedgerMovementRow, movement_id)
        session.add(AccountLedgerMovementRow(
            id=uuid.uuid4(), portfolio_id=portfolio_id,
            baseline_id=old_movement.baseline_id,
            recorded_at=datetime.now(timezone.utc), effective_at=EXECUTED,
            kind="TRADE", cash_delta=Decimal(-51),
            holdings_delta=[["CN", "000001.SZ", "5.0000"]],
            fill_price=Decimal(10), fee=Decimal(1),
            supersedes_id=movement_id, reason="report correction",
            source_type="MANUAL_ENTRY", source_ref="replacement-after-posting",
            payload_sha256="1" * 64))
        session.commit()
    with factory() as session:
        diagnostic = AccountFillReportService(session).diagnose_posting(portfolio_id, report_id)
        assert diagnostic.posting_id == posting.id
        assert diagnostic.issues == (
            "SOURCE_UNVERIFIED", "REPORT_RESOLVED_BY_DECLARATION",
            "FILL_REVERSED", "LEDGER_SUPERSEDED")


@pytest.mark.parametrize("reversal", ["fill", "ledger"])
def test_reversed_event_or_superseded_trade_cannot_be_newly_linked(env, reversal):
    factory = env["session_factory"]
    portfolio_id, report_id, event_id, movement_id, _, _ = _facts(factory)
    with factory() as session:
        if reversal == "fill":
            original = session.get(OrderFillEvent, event_id)
            session.add(OrderFillEvent(
                order_id=original.order_id, portfolio_id=portfolio_id,
                event_type="VOID", reverses_fill_id=event_id,
                quantity=original.quantity, fill_price=original.fill_price,
                fill_trade_date=TRADE_DATE, source="MANUAL",
                idempotency_key=f"void-{event_id}"))
        else:
            original = session.get(AccountLedgerMovementRow, movement_id)
            session.add(AccountLedgerMovementRow(
                id=uuid.uuid4(), portfolio_id=portfolio_id,
                baseline_id=original.baseline_id,
                recorded_at=datetime.now(timezone.utc), effective_at=EXECUTED,
                kind="TRADE", cash_delta=Decimal(-51),
                holdings_delta=[["CN", "000001.SZ", "5.0000"]],
                fill_price=Decimal(10), fee=Decimal(1),
                supersedes_id=movement_id, reason="correction claim",
                source_type="MANUAL_ENTRY", source_ref="replacement-trade",
                payload_sha256="1" * 64))
        session.commit()
    with factory() as session:
        session.add(AccountFillPostingRow(
            id=uuid.uuid4(), portfolio_id=portfolio_id, report_id=report_id,
            fill_event_id=event_id, ledger_movement_id=movement_id))
        with pytest.raises(DBAPIError, match="resolved report or reversed trade"):
            session.flush()
