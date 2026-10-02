"""Append signed manual review claims without granting account or trade authority."""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import os
import uuid

from sqlalchemy import select

from backend.modules.investment_workspace.infrastructure.fill_report_models import (
    AccountFillReportResolutionRow, AccountFillReportReviewRow, AccountFillReportRow,
)
from backend.modules.investment_workspace.infrastructure.models import Portfolio
from .fill_evidence import AccountFillEvidenceService
from .fill_reports import report_row_digest


def review_payload(*, portfolio_id: uuid.UUID, report_id: uuid.UUID,
                   report_sha256: str, evidence_sha256: str,
                   review_ref: str, reviewed_by: str, reason: str) -> bytes:
    """Canonical domain-separated bytes for an out-of-band human signer."""
    return json.dumps({
        "purpose": "LIVEPROFIT_ACCOUNT_FILL_REVIEW_V1",
        "portfolio_id": str(portfolio_id), "report_id": str(report_id),
        "report_sha256": report_sha256, "evidence_sha256": evidence_sha256,
        "review_ref": review_ref, "reviewed_by": reviewed_by,
        "reason": reason,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _review_keys() -> dict[str, bytes]:
    try:
        configured = json.loads(os.environ.get("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", ""))
    except (TypeError, ValueError):
        configured = None
    if not isinstance(configured, dict) or not configured:
        raise ValueError("account fill reviewer keys are not configured")
    keys: dict[str, bytes] = {}
    for name, encoded in configured.items():
        if (not isinstance(name, str) or not name.strip() or name != name.strip()
                or not isinstance(encoded, str)):
            raise ValueError("account fill reviewer keys are invalid")
        try:
            secret = base64.b64decode(encoded, validate=True)
        except (TypeError, ValueError, binascii.Error):
            raise ValueError("account fill reviewer keys are invalid") from None
        if len(secret) < 32 or base64.b64encode(secret).decode("ascii") != encoded:
            raise ValueError("account fill reviewer keys are invalid")
        keys[name] = secret
    if len(set(keys.values())) != len(keys):
        raise ValueError("account fill reviewer keys must be unique")
    return keys


def _verify_signature(*, portfolio_id: uuid.UUID, report_id: uuid.UUID,
                      report_sha256: str, evidence_sha256: str,
                      review_ref: str, reviewed_by: str, reason: str,
                      review_signature: str) -> None:
    secret = _review_keys().get(reviewed_by)
    if secret is None:
        raise ValueError("account fill reviewer is not configured")
    payload = review_payload(
        portfolio_id=portfolio_id, report_id=report_id,
        report_sha256=report_sha256, evidence_sha256=evidence_sha256,
        review_ref=review_ref, reviewed_by=reviewed_by, reason=reason)
    if not hmac.compare_digest(hmac.new(secret, payload, "sha256").hexdigest(),
                               review_signature):
        raise ValueError("invalid account fill review signature")


class AccountFillReviewService:
    def __init__(self, session) -> None:
        self.session = session

    def record(self, portfolio_id: uuid.UUID, report_id: uuid.UUID, *,
               review_ref: str, reviewed_by: str, reason: str,
               review_signature: str) -> tuple[AccountFillReportReviewRow, bool]:
        """Record a key-holder's review declaration; caller owns commit/rollback."""
        if not isinstance(portfolio_id, uuid.UUID) or not isinstance(report_id, uuid.UUID):
            raise ValueError("portfolio_id and report_id must be UUIDs")
        if (not isinstance(review_ref, str) or not review_ref.strip()
                or review_ref != review_ref.strip() or len(review_ref) > 256):
            raise ValueError("review_ref is invalid")
        if (not isinstance(reviewed_by, str) or not reviewed_by.strip()
                or reviewed_by != reviewed_by.strip() or len(reviewed_by) > 256):
            raise ValueError("reviewed_by is invalid")
        if (not isinstance(reason, str) or not reason.strip()
                or reason != reason.strip() or len(reason) > 512):
            raise ValueError("review reason is invalid")
        if (not isinstance(review_signature, str) or len(review_signature) != 64
                or any(char not in "0123456789abcdef" for char in review_signature)):
            raise ValueError("review signature must be lowercase sha256")
        portfolio = self.session.scalar(select(Portfolio).where(Portfolio.id == portfolio_id)
                                        .with_for_update().execution_options(populate_existing=True))
        if portfolio is None:
            raise ValueError("portfolio does not exist")
        report = self.session.get(AccountFillReportRow, report_id, populate_existing=True)
        if report is None or report.portfolio_id != portfolio_id:
            raise ValueError("report does not belong to portfolio")
        if report.evidence_sha256 is None or report.evidence_uri is None:
            raise ValueError("report has no referenced source evidence")
        if report.payload_sha256 != report_row_digest(report):
            raise ValueError("fill report digest does not match business fields")
        AccountFillEvidenceService(self.session).require_content(
            portfolio_id, report.evidence_sha256)
        _verify_signature(
            portfolio_id=portfolio_id, report_id=report_id,
            report_sha256=report.payload_sha256,
            evidence_sha256=report.evidence_sha256,
            review_ref=review_ref, reviewed_by=reviewed_by,
            reason=reason, review_signature=review_signature)
        existing = self.session.scalar(select(AccountFillReportReviewRow).where(
            AccountFillReportReviewRow.portfolio_id == portfolio_id,
            AccountFillReportReviewRow.review_ref == review_ref))
        if existing is not None:
            if (existing.report_id != report_id or existing.report_sha256 != report.payload_sha256
                    or existing.evidence_sha256 != report.evidence_sha256
                    or existing.reviewed_by != reviewed_by or existing.reason != reason
                    or existing.review_signature != review_signature):
                raise ValueError("fill review reference replay changed content")
            return existing, False
        if self.session.scalar(select(AccountFillReportResolutionRow.id).where(
                AccountFillReportResolutionRow.report_id == report_id)) is not None:
            raise ValueError("resolved report cannot receive a new review")
        if self.session.scalar(select(AccountFillReportReviewRow.id).where(
                AccountFillReportReviewRow.report_id == report_id)) is not None:
            raise ValueError("report already has a review")
        row = AccountFillReportReviewRow(
            id=uuid.uuid4(), portfolio_id=portfolio_id, report_id=report_id,
            report_sha256=report.payload_sha256, evidence_sha256=report.evidence_sha256,
            review_ref=review_ref, reviewed_by=reviewed_by, reason=reason,
            review_signature=review_signature)
        self.session.add(row)
        self.session.flush()
        return row, True

    def verify_signed_claim(self, portfolio_id: uuid.UUID,
                            report_id: uuid.UUID) -> AccountFillReportReviewRow:
        """Check current signature and report binding, never broker authenticity."""
        return self._verify_signed_claim(portfolio_id, report_id, allow_resolved=False)

    def verify_historical_claim(self, portfolio_id: uuid.UUID,
                                report_id: uuid.UUID) -> AccountFillReportReviewRow:
        """Check an already posted report after its later resolution."""
        return self._verify_signed_claim(portfolio_id, report_id, allow_resolved=True)

    def _verify_signed_claim(self, portfolio_id: uuid.UUID, report_id: uuid.UUID, *,
                             allow_resolved: bool) -> AccountFillReportReviewRow:
        if not isinstance(portfolio_id, uuid.UUID) or not isinstance(report_id, uuid.UUID):
            raise ValueError("portfolio_id and report_id must be UUIDs")
        portfolio = self.session.scalar(select(Portfolio).where(Portfolio.id == portfolio_id)
                                        .with_for_update().execution_options(populate_existing=True))
        if portfolio is None:
            raise ValueError("portfolio does not exist")
        report = self.session.get(AccountFillReportRow, report_id, populate_existing=True)
        if report is None or report.portfolio_id != portfolio_id:
            raise ValueError("report does not belong to portfolio")
        review = self.session.scalar(select(AccountFillReportReviewRow).where(
            AccountFillReportReviewRow.portfolio_id == portfolio_id,
            AccountFillReportReviewRow.report_id == report_id))
        if review is None:
            raise ValueError("report has no signed review")
        if report.payload_sha256 != report_row_digest(report):
            raise ValueError("fill report digest does not match business fields")
        if (report.evidence_sha256 is None or report.evidence_uri is None
                or review.report_sha256 != report.payload_sha256
                or review.evidence_sha256 != report.evidence_sha256):
            raise ValueError("fill review no longer matches report evidence")
        AccountFillEvidenceService(self.session).require_content(
            portfolio_id, report.evidence_sha256)
        if not allow_resolved and self.session.scalar(select(AccountFillReportResolutionRow.id).where(
                AccountFillReportResolutionRow.report_id == report_id)) is not None:
            raise ValueError("reviewed report has been resolved")
        _verify_signature(
            portfolio_id=portfolio_id, report_id=report_id,
            report_sha256=review.report_sha256,
            evidence_sha256=review.evidence_sha256,
            review_ref=review.review_ref, reviewed_by=review.reviewed_by,
            reason=review.reason, review_signature=review.review_signature)
        return review
