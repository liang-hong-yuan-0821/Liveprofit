# test-catalog-begin
# {
#   "purpose": "投资工作区 / fill_reviews（成交、审核）：Signed manual review records remain separate from source and account certification.",
#   "keywords": [
#     "投资工作区",
#     "成交",
#     "历史审计",
#     "fill_reviews",
#     "fill",
#     "history"
#   ],
#   "covers": [
#     "backend/modules/investment_workspace/application/fill_evidence.py",
#     "backend/modules/investment_workspace/application/fill_reports.py",
#     "backend/modules/investment_workspace/application/fill_reviews.py",
#     "backend/modules/investment_workspace/infrastructure/fill_report_models.py",
#     "backend/modules/investment_workspace/infrastructure/models.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Signed manual review records remain separate from source and account certification."""

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

from backend.modules.investment_workspace.application.fill_reports import AccountFillReportService
from backend.modules.investment_workspace.application.fill_evidence import AccountFillEvidenceService
from backend.modules.investment_workspace.application.fill_reviews import (
    AccountFillReviewService, review_payload,
)
from backend.modules.investment_workspace.infrastructure.fill_report_models import AccountFillReportReviewRow
from backend.modules.investment_workspace.infrastructure.models import Portfolio


CAPTURED = datetime(2026, 9, 29, 8, tzinfo=timezone.utc)
EVIDENCE_BYTES = b"broker-export-row,2026-09-28,000001.SZ,SELL,5,10"
EVIDENCE_SHA = hashlib.sha256(EVIDENCE_BYTES).hexdigest()


def test_manual_review_signature_binds_report_and_preserves_history(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id = uuid.uuid4()
    with factory() as session:
        session.add(Portfolio(id=portfolio_id, name=f"review-{portfolio_id}",
                              version=1, total_assets=Decimal(1000),
                              available_cash=Decimal(1000)))
        session.commit()
    with factory() as session:
        AccountFillEvidenceService(session).capture(
            portfolio_id, source_ref="source-file-1", media_type="text/csv",
            raw_bytes=EVIDENCE_BYTES)
        report, _ = AccountFillReportService(session).record(
            portfolio_id, order_id=None, market="CN", symbol="000001.SZ",
            side="SELL", fill_trade_date=date(2026, 9, 28), captured_at=CAPTURED,
            quantity="5", fill_price="10", fee=None,
            source_type="MANUAL_REPORT", source_ref="review-report",
            evidence_sha256=EVIDENCE_SHA, evidence_uri="review-archive://statement-1")
        session.commit()
        report_id, report_sha = report.id, report.payload_sha256
    secret = b"account-review-secret-32-bytes-0001"
    payload = review_payload(
        portfolio_id=portfolio_id, report_id=report_id,
        report_sha256=report_sha, evidence_sha256=EVIDENCE_SHA,
        review_ref="review-1", reviewed_by="auditor-1", reason="statement inspected")
    signature = hmac.new(secret, payload, "sha256").hexdigest()
    monkeypatch.delenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", raising=False)
    with factory() as session:
        service = AccountFillReviewService(session)
        with pytest.raises(ValueError, match="not configured"):
            service.record(portfolio_id, report_id, review_ref="review-1",
                           reviewed_by="auditor-1", reason="statement inspected",
                           review_signature=signature)
        monkeypatch.setenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", json.dumps({
            "auditor-1": base64.b64encode(secret).decode(),
            "auditor-2": base64.b64encode(secret).decode(),
        }))
        with pytest.raises(ValueError, match="unique"):
            service.record(portfolio_id, report_id, review_ref="review-1",
                           reviewed_by="auditor-1", reason="statement inspected",
                           review_signature=signature)
        monkeypatch.setenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", json.dumps({
            "auditor-1": base64.b64encode(secret).decode(),
        }))
        with pytest.raises(ValueError, match="invalid account fill review signature"):
            service.record(portfolio_id, report_id, review_ref="review-1",
                           reviewed_by="auditor-1", reason="statement inspected",
                           review_signature="0" * 64)
        review, created = service.record(
            portfolio_id, report_id, review_ref="review-1",
            reviewed_by="auditor-1", reason="statement inspected",
            review_signature=signature)
        assert created and review.report_sha256 == report_sha
        assert service.verify_signed_claim(portfolio_id, report_id).id == review.id
        with pytest.raises(ValueError, match="invalid account fill review signature"):
            service.record(portfolio_id, report_id, review_ref="review-1",
                           reviewed_by="auditor-1", reason="different reason",
                           review_signature=signature)
        session.commit()
        review_id = review.id
    monkeypatch.delenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS")
    with factory() as session:
        with pytest.raises(ValueError, match="not configured"):
            AccountFillReviewService(session).record(
                portfolio_id, report_id, review_ref="review-1",
                reviewed_by="auditor-1", reason="statement inspected",
                review_signature=signature)
    monkeypatch.setenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", json.dumps({
        "auditor-1": base64.b64encode(secret).decode(),
    }))
    later_signature = hmac.new(secret, review_payload(
        portfolio_id=portfolio_id, report_id=report_id,
        report_sha256=report_sha, evidence_sha256=EVIDENCE_SHA,
        review_ref="review-2", reviewed_by="auditor-1", reason="new review"),
        "sha256").hexdigest()
    with factory() as session:
        AccountFillReportService(session).resolve(
            portfolio_id, report_id, action="VOID", replacement_report_id=None,
            reason="report retracted", captured_at=CAPTURED,
            source_type="MANUAL_REPORT", source_ref="review-report-void")
        replay, created = AccountFillReviewService(session).record(
            portfolio_id, report_id, review_ref="review-1",
            reviewed_by="auditor-1", reason="statement inspected",
            review_signature=signature)
        assert not created and replay.id == review_id
        with pytest.raises(ValueError, match="resolved report"):
            AccountFillReviewService(session).record(
                portfolio_id, report_id, review_ref="review-2",
                reviewed_by="auditor-1", reason="new review",
                review_signature=later_signature)
        with pytest.raises(ValueError, match="has been resolved"):
            AccountFillReviewService(session).verify_signed_claim(portfolio_id, report_id)
        session.commit()
    with factory() as session:
        assert session.get(AccountFillReportReviewRow, review_id) is not None
        with pytest.raises(DBAPIError, match="fill review history is immutable"):
            session.execute(text("UPDATE account_fill_report_reviews SET reason='x' WHERE id=:id"),
                            {"id": review_id})
        session.rollback()


