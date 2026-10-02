# test-catalog-begin
# {
#   "purpose": "投资工作区 / fill_resolution_reviews（成交、审核）：A signed resolution claim remains separate from broker account certification.",
#   "keywords": [
#     "投资工作区",
#     "成交",
#     "fill_resolution_reviews",
#     "fill"
#   ],
#   "covers": [
#     "backend/modules/investment_workspace/application/fill_evidence.py",
#     "backend/modules/investment_workspace/application/fill_reports.py",
#     "backend/modules/investment_workspace/application/fill_resolution_reviews.py",
#     "backend/modules/investment_workspace/application/fill_reviews.py",
#     "backend/modules/investment_workspace/infrastructure/fill_report_models.py",
#     "backend/modules/investment_workspace/infrastructure/models.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""A signed resolution claim remains separate from broker account certification."""

import base64
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.modules.investment_workspace.application.fill_evidence import AccountFillEvidenceService
from backend.modules.investment_workspace.application.fill_reports import AccountFillReportService
from backend.modules.investment_workspace.application.fill_resolution_reviews import (
    AccountFillResolutionReviewService, resolution_review_payload,
)
from backend.modules.investment_workspace.application.fill_reviews import review_payload
from backend.modules.investment_workspace.infrastructure.fill_report_models import AccountFillResolutionReviewRow
from backend.modules.investment_workspace.infrastructure.models import Portfolio


CAPTURED = datetime(2026, 9, 29, 8, tzinfo=timezone.utc)
BYTES = b"account,trade,000001.SZ,SELL,5,10,REVOKED"
SHA = hashlib.sha256(BYTES).hexdigest()
SECRET = b"resolution-review-key-32-bytes-long"


def _seed(factory):
    portfolio_id = uuid.uuid4()
    with factory() as session:
        session.add(Portfolio(id=portfolio_id, name=f"resolution-{portfolio_id}",
                              version=1, total_assets=Decimal(1000),
                              available_cash=Decimal(1000)))
        session.commit()
    with factory() as session:
        reports = AccountFillReportService(session)
        report, _ = reports.record(
            portfolio_id, order_id=None, market="CN", symbol="000001.SZ",
            side="SELL", fill_trade_date=date(2026, 9, 28), captured_at=CAPTURED,
            quantity="5", fill_price="10", fee="1",
            source_type="MANUAL_REPORT", source_ref="original-trade")
        AccountFillEvidenceService(session).capture(
            portfolio_id, source_ref="revocation-file", media_type="text/csv",
            raw_bytes=BYTES)
        resolution, _ = reports.resolve(
            portfolio_id, report.id, action="VOID", replacement_report_id=None,
            reason="broker statement revoked row", captured_at=CAPTURED,
            source_type="MANUAL_REPORT", source_ref="original-void",
            evidence_sha256=SHA, evidence_uri="review-archive://revocation-file")
        session.commit()
        return portfolio_id, resolution.id, resolution.payload_sha256


def test_resolution_review_binds_bytes_signature_and_current_key(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, resolution_id, resolution_sha = _seed(factory)
    monkeypatch.setenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", json.dumps({
        "auditor": base64.b64encode(SECRET).decode(),
    }))
    signature = hmac.new(SECRET, resolution_review_payload(
        portfolio_id=portfolio_id, resolution_id=resolution_id,
        resolution_sha256=resolution_sha, evidence_sha256=SHA,
        review_ref="resolution-review-1", reviewed_by="auditor",
        reason="revocation source inspected"), "sha256").hexdigest()
    assert resolution_review_payload(
        portfolio_id=portfolio_id, resolution_id=resolution_id,
        resolution_sha256=resolution_sha, evidence_sha256=SHA,
        review_ref="resolution-review-1", reviewed_by="auditor",
        reason="revocation source inspected") != review_payload(
            portfolio_id=portfolio_id, report_id=resolution_id,
            report_sha256=resolution_sha, evidence_sha256=SHA,
            review_ref="resolution-review-1", reviewed_by="auditor",
            reason="revocation source inspected")
    with factory() as session:
        service = AccountFillResolutionReviewService(session)
        with pytest.raises(ValueError, match="invalid account fill resolution review signature"):
            service.record(portfolio_id, resolution_id,
                           review_ref="resolution-review-1", reviewed_by="auditor",
                           reason="revocation source inspected", review_signature="0" * 64)
        review, created = service.record(
            portfolio_id, resolution_id, review_ref="resolution-review-1",
            reviewed_by="auditor", reason="revocation source inspected",
            review_signature=signature)
        assert created
        replay, created = service.record(
            portfolio_id, resolution_id, review_ref="resolution-review-1",
            reviewed_by="auditor", reason="revocation source inspected",
            review_signature=signature)
        assert not created and replay.id == review.id
        assert service.verify_signed_claim(portfolio_id, resolution_id).id == review.id
        session.commit()
        review_id = review.id
    monkeypatch.delenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS")
    with factory() as session:
        with pytest.raises(ValueError, match="not configured"):
            AccountFillResolutionReviewService(session).verify_signed_claim(
                portfolio_id, resolution_id)
        with pytest.raises(DBAPIError, match="immutable"):
            session.execute(text("UPDATE account_fill_resolution_reviews SET reason='x' WHERE id=:id"),
                            {"id": review_id})
        session.rollback()


