# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle_resolved_accounting（持仓生命周期）：Pure effective-fill accounting; no database or broker access.",
#   "keywords": [
#     "量化策略",
#     "执行",
#     "成交",
#     "持仓生命周期",
#     "收益",
#     "lifecycle_resolved_accounting",
#     "execution",
#     "fill",
#     "lifecycle",
#     "returns"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/lifecycle_accounting_replay.py",
#     "backend/modules/quant_strategy/application/lifecycle_fill_resolution_chain.py",
#     "backend/modules/quant_strategy/application/lifecycle_resolved_accounting.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Pure effective-fill accounting; no database or broker access."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from backend.modules.quant_strategy.application.lifecycle_accounting_replay import ReplayFill
from backend.modules.quant_strategy.application.lifecycle_fill_resolution_chain import (
    AppliedFillResolution, PostedReportFill,
)
from backend.modules.quant_strategy.application.lifecycle_resolved_accounting import (
    PostedReplayFill, replay_resolved_accounting,
)


CN = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 9, 21)
BASELINE = datetime(2026, 9, 20, 12, tzinfo=CN)


def _posted(hour, minute, side, quantity, price, fee):
    report_id, event_id = uuid4(), uuid4()
    return (PostedReportFill(report_id, event_id),
            PostedReplayFill(report_id, ReplayFill(
                event_id, datetime(2026, 9, 21, hour, minute, tzinfo=CN),
                DAY, side, Decimal(quantity), Decimal(price), Decimal(fee),
                f"fixture:report:{report_id}")))


def _replay(postings, resolutions=(), payloads=None):
    return replay_resolved_accounting(
        postings=tuple(item[0] for item in postings),
        resolutions=resolutions,
        payloads=(tuple(item[1] for item in postings)
                  if payloads is None else payloads),
        baseline_as_of=BASELINE, baseline_quantity=Decimal(0),
        baseline_total_cost=Decimal(0), baseline_source_ref="fixture:opening-cost",
    )


def test_multistep_correction_uses_only_new_final_event_without_mutating_origins():
    buy = _posted(9, 30, "BUY", "100", "10", "1")
    original = _posted(10, 0, "SELL", "40", "12", "1")
    middle = _posted(10, 5, "SELL", "35", "13", "1")
    final = _posted(10, 10, "SELL", "30", "14", "1")
    postings = (buy, original, middle, final)
    snapshot = tuple(item[1] for item in postings)
    revisions = (
        AppliedFillResolution(uuid4(), original[0].report_id, "CORRECT",
                              middle[0].report_id, True),
        AppliedFillResolution(uuid4(), middle[0].report_id, "CORRECT",
                              final[0].report_id, True),
    )

    result = _replay(postings, revisions, payloads=tuple(reversed(snapshot)))

    assert result.status == "PROVISIONAL_ACCOUNTING"
    assert result.accounting is not None
    assert result.effective_fill_event_ids == (
        buy[0].fill_event_id, final[0].fill_event_id)
    assert result.superseded_fill_event_ids == (
        original[0].fill_event_id, middle[0].fill_event_id)
    assert result.accounting.event_ids == result.effective_fill_event_ids
    assert (result.accounting.quantity, result.accounting.total_cost,
            result.accounting.average_cost, result.accounting.realized_pnl) == (
                Decimal(70), Decimal("700.7"), Decimal("10.01"),
                Decimal("118.7"))
    assert final[0].fill_event_id not in {
        original[0].fill_event_id, middle[0].fill_event_id}
    assert tuple(item[1] for item in postings) == snapshot
    assert "LOCAL_CAUSAL_HISTORY_NOT_REPLAYED" in result.issues
    assert "HISTORICAL_VISIBILITY_UNCERTIFIED" in result.issues


def test_void_all_returns_only_baseline_accounting_not_a_negative_fill():
    buy = _posted(9, 30, "BUY", "100", "10", "1")
    result = _replay((buy,), (AppliedFillResolution(
        uuid4(), buy[0].report_id, "VOID", None, True),))

    assert result.status == "PROVISIONAL_ACCOUNTING"
    assert result.accounting is not None
    assert result.effective_fill_event_ids == ()
    assert result.superseded_fill_event_ids == (buy[0].fill_event_id,)
    assert result.accounting.event_ids == ()
    assert (result.accounting.quantity, result.accounting.total_cost,
            result.accounting.realized_pnl) == (
                Decimal(0), Decimal(0), Decimal(0))


def test_effective_ids_follow_execution_time_when_postings_are_reversed():
    buy = _posted(9, 30, "BUY", "100", "10", "1")
    sell = _posted(10, 0, "SELL", "30", "11", "1")

    result = _replay((sell, buy))

    assert result.status == "PROVISIONAL_ACCOUNTING"
    assert result.accounting is not None
    assert result.effective_fill_event_ids == (
        buy[0].fill_event_id, sell[0].fill_event_id)
    assert result.effective_fill_event_ids == result.accounting.event_ids


def test_payload_must_cover_exact_report_and_fill_identity_sets():
    buy = _posted(9, 30, "BUY", "100", "10", "1")
    sell = _posted(10, 0, "SELL", "30", "11", "1")
    extra = _posted(11, 0, "BUY", "1", "10", "0")
    cases = (
        (buy[1],),
        (buy[1], sell[1], extra[1]),
        (buy[1], buy[1]),
        (buy[1], replace(sell[1], fill=replace(
            sell[1].fill, event_id=uuid4()))),
    )
    for payloads in cases:
        result = _replay((buy, sell), payloads=payloads)
        assert result.status == "UNKNOWN"
        assert result.accounting is None
        assert result.effective_fill_event_ids == ()
        assert result.superseded_fill_event_ids == ()
        assert "RESOLVED_FILL_PAYLOAD_SET_MISMATCH" in result.issues


def test_unapplied_resolution_same_execution_time_and_oversell_stay_unknown():
    buy = _posted(9, 30, "BUY", "100", "10", "1")
    sell = _posted(9, 30, "SELL", "30", "11", "1")
    pending = _replay((buy, sell), (AppliedFillResolution(
        uuid4(), buy[0].report_id, "VOID", None, False),))
    assert pending.status == "UNKNOWN" and pending.accounting is None
    assert "FILL_RESOLUTION_IDENTITY_INVALID" in pending.issues

    same_time = _replay((buy, sell))
    assert same_time.status == "UNKNOWN" and same_time.accounting is None
    assert any(issue.startswith("EVENT_ORDER_AMBIGUOUS:")
               for issue in same_time.issues)

    oversell = _posted(10, 0, "SELL", "101", "11", "1")
    invalid = _replay((buy, oversell))
    assert invalid.status == "UNKNOWN" and invalid.accounting is None
    assert f"SELL_EXCEEDS_REPLAY_HOLDING:{oversell[0].fill_event_id}" in invalid.issues
