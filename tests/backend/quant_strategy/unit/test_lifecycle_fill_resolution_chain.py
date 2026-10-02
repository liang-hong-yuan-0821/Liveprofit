# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle_fill_resolution_chain（持仓生命周期、成交）",
#   "keywords": [
#     "量化策略",
#     "成交",
#     "持仓生命周期",
#     "lifecycle_fill_resolution_chain",
#     "fill",
#     "lifecycle"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/lifecycle_fill_resolution_chain.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

from dataclasses import replace
from uuid import uuid4

from backend.modules.quant_strategy.application.lifecycle_fill_resolution_chain import (
    AppliedFillResolution, PostedReportFill, resolve_effective_fill_chain,
)


def test_effective_fill_chain_accepts_linear_correction_then_void():
    first, replacement, other = (PostedReportFill(uuid4(), uuid4()) for _ in range(3))
    corrected = AppliedFillResolution(uuid4(), first.report_id, "CORRECT",
                                      replacement.report_id, True)
    voided = AppliedFillResolution(uuid4(), other.report_id, "VOID", None, True)
    result = resolve_effective_fill_chain(
        postings=(first, replacement, other), resolutions=(corrected, voided))
    assert result.status == "PROVISIONAL"
    assert result.effective_fill_event_ids == (replacement.fill_event_id,)
    assert result.report_paths == ((first.report_id, replacement.report_id),
                                   (other.report_id,))
    assert "FILL_RESOLUTION_SOURCE_UNCERTIFIED" in result.issues

    pending = resolve_effective_fill_chain(
        postings=(first, replacement),
        resolutions=(replace(corrected, applied=False),))
    assert pending.status == "UNKNOWN" and pending.effective_fill_event_ids == ()


def test_effective_fill_chain_rejects_missing_replacement_cycle_and_branch():
    first, second, third = (PostedReportFill(uuid4(), uuid4()) for _ in range(3))
    correction = AppliedFillResolution(uuid4(), first.report_id, "CORRECT",
                                       second.report_id, True)
    missing = resolve_effective_fill_chain(
        postings=(first,), resolutions=(correction,))
    assert missing.status == "UNKNOWN"
    cycle = resolve_effective_fill_chain(
        postings=(first, second), resolutions=(correction, AppliedFillResolution(
            uuid4(), second.report_id, "CORRECT", first.report_id, True)))
    assert cycle.status == "UNKNOWN"
    assert "FILL_RESOLUTION_ORPHAN_OR_CYCLE" in cycle.issues
    branched = resolve_effective_fill_chain(
        postings=(first, second, third), resolutions=(correction, AppliedFillResolution(
            uuid4(), third.report_id, "CORRECT", second.report_id, True)))
    assert branched.status == "UNKNOWN"
    assert "FILL_RESOLUTION_REPLACEMENT_AMBIGUOUS" in branched.issues