def test_manual_review_missing_evidence_and_direct_sql_forgery_are_rejected(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id = uuid.uuid4()
    with factory() as session:
        session.add(Portfolio(id=portfolio_id, name=f"review-forgery-{portfolio_id}",
                              version=1, total_assets=Decimal(1000),
                              available_cash=Decimal(1000)))
        session.commit()
    with factory() as session:
        reports = AccountFillReportService(session)
        AccountFillEvidenceService(session).capture(
            portfolio_id, source_ref="source-file-2", media_type="text/csv",
            raw_bytes=EVIDENCE_BYTES)
        missing, _ = reports.record(
            portfolio_id, order_id=None, market="CN", symbol="000001.SZ",
            side="SELL", fill_trade_date=date(2026, 9, 28), captured_at=CAPTURED,
            quantity="1", fill_price="10", fee="1",
            source_type="MANUAL_REPORT", source_ref="missing-evidence")
        absent_bytes, _ = reports.record(
            portfolio_id, order_id=None, market="CN", symbol="000001.SZ",
            side="SELL", fill_trade_date=date(2026, 9, 28), captured_at=CAPTURED,
            quantity="1", fill_price="10", fee="1",
            source_type="MANUAL_REPORT", source_ref="absent-bytes",
            evidence_sha256="c" * 64, evidence_uri="review-archive://absent")
        evidenced, _ = reports.record(
            portfolio_id, order_id=None, market="CN", symbol="000001.SZ",
            side="SELL", fill_trade_date=date(2026, 9, 28), captured_at=CAPTURED,
            quantity="1", fill_price="10", fee="1",
            source_type="MANUAL_REPORT", source_ref="forged-evidence",
            evidence_sha256=EVIDENCE_SHA, evidence_uri="review-archive://forged")
        session.commit()
        missing_id, absent_id = missing.id, absent_bytes.id
        evidenced_id, evidenced_sha = evidenced.id, evidenced.payload_sha256
    secret = b"account-review-secret-32-bytes-0002"
    monkeypatch.setenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", json.dumps({
        "auditor-1": base64.b64encode(secret).decode(),
    }))
    with factory() as session:
        with pytest.raises(ValueError, match="no referenced source evidence"):
            AccountFillReviewService(session).record(
                portfolio_id, missing_id, review_ref="missing", reviewed_by="auditor-1",
                reason="claimed review", review_signature="0" * 64)
        with pytest.raises(ValueError, match="bytes are missing"):
            AccountFillReviewService(session).record(
                portfolio_id, absent_id, review_ref="absent", reviewed_by="auditor-1",
                reason="claimed review", review_signature="0" * 64)
        session.add(AccountFillReportReviewRow(
            id=uuid.uuid4(), portfolio_id=portfolio_id, report_id=evidenced_id,
            report_sha256="0" * 64, evidence_sha256=EVIDENCE_SHA,
            review_ref="forged-ref", reviewed_by="auditor-1",
            reason="claimed review", review_signature="0" * 64))
        with pytest.raises(DBAPIError, match="fill review report evidence mismatch"):
            session.flush()
        session.rollback()
    with factory() as session:
        session.add(AccountFillReportReviewRow(
            id=uuid.uuid4(), portfolio_id=portfolio_id, report_id=evidenced_id,
            report_sha256=evidenced_sha, evidence_sha256=EVIDENCE_SHA,
            review_ref="forged-ref", reviewed_by="auditor-1",
            reason="claimed review", review_signature="0" * 64))
        session.commit()
    with factory() as session:
        with pytest.raises(ValueError, match="invalid account fill review signature"):
            AccountFillReviewService(session).verify_signed_claim(portfolio_id, evidenced_id)


