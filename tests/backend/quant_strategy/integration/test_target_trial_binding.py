# test-catalog-begin
# {
#   "purpose": "量化策略 / target_trial_binding：Binding tests use only conftest's liveprofit_quant_strategy_test database.",
#   "keywords": [
#     "量化策略",
#     "执行准入",
#     "ETF",
#     "不可变历史",
#     "交易意图",
#     "投资组合",
#     "来源证据",
#     "未绑定",
#     "target_trial_binding",
#     "admission",
#     "etf",
#     "immutable",
#     "intent",
#     "portfolio",
#     "source",
#     "unbound"
#   ],
#   "covers": [
#     "backend/modules/quant_research/application/trial_executor.py",
#     "backend/modules/quant_strategy/application/errors.py",
#     "backend/modules/quant_strategy/application/lifecycle_service.py",
#     "backend/modules/quant_strategy/application/portfolio_policy_contract.py",
#     "backend/modules/quant_strategy/application/target_trial_binding.py",
#     "backend/modules/quant_strategy/domain/portfolio_targets.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py",
#     "backend/modules/quant_strategy/infrastructure/models.py",
#     "backend/modules/quant_strategy/infrastructure/target_binding_models.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""Binding tests use only conftest's liveprofit_quant_strategy_test database."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from decimal import Decimal
from hashlib import sha256
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from backend.modules.quant_research.application.trial_executor import prepare_trial
from backend.modules.quant_strategy.application.lifecycle_service import LifecyclePolicyService
from backend.modules.quant_strategy.application.target_trial_binding import TargetTrialBindingService
from backend.modules.quant_strategy.application.errors import FillValidationError
from backend.modules.quant_strategy.application.portfolio_policy_contract import portfolio_policy_config
from backend.modules.quant_strategy.infrastructure.lifecycle_models import LifecyclePolicyVersion
from backend.modules.quant_strategy.infrastructure.models import QuantStrategy, QuantStrategyVersion
from backend.modules.quant_strategy.infrastructure.target_binding_models import TargetTrialBinding
from backend.modules.quant_strategy.domain.portfolio_targets import PortfolioTargetIntent, TargetLeg


STOCK_TRIAL = "stock_medium_momentum:60:10:60"
OTHER_STOCK_TRIAL = "stock_medium_momentum:120:10:60"
ETF_TRIAL = "etf_dual_momentum:60:1:WEEKLY"
SOURCE = "def strategy(context):\n    return {'action': 'HOLD'}\n"


def seed(factory, *, status="PUBLISHED", policy_id=None):
    with factory() as session:
        strategy = QuantStrategy(id=uuid4(), name=f"target-binding-{uuid4().hex}", version=1)
        session.add(strategy)
        session.flush()
        version = QuantStrategyVersion(
            id=uuid4(), strategy_id=strategy.id, version_no=1, status=status,
            source_code=SOURCE, source_hash=sha256(SOURCE.encode()).hexdigest(), version=1,
            lifecycle_policy_version_id=policy_id,
        )
        session.add(version)
        session.commit()
        return version.id


@pytest.mark.parametrize("trial_id,scope", (
    (STOCK_TRIAL, "CN_STOCK"),
    ("stock_short_reversion:2:1.5:3", "CN_STOCK"),
    (ETF_TRIAL, "CN_ETF"),
    ("etf_defensive_allocation:20:0.04:60", "CN_ETF"),
))
def test_portfolio_policy_binding_audit_requires_exact_published_content(env, trial_id, scope):
    factory = env["session_factory"]
    prepared = prepare_trial(trial_id)
    with factory() as session:
        policy = LifecyclePolicyService(session).publish_portfolio_trial(prepared)
        policy_id = policy.id
        assert policy.config == portfolio_policy_config(prepared)
        assert LifecyclePolicyService(session).publish_portfolio_trial(prepared).id == policy_id
    version_id = seed(factory, policy_id=policy_id)
    with factory() as session:
        service = TargetTrialBindingService(session)
        service.bind(strategy_version_id=version_id, trial_id=trial_id, asset_scope=scope)
        session.commit()
        assert service.audit_portfolio_policy(strategy_version_id=version_id).family == prepared.spec.family
        policy = session.get(LifecyclePolicyVersion, policy_id)
        policy.content_hash = "0" * 64
        session.flush()
        with pytest.raises(ValueError, match="hash mismatch"):
            service.audit_portfolio_policy(strategy_version_id=version_id)
        session.rollback()
        assert service.audit_portfolio_policy(strategy_version_id=version_id).family == prepared.spec.family


