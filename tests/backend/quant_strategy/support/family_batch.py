"""Shared fixtures/builders for tests.backend.quant_strategy.integration.test_family_batch; no test cases."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from threading import Barrier
from uuid import uuid4
import pytest
from sqlalchemy import select, text, inspect
from sqlalchemy.exc import DBAPIError
from backend.modules.investment_workspace.infrastructure.models import Portfolio, PortfolioPosition
from backend.modules.investment_workspace.application.fill_reports import AccountFillReportService
from backend.modules.quant_strategy.infrastructure.models import QuantStrategy, QuantStrategyVersion
from backend.modules.quant_strategy.infrastructure.lifecycle_models import SuggestedOrder
from backend.modules.quant_strategy.infrastructure.admission_models import StrategyAdmissionEvent
from backend.modules.quant_strategy.infrastructure.allocation_models import AllocationBatch, AllocationMember
from backend.modules.quant_strategy.application.family_batch import FamilyBatchService, CN_TIME
from backend.modules.quant_strategy.application.strategy_admission import StrategyAdmissionService
from backend.modules.quant_strategy.domain.portfolio_targets import PortfolioTargetIntent, TargetLeg


def seed(env, profile="BALANCED"):
    with env["session_factory"]() as session:
        account = Portfolio(id=uuid4(), name=str(uuid4()), total_assets=D(10000), available_cash=D(10000), risk_profile=profile)
        session.add(account)
        versions = []
        now = datetime.now(timezone.utc)
        for family, expectancy in (("low", ".001"), ("high", ".002")):
            strategy = QuantStrategy(id=uuid4(), name=str(uuid4()), version=1)
            session.add(strategy)
            session.flush()
            version = QuantStrategyVersion(id=uuid4(), strategy_id=strategy.id, version_no=1, status="PUBLISHED",
                source_code="def strategy(context): return {}", source_hash="a"*64, version=1)
            session.add(version)
            session.flush()
            session.add(StrategyAdmissionEvent(strategy_version_id=version.id, family_id=family,
                asset_scope="CN_STOCK", risk_profile="BALANCED", revision=1, state="ADVISORY",
                reason="isolated test fixture", request_key="fixture", recorded_at=now-timedelta(hours=1),
                evidence_ref="test://fixture", evidence_sha256="a"*64, evidence_completed_at=now-timedelta(days=1),
                valid_until=now+timedelta(days=2), net_expectancy_lower_bound=D(expectancy)))
            versions.append(version.id)
        session.commit()
        return account.id, tuple(versions)


def args(account, versions, key="batch", *, same_symbol=True):
    day = datetime.now(CN_TIME).date()
    intents = tuple(PortfolioTargetIntent(family, version, day, day, day, "frozen-policy", "fixture",
        (TargetLeg("000001.SZ" if same_symbol or family == "low" else "000002.SZ", D(1)),))
        for family, version in zip(("low", "high"), versions))
    return dict(portfolio_id=account, request_key=key, asset_scope="CN_STOCK", expected_version_ids=versions,
                intents=intents, valuation_date=day, closes={"000001.SZ": D(10), "000002.SZ": D(10)})
