"""Append reported fills without treating them as accepted broker executions."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from backend.modules.investment_workspace.infrastructure.fill_report_models import (
    AccountFillPostingRow, AccountFillReportAssessmentRow, AccountFillReportResolutionRow,
    AccountFillReportRow,
)
from backend.modules.investment_workspace.infrastructure.account_ledger_models import AccountLedgerMovementRow
from backend.modules.investment_workspace.infrastructure.models import Portfolio
from backend.modules.quant_strategy.infrastructure.lifecycle_models import OrderFillEvent, SuggestedOrder
from .account_ledger_store import _money
from .reconciliation import _key


SOURCE_TYPES = frozenset({"MANUAL_REPORT", "BROKER_EXPORT", "BROKER_API"})


def report_digest(*, portfolio_id: uuid.UUID, order_id: uuid.UUID | None,
                  market: str, symbol: str, side: str, fill_trade_date: date,
                  captured_at: datetime, quantity: Decimal, fill_price: Decimal,
                  fee: Decimal | None, source_type: str, source_ref: str,
                  executed_at: datetime | None, evidence_sha256: str | None,
                  evidence_uri: str | None, reporter_claim: str | None) -> str:
    """Canonical digest shared by declaration writes and signed-review reads."""
    payload = {
        "portfolio_id": str(portfolio_id),
        "order_id": str(order_id) if order_id else None,
        "market": market, "symbol": symbol, "side": side,
        "fill_trade_date": fill_trade_date.isoformat(),
        "captured_at": captured_at.astimezone(timezone.utc).isoformat(),
        "quantity": str(quantity), "fill_price": str(fill_price),
        "fee": str(fee) if fee is not None else None,
        "source_type": source_type, "source_ref": source_ref,
        "evidence_sha256": evidence_sha256, "evidence_uri": evidence_uri,
        "reporter_claim": reporter_claim,
    }
    if executed_at is not None:
        payload["executed_at"] = executed_at.astimezone(timezone.utc).isoformat()
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def report_row_digest(report: AccountFillReportRow) -> str:
    return report_digest(
        portfolio_id=report.portfolio_id, order_id=report.order_id,
        market=report.market, symbol=report.symbol, side=report.side,
        fill_trade_date=report.fill_trade_date, captured_at=report.captured_at,
        quantity=report.quantity, fill_price=report.fill_price, fee=report.fee,
        source_type=report.source_type, source_ref=report.source_ref,
        executed_at=report.executed_at, evidence_sha256=report.evidence_sha256,
        evidence_uri=report.evidence_uri, reporter_claim=report.reporter_claim)


def resolution_digest(*, portfolio_id: uuid.UUID, report: AccountFillReportRow,
                      replacement: AccountFillReportRow | None, action: str,
                      reason: str, captured_at: datetime, source_type: str,
                      source_ref: str, evidence_sha256: str | None,
                      evidence_uri: str | None, reporter_claim: str | None) -> str:
    payload = {
        "portfolio_id": str(portfolio_id), "report_id": str(report.id),
        "report_sha256": report.payload_sha256,
        "action": action,
        "replacement_report_id": str(replacement.id) if replacement is not None else None,
        "replacement_sha256": replacement.payload_sha256 if replacement is not None else None,
        "reason": reason, "captured_at": captured_at.astimezone(timezone.utc).isoformat(),
        "source_type": source_type, "source_ref": source_ref,
        "evidence_sha256": evidence_sha256, "evidence_uri": evidence_uri,
        "reporter_claim": reporter_claim,
    }
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FillPostingDiagnostic:
    report_id: uuid.UUID
    posting_id: uuid.UUID | None
    issues: tuple[str, ...]


class AccountFillReportService:
    def __init__(self, session) -> None:
        self.session = session

    def diagnose_posting(self, portfolio_id: uuid.UUID, report_id: uuid.UUID) -> FillPostingDiagnostic:
        """Read current link health; this is not a source or settlement certificate."""
        if not isinstance(portfolio_id, uuid.UUID) or not isinstance(report_id, uuid.UUID):
            raise ValueError("portfolio_id and report_id must be UUIDs")
        portfolio = self.session.scalar(select(Portfolio).where(Portfolio.id == portfolio_id)
                                        .with_for_update().execution_options(populate_existing=True))
        if portfolio is None:
            raise ValueError("portfolio does not exist")
        report = self.session.get(AccountFillReportRow, report_id, populate_existing=True)
        if report is None or report.portfolio_id != portfolio_id:
            raise ValueError("fill report does not belong to portfolio")
        posting = self.session.scalar(select(AccountFillPostingRow).where(
            AccountFillPostingRow.portfolio_id == portfolio_id,
            AccountFillPostingRow.report_id == report_id))
        issues = ["SOURCE_UNVERIFIED"]
        if posting is None:
            issues.append("NOT_POSTED")
        if self.session.scalar(select(AccountFillReportResolutionRow.id).where(
                AccountFillReportResolutionRow.report_id == report_id)) is not None:
            issues.append("REPORT_RESOLVED_BY_DECLARATION")
        if posting is not None:
            if self.session.scalar(select(OrderFillEvent.id).where(
                    OrderFillEvent.portfolio_id == portfolio_id,
                    OrderFillEvent.reverses_fill_id == posting.fill_event_id)) is not None:
                issues.append("FILL_REVERSED")
            if self.session.scalar(select(AccountLedgerMovementRow.id).where(
                    AccountLedgerMovementRow.portfolio_id == portfolio_id,
                    AccountLedgerMovementRow.supersedes_id == posting.ledger_movement_id)) is not None:
                issues.append("LEDGER_SUPERSEDED")
        return FillPostingDiagnostic(report_id, posting.id if posting else None, tuple(issues))

    def record(
        self, portfolio_id: uuid.UUID, *, order_id: uuid.UUID | None,
        market: str, symbol: str, side: str,
        fill_trade_date: date, captured_at: datetime,
        quantity: object, fill_price: object, fee: object | None,
        source_type: str, source_ref: str,
        executed_at: datetime | None = None,
        evidence_sha256: str | None = None, evidence_uri: str | None = None,
        reporter_claim: str | None = None,
    ) -> tuple[AccountFillReportRow, bool]:
        """Stage a declaration; caller commits. No order or account projection changes."""
        if not isinstance(portfolio_id, uuid.UUID):
            raise ValueError("portfolio_id must be a UUID")
        if order_id is not None and not isinstance(order_id, uuid.UUID):
            raise ValueError("order_id must be a UUID")
        instrument = _key(market, symbol)
        if instrument is None or len(market) > 8 or len(symbol) > 32 or side not in {"BUY", "SELL"}:
            raise ValueError("invalid reported instrument or side")
        if type(fill_trade_date) is not date:
            raise ValueError("fill_trade_date must be a date")
        if (not isinstance(captured_at, datetime) or captured_at.utcoffset() is None
                or captured_at.astimezone(ZoneInfo("Asia/Shanghai")).date() < fill_trade_date):
            raise ValueError("captured_at must be aware and after the trade date in China")
        if executed_at is not None and (
            not isinstance(executed_at, datetime) or executed_at.utcoffset() is None
            or executed_at > captured_at
            or (market == "CN" and executed_at.astimezone(
                ZoneInfo("Asia/Shanghai")).date() != fill_trade_date)
        ):
            raise ValueError("executed_at must be an aware execution time on the CN trade date")
        qty = _money(quantity, signed=False)
        price = _money(fill_price, signed=False)
        actual_fee = None if fee is None else _money(fee, signed=False)
        if qty <= 0 or price <= 0:
            raise ValueError("reported quantity and price must be positive")
        if (source_type not in SOURCE_TYPES or not isinstance(source_ref, str)
                or not source_ref.strip() or len(source_ref) > 256):
            raise ValueError("invalid reported fill source identity")
        if evidence_sha256 is not None and (
            not isinstance(evidence_sha256, str) or len(evidence_sha256) != 64
            or any(char not in "0123456789abcdef" for char in evidence_sha256)
        ):
            raise ValueError("evidence_sha256 must be a lowercase sha256")
        if evidence_uri is not None and (
            not isinstance(evidence_uri, str) or not evidence_uri.strip() or len(evidence_uri) > 1024
        ):
            raise ValueError("evidence_uri is invalid")
        if reporter_claim is not None and (
            not isinstance(reporter_claim, str) or not reporter_claim.strip() or len(reporter_claim) > 256
        ):
            raise ValueError("reporter_claim is invalid")

        portfolio = self.session.scalar(select(Portfolio).where(Portfolio.id == portfolio_id)
                                        .with_for_update().execution_options(populate_existing=True))
        if portfolio is None:
            raise ValueError("portfolio does not exist")
        if order_id is not None:
            order = self.session.get(SuggestedOrder, order_id, populate_existing=True)
            if order is None or order.portfolio_id != portfolio_id:
                raise ValueError("reported order does not belong to portfolio")
        now = self.session.scalar(select(func.clock_timestamp()))
        if captured_at > now:
            raise ValueError("captured_at cannot be in the future")
        payload_sha256 = report_digest(
            portfolio_id=portfolio_id, order_id=order_id, market=market,
            symbol=symbol, side=side, fill_trade_date=fill_trade_date,
            captured_at=captured_at, quantity=qty, fill_price=price,
            fee=actual_fee, source_type=source_type, source_ref=source_ref,
            executed_at=executed_at, evidence_sha256=evidence_sha256,
            evidence_uri=evidence_uri, reporter_claim=reporter_claim)
        existing = self.session.scalar(select(AccountFillReportRow).where(
            AccountFillReportRow.portfolio_id == portfolio_id,
            AccountFillReportRow.source_type == source_type,
            AccountFillReportRow.source_ref == source_ref,
        ))
        if existing is not None:
            if existing.payload_sha256 != payload_sha256:
                raise ValueError("fill report source replay changed content")
            return existing, False
        row = AccountFillReportRow(
            id=uuid.uuid4(), portfolio_id=portfolio_id, order_id=order_id,
            market=market, symbol=symbol, side=side,
            fill_trade_date=fill_trade_date, executed_at=executed_at, captured_at=captured_at,
            quantity=qty, fill_price=price, fee=actual_fee,
            source_type=source_type, source_ref=source_ref,
            evidence_sha256=evidence_sha256, evidence_uri=evidence_uri,
            reporter_claim=reporter_claim, payload_sha256=payload_sha256,
        )
        self.session.add(row)
        self.session.flush()
        return row, True

    def assess(self, portfolio_id: uuid.UUID, report_id: uuid.UUID, *,
               assessment_ref: str) -> tuple[AccountFillReportAssessmentRow, bool]:
        """Freeze current local issues. All results remain unverified declarations."""
        if not isinstance(portfolio_id, uuid.UUID) or not isinstance(report_id, uuid.UUID):
            raise ValueError("portfolio_id and report_id must be UUIDs")
        if (not isinstance(assessment_ref, str) or not assessment_ref.strip()
                or len(assessment_ref) > 256):
            raise ValueError("assessment_ref is invalid")
        portfolio = self.session.scalar(select(Portfolio).where(Portfolio.id == portfolio_id)
                                        .with_for_update().execution_options(populate_existing=True))
        if portfolio is None:
            raise ValueError("portfolio does not exist")
        report = self.session.get(AccountFillReportRow, report_id, populate_existing=True)
        if report is None or report.portfolio_id != portfolio_id:
            raise ValueError("fill report does not belong to portfolio")
        existing = self.session.scalar(select(AccountFillReportAssessmentRow).where(
            AccountFillReportAssessmentRow.portfolio_id == portfolio_id,
            AccountFillReportAssessmentRow.assessment_ref == assessment_ref,
        ))
        if existing is not None:
            if existing.report_id != report_id:
                raise ValueError("fill assessment reference replay changed report")
            return existing, False
        order = None
        if report.order_id is not None:
            order = self.session.scalar(select(SuggestedOrder).where(
                SuggestedOrder.id == report.order_id,
                SuggestedOrder.portfolio_id == portfolio_id,
            ).with_for_update().execution_options(populate_existing=True))
            if order is None:
                raise ValueError("linked order is missing")
        issues = ["SOURCE_UNVERIFIED"]
        if report.fee is None:
            issues.append("FEE_UNKNOWN")
        if self.session.scalar(select(AccountFillReportResolutionRow.id).where(
                AccountFillReportResolutionRow.report_id == report_id)) is not None:
            issues.append("REPORT_RESOLVED_BY_DECLARATION")
        if order is None:
            issues.append("ORDER_UNLINKED")
        else:
            if (report.market, report.symbol, report.side) != (order.market, order.symbol, order.side):
                issues.append("ORDER_INSTRUMENT_OR_SIDE_MISMATCH")
            if report.quantity > order.quantity - order.filled_quantity:
                issues.append("ORDER_REMAINING_EXCEEDED")
            if (order.earliest_execution_trade_date is not None
                    and report.fill_trade_date < order.earliest_execution_trade_date):
                issues.append("TRADE_BEFORE_ORDER_ELIGIBILITY")
            if order.status not in {"PROPOSED", "EXECUTING", "PARTIALLY_FILLED",
                                    "RECONCILIATION_REQUIRED"}:
                issues.append("ORDER_TERMINAL_LOCAL_STATUS")
        payload_sha256 = hashlib.sha256(json.dumps({
            "portfolio_id": str(portfolio_id), "report_id": str(report_id),
            "report_sha256": report.payload_sha256,
            "order_revision": order.revision if order is not None else None,
            "issues": issues, "assessment_ref": assessment_ref,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        row = AccountFillReportAssessmentRow(
            id=uuid.uuid4(), portfolio_id=portfolio_id, report_id=report_id,
            order_revision=order.revision if order is not None else None,
            issues=issues, assessment_ref=assessment_ref, payload_sha256=payload_sha256,
        )
        self.session.add(row)
        self.session.flush()
        return row, True

    def resolve(
        self, portfolio_id: uuid.UUID, report_id: uuid.UUID, *,
        action: str, replacement_report_id: uuid.UUID | None,
        reason: str, captured_at: datetime,
        source_type: str, source_ref: str,
        evidence_sha256: str | None = None, evidence_uri: str | None = None,
        reporter_claim: str | None = None,
    ) -> tuple[AccountFillReportResolutionRow, bool]:
        """Record a declared CORRECT/VOID edge; never reverse an accepted fill."""
        if not isinstance(portfolio_id, uuid.UUID) or not isinstance(report_id, uuid.UUID):
            raise ValueError("portfolio_id and report_id must be UUIDs")
        if action not in {"CORRECT", "VOID"} or (
            (action == "CORRECT") != isinstance(replacement_report_id, uuid.UUID)
        ):
            raise ValueError("correction requires a replacement; void forbids one")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 512:
            raise ValueError("resolution reason is invalid")
        if not isinstance(captured_at, datetime) or captured_at.utcoffset() is None:
            raise ValueError("resolution captured_at must be timezone aware")
        if (source_type not in SOURCE_TYPES or not isinstance(source_ref, str)
                or not source_ref.strip() or len(source_ref) > 256):
            raise ValueError("invalid resolution source identity")
        if evidence_sha256 is not None and (
            not isinstance(evidence_sha256, str) or len(evidence_sha256) != 64
            or any(char not in "0123456789abcdef" for char in evidence_sha256)
        ):
            raise ValueError("evidence_sha256 must be a lowercase sha256")
        if evidence_uri is not None and (
            not isinstance(evidence_uri, str) or not evidence_uri.strip() or len(evidence_uri) > 1024
        ):
            raise ValueError("evidence_uri is invalid")
        if reporter_claim is not None and (
            not isinstance(reporter_claim, str) or not reporter_claim.strip()
            or len(reporter_claim) > 256
        ):
            raise ValueError("reporter_claim is invalid")
        portfolio = self.session.scalar(select(Portfolio).where(Portfolio.id == portfolio_id)
                                        .with_for_update().execution_options(populate_existing=True))
        if portfolio is None:
            raise ValueError("portfolio does not exist")
        report = self.session.get(AccountFillReportRow, report_id, populate_existing=True)
        if report is None or report.portfolio_id != portfolio_id:
            raise ValueError("fill report does not belong to portfolio")
        replacement = None
        if replacement_report_id is not None:
            replacement = self.session.get(AccountFillReportRow, replacement_report_id,
                                           populate_existing=True)
            if (replacement is None or replacement.portfolio_id != portfolio_id
                    or replacement.id == report_id):
                raise ValueError("replacement report does not belong to portfolio or equals original")
            if replacement.recorded_at < report.recorded_at:
                raise ValueError("replacement report must follow original")
            cursor = replacement.id
            seen: set[uuid.UUID] = set()
            while cursor is not None:
                if cursor == report_id or cursor in seen:
                    raise ValueError("report correction chain would cycle")
                seen.add(cursor)
                edge = self.session.scalar(select(AccountFillReportResolutionRow).where(
                    AccountFillReportResolutionRow.report_id == cursor,
                ))
                cursor = edge.replacement_report_id if edge is not None else None
        now = self.session.scalar(select(func.clock_timestamp()))
        if captured_at > now or captured_at < report.captured_at or (
            replacement is not None and captured_at < replacement.captured_at
        ):
            raise ValueError("resolution capture time is outside the report window")
        payload_sha256 = resolution_digest(
            portfolio_id=portfolio_id, report=report, replacement=replacement,
            action=action, reason=reason, captured_at=captured_at,
            source_type=source_type, source_ref=source_ref,
            evidence_sha256=evidence_sha256, evidence_uri=evidence_uri,
            reporter_claim=reporter_claim)
        existing = self.session.scalar(select(AccountFillReportResolutionRow).where(
            AccountFillReportResolutionRow.portfolio_id == portfolio_id,
            AccountFillReportResolutionRow.source_type == source_type,
            AccountFillReportResolutionRow.source_ref == source_ref,
        ))
        if existing is not None:
            if existing.payload_sha256 != payload_sha256:
                raise ValueError("resolution source replay changed content")
            return existing, False
        if self.session.scalar(select(AccountFillReportResolutionRow.id).where(
                AccountFillReportResolutionRow.report_id == report_id)) is not None:
            raise ValueError("report already has a resolution")
        if replacement_report_id is not None and self.session.scalar(select(
                AccountFillReportResolutionRow.id).where(
                    AccountFillReportResolutionRow.replacement_report_id == replacement_report_id,
                )) is not None:
            raise ValueError("replacement report already used")
        row = AccountFillReportResolutionRow(
            id=uuid.uuid4(), portfolio_id=portfolio_id, report_id=report_id,
            replacement_report_id=replacement_report_id, action=action,
            reason=reason, captured_at=captured_at, source_type=source_type,
            source_ref=source_ref, evidence_sha256=evidence_sha256,
            evidence_uri=evidence_uri, reporter_claim=reporter_claim,
            payload_sha256=payload_sha256,
        )
        self.session.add(row)
        self.session.flush()
        return row, True