def test_portfolio_policy_audit_rejects_absent_and_wrong_policy(env):
    factory = env["session_factory"]
    no_policy = seed(factory)
    with factory() as session:
        service = TargetTrialBindingService(session)
        service.bind(strategy_version_id=no_policy, trial_id=STOCK_TRIAL, asset_scope="CN_STOCK")
        with pytest.raises(ValueError, match="frozen lifecycle policy required"):
            service.audit_portfolio_policy(strategy_version_id=no_policy)
        session.rollback()
        prepared = prepare_trial(STOCK_TRIAL)
        malformed = portfolio_policy_config(prepared)
        malformed["portfolio_trial"]["holding_policy"]["trend_ma"] = 120
        with pytest.raises(FillValidationError, match="portfolio trial policy config"):
            LifecyclePolicyService(session).publish(
                policy_key=prepared.spec.management_policy, required_fields=[], config=malformed)


def test_target_intent_requires_exact_trial_binding_and_policy(env):
    factory = env["session_factory"]
    prepared = prepare_trial(STOCK_TRIAL)
    with factory() as session:
        policy = LifecyclePolicyService(session).publish_portfolio_trial(prepared)
        policy_id = policy.id
    version_id = seed(factory, policy_id=policy_id)
    day = date(2026, 9, 24)
    target = PortfolioTargetIntent(
        family_id=prepared.spec.family, strategy_version_id=version_id,
        evaluation_as_of=day, decision_date=day, valid_until=day,
        policy_id=prepared.spec.management_policy, reason="fixture",
        legs=(TargetLeg("000001.SZ", Decimal("0.5")),),
        trial_id=STOCK_TRIAL, definition_hash=prepared.definition_hash,
    )
    with factory() as session:
        service = TargetTrialBindingService(session)
        with pytest.raises(ValueError, match="no frozen version binding"):
            service.audit_target_intent(intent=target, asset_scope="CN_STOCK")
        with pytest.raises(ValueError, match="portfolio policy requires frozen trial binding"):
            service.audit_target_intent(intent=replace(target, trial_id=None, definition_hash=None),
                                        asset_scope="CN_STOCK")
        service.bind(strategy_version_id=version_id, trial_id=STOCK_TRIAL, asset_scope="CN_STOCK")
        assert service.audit_target_intent(intent=target, asset_scope="CN_STOCK").family == prepared.spec.family
        for changed in (
            replace(target, trial_id=OTHER_STOCK_TRIAL),
            replace(target, definition_hash="0" * 64),
            replace(target, policy_id="wrong"),
            replace(target, family_id="wrong"),
            replace(target, trial_id=None, definition_hash=None),
        ):
            with pytest.raises(ValueError, match="differs from frozen trial binding"):
                service.audit_target_intent(intent=changed, asset_scope="CN_STOCK")
        with pytest.raises(ValueError, match="differs from frozen trial binding"):
            service.audit_target_intent(intent=target, asset_scope="CN_ETF")


def test_unbound_portfolio_policy_claim_is_rejected_even_without_version_policy(env):
    factory = env["session_factory"]
    version_id = seed(factory)
    day = date(2026, 9, 24)
    target = PortfolioTargetIntent(
        family_id="stock_medium_momentum", strategy_version_id=version_id,
        evaluation_as_of=day, decision_date=day, valid_until=day,
        policy_id=prepare_trial(STOCK_TRIAL).spec.management_policy, reason="fixture",
        legs=(TargetLeg("000001.SZ", Decimal("0.5")),),
    )
    with factory() as session:
        with pytest.raises(ValueError, match="portfolio policy requires frozen trial binding"):
            TargetTrialBindingService(session).audit_target_intent(intent=target, asset_scope="CN_STOCK")