def test_resolution_review_requires_stored_bytes_and_database_binding(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, resolution_id, resolution_sha = _seed(factory)
    monkeypatch.setenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", json.dumps({
        "auditor": base64.b64encode(SECRET).decode(),
    }))
    signature = hmac.new(SECRET, resolution_review_payload(
        portfolio_id=portfolio_id, resolution_id=resolution_id,
        resolution_sha256=resolution_sha, evidence_sha256=SHA,
        review_ref="resolution-review-2", reviewed_by="auditor",
        reason="revocation source inspected"), "sha256").hexdigest()
    with factory() as session:
        with pytest.raises(DBAPIError, match="evidence mismatch"):
            session.execute(text("""INSERT INTO account_fill_resolution_reviews
                (id, portfolio_id, resolution_id, resolution_sha256, evidence_sha256,
                 review_ref, reviewed_by, reason, review_signature)
                VALUES (:id, :portfolio, :resolution, :wrong_sha, :evidence,
                        'forged', 'auditor', 'reason', :signature)"""), {
                "id": uuid.uuid4(), "portfolio": portfolio_id, "resolution": resolution_id,
                "wrong_sha": "a" * 64, "evidence": SHA, "signature": "0" * 64,
            })
        session.rollback()
        review, _ = AccountFillResolutionReviewService(session).record(
            portfolio_id, resolution_id, review_ref="resolution-review-2",
            reviewed_by="auditor", reason="revocation source inspected",
            review_signature=signature)
        assert review.evidence_sha256 == SHA


def test_resolution_review_rejects_sql_declared_hash_that_does_not_cover_fields(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, _, _ = _seed(factory)
    monkeypatch.setenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", json.dumps({
        "auditor": base64.b64encode(SECRET).decode(),
    }))
    with factory() as session:
        report, _ = AccountFillReportService(session).record(
            portfolio_id, order_id=None, market="CN", symbol="000001.SZ",
            side="SELL", fill_trade_date=date(2026, 9, 28), captured_at=CAPTURED,
            quantity="1", fill_price="10", fee="0",
            source_type="MANUAL_REPORT", source_ref="sql-mismatch-report")
        session.flush()
        resolution_id = uuid.uuid4()
        session.execute(text("""INSERT INTO account_fill_report_resolutions
            (id, portfolio_id, report_id, replacement_report_id, action, reason,
             captured_at, source_type, source_ref, evidence_sha256, evidence_uri,
             payload_sha256)
            VALUES (:id, :portfolio, :report, NULL, 'VOID', 'actual fields differ',
                    :captured, 'MANUAL_REPORT', 'sql-mismatch-void', :evidence,
                    'review-archive://revocation-file', :fake_sha)"""), {
            "id": resolution_id, "portfolio": portfolio_id, "report": report.id,
            "captured": CAPTURED, "evidence": SHA, "fake_sha": "0" * 64,
        })
        session.commit()
    with factory() as session:
        with pytest.raises(ValueError, match="content digest"):
            AccountFillResolutionReviewService(session).verify_signed_claim(
                portfolio_id, resolution_id)
