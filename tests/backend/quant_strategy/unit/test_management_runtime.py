# test-catalog-begin
# {
#   "purpose": "量化策略 / management_runtime",
#   "keywords": [
#     "量化策略",
#     "规划器",
#     "来源证据",
#     "management_runtime",
#     "planner",
#     "source"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/management_runtime.py",
#     "backend/modules/quant_strategy/application/position_planner.py",
#     "backend/modules/quant_strategy/domain/family_management.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.modules.quant_strategy.application.management_runtime import (
    resolve_management_snapshot, adapt_management_output,
)
from backend.modules.quant_strategy.domain.family_management import TREND_3ATR, MACD_MEAN_REVERSION


def snapshot(policy=TREND_3ATR, template="ma_trend_cross_v1"):
    config = {"management_policy": policy.to_config()}
    digest = hashlib.sha256(json.dumps({"config": config, "required_fields": []},
                            sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return {"template_id": template, "lifecycle_policy": {
        "policy_key": policy.policy_id, "config": config, "required_fields": [], "content_hash": digest,
    }}


def test_only_valid_frozen_matching_policy_authorizes_null_target():
    assert resolve_management_snapshot(snapshot()) == TREND_3ATR
    assert resolve_management_snapshot({}) is None
    bad = snapshot()
    bad["lifecycle_policy"]["config"]["management_policy"]["trailing_atr_multiple"] = "2"
    with pytest.raises(ValueError):
        resolve_management_snapshot(bad)
    with pytest.raises(ValueError):
        resolve_management_snapshot(snapshot(template="macd_rsi_reversal_v1"))
    bad = snapshot()
    bad["lifecycle_policy"]["content_hash"] = "wrong"
    with pytest.raises(ValueError):
        resolve_management_snapshot(bad)


BUY = dict(action="BUY", score=80, entry_price=10, stop_loss=9,
           take_profit=12, sell_ratio=None, reason="entry")


def test_host_removes_target_and_requires_atr_without_mutating_source_output():
    result = adapt_management_output(BUY, TREND_3ATR, {}, {"atr_qfq": .5})
    assert result["take_profit"] is None and BUY["take_profit"] == 12
    assert adapt_management_output(BUY, TREND_3ATR, {}, {})["action"] == "HOLD"


@pytest.mark.parametrize("ma,entry,expected", [(11, 10, "BUY"), (10, 10, "HOLD"),
                                              (11, 12, "HOLD"), (None, 10, "HOLD")])
def test_macd_already_exiting_cannot_enter(ma, entry, expected):
    result = adapt_management_output({**BUY, "entry_price": entry}, MACD_MEAN_REVERSION,
                                     {"indicators": {"ma_qfq_20": [ma]}, "ohlcv": {"close": [10]}}, {})
    assert result["action"] == expected and result["take_profit"] is None


def test_null_target_planner_rejects_without_exception_or_fabricated_rr():
    from backend.modules.quant_strategy.application.position_planner import PositionPlanner
    planner = PositionPlanner.__new__(PositionPlanner)
    planner._reject = Mock()
    signal = SimpleNamespace(entry_price=10, stop_loss=9, take_profit=None)
    assert planner._plan_buy(signal, Mock(), total_assets=Decimal(10000)) == (False, None, Decimal(0))
    planner._reject.assert_called_once()
