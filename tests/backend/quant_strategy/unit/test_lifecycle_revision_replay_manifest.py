# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle_revision_replay_manifest（持仓生命周期、版本修订、重放）：3ap pure work-list mapping; no broker or projection authorization.",
#   "keywords": [
#     "量化策略",
#     "重复请求",
#     "指数",
#     "持仓生命周期",
#     "订单",
#     "重放",
#     "收益",
#     "版本修订",
#     "lifecycle_revision_replay_manifest",
#     "duplicate",
#     "index",
#     "lifecycle",
#     "order",
#     "replay",
#     "returns",
#     "revision"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/lifecycle_accounting_replay.py",
#     "backend/modules/quant_strategy/application/lifecycle_resolved_accounting.py",
#     "backend/modules/quant_strategy/application/lifecycle_revision_impact.py",
#     "backend/modules/quant_strategy/application/lifecycle_revision_impact_surface.py",
#     "backend/modules/quant_strategy/application/lifecycle_revision_replay_manifest.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""3ap pure work-list mapping; no broker or projection authorization."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from backend.modules.quant_strategy.application.lifecycle_accounting_replay import (
    AccountingEventState, AccountingReplay,
)
from backend.modules.quant_strategy.application.lifecycle_resolved_accounting import (
    ResolvedAccountingReplay,
)
from backend.modules.quant_strategy.application.lifecycle_revision_impact import (
    LifecycleRevisionImpact, RevisionDeclaration, RevisionImpactPath,
)
from backend.modules.quant_strategy.application.lifecycle_revision_impact_surface import (
    AffectedDailyFact, AffectedIntent, LifecycleRevisionImpactSurface,
    OriginalCausalStep,
)
from backend.modules.quant_strategy.application.lifecycle_revision_replay_manifest import (
    build_revision_replay_manifest,
)


CN = ZoneInfo("Asia/Shanghai")
DAY = date(2026, 9, 28)
AT = datetime(2026, 9, 28, 9, tzinfo=CN)


def _path(root, *, terminal=None, action="VOID", initial=False, at=AT):
    report = uuid4()
    replacement = uuid4() if terminal else None
    return RevisionImpactPath(
        root_report_id=report, root_fill_event_id=root,
        report_ids=(report, replacement) if replacement else (report,),
        declarations=(RevisionDeclaration(uuid4(), report, action, replacement),),
        terminal_report_id=replacement, terminal_fill_event_id=terminal,
        kind=("INITIAL_ANCHOR_" if initial else "LATER_FILL_") + action,
        earliest_executed_at=at,
    )


def _case(*, roots=None, paths=None, effective=None, sides=None, times=None,
          steps=None, superseded=None):
    roots = roots or (uuid4(), uuid4(), uuid4())
    replacement = uuid4()
    paths = paths if paths is not None else (
        _path(roots[1]), _path(roots[2], terminal=replacement, action="CORRECT"),
    )
    effective = effective if effective is not None else (roots[0], replacement)
    sides = sides if sides is not None else ("BUY", "SELL")
    times = times if times is not None else (AT, AT + timedelta(hours=1))
    steps = steps if steps is not None else (
        OriginalCausalStep(roots[1], 1, 2), OriginalCausalStep(roots[2], 2, 3),
    )
    superseded = superseded if superseded is not None else (roots[1], roots[2])
    states = tuple(AccountingEventState(
        event_id, at, Decimal(10), Decimal(100), Decimal(10), Decimal(0),
        None, side,
    ) for event_id, side, at in zip(effective, sides, times))
    accounting_result = AccountingReplay(
        "CALCULATED", (), Decimal(10), Decimal(100), Decimal(10), Decimal(0),
        effective, states, AT - timedelta(days=1), Decimal(10),
    )
    impact = LifecycleRevisionImpact("LOCAL_IMPACT", paths, AT, ())
    daily = (AffectedDailyFact(DAY, uuid4(), uuid4()),)
    intents = (AffectedIntent(uuid4(), uuid4()),)
    surface = LifecycleRevisionImpactSurface(
        "LOCAL_IMPACT", DAY, "PRESENT", daily, "PRESENT", intents,
        "PRESENT" if steps else "LOCAL_EMPTY", roots, steps, (),
    )
    accounting = ResolvedAccountingReplay(
        "PROVISIONAL_ACCOUNTING", accounting_result, effective,
        superseded, (),
    )
    return roots[0], impact, surface, accounting


def _build(case):
    initial, impact, surface, accounting = case
    return build_revision_replay_manifest(
        initial_fill_id=initial, impact=impact, surface=surface,
        accounting=accounting)


def test_void_before_corrected_path_maps_by_root_not_effective_index():
    initial, impact, surface, accounting = _case()

    result = _build((initial, impact, surface, accounting))

    assert result.status == "LOCAL_MAPPING"
    assert result.initial_gate == "UNCHANGED_BUY_CANDIDATE"
    assert result.impact_date == DAY
    assert tuple(row.disposition for row in result.roots) == (
        "UNCHANGED", "VOID", "CORRECT")
    assert tuple(row.terminal_fill_event_id for row in result.roots) == (
        initial, None, accounting.effective_fill_event_ids[1])
    assert result.effective_root_execution_order == (
        initial, surface.original_root_fill_ids[2])
    assert result.roots[1].old_version_before == 1
    assert result.roots[2].old_version_after == 3
    assert result.roots[0].old_version_before is None
    assert result.daily_facts_to_recompute == surface.daily_facts
    assert result.intents_to_recompute == surface.intents
    assert "REPLAY_BLOCKED:CAUSAL_POLICY_INTENT_REPLAY_REQUIRED" in result.issues


