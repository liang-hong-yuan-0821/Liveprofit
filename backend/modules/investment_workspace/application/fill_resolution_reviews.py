"""Signed manual review of a fill correction or void declaration."""

from __future__ import annotations

import hmac
import json
import uuid

from sqlalchemy import select

from backend.modules.investment_workspace.infrastructure.fill_report_models import (
    AccountFillReportResolutionRow, AccountFillResolutionReviewRow, AccountFillReportRow,
)
from backend.modules.investment_workspace.infrastructure.models import Portfolio

from .fill_evidence import AccountFillEvidenceService
from .fill_reports import resolution_digest
from .fill_reviews import _review_keys


def resolution_review_payload(*, portfolio_id: uuid.UUID, resolution_id: uuid.UUID,
                              resolution_sha256: str, evidence_sha256: str,
                              review_ref: str, reviewed_by: str, reason: str) -> bytes:
    """Use a distinct signing domain from original fill reviews."""
    return json.dumps({
        "purpose": "LIVEPROFIT_ACCOUNT_FILL_RESOLUTION_REVIEW_V1",
        "portfolio_id": str(portfolio_id), "resolution_id": str(resolution_id),
        "resolution_sha256": resolution_sha256,
        "evidence_sha256": evidence_sha256,
        "review_ref": review_ref, "reviewed_by": reviewed_by, "reason": reason,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _verify_signature(*, portfolio_id: uuid.UUID, resolution_id: uuid.UUID,
                      resolution_sha256: str, evidence_sha256: str,
                      review_ref: str, reviewed_by: str, reason: str,
                      review_signature: str) -> None:
    secret = _review_keys().get(reviewed_by)
    if secret is None:
        raise ValueError("account fill resolution reviewer is not configured")
    payload = resolution_review_payload(
        portfolio_id=portfolio_id, resolution_id=resolution_id,
        resolution_sha256=resolution_sha256, evidence_sha256=evidence_sha256,
        review_ref=review_ref, reviewed_by=reviewed_by, reason=reason)
    if not hmac.compare_digest(hmac.new(secret, payload, "sha256").hexdigest(),
                               review_signature):
        raise ValueError("invalid account fill resolution review signature")


class AccountFillResolutionReviewService:
    def __init__(self, session) -> None:
        self.session = session

    def _resolution(self, portfolio_id: uuid.UUID,
                    resolution_id: uuid.UUID) -> AccountFillReportResolutionRow:
        if not isinstance(portfolio_id, uuid.UUID) or not isinstance(resolution_id, uuid.UUID):
            raise ValueError("portfolio_id and resolution_id must be UUIDs")
        portfolio = self.session.scalar(select(Portfolio).where(Portfolio.id == portfolio_id)
                                        .with_for_update().execution_options(populate_existing=True))
        if portfolio is None:
            raise ValueError("portfolio does not exist")
        resolution = self.session.get(AccountFillReportResolutionRow, resolution_id,
                                      populate_existing=True)
        if resolution is None or resolution.portfolio_id != portfolio_id:
            raise ValueError("resolution does not belong to portfolio")
        if resolution.evidence_sha256 is None or resolution.evidence_uri is None:
            raise ValueError("resolution has no referenced source evidence")
        report = self.session.get(AccountFillReportRow, resolution.report_id,
                                  populate_existing=True)
        replacement = (self.session.get(AccountFillReportRow, resolution.replacement_report_id,
                                        populate_existing=True)
                       if resolution.replacement_report_id is not None else None)
        if (report is None or report.portfolio_id != portfolio_id
                or (resolution.replacement_report_id is not None and (
                    replacement is None or replacement.portfolio_id != portfolio_id))
                or resolution.payload_sha256 != resolution_digest(
                    portfolio_id=portfolio_id, report=report, replacement=replacement,
                    action=resolution.action, reason=resolution.reason,
                    captured_at=resolution.captured_at, source_type=resolution.source_type,
                    source_ref=resolution.source_ref,
                    evidence_sha256=resolution.evidence_sha256,
                    evidence_uri=resolution.evidence_uri,
                    reporter_claim=resolution.reporter_claim)):
            raise ValueError("resolution content digest does not match its fields")
        AccountFillEvidenceService(self.session).require_content(
            portfolio_id, resolution.evidence_sha256)
        return resolution

    def record(self, portfolio_id: uuid.UUID, resolution_id: uuid.UUID, *,
               review_ref: str, reviewed_by: str, reason: str,
               review_signature: str) -> tuple[AccountFillResolutionReviewRow, bool]:
        """Append a key-holder claim without certifying the broker or account."""
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
        resolution = self._resolution(portfolio_id, resolution_id)
        _verify_signature(
            portfolio_id=portfolio_id, resolution_id=resolution_id,
            resolution_sha256=resolution.payload_sha256,
            evidence_sha256=resolution.evidence_sha256,
            review_ref=review_ref, reviewed_by=reviewed_by, reason=reason,
            review_signature=review_signature)
        existing = self.session.scalar(select(AccountFillResolutionReviewRow).where(
            AccountFillResolutionReviewRow.portfolio_id == portfolio_id,
            AccountFillResolutionReviewRow.review_ref == review_ref))
        if existing is not None:
            if (existing.resolution_id != resolution_id
                    or existing.resolution_sha256 != resolution.payload_sha256
                    or existing.evidence_sha256 != resolution.evidence_sha256
                    or existing.reviewed_by != reviewed_by or existing.reason != reason
                    or existing.review_signature != review_signature):
                raise ValueError("resolution review reference replay changed content")
            return existing, False
        if self.session.scalar(select(AccountFillResolutionReviewRow.id).where(
                AccountFillResolutionReviewRow.resolution_id == resolution_id)) is not None:
            raise ValueError("resolution already has a review")
        row = AccountFillResolutionReviewRow(
            id=uuid.uuid4(), portfolio_id=portfolio_id, resolution_id=resolution_id,
            resolution_sha256=resolution.payload_sha256,
            evidence_sha256=resolution.evidence_sha256,
            review_ref=review_ref, reviewed_by=reviewed_by, reason=reason,
            review_signature=review_signature)
        self.session.add(row)
        self.session.flush()
        return row, True

    def verify_signed_claim(self, portfolio_id: uuid.UUID,
                            resolution_id: uuid.UUID) -> AccountFillResolutionReviewRow:
        """Recheck stored bytes and current key, without certifying external origin."""
        resolution = self._resolution(portfolio_id, resolution_id)
        review = self.session.scalar(select(AccountFillResolutionReviewRow).where(
            AccountFillResolutionReviewRow.portfolio_id == portfolio_id,
            AccountFillResolutionReviewRow.resolution_id == resolution_id))
        if review is None:
            raise ValueError("resolution has no signed review")
        if (review.resolution_sha256 != resolution.payload_sha256
                or review.evidence_sha256 != resolution.evidence_sha256):
            raise ValueError("resolution review no longer matches evidence")
        _verify_signature(
            portfolio_id=portfolio_id, resolution_id=resolution_id,
            resolution_sha256=review.resolution_sha256,
            evidence_sha256=review.evidence_sha256,
            review_ref=review.review_ref, reviewed_by=review.reviewed_by,
            reason=review.reason, review_signature=review.review_signature)
        return review
