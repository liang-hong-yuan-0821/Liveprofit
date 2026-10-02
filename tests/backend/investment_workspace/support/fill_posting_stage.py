"""Shared fixtures/builders for tests.backend.investment_workspace.integration.test_fill_posting_stage; no test cases."""

import base64
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hmac
import json
import uuid
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from backend.modules.investment_workspace.application.account_ledger_store import AccountLedgerStore
from backend.modules.investment_workspace.application.account_observations import AccountObservationService
from backend.modules.investment_workspace.application.fill_evidence import AccountFillEvidenceService
from backend.modules.investment_workspace.application.fill_posting_stage import AccountFillPostingStage
from backend.modules.investment_workspace.application.fill_resolution_reviews import (
    AccountFillResolutionReviewService, resolution_review_payload,
)
from backend.modules.investment_workspace.application.fill_reports import AccountFillReportService
from backend.modules.investment_workspace.application.fill_reviews import AccountFillReviewService, review_payload
from backend.modules.investment_workspace.application.reconciliation import AccountObservation, ObservedHolding
from backend.modules.investment_workspace.infrastructure.account_ledger_models import AccountLedgerMovementRow
from backend.modules.investment_workspace.infrastructure.fill_report_models import (
    AccountFillPostingRow, AccountFillPostingVoidRow, AccountFillPostingCorrectionRow,
    AccountFillReportRow, AccountFillReportReviewRow,
    AccountFillReportResolutionRow, AccountFillResolutionReviewRow,
)
from backend.modules.investment_workspace.infrastructure.models import Portfolio, PortfolioPosition
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    LifecyclePolicyVersion, OrderFillEvent, PositionIntent, PositionIntentRevision,
    PositionLifecycleState,
    SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.models import QuantStrategy, QuantStrategyVersion
from backend.modules.quant_strategy.application.lifecycle_service import LifecycleOrderService
from backend.modules.quant_strategy.application.lifecycle_replay_inventory import inventory_lifecycle_replay
from backend.modules.quant_strategy.application.lifecycle_fill_resolution_inventory import inventory_effective_fill_chain
from backend.modules.quant_strategy.application.lifecycle_persisted_fill_inputs import load_local_fill_replay_inputs
from backend.modules.quant_strategy.application.lifecycle_persisted_revision_impact import load_local_lifecycle_revision_impact
from backend.modules.quant_strategy.application.lifecycle_persisted_diagnosis import diagnose_persisted_lifecycle
from backend.modules.quant_strategy.application.position_lifecycle_manager import LifecycleStateInput


BASELINE = datetime(2026, 9, 27, 8, tzinfo=timezone.utc)


EXECUTED = datetime(2026, 9, 28, 3, 30, tzinfo=timezone.utc)


CAPTURED = datetime(2026, 9, 29, 8, tzinfo=timezone.utc)


SECRET = b"fill-posting-stage-review-key-32-bytes"


VOID_SECRET = b"fill-posting-void-review-key-32bytes"


def _seed(factory, monkeypatch):
    portfolio_id, order_id = uuid.uuid4(), uuid.uuid4()
    with factory() as session:
        session.add(Portfolio(
            id=portfolio_id, name=f"atomic-stage-{portfolio_id}",
            version=1, total_assets=Decimal(2000), available_cash=Decimal(1000)))
        session.add(PortfolioPosition(
            portfolio_id=portfolio_id, market="CN", symbol="000001.SZ",
            quantity=Decimal(10), average_cost=Decimal(8)))
        session.add(SuggestedOrder(
            id=order_id, portfolio_id=portfolio_id,
            market="CN", symbol="000001.SZ", side="SELL",
            quantity=Decimal(10), limit_price=Decimal(10),
            reason_code="TEST_ATOMIC_STAGE", status="PROPOSED", revision=1))
        session.commit()
    with factory() as session:
        observation, _ = AccountObservationService(session).record(AccountObservation(
            portfolio_id=portfolio_id, trade_date=BASELINE.date(),
            captured_at=BASELINE, cash="1000",
            holdings=(ObservedHolding("CN", "000001.SZ", "10", "10"),),
            complete_holdings=True, source_ref="opening"),
            source_type="MANUAL_IMPORT")
        AccountLedgerStore(session).establish_baseline(portfolio_id, observation.id)
        artifact, _ = AccountFillEvidenceService(session).capture(
            portfolio_id, source_ref="statement-1", media_type="text/csv",
            raw_bytes=b"account,trade,000001.SZ,SELL,5,10,1")
        report, _ = AccountFillReportService(session).record(
            portfolio_id, order_id=order_id, market="CN", symbol="000001.SZ",
            side="SELL", fill_trade_date=date(2026, 9, 28),
            executed_at=EXECUTED, captured_at=CAPTURED,
            quantity="5", fill_price="10", fee="1",
            source_type="MANUAL_REPORT", source_ref="trade-row-1",
            evidence_sha256=artifact.content_sha256,
            evidence_uri="review-archive://statement-1")
        session.commit()
        report_id, report_sha, evidence_sha = report.id, report.payload_sha256, artifact.content_sha256
    monkeypatch.setenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", json.dumps({
        "auditor": base64.b64encode(SECRET).decode(),
    }))
    signature = hmac.new(SECRET, review_payload(
        portfolio_id=portfolio_id, report_id=report_id,
        report_sha256=report_sha, evidence_sha256=evidence_sha,
        review_ref="review-1", reviewed_by="auditor",
        reason="fixture account and statement inspected"), "sha256").hexdigest()
    with factory() as session:
        AccountFillReviewService(session).record(
            portfolio_id, report_id, review_ref="review-1",
            reviewed_by="auditor", reason="fixture account and statement inspected",
            review_signature=signature)
        session.commit()
    return portfolio_id, order_id, report_id


