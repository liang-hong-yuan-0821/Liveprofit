"""Shared fixtures/builders for tests.backend.investment_workspace.integration.test_lifecycle_revision_impact_surface; no test cases."""

import base64
import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy import select, text
from backend.modules.investment_workspace.application.fill_posting_stage import AccountFillPostingStage
from backend.modules.investment_workspace.infrastructure.fill_report_models import AccountFillPostingRow
from backend.modules.investment_workspace.infrastructure.models import PortfolioPosition
from backend.modules.quant_strategy.application.lifecycle_revision_impact_surface import (
    load_local_lifecycle_revision_impact_surface,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    FillLifecycleVersionStep, LifecyclePolicyVersion, PositionDailyFact,
    PositionDailyFactRevision, PositionDailyFactInputProposal, PositionIntent,
    PositionIntentRevision, PositionLifecycleState, SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.models import QuantStrategy, QuantStrategyVersion
from tests.backend.investment_workspace.support.fill_posting_stage import EXECUTED, SECRET, VOID_SECRET, _post_and_review_correction, _seed
from tests.backend.investment_workspace.support.lifecycle_persisted_diagnosis import _reviewed_fill


def _historical_corrected_anchor(factory, monkeypatch):
    """Apply a real signed correction before attaching its old root as anchor."""
    portfolio_id, order_id, report_id, replacement_id, resolution_id = (
        _post_and_review_correction(factory, monkeypatch))
    with factory() as session:
        AccountFillPostingStage(session)._stage_reviewed_correction(
            portfolio_id, resolution_id, expected_order_revision=2)
        session.commit()
    with factory() as session:
        original = session.scalar(select(AccountFillPostingRow).where(
            AccountFillPostingRow.report_id == report_id))
        position = session.scalar(select(PortfolioPosition).where(
            PortfolioPosition.portfolio_id == portfolio_id))
        strategy = QuantStrategy(name=f"impact-surface-{uuid.uuid4()}")
        policy = LifecyclePolicyVersion(
            policy_key=f"impact-surface-{uuid.uuid4()}", version_no=1,
            status="PUBLISHED", required_fields=[], config={}, content_hash="a" * 64)
        session.add_all((strategy, policy))
        session.flush()
        version = QuantStrategyVersion(
            strategy_id=strategy.id, version_no=1, status="PUBLISHED",
            source_code="pass", source_hash="b" * 64)
        session.add(version)
        session.flush()
        lifecycle = PositionLifecycleState(
            portfolio_id=portfolio_id, position_id=position.id,
            market="CN", symbol="000001.SZ",
            strategy_version_id=version.id, lifecycle_policy_version_id=policy.id,
            initial_fill_id=original.fill_event_id,
            initial_fill_price=Decimal(10), initial_stop_price=Decimal(8),
            risk_capacity_shares=Decimal(10), target_exposure_pct=Decimal("0.5"),
            target_shares=Decimal(5), phase="ACTIVE")
        session.add(lifecycle)
        session.commit()
        return portfolio_id, lifecycle.id, original.fill_event_id


def _daily_fact(session, lifecycle_id, trade_date):
    payload = {"close": "10", "is_suspended": False}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
    fact = PositionDailyFact(
        id=uuid.uuid4(), lifecycle_id=lifecycle_id, trade_date=trade_date,
        price_basis="raw", data_as_of=datetime(
            trade_date.year, trade_date.month, trade_date.day, 2,
            tzinfo=timezone.utc), input_payload=payload,
        input_hash=hashlib.sha256(canonical.encode()).hexdigest(),
        planning_result=None, rule_version="fixture", state_version_before=1,
        state_version_after=1, final_target_shares=Decimal(5))
    session.add(fact)
    session.flush()  # 0044 creates the first LIVE revision.
    return fact