def test_portfolio_policy_audit_rejects_live_version_and_policy_drift(env):
    factory = env["session_factory"]
    with factory() as session:
        service = LifecyclePolicyService(session)
        correct = service.publish_portfolio_trial(prepare_trial(STOCK_TRIAL))
        alternate = service.publish_portfolio_trial(prepare_trial(OTHER_STOCK_TRIAL))
        correct_id, alternate_id = correct.id, alternate.id
    version_id = seed(factory, policy_id=correct_id)
    with factory() as session:
        TargetTrialBindingService(session).bind(strategy_version_id=version_id,
            trial_id=STOCK_TRIAL, asset_scope="CN_STOCK")
        session.commit()

    def rejects(change, message):
        with factory() as session:
            change(session)
            session.flush()
            with pytest.raises(ValueError, match=message):
                TargetTrialBindingService(session).audit_portfolio_policy(
                    strategy_version_id=version_id)
            session.rollback()

    rejects(lambda session: setattr(session.get(QuantStrategyVersion, version_id),
                                    "lifecycle_policy_version_id", alternate_id), "binding content mismatch")
    rejects(lambda session: setattr(session.get(QuantStrategyVersion, version_id),
                                    "source_code", SOURCE + "# drift\n"), "published strategy")
    rejects(lambda session: setattr(session.get(LifecyclePolicyVersion, correct_id),
                                    "status", "ARCHIVED"), "lifecycle policy does not match")
    rejects(lambda session: setattr(session.get(LifecyclePolicyVersion, correct_id),
                                    "policy_key", "wrong"), "lifecycle policy does not match")
    rejects(lambda session: setattr(session.get(LifecyclePolicyVersion, correct_id),
                                    "config", {"legacy": True}), "lifecycle policy does not match")
    with factory() as session:
        assert TargetTrialBindingService(session).audit_portfolio_policy(
            strategy_version_id=version_id).family == "stock_medium_momentum"


def test_binding_is_transactional_frozen_and_replays_after_archive(env):
    factory = env["session_factory"]
    version_id = seed(factory)
    with factory() as session:
        service = TargetTrialBindingService(session)
        first = service.bind(strategy_version_id=version_id, trial_id=STOCK_TRIAL,
                             asset_scope="CN_STOCK")
        original_time = first.bound_at
        with factory() as observer:
            assert observer.get(TargetTrialBinding, version_id) is None
        session.rollback()
        assert session.get(TargetTrialBinding, version_id) is None

        first = service.bind(strategy_version_id=version_id, trial_id=STOCK_TRIAL,
                             asset_scope="CN_STOCK")
        session.commit()
        assert first.definition_hash == prepare_trial(STOCK_TRIAL).definition_hash
        assert first.scanner_source_hash == sha256(SOURCE.encode()).hexdigest()
        assert first.trial_spec["parameters"] == [["lookback", 60], ["slots", 10], ["benchmark_ma", 60]]
        assert first.bound_at >= original_time
        session.get(QuantStrategyVersion, version_id).status = "ARCHIVED"
        session.commit()
        replay = service.bind(strategy_version_id=version_id, trial_id=STOCK_TRIAL,
                              asset_scope="CN_STOCK")
        assert replay.bound_at == first.bound_at
        assert session.scalar(select(TargetTrialBinding).where(
            TargetTrialBinding.strategy_version_id == version_id)).trial_id == STOCK_TRIAL


