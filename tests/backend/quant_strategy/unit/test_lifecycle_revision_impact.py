# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle_revision_impact（持仓生命周期、版本修订）：Pure revision path ownership and earliest impact diagnostics.",
#   "keywords": [
#     "量化策略",
#     "执行",
#     "分析图",
#     "交易意图",
#     "持仓生命周期",
#     "订单",
#     "收益",
#     "版本修订",
#     "lifecycle_revision_impact",
#     "execution",
#     "graph",
#     "intent",
#     "lifecycle",
#     "order",
#     "returns",
#     "revision"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/lifecycle_fill_resolution_chain.py",
#     "backend/modules/quant_strategy/application/lifecycle_revision_impact.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Pure revision path ownership and earliest impact diagnostics."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from backend.modules.quant_strategy.application.lifecycle_fill_resolution_chain import (
    AppliedFillResolution, PostedReportFill, resolve_effective_fill_chain,
)
from backend.modules.quant_strategy.application.lifecycle_revision_impact import (
    FrozenReportFill, classify_lifecycle_revision_impact,
)


CN = ZoneInfo("Asia/Shanghai")
LIFECYCLE = uuid4()
OTHER_LIFECYCLE = uuid4()
DAY = date(2026, 9, 21)


def _fill(day=21, hour=9, minute=30, *, order=None, intent=None,
          lifecycle=LIFECYCLE, origin="LIVE"):
    report_id, event_id = uuid4(), uuid4()
    frozen = FrozenReportFill(
        report_id, event_id, datetime(2026, 9, day, hour, minute, tzinfo=CN),
        date(2026, 9, day), order or uuid4(), lifecycle, intent,
        origin,
    )
    return PostedReportFill(report_id, event_id), frozen


def _resolution(fill, action="VOID", replacement=None):
    return AppliedFillResolution(
        uuid4(), fill[0].report_id, action,
        replacement[0].report_id if replacement else None, True,
    )


def _classify(fills, resolutions=(), *, originals=None, initial=None,
              frozen=None):
    originals = (tuple(fill[0].fill_event_id for fill in fills)
                 if originals is None else originals)
    return classify_lifecycle_revision_impact(
        lifecycle_id=LIFECYCLE,
        initial_fill_id=fills[0][0].fill_event_id if initial is None else initial,
        original_fill_event_ids=originals,
        postings=tuple(fill[0] for fill in fills),
        resolutions=resolutions,
        frozen_fills=(tuple(fill[1] for fill in fills) if frozen is None else frozen),
    )


def test_no_revision_requires_complete_original_roots_and_returns_no_impact():
    initial = _fill(lifecycle=None)
    later = _fill(hour=10, intent=uuid4())

    result = _classify((initial, later))

    assert result.status == "NO_REVISION"
    assert result.paths == ()
    assert result.earliest_impact_at is None
    assert "BROKER_FILL_SET_UNCERTIFIED" in result.issues


def test_initial_anchor_multistep_correction_includes_backdated_terminal_time():
    order = uuid4()
    initial = _fill(day=22, hour=10, order=order, lifecycle=None)
    middle = _fill(day=23, hour=11, order=order)
    final = _fill(day=21, hour=9, order=order)
    first = _resolution(initial, "CORRECT", middle)
    second = _resolution(middle, "CORRECT", final)

    result = _classify(
        (initial, middle, final), (first, second),
        originals=(initial[0].fill_event_id,),
    )

    assert result.status == "LOCAL_IMPACT"
    assert len(result.paths) == 1
    path = result.paths[0]
    assert path.kind == "INITIAL_ANCHOR_CORRECT"
    assert path.root_report_id == initial[0].report_id
    assert path.root_fill_event_id == initial[0].fill_event_id
    assert path.report_ids == tuple(fill[0].report_id for fill in (initial, middle, final))
    assert tuple(item.resolution_id for item in path.declarations) == (
        first.resolution_id, second.resolution_id)
    assert tuple(item.action for item in path.declarations) == ("CORRECT", "CORRECT")
    assert path.terminal_report_id == final[0].report_id
    assert path.terminal_fill_event_id == final[0].fill_event_id
    assert path.earliest_executed_at == final[1].executed_at
    assert result.earliest_impact_at == final[1].executed_at
    assert "LOCAL_CAUSAL_HISTORY_NOT_REPLAYED" in result.issues


