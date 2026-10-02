# test-catalog-begin
# {
#   "purpose": "量化策略 / portfolio_policy_contract：Frozen portfolio policies must match preregistered exits exactly.",
#   "keywords": [
#     "量化策略",
#     "策略族",
#     "投资组合",
#     "portfolio_policy_contract",
#     "family",
#     "portfolio"
#   ],
#   "covers": [
#     "backend/modules/quant_research/application/trial_executor.py",
#     "backend/modules/quant_strategy/application/portfolio_policy_contract.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Frozen portfolio policies must match preregistered exits exactly."""
from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from backend.modules.quant_research.application.trial_executor import prepare_trial
from backend.modules.quant_strategy.application.portfolio_policy_contract import (
    portfolio_policy_config, read_portfolio_policy_version,
    validate_portfolio_policy_config,
)


TRIALS = (
    "stock_medium_momentum:60:10:60",
    "stock_short_reversion:2:1.5:3",
    "etf_dual_momentum:60:1:WEEKLY",
    "etf_defensive_allocation:20:0.04:60",
)
VARIANTS = (
    "stock_medium_momentum:120:20:200",
    "stock_short_reversion:3:2:5",
    "etf_dual_momentum:180:2:MONTHLY",
    "etf_defensive_allocation:60:0.08:120",
)


def row(prepared, config):
    payload = {"required_fields": [], "config": config}
    digest = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False).encode()).hexdigest()
    return SimpleNamespace(status="PUBLISHED", policy_key=prepared.spec.management_policy,
                           required_fields=[], config=config, content_hash=digest)


@pytest.mark.parametrize("trial_id", TRIALS)
def test_exact_four_family_policy_roundtrip(trial_id):
    prepared = prepare_trial(trial_id)
    config = portfolio_policy_config(prepared)
    assert validate_portfolio_policy_config(prepared.spec.management_policy, [], config) == prepared
    assert read_portfolio_policy_version(row(prepared, config), prepared).family == prepared.spec.family


@pytest.mark.parametrize("first_id,second_id", tuple(zip(TRIALS, VARIANTS)))
def test_same_family_parameter_variants_have_distinct_frozen_policies(first_id, second_id):
    first, second = prepare_trial(first_id), prepare_trial(second_id)
    assert first.spec.management_policy == second.spec.management_policy
    assert first.definition_hash != second.definition_hash
    assert portfolio_policy_config(first) != portfolio_policy_config(second)
    with pytest.raises(ValueError, match="does not match"):
        read_portfolio_policy_version(row(first, portfolio_policy_config(first)), second)


def test_exit_parameter_drift_and_digest_tampering_fail_closed():
    prepared = prepare_trial(TRIALS[0])
    config = portfolio_policy_config(prepared)
    drifted = deepcopy(config)
    drifted["portfolio_trial"]["holding_policy"]["trend_ma"] = 120
    with pytest.raises(ValueError, match="differs"):
        validate_portfolio_policy_config(prepared.spec.management_policy, [], drifted)
    with pytest.raises(ValueError, match="does not match"):
        read_portfolio_policy_version(row(prepared, drifted), prepared)
    corrupt = row(prepared, config)
    corrupt.content_hash = "0" * 64
    with pytest.raises(ValueError, match="hash mismatch"):
        read_portfolio_policy_version(corrupt, prepared)
    with pytest.raises(ValueError, match="envelope"):
        validate_portfolio_policy_config(prepared.spec.management_policy, ["close"], config)