def test_binding_rejects_unpublished_wrong_scope_unknown_and_single_symbol(env):
    factory = env["session_factory"]
    published = seed(factory)
    draft = seed(factory, status="DRAFT")
    with factory() as session:
        service = TargetTrialBindingService(session)
        with pytest.raises(ValueError, match="published"):
            service.bind(strategy_version_id=draft, trial_id=STOCK_TRIAL,
                         asset_scope="CN_STOCK")
        with pytest.raises(ValueError, match="scope"):
            service.bind(strategy_version_id=published, trial_id=ETF_TRIAL,
                         asset_scope="CN_STOCK")
        with pytest.raises(ValueError, match="preregistered"):
            service.bind(strategy_version_id=published, trial_id="invented",
                         asset_scope="CN_STOCK")
        with pytest.raises(ValueError, match="portfolio trial"):
            service.bind(strategy_version_id=published,
                         trial_id="ma_trend_cross_v1:BASELINE:FAMILY_POLICY",
                         asset_scope="CN_STOCK")
        assert session.get(TargetTrialBinding, published) is None


def test_etf_trial_scope_is_frozen_without_creating_admission(env):
    factory = env["session_factory"]
    version_id = seed(factory)
    with factory() as session:
        binding = TargetTrialBindingService(session).bind(
            strategy_version_id=version_id, trial_id=ETF_TRIAL, asset_scope="CN_ETF")
        session.commit()
        assert binding.family_id == "etf_dual_momentum"
        assert binding.asset_scope == "CN_ETF"
        assert binding.management_config["policy_id"] == "ETF_DUAL_MOMENTUM_3ATR"
        assert session.execute(text("SELECT count(*) FROM strategy_admission_events "
            "WHERE strategy_version_id=:id"), {"id": version_id}).scalar_one() == 0


def test_binding_rejects_source_drift_and_changed_trial(env):
    factory = env["session_factory"]
    version_id = seed(factory)
    with factory() as session:
        service = TargetTrialBindingService(session)
        first = service.bind(strategy_version_id=version_id, trial_id=STOCK_TRIAL,
                             asset_scope="CN_STOCK")
        session.commit()
        with pytest.raises(ValueError, match="different trial content"):
            service.bind(strategy_version_id=version_id, trial_id=OTHER_STOCK_TRIAL,
                         asset_scope="CN_STOCK")
        version = session.get(QuantStrategyVersion, version_id)
        version.source_code += "# changed\n"
        session.flush()
        with pytest.raises(ValueError, match="source hash mismatch"):
            service.bind(strategy_version_id=version_id, trial_id=STOCK_TRIAL,
                         asset_scope="CN_STOCK")
        session.rollback()
        assert session.get(TargetTrialBinding, version_id).trial_id == first.trial_id


def test_binding_rows_are_immutable_in_database(env):
    factory = env["session_factory"]
    version_id = seed(factory)
    with factory() as session:
        TargetTrialBindingService(session).bind(strategy_version_id=version_id,
            trial_id=STOCK_TRIAL, asset_scope="CN_STOCK")
        session.commit()
        for statement in (
            "UPDATE quant_target_trial_bindings SET trial_id='changed' WHERE strategy_version_id=:id",
            "DELETE FROM quant_target_trial_bindings WHERE strategy_version_id=:id",
        ):
            with pytest.raises(DBAPIError, match="immutable"):
                with session.begin_nested():
                    session.execute(text(statement), {"id": version_id})
        assert session.get(TargetTrialBinding, version_id).trial_id == STOCK_TRIAL


def test_competing_bindings_cannot_claim_one_version(env):
    factory = env["session_factory"]
    version_id = seed(factory)
    barrier = Barrier(2)

    def bind(trial_id):
        with factory() as session:
            barrier.wait(timeout=10)
            try:
                TargetTrialBindingService(session).bind(strategy_version_id=version_id,
                    trial_id=trial_id, asset_scope="CN_STOCK")
                session.commit()
                return "bound"
            except ValueError:
                session.rollback()
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(bind, (STOCK_TRIAL, OTHER_STOCK_TRIAL)))
    assert sorted(outcomes) == ["bound", "conflict"]
    with factory() as session:
        assert session.scalar(select(TargetTrialBinding).where(
            TargetTrialBinding.strategy_version_id == version_id)).trial_id in (
                STOCK_TRIAL, OTHER_STOCK_TRIAL)