def test_signed_review_recomputes_report_fields_for_direct_sql_row(env, monkeypatch):
    factory = env["session_factory"]
    portfolio_id, report_id, review_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    secret = b"account-review-secret-32-bytes-0003"
    monkeypatch.setenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", json.dumps({
        "auditor-1": base64.b64encode(secret).decode(),
    }))
    claimed_sha = "a" * 64
    signature = hmac.new(secret, review_payload(
        portfolio_id=portfolio_id, report_id=report_id,
        report_sha256=claimed_sha, evidence_sha256=EVIDENCE_SHA,
        review_ref="forged-fields", reviewed_by="auditor-1",
        reason="signed claimed digest"), "sha256").hexdigest()
    with factory() as session:
        session.add(Portfolio(id=portfolio_id, name=f"review-digest-{portfolio_id}",
                              version=1, total_assets=Decimal(1000),
                              available_cash=Decimal(1000)))
        session.flush()
        AccountFillEvidenceService(session).capture(
            portfolio_id, source_ref="direct-sql-evidence", media_type="text/csv",
            raw_bytes=EVIDENCE_BYTES)
        session.execute(text("""
            INSERT INTO account_fill_reports
                (id, portfolio_id, market, symbol, side, fill_trade_date,
                 captured_at, quantity, fill_price, fee, source_type, source_ref,
                 evidence_sha256, evidence_uri, payload_sha256)
            VALUES (:id, :portfolio_id, 'CN', '000001.SZ', 'SELL', '2026-09-28',
                    :captured_at, 5, 10, 0, 'MANUAL_REPORT', 'forged-fields',
                    :evidence_sha, 'review-archive://forged-fields', :claimed_sha)
        """), {"id": report_id, "portfolio_id": portfolio_id,
               "captured_at": CAPTURED, "evidence_sha": EVIDENCE_SHA,
               "claimed_sha": claimed_sha})
        session.execute(text("""
            INSERT INTO account_fill_report_reviews
                (id, portfolio_id, report_id, report_sha256, evidence_sha256,
                 review_ref, reviewed_by, reason, review_signature)
            VALUES (:id, :portfolio_id, :report_id, :claimed_sha, :evidence_sha,
                    'forged-fields', 'auditor-1', 'signed claimed digest', :signature)
        """), {"id": review_id, "portfolio_id": portfolio_id,
               "report_id": report_id, "claimed_sha": claimed_sha,
               "evidence_sha": EVIDENCE_SHA, "signature": signature})
        with pytest.raises(ValueError, match="digest does not match business fields"):
            AccountFillReviewService(session).verify_signed_claim(portfolio_id, report_id)
        session.rollback()