def _post_and_review_void(factory, monkeypatch):
    portfolio_id, order_id, report_id = _seed(factory, monkeypatch)
    monkeypatch.setenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", json.dumps({
        "auditor": base64.b64encode(SECRET).decode(),
        "void-auditor": base64.b64encode(VOID_SECRET).decode(),
    }))
    with factory() as session:
        AccountFillPostingStage(session)._stage_reviewed_report(
            portfolio_id, report_id, expected_order_revision=1)
        session.commit()
    with factory() as session:
        artifact, _ = AccountFillEvidenceService(session).capture(
            portfolio_id, source_ref="void-statement", media_type="text/csv",
            raw_bytes=b"account,trade,000001.SZ,SELL,5,10,1,VOID")
        resolution, _ = AccountFillReportService(session).resolve(
            portfolio_id, report_id, action="VOID", replacement_report_id=None,
            reason="broker report revoked", captured_at=CAPTURED,
            source_type="MANUAL_REPORT", source_ref="trade-row-1-void",
            evidence_sha256=artifact.content_sha256,
            evidence_uri="review-archive://void-statement")
        session.commit()
        resolution_id, resolution_sha, evidence_sha = (
            resolution.id, resolution.payload_sha256, artifact.content_sha256)
    signature = hmac.new(VOID_SECRET, resolution_review_payload(
        portfolio_id=portfolio_id, resolution_id=resolution_id,
        resolution_sha256=resolution_sha, evidence_sha256=evidence_sha,
        review_ref="void-review-1", reviewed_by="void-auditor",
        reason="fixture revocation statement inspected"), "sha256").hexdigest()
    with factory() as session:
        AccountFillResolutionReviewService(session).record(
            portfolio_id, resolution_id, review_ref="void-review-1",
            reviewed_by="void-auditor", reason="fixture revocation statement inspected",
            review_signature=signature)
        session.commit()
    return portfolio_id, order_id, report_id, resolution_id


def _post_and_review_correction(factory, monkeypatch, *, replacement_fee="0.5"):
    portfolio_id, order_id, report_id = _seed(factory, monkeypatch)
    monkeypatch.setenv("LIVEPROFIT_ACCOUNT_FILL_REVIEW_KEYS", json.dumps({
        "auditor": base64.b64encode(SECRET).decode(),
        "void-auditor": base64.b64encode(VOID_SECRET).decode(),
    }))
    with factory() as session:
        AccountFillPostingStage(session)._stage_reviewed_report(
            portfolio_id, report_id, expected_order_revision=1)
        session.commit()
    with factory() as session:
        evidence = AccountFillEvidenceService(session)
        artifact, _ = evidence.capture(
            portfolio_id, source_ref="corrected-statement", media_type="text/csv",
            raw_bytes=f"account,trade,000001.SZ,SELL,4,11,{replacement_fee}".encode())
        replacement, _ = AccountFillReportService(session).record(
            portfolio_id, order_id=order_id, market="CN", symbol="000001.SZ",
            side="SELL", fill_trade_date=date(2026, 9, 28),
            executed_at=EXECUTED, captured_at=CAPTURED,
            quantity="4", fill_price="11", fee=replacement_fee,
            source_type="MANUAL_REPORT", source_ref="corrected-trade-row",
            evidence_sha256=artifact.content_sha256,
            evidence_uri="review-archive://corrected-statement")
        session.commit()
        replacement_id, replacement_sha, replacement_evidence_sha = (
            replacement.id, replacement.payload_sha256, artifact.content_sha256)
    replacement_signature = hmac.new(SECRET, review_payload(
        portfolio_id=portfolio_id, report_id=replacement_id,
        report_sha256=replacement_sha, evidence_sha256=replacement_evidence_sha,
        review_ref="replacement-review", reviewed_by="auditor",
        reason="corrected row inspected"), "sha256").hexdigest()
    with factory() as session:
        AccountFillReviewService(session).record(
            portfolio_id, replacement_id, review_ref="replacement-review",
            reviewed_by="auditor", reason="corrected row inspected",
            review_signature=replacement_signature)
        artifact, _ = AccountFillEvidenceService(session).capture(
            portfolio_id, source_ref="correction-notice", media_type="text/csv",
            raw_bytes=b"original row corrected to 4 shares at 11, fee 0.5")
        resolution, _ = AccountFillReportService(session).resolve(
            portfolio_id, report_id, action="CORRECT",
            replacement_report_id=replacement_id,
            reason="broker corrected execution", captured_at=CAPTURED,
            source_type="MANUAL_REPORT", source_ref="trade-row-correction",
            evidence_sha256=artifact.content_sha256,
            evidence_uri="review-archive://correction-notice")
        session.commit()
        resolution_id, resolution_sha, notice_sha = (
            resolution.id, resolution.payload_sha256, artifact.content_sha256)
    signature = hmac.new(VOID_SECRET, resolution_review_payload(
        portfolio_id=portfolio_id, resolution_id=resolution_id,
        resolution_sha256=resolution_sha, evidence_sha256=notice_sha,
        review_ref="correction-review", reviewed_by="void-auditor",
        reason="correction notice inspected"), "sha256").hexdigest()
    with factory() as session:
        AccountFillResolutionReviewService(session).record(
            portfolio_id, resolution_id, review_ref="correction-review",
            reviewed_by="void-auditor", reason="correction notice inspected",
            review_signature=signature)
        session.commit()
    return portfolio_id, order_id, report_id, replacement_id, resolution_id
