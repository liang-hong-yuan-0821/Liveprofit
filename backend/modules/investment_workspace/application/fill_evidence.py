"""Retain bounded raw fill evidence; provenance remains a separate decision."""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select

from backend.modules.investment_workspace.infrastructure.fill_report_models import (
    AccountFillEvidenceArtifactRow,
)
from backend.modules.investment_workspace.infrastructure.models import Portfolio


MAX_EVIDENCE_BYTES = 8 * 1024 * 1024


class AccountFillEvidenceService:
    def __init__(self, session) -> None:
        self.session = session

    def capture(self, portfolio_id: uuid.UUID, *, source_ref: str,
                media_type: str, raw_bytes: bytes) -> tuple[AccountFillEvidenceArtifactRow, bool]:
        """Stage imported bytes; caller owns the transaction."""
        if not isinstance(portfolio_id, uuid.UUID):
            raise ValueError("portfolio_id must be a UUID")
        if (not isinstance(source_ref, str) or not source_ref.strip()
                or len(source_ref) > 256):
            raise ValueError("evidence source_ref is invalid")
        if (not isinstance(media_type, str) or not media_type.strip()
                or len(media_type) > 128):
            raise ValueError("evidence media_type is invalid")
        if not isinstance(raw_bytes, bytes) or not 1 <= len(raw_bytes) <= MAX_EVIDENCE_BYTES:
            raise ValueError("evidence bytes must be bounded nonempty bytes")
        content_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        portfolio = self.session.scalar(select(Portfolio).where(Portfolio.id == portfolio_id)
                                        .with_for_update().execution_options(populate_existing=True))
        if portfolio is None:
            raise ValueError("portfolio does not exist")
        existing = self.session.scalar(select(AccountFillEvidenceArtifactRow).where(
            AccountFillEvidenceArtifactRow.portfolio_id == portfolio_id,
            AccountFillEvidenceArtifactRow.source_ref == source_ref))
        if existing is not None:
            if (existing.content_sha256 != content_sha256 or existing.media_type != media_type
                    or existing.size_bytes != len(raw_bytes)
                    or bytes(existing.raw_bytes) != raw_bytes):
                raise ValueError("evidence source replay changed content")
            return existing, False
        if self.session.scalar(select(AccountFillEvidenceArtifactRow.id).where(
                AccountFillEvidenceArtifactRow.portfolio_id == portfolio_id,
                AccountFillEvidenceArtifactRow.content_sha256 == content_sha256)) is not None:
            raise ValueError("evidence content already has another source reference")
        row = AccountFillEvidenceArtifactRow(
            id=uuid.uuid4(), portfolio_id=portfolio_id,
            source_ref=source_ref, media_type=media_type,
            content_sha256=content_sha256, size_bytes=len(raw_bytes),
            raw_bytes=raw_bytes)
        self.session.add(row)
        self.session.flush()
        return row, True

    def require_content(self, portfolio_id: uuid.UUID,
                        content_sha256: str) -> AccountFillEvidenceArtifactRow:
        """Verify stored bytes match the claimed digest, without certifying origin."""
        if (not isinstance(portfolio_id, uuid.UUID) or not isinstance(content_sha256, str)
                or len(content_sha256) != 64
                or any(char not in "0123456789abcdef" for char in content_sha256)):
            raise ValueError("evidence identity is invalid")
        row = self.session.scalar(select(AccountFillEvidenceArtifactRow).where(
            AccountFillEvidenceArtifactRow.portfolio_id == portfolio_id,
            AccountFillEvidenceArtifactRow.content_sha256 == content_sha256))
        if row is None:
            raise ValueError("report evidence bytes are missing")
        raw = bytes(row.raw_bytes)
        if (row.size_bytes != len(raw) or not 1 <= len(raw) <= MAX_EVIDENCE_BYTES
                or hashlib.sha256(raw).hexdigest() != content_sha256):
            raise ValueError("stored report evidence bytes changed")
        return row
