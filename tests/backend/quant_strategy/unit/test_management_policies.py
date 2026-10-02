# test-catalog-begin
# {
#   "purpose": "量化策略 / management_policies",
#   "keywords": [
#     "量化策略",
#     "交易信号",
#     "止损",
#     "management_policies",
#     "signal",
#     "stop"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/domain/management_policies.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from backend.modules.quant_strategy.domain.management_policies import (
    ExitPolicyKind,
    InitialStopRule,
    ManagementPolicy,
    StopMode,
)

STOP = InitialStopRule(StopMode.FRACTION, Decimal("0.06"))


def test_three_exit_kinds_have_explicit_protection_and_null_target_contract():
    fixed = ManagementPolicy("CORRECTED_LEGACY", ExitPolicyKind.FIXED_TARGET, STOP,
                             fixed_target_r=Decimal("2.5"))
    trailing = ManagementPolicy("TREND_3ATR", ExitPolicyKind.TRAILING, STOP,
                                trailing_atr_multiple=Decimal(3), initial_exposure=Decimal("0.5"),
                                add_at_r=Decimal(1), max_adds=1)
    rule = ManagementPolicy("REVERSION_MA5_H5", ExitPolicyKind.RULE_BASED, STOP,
                            rule_exit_conditions=("CLOSE_GE_MA5", "INITIAL_STOP", "TIMEOUT"),
                            max_holding_sessions=5)
    assert not fixed.permits_null_take_profit
    assert trailing.permits_null_take_profit and rule.permits_null_take_profit
    with pytest.raises(FrozenInstanceError):
        trailing.max_adds = 2


@pytest.mark.parametrize("kwargs", [
    {"kind": ExitPolicyKind.FIXED_TARGET},
    {"kind": ExitPolicyKind.TRAILING},
    {"kind": ExitPolicyKind.RULE_BASED},
    {"kind": ExitPolicyKind.RULE_BASED, "rule_exit_conditions": ("",)},
    {"kind": ExitPolicyKind.FIXED_TARGET, "fixed_target_r": Decimal(2), "max_adds": 1},
])
def test_incomplete_exit_policy_is_rejected(kwargs):
    with pytest.raises(ValueError):
        ManagementPolicy(policy_id="invalid", initial_stop=STOP, **kwargs)


def test_invalid_initial_stop_cannot_be_reinterpreted_as_two_r_target():
    with pytest.raises(ValueError):
        InitialStopRule(StopMode.FRACTION, Decimal("1.1"))
    with pytest.raises(ValueError):
        ManagementPolicy("fake", ExitPolicyKind.FIXED_TARGET, STOP,
                         fixed_target_r=Decimal("NaN"))


def test_entry_signal_stop_has_no_fabricated_numeric_value():
    inherited = InitialStopRule(StopMode.ENTRY_SIGNAL, None)
    policy = ManagementPolicy("trend", ExitPolicyKind.TRAILING, inherited,
                              trailing_atr_multiple=Decimal(3))
    assert policy.to_config()["initial_stop"] == {"mode": "ENTRY_SIGNAL", "value": None}
    assert ManagementPolicy.from_config(policy.to_config()) == policy
    with pytest.raises(ValueError):
        InitialStopRule(StopMode.ENTRY_SIGNAL, Decimal(1))
    with pytest.raises(TypeError):
        InitialStopRule(StopMode.FRACTION, None)


@pytest.mark.parametrize("policy", [
    ManagementPolicy("fixed", ExitPolicyKind.FIXED_TARGET, STOP, fixed_target_r=Decimal("2.50")),
    ManagementPolicy("trail", ExitPolicyKind.TRAILING, STOP, trailing_atr_multiple=Decimal("3.0"),
                     initial_exposure=Decimal("0.50"), add_at_r=Decimal("1.25"), max_adds=1),
    ManagementPolicy("rule", ExitPolicyKind.RULE_BASED, STOP,
                     rule_exit_conditions=("CLOSE_GE_MA5", "TIMEOUT"), max_holding_sessions=5),
])
def test_management_policy_json_round_trip_preserves_exact_values(policy):
    import json

    config = json.loads(json.dumps(policy.to_config()))
    assert ManagementPolicy.from_config(config) == policy


def test_management_policy_config_rejects_unknown_fields_and_numeric_json():
    config = ManagementPolicy("fixed", ExitPolicyKind.FIXED_TARGET, STOP,
                              fixed_target_r=Decimal("2.5")).to_config()
    config["unexpected"] = True
    with pytest.raises(ValueError):
        ManagementPolicy.from_config(config)
    del config["unexpected"]
    config["initial_stop"]["value"] = 0.06
    with pytest.raises(ValueError):
        ManagementPolicy.from_config(config)
