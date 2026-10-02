"""Shared fixtures/builders for tests.backend.quant_strategy.integration.test_entry_batch; no test cases."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import base64
import json
from decimal import Decimal as D
from hashlib import sha256
from uuid import uuid4, UUID
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from sqlalchemy import select
from tests.backend.quant_strategy.support.family_batch import seed, args
from backend.modules.analysis.infrastructure.models import AnalysisTask
from backend.modules.investment_workspace.infrastructure.models import Portfolio
from backend.modules.quant_strategy.application.family_batch import FamilyBatchService
from backend.modules.quant_strategy.application.entry_batch import EntryBatchService
from backend.modules.quant_strategy.application.strategy_admission import StrategyAdmissionService
from backend.modules.quant_strategy.infrastructure.allocation_models import AllocationExecution
from backend.modules.quant_strategy.infrastructure.signals import QuantExecutionSignal
from backend.modules.quant_strategy.infrastructure.lifecycle_models import SuggestedOrder, PositionLifecycleState
from backend.modules.quant_strategy.application.lifecycle_service import LifecyclePolicyService, LifecycleOrderService
from backend.modules.quant_strategy.infrastructure.models import QuantStrategyVersion
from backend.modules.quant_research.application.trial_executor import prepare_trial
from backend.modules.quant_strategy.application.target_trial_binding import TargetTrialBindingService


DAY = date(2026, 9, 22)


CLOSES = {"000001.SZ": D(10), "000002.SZ": D(10)}


INDUSTRIES = {s: {"industry_code": "801010"} for s in CLOSES}


@pytest.fixture
def certified_rule(env, monkeypatch):
    """Current-day signed stock rule, committed before each order decision."""
    from backend.modules.quant_strategy.application.family_batch import CN_TIME
    from backend.modules.quant_strategy.infrastructure.instrument_rule_certificates import (
        InstrumentRuleCertificateRepository,
    )
    from backend.modules.quant_strategy.infrastructure.instrument_rule_models import InstrumentRuleCertificate
    from tests.backend.quant_strategy.support.instrument_rule_certificates import _signed, KEY

    monkeypatch.setitem(setup.__globals__, "DAY", datetime.now(CN_TIME).date())
    monkeypatch.setenv("LIVEPROFIT_RULE_REVIEW_KEYS", json.dumps({
        "rule-reviewer": base64.b64encode(KEY).decode(),
    }))

    def issue(symbol="000001.SZ"):
        with env["session_factory"]() as session:
            existing = session.scalar(select(InstrumentRuleCertificate).where(
                InstrumentRuleCertificate.symbol == symbol))
            if existing is not None:
                return existing.id
            certificate_id, observation, rule_capture, identity_capture, signature, rule = _signed(
                session, symbol=symbol, step=100)
            InstrumentRuleCertificateRepository(session).issue(
                certificate_id=certificate_id, observation_id=observation.id,
                rule_capture_id=rule_capture.id, identity_capture_id=identity_capture.id,
                reviewer_id="rule-reviewer", interpretation_note="verified fixture",
                identity_symbol=rule.symbol, identity_asset_type="stock",
                identity_locator="fixture identity row 1", review_signature=signature,
            )
            session.commit()
        return certificate_id

    return issue


def setup(env, *, lifecycle=False, same_symbol=True):
    pid, versions = seed(env)
    request = args(pid, versions, same_symbol=same_symbol)
    request["valuation_date"] = DAY
    request["intents"] = tuple(replace(i, evaluation_as_of=DAY) for i in request["intents"])
    with env["session_factory"]() as session:
        portfolio = session.get(Portfolio, pid)
        portfolio.total_assets = portfolio.available_cash = D(30000)
        portfolio.risk_per_trade_pct = D(".005")
        portfolio.max_total_position_pct = D(".75")
        portfolio.max_single_stock_pct = D(".08")
        portfolio.max_sector_pct = D(".8")
        portfolio.max_portfolio_open_risk_pct = D(".04")
        portfolio.max_sector_open_risk_pct = portfolio.max_daily_new_risk_pct = D(".02")
        portfolio.max_drawdown_pct = D(".15")
        portfolio.net_asset_value = portfolio.peak_net_asset_value = portfolio.day_start_net_asset_value = D(30000)
        portfolio.risk_facts_as_of = DAY
        policy = LifecyclePolicyService(session).publish(policy_key=str(uuid4()), required_fields=["close"],
            config={"template_id": "ma_trend_cross_v1", "reward_multiple": "3"}) if lifecycle else None
        ids = []
        for index, version in enumerate(versions):
            frozen = {"version_id": str(version)}
            if policy:
                session.get(QuantStrategyVersion, version).lifecycle_policy_version_id = policy.id
                frozen["lifecycle_policy"] = {"id": str(policy.id), "config": policy.config, "content_hash": policy.content_hash}
            task = AnalysisTask(id=uuid4(), task_type="MARKET_WIDE", status="SUCCEEDED", attempt_no=1, effective_trade_date=DAY,
                request_params={"execution_snapshot": {"strategy": frozen, "portfolio": {"id": str(pid)}}},
                selected_layers=["position"], input_hash="a"*64)
            session.add(task)
            session.flush()
            symbol = "000001.SZ" if same_symbol or index == 0 else "000002.SZ"
            signal = QuantExecutionSignal(task_id=task.id, attempt_no=1, strategy_version_id=version,
                ts_code=symbol, signal_kind="BUY", action="BUY", score=D(99-index), entry_price=D(10),
                stop_loss=D("9.6"), take_profit=D(13), valuation_price=D(10), signal_trade_date=DAY,
                signal_price_basis="qfq", execution_price_basis="raw", execution_market={
                    "trade_date": DAY.isoformat(), "raw_close": "10", "qfq_close": "10",
                    "raw_amount": "1000000", "adv20_amount": "1000000",
                    "up_limit": "11", "is_st": False, "is_suspended": False})
            session.add(signal)
            session.flush()
            ids.append(signal.id)
        batch = FamilyBatchService(session).project(**request)
        session.commit()
        return pid, versions, batch.id, tuple(ids)


def consume(session, batch_id, signal_ids):
    scans = {s.strategy_version_id: (s.task_id, s.attempt_no) for s in
             (session.get(QuantExecutionSignal, signal_id) for signal_id in signal_ids)}
    return EntryBatchService(session).materialize(batch_id=batch_id, scan_attempts=scans,
        closes=CLOSES, industry_map=INDUSTRIES, industry_bucket_available=True)
