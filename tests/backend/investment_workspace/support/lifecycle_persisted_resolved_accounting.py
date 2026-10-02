"""Shared fixtures/builders for tests.backend.investment_workspace.integration.test_lifecycle_persisted_resolved_accounting; no test cases."""

import base64
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import select, text
from backend.modules.investment_workspace.application.fill_posting_stage import AccountFillPostingStage
from backend.modules.investment_workspace.application.fill_reports import AccountFillReportService
from backend.modules.investment_workspace.infrastructure.fill_report_models import AccountFillPostingRow
from backend.modules.investment_workspace.infrastructure.models import PortfolioPosition
from backend.modules.quant_strategy.application.lifecycle_persisted_resolved_accounting import (
    load_local_resolved_accounting,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    LifecyclePolicyVersion, PositionIntent, PositionLifecycleState, SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.models import QuantStrategy, QuantStrategyVersion
from tests.backend.investment_workspace.support.fill_posting_stage import BASELINE, CAPTURED, EXECUTED, SECRET, VOID_SECRET, _post_and_review_correction, _post_and_review_void, _seed
from tests.backend.investment_workspace.support.lifecycle_persisted_diagnosis import _reviewed_fill
from tests.backend.investment_workspace.support.lifecycle_revision_impact_surface import _historical_corrected_anchor


def _anchor_existing_fill(factory, portfolio_id, report_id):
    with factory() as session:
        posting = session.scalar(select(AccountFillPostingRow).where(
            AccountFillPostingRow.report_id == report_id))
        position = session.scalar(select(PortfolioPosition).where(
            PortfolioPosition.portfolio_id == portfolio_id))
        strategy = QuantStrategy(name=f"accounting-{uuid.uuid4()}")
        policy = LifecyclePolicyVersion(
            policy_key=f"accounting-{uuid.uuid4()}", version_no=1,
            status="PUBLISHED", required_fields=[], config={},
            content_hash="a" * 64)
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
            initial_fill_id=posting.fill_event_id, initial_fill_price=Decimal(10),
            initial_stop_price=Decimal(8), risk_capacity_shares=Decimal(10),
            target_exposure_pct=Decimal("0.5"), target_shares=Decimal(5),
            phase="ACTIVE")
        session.add(lifecycle)
        session.commit()
        return lifecycle.id, posting.fill_event_id


def _accounting(session, portfolio_id, lifecycle_id, **baseline_changes):
    baseline = dict(
        baseline_as_of=BASELINE, baseline_quantity=Decimal(10),
        baseline_total_cost=Decimal(100),
        baseline_source_ref="fixture:explicit-opening-cost")
    baseline.update(baseline_changes)
    return load_local_resolved_accounting(
        session, portfolio_id=portfolio_id, lifecycle_id=lifecycle_id, **baseline)
