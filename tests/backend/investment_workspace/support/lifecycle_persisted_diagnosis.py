"""Shared fixtures/builders for tests.backend.investment_workspace.integration.test_lifecycle_persisted_diagnosis; no test cases."""

from tests.support.python.paths import PROJECT_ROOT
import hashlib
import base64
from concurrent.futures import ThreadPoolExecutor
import hmac
import json
from pathlib import Path
import time
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from backend.modules.investment_workspace.application.account_ledger_store import AccountLedgerStore
from backend.modules.investment_workspace.application.account_observations import AccountObservationService
from backend.modules.investment_workspace.application.fill_evidence import AccountFillEvidenceService
from backend.modules.investment_workspace.application.fill_posting_stage import AccountFillPostingStage
from backend.modules.investment_workspace.application.fill_reports import AccountFillReportService
from backend.modules.investment_workspace.application.fill_reviews import AccountFillReviewService, review_payload
from backend.modules.investment_workspace.application.reconciliation import AccountObservation
from backend.modules.investment_workspace.infrastructure.models import Portfolio, PortfolioPosition
from backend.modules.quant_strategy.application.lifecycle_persisted_diagnosis import diagnose_persisted_lifecycle
from backend.modules.quant_strategy.application.lifecycle_persisted_terminal import diagnose_persisted_terminal_marker
from backend.modules.quant_strategy.application.position_lifecycle_manager import LifecycleStateInput
from backend.modules.quant_strategy.application.position_lifecycle_manager import PositionLifecycleManager
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    FillLifecycleVersionStep, LifecyclePolicyVersion, OrderFillEvent,
    PositionDailyFact, PositionIntent,
    PositionLifecycleState, SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.models import QuantStrategy, QuantStrategyVersion


FIRST_DAY, SECOND_DAY = date(2026, 9, 21), date(2026, 9, 22)


FIRST_AT = datetime(2026, 9, 21, 2, tzinfo=timezone.utc)


SECOND_AT = datetime(2026, 9, 22, 2, tzinfo=timezone.utc)


CAPTURED_AT = datetime(2026, 9, 23, 8, tzinfo=timezone.utc)


def _reviewed_fill(session, *, portfolio_id, order, day, executed_at,
                   source_ref, review_key, quantity="5", captured_at=CAPTURED_AT,
                   expected_order_revision=1):
    artifact, _ = AccountFillEvidenceService(session).capture(
        portfolio_id, source_ref=f"replay-{source_ref}-original",
        media_type="text/csv", raw_bytes=(
            f"account,{source_ref},000001.SZ,{order.side},{quantity},10,1".encode("utf-8")))
    report, _ = AccountFillReportService(session).record(
        portfolio_id, order_id=order.id, market="CN", symbol="000001.SZ",
        side=order.side, fill_trade_date=day, executed_at=executed_at,
        captured_at=captured_at, quantity=quantity, fill_price="10", fee="1",
        source_type="MANUAL_REPORT", source_ref=source_ref,
        evidence_sha256=artifact.content_sha256,
        evidence_uri=f"review-archive://replay-{source_ref}")
    signature = hmac.new(review_key, review_payload(
        portfolio_id=portfolio_id, report_id=report.id,
        report_sha256=report.payload_sha256,
        evidence_sha256=artifact.content_sha256,
        review_ref=f"replay-{source_ref}-review", reviewed_by="replay-reviewer",
        reason="isolated fixture reviewed"), "sha256").hexdigest()
    AccountFillReviewService(session).record(
        portfolio_id, report.id, review_ref=f"replay-{source_ref}-review",
        reviewed_by="replay-reviewer", reason="isolated fixture reviewed",
        review_signature=signature)
    posting, created = AccountFillPostingStage(session)._stage_reviewed_report(
        portfolio_id, report.id,
        expected_order_revision=expected_order_revision)
    assert created is True
    event = session.get(OrderFillEvent, posting.fill_event_id)
    session.refresh(event)
    return event