def test_initial_void_has_no_terminal_and_does_not_make_a_new_seed():
    initial = _fill(lifecycle=None)
    void = _resolution(initial)

    result = _classify((initial,), (void,))

    assert result.status == "LOCAL_IMPACT"
    assert result.paths[0].kind == "INITIAL_ANCHOR_VOID"
    assert result.paths[0].report_ids == (initial[0].report_id,)
    assert result.paths[0].declarations[0].action == "VOID"
    assert result.paths[0].terminal_report_id is None
    assert result.paths[0].terminal_fill_event_id is None


def test_void_path_before_effective_path_is_not_zipped_to_effective_event_ids():
    initial = _fill(lifecycle=None)
    voided = _fill(hour=10, intent=uuid4())
    original = _fill(hour=11, intent=uuid4())
    replacement = _fill(hour=12, order=original[1].order_id,
                        intent=original[1].intent_id_at_fill)
    void = _resolution(voided)
    correction = _resolution(original, "CORRECT", replacement)
    fills = (initial, voided, original, replacement)
    revisions = (void, correction)
    graph = resolve_effective_fill_chain(
        postings=tuple(fill[0] for fill in fills), resolutions=revisions)

    result = _classify(
        fills, revisions,
        originals=tuple(fill[0].fill_event_id for fill in fills[:3]),
    )

    assert graph.effective_fill_event_ids == (
        initial[0].fill_event_id, replacement[0].fill_event_id)
    assert len(graph.report_paths) == 3
    assert result.status == "LOCAL_IMPACT"
    assert tuple(path.kind for path in result.paths) == (
        "LATER_FILL_VOID", "LATER_FILL_CORRECT")
    assert result.paths[0].root_report_id == voided[0].report_id
    assert result.paths[0].terminal_fill_event_id is None
    assert result.paths[1].root_report_id == original[0].report_id
    assert result.paths[1].terminal_fill_event_id == replacement[0].fill_event_id
    assert result.earliest_impact_at == voided[1].executed_at

    reversed_result = _classify(
        tuple(reversed(fills)), revisions,
        originals=tuple(fill[0].fill_event_id for fill in fills[:3]),
        initial=initial[0].fill_event_id,
    )
    assert reversed_result.status == "LOCAL_IMPACT"
    assert {
        path.root_report_id: path.terminal_fill_event_id
        for path in reversed_result.paths
    } == {
        voided[0].report_id: None,
        original[0].report_id: replacement[0].fill_event_id,
    }


def test_correct_then_void_reports_complete_declarations_and_all_path_times():
    initial = _fill(lifecycle=None)
    order, intent = uuid4(), uuid4()
    later = _fill(day=23, hour=10, order=order, intent=intent)
    replacement = _fill(day=21, hour=11, order=order, intent=intent)
    correct = _resolution(later, "CORRECT", replacement)
    void = _resolution(replacement)

    result = _classify(
        (initial, later, replacement), (correct, void),
        originals=(initial[0].fill_event_id, later[0].fill_event_id),
    )

    assert result.status == "LOCAL_IMPACT"
    path = result.paths[0]
    assert path.kind == "LATER_FILL_VOID"
    assert path.report_ids == (
        later[0].report_id, replacement[0].report_id)
    assert tuple(item.action for item in path.declarations) == ("CORRECT", "VOID")
    assert path.terminal_report_id is None
    assert path.earliest_executed_at == replacement[1].executed_at


def test_later_replacement_must_keep_frozen_order_lifecycle_and_intent():
    initial = _fill(lifecycle=None)
    later = _fill(hour=10, intent=uuid4())
    replacement = _fill(hour=11, order=later[1].order_id,
                        intent=later[1].intent_id_at_fill)
    correction = _resolution(later, "CORRECT", replacement)
    original_ids = (initial[0].fill_event_id, later[0].fill_event_id)
    for changed, expected_issue in (
        (replace(replacement[1], order_id=uuid4()), "REVISION_ORDER_MIGRATION"),
        (replace(replacement[1], lifecycle_id_at_fill=OTHER_LIFECYCLE),
         "REVISION_LATER_BINDING_MIGRATION"),
        (replace(replacement[1], intent_id_at_fill=uuid4()),
         "REVISION_LATER_BINDING_MIGRATION"),
    ):
        result = _classify(
            (initial, later, replacement), (correction,),
            originals=original_ids,
            frozen=(initial[1], later[1], changed),
        )
        assert result.status == "UNKNOWN"
        assert result.paths == () and result.earliest_impact_at is None
        assert expected_issue in result.issues