def test_corrected_initial_buy_needs_reseed_and_void_has_no_seed():
    roots = (uuid4(),)
    terminal = uuid4()
    corrected = _case(
        roots=roots, paths=(_path(roots[0], terminal=terminal,
                                  action="CORRECT", initial=True),),
        effective=(terminal,), sides=("BUY",),
        steps=(), superseded=(roots[0],))
    assert _build(corrected).initial_gate == "RESEED_REQUIRED"
    voided = _case(
        roots=roots, paths=(_path(roots[0], initial=True),),
        effective=(), sides=(), times=(), steps=(), superseded=(roots[0],))
    result = _build(voided)
    assert result.status == "LOCAL_MAPPING"
    assert result.initial_gate == "INITIAL_VOID"
    assert result.roots[0].terminal_fill_event_id is None
    assert result.effective_root_execution_order == ()


def test_nonbuy_initial_and_reordered_initial_are_explicitly_blocked():
    case = _case()
    initial, impact, surface, accounting = case
    sell_states = (replace(accounting.accounting.event_states[0], side="SELL"),
                   accounting.accounting.event_states[1])
    sell_accounting = replace(accounting, accounting=replace(
        accounting.accounting, event_states=sell_states))
    assert _build((initial, impact, surface, sell_accounting)).initial_gate == (
        "NONBUY_INITIAL")

    reordered_ids = tuple(reversed(accounting.effective_fill_event_ids))
    reordered_states = (
        replace(accounting.accounting.event_states[1],
                effective_at=AT - timedelta(hours=1)),
        accounting.accounting.event_states[0],
    )
    reordered = replace(accounting,
                        effective_fill_event_ids=reordered_ids,
                        accounting=replace(accounting.accounting,
                                           event_ids=reordered_ids,
                                           event_states=reordered_states))
    result = _build((initial, impact, surface, reordered))
    assert result.status == "LOCAL_MAPPING"
    assert result.initial_gate == "INITIAL_NOT_FIRST"
    assert result.effective_root_execution_order == (
        surface.original_root_fill_ids[2], initial)


def test_missing_duplicate_or_wrong_terminal_clears_entire_manifest():
    initial, impact, surface, accounting = _case()
    cases = (
        (impact, replace(surface, original_root_fill_ids=(initial,)), accounting),
        (impact, replace(surface, original_root_fill_ids=(initial, initial)), accounting),
        (impact, surface, replace(accounting, effective_fill_event_ids=(initial,))),
        (impact, surface, replace(accounting,
                                  effective_fill_event_ids=(initial, initial))),
        (replace(impact, paths=(impact.paths[0],
                                replace(impact.paths[1],
                                        terminal_fill_event_id=initial))),
         surface, accounting),
        (replace(impact, paths=(impact.paths[0],
                                replace(impact.paths[1],
                                        terminal_report_id=uuid4()))),
         surface, accounting),
    )
    for changed_impact, changed_surface, changed_accounting in cases:
        result = _build((initial, changed_impact, changed_surface,
                         changed_accounting))
        assert result.status == "UNKNOWN"
        assert result.roots == result.effective_root_execution_order == ()
        assert result.daily_facts_to_recompute == result.intents_to_recompute == ()


def test_bad_old_step_impact_date_and_accounting_order_fail_closed():
    initial, impact, surface, accounting = _case()
    cases = (
        (impact, replace(surface, old_steps=surface.old_steps[:1]), accounting),
        (impact, replace(surface, old_steps=(surface.old_steps[0],
                                           surface.old_steps[0])), accounting),
        (impact, replace(surface, old_steps=(replace(surface.old_steps[0],
                                                    version_after=5),
                                             surface.old_steps[1])), accounting),
        (impact, replace(surface, impact_date=DAY + timedelta(days=1)), accounting),
        (replace(impact, paths=(replace(impact.paths[0],
                                      earliest_executed_at=None), impact.paths[1])),
         surface, accounting),
        (impact, surface, replace(accounting, accounting=replace(
            accounting.accounting, event_states=(
                accounting.accounting.event_states[0],
                replace(accounting.accounting.event_states[1],
                        effective_at=AT - timedelta(hours=1)))))),
    )
    for changed_impact, changed_surface, changed_accounting in cases:
        result = _build((initial, changed_impact, changed_surface,
                         changed_accounting))
        assert result.status == "UNKNOWN"
        assert result.roots == result.daily_facts_to_recompute == ()


def test_no_revision_returns_empty_work_list_and_unknown_is_atomic():
    initial, impact, surface, accounting = _case()
    unrevised_accounting = replace(accounting,
                                   superseded_fill_event_ids=())
    no_revision = _build((
        initial, LifecycleRevisionImpact("NO_REVISION", (), None, ()),
        LifecycleRevisionImpactSurface(
            "NO_REVISION", None, "LOCAL_EMPTY", (), "LOCAL_EMPTY", (),
            "LOCAL_EMPTY", (), (), ()),
        unrevised_accounting,
    ))
    assert no_revision.status == "NO_REVISION"
    assert no_revision.initial_gate == "UNKNOWN"
    assert no_revision.roots == no_revision.effective_root_execution_order == ()

    unknown = _build((initial, replace(impact, status="UNKNOWN"),
                      surface, accounting))
    assert unknown.status == "UNKNOWN"
    assert unknown.roots == unknown.daily_facts_to_recompute == ()
