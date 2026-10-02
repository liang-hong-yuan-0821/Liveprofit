# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle_version_chain（持仓生命周期）",
#   "keywords": [
#     "量化策略",
#     "重复请求",
#     "成交",
#     "持仓生命周期",
#     "lifecycle_version_chain",
#     "duplicate",
#     "fill",
#     "lifecycle"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/lifecycle_version_chain.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from dataclasses import replace
from datetime import date
from uuid import uuid4

from backend.modules.quant_strategy.application.lifecycle_version_chain import (
    DailyVersionStep, FillVersionStep, reconcile_local_version_chain,
)


def test_version_chain_attributes_between_day_fill_without_timestamps():
    first, second = date(2026, 9, 21), date(2026, 9, 22)
    later_fill = FillVersionStep(uuid4(), 1, 2)
    result = reconcile_local_version_chain(
        fills=(later_fill,),
        days=(DailyVersionStep(first, 1, 1), DailyVersionStep(second, 2, 3)),
        current_version=3)
    assert result.status == "PROVISIONAL"
    assert result.daily_advances == (False, True)
    assert result.ending_version == 3
    assert "VERSION_CHAIN_SOURCE_UNCERTIFIED" in result.issues

    missing = reconcile_local_version_chain(
        fills=(), days=(DailyVersionStep(first, 1, 1),
                         DailyVersionStep(second, 2, 3)), current_version=3)
    assert missing.status == "UNKNOWN"
    assert f"VERSION_CHAIN_DAY_BOUNDARY_MISMATCH:{second}" in missing.issues
    collision = reconcile_local_version_chain(
        fills=(later_fill,),
        days=(DailyVersionStep(first, 1, 2),), current_version=2)
    assert collision.status == "UNKNOWN"
    assert "VERSION_CHAIN_GAP_AFTER_DAILY" in collision.issues


def test_version_chain_rejects_missing_duplicate_and_trailing_steps():
    first = date(2026, 9, 21)
    fill = FillVersionStep(uuid4(), 1, 2)
    common = dict(days=(DailyVersionStep(first, 2, 3),), current_version=3)
    assert reconcile_local_version_chain(fills=(fill,), **common).status == "PROVISIONAL"
    duplicate = reconcile_local_version_chain(fills=(fill, fill), **common)
    assert duplicate.status == "UNKNOWN"
    assert "VERSION_CHAIN_FILL_INVALID" in duplicate.issues
    gap = reconcile_local_version_chain(fills=(replace(fill, before=9, after=10),), **common)
    assert gap.status == "UNKNOWN"
    after = reconcile_local_version_chain(fills=(fill,), days=common["days"],
                                          current_version=4)
    assert after.status == "UNKNOWN"
    assert "VERSION_CHAIN_CURRENT_MISMATCH" in after.issues
