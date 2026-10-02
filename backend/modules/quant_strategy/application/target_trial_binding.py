"""Bind one published version to an exact preregistered portfolio trial.

This records identity only. It never creates a target, admission or order.
"""
from dataclasses import asdict
import hashlib
import json

from sqlalchemy import select

from backend.modules.quant_research.application.trial_executor import prepare_trial
from backend.modules.quant_research.domain.trial_registry import preregistered_trials
from backend.modules.quant_strategy.application.portfolio_policy_contract import (
    read_portfolio_policy_version,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_models import LifecyclePolicyVersion
from backend.modules.quant_strategy.infrastructure.models import QuantStrategyVersion
from backend.modules.quant_strategy.infrastructure.target_binding_models import TargetTrialBinding


FAMILY_SCOPE = {
    "stock_medium_momentum": "CN_STOCK",
    "stock_short_reversion": "CN_STOCK",
    "etf_dual_momentum": "CN_ETF",
    "etf_defensive_allocation": "CN_ETF",
}
PORTFOLIO_POLICY_KEYS = frozenset(
    spec.management_policy for spec in preregistered_trials() if spec.family in FAMILY_SCOPE
)


class TargetTrialBindingService:
    def __init__(self, session):
        self.session = session

    def bind(self, *, strategy_version_id, trial_id: str, asset_scope: str) -> TargetTrialBinding:
        """Caller owns transaction; lock the version before checking or inserting.

        This association must later be combined with independent evidence for
        candidate completeness, lifecycle policy consistency and admission.
        """
        version = self.session.scalar(select(QuantStrategyVersion).where(
            QuantStrategyVersion.id == strategy_version_id).with_for_update()
            .execution_options(populate_existing=True))
        if version is None:
            raise ValueError("strategy version not found")
        if (not isinstance(version.source_code, str)
                or hashlib.sha256(version.source_code.encode("utf-8")).hexdigest() != version.source_hash):
            raise ValueError("strategy source hash mismatch")
        prepared = prepare_trial(trial_id)
        expected_scope = FAMILY_SCOPE.get(prepared.spec.family)
        if prepared.source_code is not None or expected_scope is None:
            raise ValueError("portfolio trial required")
        if asset_scope != expected_scope:
            raise ValueError("trial asset scope mismatch")
        snapshot = json.loads(json.dumps(asdict(prepared.spec)))
        management = json.loads(prepared.management_config_json)
        previous = self.session.get(TargetTrialBinding, strategy_version_id)
        values = dict(
            trial_id=prepared.spec.trial_id,
            definition_hash=prepared.definition_hash,
            family_id=prepared.spec.family,
            asset_scope=expected_scope,
            scanner_source_hash=version.source_hash,
            lifecycle_policy_version_id=version.lifecycle_policy_version_id,
            trial_spec=snapshot,
            management_config=management,
        )
        if previous is not None:
            if any(getattr(previous, key) != value for key, value in values.items()):
                raise ValueError("strategy version already bound to different trial content")
            return previous
        if version.status != "PUBLISHED" or version.archived_at is not None:
            raise ValueError("only a published strategy version can be bound")
        binding = TargetTrialBinding(strategy_version_id=strategy_version_id, **values)
        self.session.add(binding)
        self.session.flush()
        return binding

    def audit_portfolio_policy(self, *, strategy_version_id):
        """Prove stored trial/policy equality, without granting a target or admission.

        Callers that make a joint execution decision must already hold the
        portfolio lock, then keep this transaction open through that decision.
        """
        version = self.session.scalar(select(QuantStrategyVersion).where(
            QuantStrategyVersion.id == strategy_version_id).with_for_update(read=True)
            .execution_options(populate_existing=True))
        if (version is None or version.status != "PUBLISHED" or version.archived_at is not None
                or version.lifecycle_policy_version_id is None
                or not isinstance(version.source_code, str)
                or hashlib.sha256(version.source_code.encode("utf-8")).hexdigest() != version.source_hash):
            raise ValueError("published strategy and frozen lifecycle policy required")
        binding = self.session.scalar(select(TargetTrialBinding).where(
            TargetTrialBinding.strategy_version_id == strategy_version_id)
            .execution_options(populate_existing=True))
        if binding is None:
            raise ValueError("frozen portfolio trial binding required")
        prepared = prepare_trial(binding.trial_id)
        scope = FAMILY_SCOPE.get(prepared.spec.family)
        if (prepared.source_code is not None or scope is None
                or binding.definition_hash != prepared.definition_hash
                or binding.family_id != prepared.spec.family or binding.asset_scope != scope
                or binding.scanner_source_hash != version.source_hash
                or binding.lifecycle_policy_version_id != version.lifecycle_policy_version_id
                or binding.trial_spec != json.loads(json.dumps(asdict(prepared.spec)))
                or binding.management_config != json.loads(prepared.management_config_json)):
            raise ValueError("frozen portfolio trial binding content mismatch")
        policy = self.session.scalar(select(LifecyclePolicyVersion).where(
            LifecyclePolicyVersion.id == version.lifecycle_policy_version_id)
            .with_for_update(read=True).execution_options(populate_existing=True))
        return read_portfolio_policy_version(policy, prepared)

    def audit_target_intent(self, *, intent, asset_scope: str):
        """Require a target's trial identity to match its bound version and policy.

        The caller holds the portfolio lock. A successful audit is still only
        identity evidence; source coverage and admission are separate gates.
        """
        version = self.session.scalar(select(QuantStrategyVersion).where(
            QuantStrategyVersion.id == intent.strategy_version_id).with_for_update(read=True)
            .execution_options(populate_existing=True))
        if version is None:
            raise ValueError("frozen target strategy version missing")
        binding = self.session.scalar(select(TargetTrialBinding).where(
            TargetTrialBinding.strategy_version_id == intent.strategy_version_id)
            .execution_options(populate_existing=True))
        if binding is None:
            if intent.trial_id is not None or intent.definition_hash is not None:
                raise ValueError("trial target has no frozen version binding")
            if intent.policy_id in PORTFOLIO_POLICY_KEYS:
                raise ValueError("portfolio policy requires frozen trial binding")
            if version.lifecycle_policy_version_id is not None:
                policy = self.session.scalar(select(LifecyclePolicyVersion).where(
                    LifecyclePolicyVersion.id == version.lifecycle_policy_version_id)
                    .with_for_update(read=True).execution_options(populate_existing=True))
                if policy is None or policy.policy_key in PORTFOLIO_POLICY_KEYS or (
                    isinstance(policy.config, dict) and "portfolio_trial" in policy.config
                ):
                    raise ValueError("portfolio policy requires frozen trial binding")
            return None
        if (intent.trial_id != binding.trial_id
                or intent.definition_hash != binding.definition_hash
                or intent.family_id != binding.family_id
                or asset_scope != binding.asset_scope
                or intent.policy_id != binding.management_config.get("policy_id")):
            raise ValueError("portfolio target differs from frozen trial binding")
        return self.audit_portfolio_policy(strategy_version_id=intent.strategy_version_id)