def test_initial_foreign_lifecycle_and_missing_live_binding_fail_closed():
    initial = _fill(lifecycle=None)
    later = _fill(hour=10, intent=uuid4())
    cases = (
        ((replace(initial[1], lifecycle_id_at_fill=OTHER_LIFECYCLE), later[1]),
         "REVISION_INITIAL_ANCHOR_MISBOUND"),
        ((initial[1], replace(later[1], lifecycle_id_at_fill=OTHER_LIFECYCLE)),
         "REVISION_LATER_ROOT_BINDING_INVALID"),
        ((initial[1], replace(later[1], intent_id_at_fill=None)),
         "REVISION_LATER_ROOT_BINDING_INVALID"),
        ((initial[1], replace(later[1], binding_origin="MIGRATED")),
         "REVISION_FROZEN_FILL_INVALID"),
    )
    for frozen, issue in cases:
        result = _classify((initial, later), frozen=frozen)
        assert result.status == "UNKNOWN"
        assert result.paths == () and result.earliest_impact_at is None
        assert issue in result.issues

    replacement = _fill(hour=11, order=initial[1].order_id,
                        lifecycle=OTHER_LIFECYCLE)
    corrected = _classify(
        (initial, replacement), (_resolution(initial, "CORRECT", replacement),),
        originals=(initial[0].fill_event_id,),
    )
    assert corrected.status == "UNKNOWN" and corrected.paths == ()
    assert "REVISION_INITIAL_ANCHOR_MISBOUND" in corrected.issues


def test_original_roots_and_posted_frozen_identity_must_be_exact():
    initial = _fill(lifecycle=None)
    later = _fill(hour=10, intent=uuid4())
    replacement = _fill(hour=11, order=later[1].order_id,
                        intent=later[1].intent_id_at_fill)
    correction = _resolution(later, "CORRECT", replacement)
    fills = (initial, later, replacement)
    cases = (
        ((initial[0].fill_event_id,), None, "REVISION_ROOT_SET_MISMATCH"),
        ((initial[0].fill_event_id, later[0].fill_event_id,
          replacement[0].fill_event_id), None, "REVISION_ROOT_SET_MISMATCH"),
        ((initial[0].fill_event_id, later[0].fill_event_id,
          later[0].fill_event_id), None, "REVISION_ORIGINAL_FILL_SET_INVALID"),
        ((initial[0].fill_event_id, later[0].fill_event_id),
         (initial[1], later[1]), "REVISION_FROZEN_FILL_SET_MISMATCH"),
        ((initial[0].fill_event_id, later[0].fill_event_id),
         (initial[1], later[1], replace(replacement[1], fill_event_id=uuid4())),
         "REVISION_FROZEN_FILL_SET_MISMATCH"),
    )
    for originals, frozen, issue in cases:
        result = _classify(
            fills, (correction,), originals=originals,
            frozen=tuple(fill[1] for fill in fills) if frozen is None else frozen,
        )
        assert result.status == "UNKNOWN"
        assert result.paths == () and result.earliest_impact_at is None
        assert issue in result.issues


def test_replacement_cannot_also_be_an_original_root():
    initial = _fill(lifecycle=None)
    later = _fill(hour=10, intent=uuid4())
    correction = _resolution(initial, "CORRECT", later)

    result = _classify((initial, later), (correction,))

    assert result.status == "UNKNOWN"
    assert result.paths == ()
    assert "REVISION_ROOT_SET_MISMATCH" in result.issues


def test_execution_timestamp_and_trade_date_must_be_real_and_consistent():
    initial = _fill(lifecycle=None)
    changed_rows = (
        replace(initial[1], executed_at=initial[1].executed_at.replace(tzinfo=None)),
        replace(initial[1], fill_trade_date=DAY + timedelta(days=1)),
        replace(initial[1], executed_at=None),
        replace(initial[1], executed_at=datetime(
            9999, 12, 31, 23, 59, tzinfo=timezone(-timedelta(hours=12)))),
    )
    for changed in changed_rows:
        result = _classify((initial,), frozen=(changed,))
        assert result.status == "UNKNOWN"
        assert result.paths == () and result.earliest_impact_at is None
        assert "REVISION_FROZEN_FILL_INVALID" in result.issues


def test_broken_graph_rejects_entire_classification_without_partial_paths():
    initial = _fill(lifecycle=None)
    later = _fill(hour=10, intent=uuid4())
    valid_void = _resolution(initial)
    pending = replace(_resolution(later), applied=False)

    result = _classify((initial, later), (valid_void, pending))

    assert result.status == "UNKNOWN"
    assert result.paths == () and result.earliest_impact_at is None
    assert "FILL_RESOLUTION_IDENTITY_INVALID" in result.issues
