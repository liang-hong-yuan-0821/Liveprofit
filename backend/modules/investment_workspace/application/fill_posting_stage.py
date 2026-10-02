"""Internal transaction stage for reviewed fills; no production entry is wired."""

from __future__ import annotations

import uuid
import os
from decimal import Decimal

from sqlalchemy import func, select

from backend.modules.investment_workspace.infrastructure.fill_report_models import (
    AccountFillPostingRow, AccountFillPostingVoidRow, AccountFillPostingCorrectionRow,
    AccountFillReportResolutionRow,
    AccountFillReportRow,
)
from backend.modules.investment_workspace.infrastructure.account_ledger_models import AccountLedgerMovementRow
from backend.modules.investment_workspace.infrastructure.models import Portfolio, PortfolioPosition
from backend.modules.quant_strategy.application.lifecycle_service import LifecycleOrderService
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    OrderFillEvent, PositionLifecycleState, SuggestedOrder,
)

from .account_ledger_store import AccountLedgerStore
from .fill_reviews import AccountFillReviewService
from .fill_resolution_reviews import AccountFillResolutionReviewService


class AccountFillPostingStage:
    """Software-only stage. No public API may invoke it before account certification."""

    def __init__(self, session) -> None:
        self.session = session

    def _require_isolated_test_database(self) -> None:
        with self.session.no_autoflush:
            isolated_database = (
                "PYTEST_CURRENT_TEST" in os.environ
                and self.session.scalar(select(func.current_database())) ==
                "liveprofit_workspace_test"
            )
        if not isolated_database:
            raise ValueError("production fill projection is disabled pending account certification")

    def _stage_reviewed_report(
        self, portfolio_id: uuid.UUID, report_id: uuid.UUID, *,
        expected_order_revision: int,
    ) -> tuple[AccountFillPostingRow, bool]:
        """Stage all projections under the caller's transaction; never commit."""
        if not isinstance(portfolio_id, uuid.UUID) or not isinstance(report_id, uuid.UUID):
            raise ValueError("portfolio_id and report_id must be UUIDs")
        self._require_isolated_test_database()
        AccountFillReviewService(self.session).verify_signed_claim(portfolio_id, report_id)
        report = self.session.get(AccountFillReportRow, report_id, populate_existing=True)
        existing = self.session.scalar(select(AccountFillPostingRow).where(
            AccountFillPostingRow.portfolio_id == portfolio_id,
            AccountFillPostingRow.report_id == report_id))
        if existing is not None:
            return existing, False
        if (report is None or report.portfolio_id != portfolio_id
                or report.order_id is None or report.market != "CN"
                or report.executed_at is None or report.fee is None):
            raise ValueError("report is not complete for CN order projection")
        lifecycle = LifecycleOrderService(self.session)
        order = lifecycle._lock_order(report.order_id, expected_order_revision)
        if (order.portfolio_id != portfolio_id
                or (order.market, order.symbol, order.side) !=
                (report.market, report.symbol, report.side)):
            raise ValueError("report and order identity disagree")
        if (order.earliest_execution_trade_date is not None
                and report.fill_trade_date < order.earliest_execution_trade_date):
            raise ValueError("report predates order eligibility")
        if report.quantity > order.quantity - order.filled_quantity:
            raise ValueError("report exceeds order remaining quantity")
        ledger = AccountLedgerStore(self.session)
        balance = ledger.replay_current(portfolio_id)
        portfolio = self.session.scalar(select(Portfolio).where(Portfolio.id == portfolio_id)
                                        .with_for_update().execution_options(populate_existing=True))
        positions = list(self.session.scalars(select(PortfolioPosition).where(
            PortfolioPosition.portfolio_id == portfolio_id,
        ).with_for_update().execution_options(populate_existing=True)))
        projected_holdings = tuple(sorted(
            (position.market, position.symbol, Decimal(position.quantity))
            for position in positions if position.quantity != 0))
        if (balance.issues or balance.cash != Decimal(portfolio.available_cash)
                or balance.holdings != projected_holdings):
            raise ValueError("diagnostic ledger and production account differ")
        position_before = next((position for position in positions
                                if (position.market, position.symbol) ==
                                (order.market, order.symbol)), None)
        quantity_before = (Decimal(position_before.quantity) if position_before is not None
                           else Decimal(0))
        average_cost_before = (Decimal(position_before.average_cost) if position_before is not None
                               else Decimal(0))
        signed_quantity = report.quantity if report.side == "BUY" else -report.quantity
        movement, created = ledger.append_trade(
            portfolio_id, effective_at=report.executed_at,
            market=report.market, symbol=report.symbol,
            quantity_delta=signed_quantity, fill_price=report.fill_price,
            fee=report.fee, source_type="MANUAL_ENTRY",
            source_ref=f"reviewed-report-{report.id}")
        if not created:
            raise ValueError("report ledger trade already exists without a posting")
        event = lifecycle._stage_confirm_fill_locked(
            order, qty=report.quantity, price=report.fill_price,
            fill_trade_date=report.fill_trade_date,
            idempotency_key=f"reviewed-report-{report.id}",
            source="MANUAL", note="reviewed report staging only",
            fee=report.fee)
        posting = AccountFillPostingRow(
            id=uuid.uuid4(), portfolio_id=portfolio_id,
            report_id=report_id, fill_event_id=event.id,
            ledger_movement_id=movement.id,
            position_quantity_before=quantity_before,
            position_average_cost_before=average_cost_before)
        self.session.add(posting)
        self.session.flush()
        return posting, True

    def _stage_reviewed_void(
        self, portfolio_id: uuid.UUID, resolution_id: uuid.UUID, *,
        expected_order_revision: int,
        _for_correction: bool = False,
    ) -> tuple[AccountFillPostingVoidRow | tuple[AccountFillPostingRow, OrderFillEvent,
                                                AccountLedgerMovementRow], bool]:
        """Stage a latest-fill reversal; caller owns commit/rollback, production closed."""
        if not isinstance(portfolio_id, uuid.UUID) or not isinstance(resolution_id, uuid.UUID):
            raise ValueError("portfolio_id and resolution_id must be UUIDs")
        self._require_isolated_test_database()
        AccountFillResolutionReviewService(self.session).verify_signed_claim(
            portfolio_id, resolution_id)
        resolution = self.session.get(AccountFillReportResolutionRow, resolution_id,
                                      populate_existing=True)
        if _for_correction:
            if resolution.action != "CORRECT" or resolution.replacement_report_id is None:
                raise ValueError("resolution is not a report correction")
        else:
            if resolution.action != "VOID" or resolution.replacement_report_id is not None:
                raise ValueError("resolution is not a report void")
            existing = self.session.scalar(select(AccountFillPostingVoidRow).where(
                AccountFillPostingVoidRow.resolution_id == resolution_id))
            if existing is not None:
                return existing, False
        posting = self.session.scalar(select(AccountFillPostingRow).where(
            AccountFillPostingRow.report_id == resolution.report_id,
            AccountFillPostingRow.portfolio_id == portfolio_id))
        if posting is None:
            raise ValueError("void resolution has no accepted posting")
        AccountFillReviewService(self.session).verify_historical_claim(
            portfolio_id, posting.report_id)
        original = self.session.get(OrderFillEvent, posting.fill_event_id, populate_existing=True)
        report = self.session.get(AccountFillReportRow, posting.report_id, populate_existing=True)
        if (original is None or original.event_type != "CONFIRM" or report is None
                or report.fee is None or original.portfolio_id != portfolio_id):
            raise ValueError("original posting is not a reversible fill")
        lifecycle = LifecycleOrderService(self.session)
        order = lifecycle._lock_order(original.order_id, expected_order_revision,
                                      allow_terminal=True)
        if (order.portfolio_id != portfolio_id or report.order_id != order.id
                or (order.market, order.symbol, order.side) !=
                (report.market, report.symbol, report.side)):
            raise ValueError("void posting and order identity disagree")
        if (order.lifecycle_id is not None or self.session.scalar(
                select(PositionLifecycleState.id).where(
                    PositionLifecycleState.initial_fill_id == original.id).limit(1)
        ) is not None):
            raise ValueError("lifecycle-linked fill reversal requires state replay")
        lifecycle._lock_fill(original.id)
        lifecycle._ensure_not_reversed(original.id)
        ledger = AccountLedgerStore(self.session)
        balance = ledger.replay_current(portfolio_id)
        if not balance.effective_event_ids or balance.effective_event_ids[-1] != posting.ledger_movement_id:
            raise ValueError("void requires the original trade to be the latest effective movement")
        original_movement = self.session.get(AccountLedgerMovementRow,
                                             posting.ledger_movement_id,
                                             populate_existing=True)
        signed_original_quantity = (Decimal(report.quantity) if report.side == "BUY"
                                    else -Decimal(report.quantity))
        if (original_movement is None or original_movement.portfolio_id != portfolio_id
                or original_movement.kind != "TRADE"
                or original_movement.effective_at != report.executed_at
                or Decimal(original_movement.fill_price) != Decimal(report.fill_price)
                or Decimal(original_movement.fee) != Decimal(report.fee)
                or original_movement.holdings_delta != [
                    [report.market, report.symbol, str(signed_original_quantity.quantize(Decimal("0.0001")))]]
                or Decimal(original_movement.cash_delta) !=
                -signed_original_quantity * Decimal(report.fill_price) - Decimal(report.fee)
                or original.quantity != report.quantity
                or original.fill_price != report.fill_price
                or original.fill_trade_date != report.fill_trade_date):
            raise ValueError("original posting values no longer match report")
        if self.session.scalar(select(AccountLedgerMovementRow.id).where(
                AccountLedgerMovementRow.portfolio_id == portfolio_id,
                AccountLedgerMovementRow.id != posting.ledger_movement_id,
                AccountLedgerMovementRow.recorded_at >= original_movement.recorded_at,
        ).limit(1)) is not None:
            raise ValueError("void requires no later recorded ledger movement")
        portfolio = self.session.scalar(select(Portfolio).where(Portfolio.id == portfolio_id)
                                        .with_for_update().execution_options(populate_existing=True))
        positions = list(self.session.scalars(select(PortfolioPosition).where(
            PortfolioPosition.portfolio_id == portfolio_id,
        ).with_for_update().execution_options(populate_existing=True)))
        projected_holdings = tuple(sorted(
            (position.market, position.symbol, Decimal(position.quantity))
            for position in positions if position.quantity != 0))
        if (balance.issues or balance.cash != Decimal(portfolio.available_cash)
                or balance.holdings != projected_holdings):
            raise ValueError("diagnostic ledger and production account differ")
        if (posting.position_quantity_before is None
                or posting.position_average_cost_before is None):
            raise ValueError("original posting lacks cost basis snapshot")
        position = next((item for item in positions if (item.market, item.symbol) ==
                         (order.market, order.symbol)), None)
        signed_quantity = (Decimal(original.quantity) if order.side == "BUY"
                           else -Decimal(original.quantity))
        if (position is None or Decimal(position.quantity) !=
                Decimal(posting.position_quantity_before) + signed_quantity):
            raise ValueError("position quantity has changed since original posting")
        movement, created = ledger.void_trade(
            portfolio_id, trade_movement_id=posting.ledger_movement_id,
            reason=resolution.reason, source_type="MANUAL_ENTRY",
            source_ref=f"reviewed-resolution-{resolution.id}")
        if not created:
            raise ValueError("resolution ledger void already exists without a posting void")
        order.status = "RECONCILIATION_REQUIRED"
        lifecycle._apply_delta(order, -Decimal(original.quantity),
                               Decimal(original.fill_price), fee=-Decimal(report.fee))
        if order.side == "BUY":
            position.average_cost = Decimal(posting.position_average_cost_before)
        event = OrderFillEvent(
            order_id=order.id, portfolio_id=portfolio_id, position_id=order.position_id,
            event_type="VOID", reverses_fill_id=original.id,
            quantity=original.quantity, fill_price=original.fill_price,
            fill_trade_date=original.fill_trade_date, source=original.source,
            idempotency_key=f"reviewed-resolution-{resolution.id}",
            note="reviewed resolution staging only")
        self.session.add(event)
        self.session.flush()
        if not _for_correction:
            void = AccountFillPostingVoidRow(
                id=uuid.uuid4(), portfolio_id=portfolio_id,
                resolution_id=resolution_id, posting_id=posting.id,
                fill_event_id=event.id, ledger_movement_id=movement.id)
            self.session.add(void)
            self.session.flush()
        projected = ledger.replay_current(portfolio_id)
        actual_cash = Decimal(self.session.scalar(select(Portfolio.available_cash).where(
            Portfolio.id == portfolio_id)))
        actual_positions = tuple(sorted(
            (item.market, item.symbol, Decimal(item.quantity))
            for item in self.session.scalars(select(PortfolioPosition).where(
                PortfolioPosition.portfolio_id == portfolio_id)) if item.quantity != 0))
        if (projected.issues or projected.cash != actual_cash
                or projected.holdings != actual_positions):
            raise ValueError("void ledger and production account differ after staging")
        if _for_correction:
            return (posting, event, movement), True
        return void, True

    def _stage_reviewed_correction(
        self, portfolio_id: uuid.UUID, resolution_id: uuid.UUID, *,
        expected_order_revision: int,
    ) -> tuple[AccountFillPostingCorrectionRow, bool]:
        """Stage old reversal plus accepted replacement in one isolated transaction."""
        if not isinstance(portfolio_id, uuid.UUID) or not isinstance(resolution_id, uuid.UUID):
            raise ValueError("portfolio_id and resolution_id must be UUIDs")
        self._require_isolated_test_database()
        AccountFillResolutionReviewService(self.session).verify_signed_claim(
            portfolio_id, resolution_id)
        resolution = self.session.get(AccountFillReportResolutionRow, resolution_id,
                                      populate_existing=True)
        if resolution.action != "CORRECT" or resolution.replacement_report_id is None:
            raise ValueError("resolution is not a report correction")
        AccountFillReviewService(self.session).verify_historical_claim(
            portfolio_id, resolution.report_id)
        existing = self.session.scalar(select(AccountFillPostingCorrectionRow).where(
            AccountFillPostingCorrectionRow.resolution_id == resolution_id))
        if existing is not None:
            AccountFillReviewService(self.session).verify_historical_claim(
                portfolio_id, resolution.replacement_report_id)
            return existing, False
        AccountFillReviewService(self.session).verify_signed_claim(
            portfolio_id, resolution.replacement_report_id)
        original_report = self.session.get(AccountFillReportRow, resolution.report_id,
                                           populate_existing=True)
        replacement_report = self.session.get(AccountFillReportRow,
                                              resolution.replacement_report_id,
                                              populate_existing=True)
        if (original_report.order_id is None
                or replacement_report.order_id != original_report.order_id
                or (replacement_report.market, replacement_report.symbol,
                    replacement_report.side) !=
                   (original_report.market, original_report.symbol,
                    original_report.side)):
            raise ValueError("replacement report changes order or instrument identity")
        result, created = self._stage_reviewed_void(
            portfolio_id, resolution_id, expected_order_revision=expected_order_revision,
            _for_correction=True)
        if not created:
            raise ValueError("correction reversal was not newly staged")
        original_posting, void_event, void_movement = result
        replacement_posting, created = self._stage_reviewed_report(
            portfolio_id, replacement_report.id,
            expected_order_revision=expected_order_revision + 1)
        if not created:
            raise ValueError("replacement report was already posted")
        correction = AccountFillPostingCorrectionRow(
            id=uuid.uuid4(), portfolio_id=portfolio_id,
            resolution_id=resolution_id, original_posting_id=original_posting.id,
            void_fill_event_id=void_event.id,
            void_ledger_movement_id=void_movement.id,
            replacement_posting_id=replacement_posting.id)
        self.session.add(correction)
        self.session.flush()
        balance = AccountLedgerStore(self.session).replay_current(portfolio_id)
        cash = Decimal(self.session.scalar(select(Portfolio.available_cash).where(
            Portfolio.id == portfolio_id)))
        positions = tuple(sorted(
            (item.market, item.symbol, Decimal(item.quantity))
            for item in self.session.scalars(select(PortfolioPosition).where(
                PortfolioPosition.portfolio_id == portfolio_id)) if item.quantity != 0))
        if balance.issues or balance.cash != cash or balance.holdings != positions:
            raise ValueError("corrected ledger and production account differ after staging")
        return correction, True
