"""Shared fixtures/builders for tests.backend.quant_strategy.integration.test_lifecycle_service; no test cases."""

from __future__ import annotations
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import base64
import json
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace
import pytest
from backend.modules.quant_research.application.trial_executor import prepare_trial
from backend.modules.investment_workspace.infrastructure.models import Portfolio, PortfolioPosition
from backend.modules.analysis.infrastructure.models import AnalysisTask
from backend.modules.quant_strategy.application.errors import (
    FillValidationError,
    LifecycleInvalidStateError,
    LifecycleRevisionConflictError,
)
from backend.modules.quant_strategy.application.lifecycle_service import (
    LifecycleOrderService,
    LifecyclePolicyService,
    LifecycleStateService,
)
from backend.modules.quant_strategy.application.position_lifecycle_manager import (
    LifecycleStateInput, PositionLifecycleManager,
)
from backend.modules.quant_strategy.application.lifecycle_replay_inventory import inventory_lifecycle_replay
from backend.modules.quant_strategy.application.lifecycle_persisted_daily_inputs import load_local_daily_fact_inputs
from backend.modules.quant_strategy.application.lifecycle_persisted_intent_inputs import load_local_intent_definitions
from backend.modules.quant_strategy.application.lifecycle_persisted_diagnosis import diagnose_persisted_lifecycle
from backend.modules.quant_strategy.application.daily_fact_input_proposals import propose_daily_fact_input
from backend.modules.quant_strategy.application.lifecycle_projection_diff import load_current_lifecycle_projection
from backend.modules.quant_strategy.domain.management_policies import (
    ExitPolicyKind, InitialStopRule, ManagementPolicy, StopMode,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    OrderFillEvent,
    PositionDailyFact,
    PositionDailyFactInputProposal,
    PositionDailyFactRevision,
    PositionIntent,
    PositionExpectation,
    PositionLifecycleState,
    PositionTrailingStop,
    SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.models import QuantStrategy, QuantStrategyVersion
from backend.modules.quant_strategy.infrastructure.signals import QuantExecutionSignal


BUY_DAY = datetime.now(ZoneInfo("Asia/Shanghai")).date()


@pytest.fixture
def certified_stock_rules(env, monkeypatch):
    from sqlalchemy import select
    from backend.modules.quant_strategy.infrastructure.instrument_rule_certificates import InstrumentRuleCertificateRepository
    from backend.modules.quant_strategy.infrastructure.instrument_rule_models import InstrumentRuleCertificate
    from tests.backend.quant_strategy.support.instrument_rule_certificates import _signed, KEY

    monkeypatch.setenv("LIVEPROFIT_RULE_REVIEW_KEYS", json.dumps({
        "rule-reviewer": base64.b64encode(KEY).decode(),
    }))
    with env["session_factory"]() as session:
        for symbol in ("000001.SZ", "000002.SZ"):
            if session.scalar(select(InstrumentRuleCertificate).where(
                    InstrumentRuleCertificate.symbol == symbol)) is not None:
                continue
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


def _buy_context(day, close="10.5"):
    return {"asset_scope": "CN_STOCK", "valuation_date": day, "closes": {"000001.SZ": Decimal(close)},
            "industry_map": {"000001.SZ": {"industry_code": "801010"}},
            "industry_bucket_available": True}


def _certify_buy_account(session, portfolio_id, day):
    portfolio = session.get(Portfolio, portfolio_id)
    portfolio.total_assets = Decimal("300000")
    portfolio.min_risk_reward_ratio = Decimal("1")
    portfolio.risk_per_trade_pct = Decimal("0.005")
    portfolio.max_total_position_pct = Decimal("0.75")
    portfolio.max_single_stock_pct = Decimal("0.08")
    portfolio.max_sector_pct = Decimal("0.8")
    portfolio.max_portfolio_open_risk_pct = Decimal("0.04")
    portfolio.max_sector_open_risk_pct = Decimal("0.02")
    portfolio.max_daily_new_risk_pct = Decimal("0.02")
    portfolio.max_drawdown_pct = Decimal("0.15")
    portfolio.net_asset_value = portfolio.peak_net_asset_value = portfolio.day_start_net_asset_value = Decimal("300000")
    portfolio.risk_facts_as_of = day
    portfolio.risk_profile = "BALANCED"
    session.flush()
    # Explicit isolated-test evidence, never a production qualification writer.
    from backend.modules.quant_strategy.infrastructure.admission_models import StrategyAdmissionEvent
    for lifecycle in session.query(PositionLifecycleState).filter_by(portfolio_id=portfolio_id).all():
        if session.query(StrategyAdmissionEvent).filter_by(strategy_version_id=lifecycle.strategy_version_id).first():
            continue
        session.add(StrategyAdmissionEvent(
            strategy_version_id=lifecycle.strategy_version_id, family_id="trend", asset_scope="CN_STOCK",
            risk_profile="BALANCED", revision=1, state="ADVISORY", reason="isolated fixture only", request_key="fixture",
            recorded_at=datetime(2026, 9, 1, tzinfo=timezone.utc), evidence_ref="test://fixture",
            evidence_sha256="a"*64, evidence_completed_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
            valid_until=datetime(2030, 1, 1, tzinfo=timezone.utc), net_expectancy_lower_bound=Decimal(".001"),
        ))
    session.flush()


def _seed_order(session_factory, *, side="BUY", quantity=Decimal("100"), cash=Decimal("10000")):
    with session_factory() as session:
        portfolio = Portfolio(
            id=uuid.uuid4(), name=f"p-{uuid.uuid4().hex[:8]}", version=1,
            total_assets=Decimal("10000"), available_cash=cash,
            risk_per_trade_pct=Decimal("0.01"), min_risk_reward_ratio=Decimal("2"),
            max_total_position_pct=Decimal("0.8"), max_single_stock_pct=Decimal("0.1"),
            max_sector_pct=Decimal("0.3"), max_portfolio_open_risk_pct=Decimal("0.06"),
            max_sector_open_risk_pct=Decimal("0.03"), max_daily_new_risk_pct=Decimal("0.02"),
            max_drawdown_pct=Decimal("0.1"), max_daily_loss_pct=Decimal("0.03"),
        )
        session.add(portfolio)
        if side == "SELL":
            session.add(PortfolioPosition(
                id=uuid.uuid4(), portfolio_id=portfolio.id, market="CN", symbol="000001.SZ",
                quantity=quantity, average_cost=Decimal("7"), active_stop_price=Decimal("6"),
            ))
        order = SuggestedOrder(
            id=uuid.uuid4(), portfolio_id=portfolio.id, market="CN", symbol="000001.SZ",
            industry_code="801010", side=side, quantity=quantity, filled_quantity=Decimal(0),
            limit_price=Decimal("10"), stop_price=Decimal("8"),
            reserved_cash=quantity * Decimal("10") if side == "BUY" else Decimal(0),
            reserved_risk=quantity * Decimal("2") if side == "BUY" else Decimal(0),
            reason_code="TEST", status="PROPOSED", revision=1,
        )
        session.add(order)
        session.commit()
        return portfolio.id, order.id


def _seed_buy_lifecycles(factory, *, cash="2500", symbols=("000001.SZ", "000002.SZ"),
                         day=date(2026, 9, 22)):
    with factory() as session:
        policy = LifecyclePolicyService(session).publish(
            policy_key=f"buy-gate-{uuid.uuid4().hex[:8]}", required_fields=["close"],
            config={"template_id": "ma_trend_cross_v1", "reward_multiple": "3"},
        )
        strategy = QuantStrategy(id=uuid.uuid4(), name=f"buy-{uuid.uuid4().hex[:8]}", version=1)
        portfolio = Portfolio(id=uuid.uuid4(), name=f"buy-p-{uuid.uuid4().hex[:8]}",
                              total_assets=Decimal(30000), available_cash=Decimal(cash), version=1)
        session.add_all([strategy, portfolio])
        session.flush()
        version = QuantStrategyVersion(
            id=uuid.uuid4(), strategy_id=strategy.id, version_no=1, status="PUBLISHED",
            source_code="def strategy(context): return {}", source_hash="e" * 64,
            template_id="ma_trend_cross_v1", template_params={},
            lifecycle_policy_version_id=policy.id, version=1,
        )
        session.add(version)
        session.flush()
        ids = []
        for symbol in symbols:
            position = PortfolioPosition(id=uuid.uuid4(), portfolio_id=portfolio.id, market="CN",
                                         symbol=symbol, quantity=Decimal(100), average_cost=Decimal(10),
                                         active_stop_price=Decimal(9))
            session.add(position)
            session.flush()
            lifecycle = PositionLifecycleState(
                id=uuid.uuid4(), portfolio_id=portfolio.id, position_id=position.id,
                market="CN", symbol=symbol, strategy_version_id=version.id,
                lifecycle_policy_version_id=policy.id, initial_fill_price=Decimal(10),
                initial_stop_price=Decimal(9), risk_capacity_shares=Decimal(1000),
                target_exposure_pct=Decimal(".5"), target_shares=Decimal(500),
                profit_take_price=Decimal(13), state_version=1, phase="INITIALIZED",
            )
            session.add(lifecycle)
            ids.append(lifecycle.id)
        _certify_buy_account(session, portfolio.id, day)
        session.commit()
        return portfolio.id, ids


def _buy_day(session, lifecycle_id, *, context=True, defer_buy=False):
    fact = {"close": "10", "high": "10", "ma5": "9.9", "ma20": "9.8", "ma60": "9.7",
            "execution_market": {"trade_date": BUY_DAY.isoformat(), "raw_close": "10", "raw_amount": "100000",
                                 "adv20_amount": "100000",
                                 "up_limit": "11", "is_st": False, "is_suspended": False}}
    return PositionLifecycleManager(session).process_day(
        lifecycle_id, trade_date=BUY_DAY, fact=fact, defer_buy=defer_buy,
        data_as_of=datetime.now(timezone.utc),
        buy_context={"asset_scope": "CN_STOCK", "valuation_date": BUY_DAY,
                     "closes": {s: Decimal(10) for s in ("000001.SZ", "000002.SZ")},
                     "industry_map": {s: {"industry_code": "801010"} for s in ("000001.SZ", "000002.SZ")},
                     "industry_bucket_available": True} if context else None,
    )


def _deferred_batch(factory, *, bounds=(None, None)):
    from backend.modules.quant_strategy.application.family_batch import FamilyBatchService, CN_TIME
    from backend.modules.quant_strategy.domain.portfolio_targets import PortfolioTargetIntent, TargetLeg
    portfolio_id, ids = _seed_buy_lifecycles(factory, cash="20000", symbols=("000001.SZ",), day=BUY_DAY)
    with factory() as session:
        daily, intent, order, _ = _buy_day(session, ids[0], defer_buy=True)
        assert order is None
        assert daily.planning_result["stage"] == "AWAITING_BATCH"
        lifecycle = session.get(PositionLifecycleState, ids[0])
        today = datetime.now(CN_TIME).date()
        target = PortfolioTargetIntent("trend", lifecycle.strategy_version_id, daily.trade_date,
            today, today, "fixture-policy", "fixture", (TargetLeg("000001.SZ", Decimal(".011"), *bounds),))
        batch = FamilyBatchService(session).project(portfolio_id=portfolio_id, request_key="joint-fixture",
            asset_scope="CN_STOCK", expected_version_ids=(lifecycle.strategy_version_id,), intents=(target,),
            valuation_date=daily.trade_date, closes={"000001.SZ": Decimal(10)})
        session.commit()
        return portfolio_id, ids[0], daily.id, batch.id


def _consume_deferred(session, daily_id, batch_id):
    from backend.modules.quant_strategy.application.lifecycle_batch import LifecycleBatchService
    return LifecycleBatchService(session).materialize(daily_id=daily_id, batch_id=batch_id,
        buy_context={"asset_scope": "CN_STOCK", "valuation_date": BUY_DAY,
            "closes": {"000001.SZ": Decimal(10)}, "industry_map": {"000001.SZ": {"industry_code": "801010"}},
            "industry_bucket_available": True})
