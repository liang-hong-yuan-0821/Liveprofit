"""Frozen portfolio holding policy identity; never grants trading admission."""
from dataclasses import asdict
import hashlib
import json

from backend.modules.quant_research.application.trial_executor import (
    PreparedTrial, holding_policy_for_trial, prepare_trial,
)
from backend.modules.quant_strategy.domain.portfolio_management import PortfolioHoldingPolicy


def portfolio_policy_config(prepared: PreparedTrial) -> dict:
    """Derive one persisted exit policy from a preregistered portfolio trial."""
    policy = holding_policy_for_trial(prepared)
    return {"portfolio_trial": {
        "trial_id": prepared.spec.trial_id,
        "definition_hash": prepared.definition_hash,
        "holding_policy": asdict(policy),
    }}


def validate_portfolio_policy_config(policy_key: str, required_fields: list,
                                     config: dict) -> PreparedTrial:
    if (not isinstance(config, dict) or set(config) != {"portfolio_trial"}
            or required_fields != [] or not isinstance(config["portfolio_trial"], dict)):
        raise ValueError("portfolio trial policy envelope is invalid")
    trial_id = config["portfolio_trial"].get("trial_id")
    if not isinstance(trial_id, str):
        raise ValueError("portfolio trial policy identity is invalid")
    prepared = prepare_trial(trial_id)
    if (prepared.source_code is not None or policy_key != prepared.spec.management_policy
            or config != portfolio_policy_config(prepared)):
        raise ValueError("portfolio trial policy differs from preregistered exit semantics")
    return prepared


def read_portfolio_policy_version(version, prepared: PreparedTrial) -> PortfolioHoldingPolicy:
    """Check a stored policy row, including its ledger digest, against one trial."""
    expected_config = portfolio_policy_config(prepared)
    if (version is None or version.status != "PUBLISHED"
            or version.policy_key != prepared.spec.management_policy
            or version.required_fields != [] or version.config != expected_config):
        raise ValueError("lifecycle policy does not match frozen portfolio trial")
    canonical = json.dumps({"required_fields": [], "config": expected_config},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if version.content_hash != hashlib.sha256(canonical.encode("utf-8")).hexdigest():
        raise ValueError("lifecycle policy content hash mismatch")
    return holding_policy_for_trial(prepared)
