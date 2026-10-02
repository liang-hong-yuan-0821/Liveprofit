# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle_revision_chronology（持仓生命周期、版本修订）：Pure checks for the revised lifecycle's local timestamp work list.",
#   "keywords": [
#     "量化策略",
#     "交易日历",
#     "每日",
#     "重复请求",
#     "成交",
#     "持仓生命周期",
#     "订单",
#     "收益",
#     "版本修订",
#     "lifecycle_revision_chronology",
#     "calendar",
#     "daily",
#     "duplicate",
#     "fill",
#     "lifecycle",
#     "order",
#     "returns",
#     "revision"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/lifecycle_accounting_replay.py",
#     "backend/modules/quant_strategy/application/lifecycle_persisted_daily_inputs.py",
#     "backend/modules/quant_strategy/application/lifecycle_resolved_accounting.py",
#     "backend/modules/quant_strategy/application/lifecycle_revision_chronology.py",
#     "backend/modules/quant_strategy/application/lifecycle_revision_impact_surface.py",
#     "backend/modules/quant_strategy/application/lifecycle_revision_replay_manifest.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Pure checks for the revised lifecycle's local timestamp work list."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from backend.modules.quant_strategy.application.lifecycle_accounting_replay import (
    AccountingEventState, AccountingReplay,
)
from backend.modules.quant_strategy.application.lifecycle_persisted_daily_inputs import (
    LocalDailyFact, LocalDailyFactInputs,
)
from backend.modules.quant_strategy.application.lifecycle_resolved_accounting import (
    ResolvedAccountingReplay,
)
from backend.modules.quant_strategy.application.lifecycle_revision_chronology import (
    LocalDailyInputSlot, LocalEffectiveFillSlot, build_revision_chronology,
)
from backend.modules.quant_strategy.application.lifecycle_revision_impact_surface import (
    AffectedDailyFact,
)
from backend.modules.quant_strategy.application.lifecycle_revision_replay_manifest import (
    RevisionReplayManifest, RevisionReplayRoot,
)


CN = ZoneInfo("Asia/Shanghai")
D21 = date(2026, 9, 21)
D22 = date(2026, 9, 22)
D23 = date(2026, 9, 23)


def _at(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=CN)


def _case():
    initial, voided, corrected, replacement = (uuid4() for _ in range(4))
    dates = (D21, D22, D23)
    daily_rows = tuple(LocalDailyFact(
        day, uuid4(), "raw", _at(day, 16), {}, 1, 1,
    ) for day in dates)
    affected = tuple(AffectedDailyFact(day.trade_date, uuid4(), day.revision_id)
                     for day in daily_rows[1:])
    manifest = RevisionReplayManifest(
        "LOCAL_MAPPING", "UNCHANGED_BUY_CANDIDATE", D22,
        (RevisionReplayRoot(initial, initial, "UNCHANGED", None, None, False),
         RevisionReplayRoot(voided, None, "VOID", 1, 2, True),
         RevisionReplayRoot(corrected, replacement, "CORRECT", 2, 3, True)),
        (initial, corrected), affected, (),
        ("REPLAY_BLOCKED:CAUSAL_POLICY_INTENT_REPLAY_REQUIRED",),
    )
    states = (
        AccountingEventState(initial, _at(D21, 10), Decimal(100),
                             Decimal(1000), Decimal(10), Decimal(0), None, "BUY"),
        AccountingEventState(replacement, _at(D22, 11), Decimal(50),
                             Decimal(500), Decimal(10), Decimal(0), None, "SELL"),
    )
    accounting = ResolvedAccountingReplay(
        "PROVISIONAL_ACCOUNTING",
        AccountingReplay("CALCULATED", (), Decimal(50), Decimal(500),
                         Decimal(10), Decimal(0), (initial, replacement), states,
                         _at(D21, 9), Decimal(0)),
        (initial, replacement), (voided, corrected),
        ("BROKER_FILL_SET_UNCERTIFIED",),
    )
    return manifest, accounting, LocalDailyFactInputs(
        "LOCAL_CANDIDATE", daily_rows, ("CALENDAR_SOURCE_UNCERTIFIED",))


def _build(case):
    manifest, accounting, daily = case
    return build_revision_chronology(
        manifest=manifest, accounting=accounting, daily=daily)


def test_corrected_sell_and_void_map_to_local_timestamp_slots_only():
    manifest, accounting, daily = _case()

    result = _build((manifest, accounting, daily))

    assert result.status == "LOCAL_ORDER"
    assert tuple(type(slot) for slot in result.slots) == (
        LocalEffectiveFillSlot, LocalDailyInputSlot,
        LocalEffectiveFillSlot, LocalDailyInputSlot, LocalDailyInputSlot)
    first, day21, second, day22, day23 = result.slots
    assert (first.root_fill_event_id, first.terminal_fill_event_id,
            first.fill_trade_date) == (
                manifest.roots[0].root_fill_event_id,
                accounting.effective_fill_event_ids[0], D21)
    assert (second.root_fill_event_id, second.terminal_fill_event_id) == (
        manifest.roots[2].root_fill_event_id,
        accounting.effective_fill_event_ids[1])
    assert day21.effective_fill_event_ids_before_cutoff == (
        accounting.effective_fill_event_ids[0],)
    assert day22.effective_fill_event_ids_before_cutoff == (
        *accounting.effective_fill_event_ids,)
    assert day23.effective_fill_event_ids_before_cutoff == (
        *accounting.effective_fill_event_ids,)
    assert tuple(slot.revision_id for slot in (day21, day22, day23)) == (
        *(row.revision_id for row in daily.days),)
    assert "CHRONOLOGY_LOCAL_ONLY" in result.issues
    assert "REPLAY_BLOCKED:CAUSAL_POLICY_INTENT_REPLAY_REQUIRED" in result.issues


def test_no_daily_facts_cannot_make_historical_calendar_evidence():
    manifest, accounting, _daily = _case()
    empty = LocalDailyFactInputs("LOCAL_CANDIDATE", (), ())

    result = _build((manifest, accounting, empty))

    assert result.status == "UNKNOWN"
    assert result.slots == ()
    assert "CHRONOLOGY_FULL_DAILY_UNKNOWN" in result.issues


def test_effective_fill_after_last_daily_remains_explicit_tail_slot():
    manifest, accounting, daily = _case()
    states = accounting.accounting.event_states
    late_state = replace(states[1], effective_at=_at(date(2026, 9, 24), 10))
    changed = replace(accounting, accounting=replace(
        accounting.accounting, event_states=(states[0], late_state)))

    result = _build((manifest, changed, daily))

    assert result.status == "LOCAL_ORDER"
    assert isinstance(result.slots[-1], LocalEffectiveFillSlot)
    assert result.slots[-1].terminal_fill_event_id == late_state.event_id
    assert isinstance(result.slots[-2], LocalDailyInputSlot)
    assert result.slots[-2].effective_fill_event_ids_before_cutoff == (
        states[0].event_id,)
    assert "HISTORICAL_VISIBILITY_UNCERTIFIED" in result.issues


def test_no_revision_or_changed_initial_has_no_local_order():
    manifest, accounting, daily = _case()
    for altered in (
        replace(manifest, status="NO_REVISION"),
        replace(manifest, initial_gate="RESEED_REQUIRED"),
        replace(manifest, initial_gate="INITIAL_VOID"),
        replace(manifest, initial_gate="NONBUY_INITIAL"),
    ):
        result = _build((altered, accounting, daily))
        assert result.status == "UNKNOWN"
        assert result.slots == ()


def test_same_day_fill_after_or_equal_to_daily_cutoff_is_unknown():
    manifest, accounting, daily = _case()
    for cutoff in (_at(D22, 10, 30), _at(D22, 11)):
        changed = replace(daily, days=(daily.days[0], replace(
            daily.days[1], data_as_of=cutoff), daily.days[2]))
        result = _build((manifest, accounting, changed))
        assert result.status == "UNKNOWN"
        assert result.slots == ()
        assert any(issue.startswith("CHRONOLOGY_FILL_") for issue in result.issues)


def test_first_fill_after_first_daily_cutoff_is_unknown():
    manifest, accounting, daily = _case()
    changed = replace(daily, days=(replace(daily.days[0],
                                          data_as_of=_at(D21, 9, 30)),
                                         *daily.days[1:]))

    result = _build((manifest, accounting, changed))

    assert result.status == "UNKNOWN"
    assert result.slots == ()
    assert "CHRONOLOGY_INITIAL_AFTER_FIRST_DAILY_CUTOFF" in result.issues


def test_affected_suffix_must_match_full_daily_revision_ids():
    manifest, accounting, daily = _case()
    wrong_revision = replace(manifest, daily_facts_to_recompute=(
        replace(manifest.daily_facts_to_recompute[0], current_revision_id=uuid4()),
        manifest.daily_facts_to_recompute[1]))
    missing_affected = replace(manifest,
                               daily_facts_to_recompute=manifest.daily_facts_to_recompute[:1])
    for altered in (wrong_revision, missing_affected):
        result = _build((altered, accounting, daily))
        assert result.status == "UNKNOWN"
        assert result.slots == ()
        assert "CHRONOLOGY_AFFECTED_DAILY_MISMATCH" in result.issues


def test_malformed_full_daily_dates_or_cutoffs_clear_all_slots():
    manifest, accounting, daily = _case()
    cases = (
        (daily.days[1], daily.days[0], daily.days[2]),
        (daily.days[0], replace(daily.days[1], trade_date=D21), daily.days[2]),
        (daily.days[0], replace(daily.days[1], data_as_of=_at(D21, 16)), daily.days[2]),
        (daily.days[0], replace(daily.days[1], data_as_of=datetime(
            2026, 9, 22, 16)), daily.days[2]),
    )
    for rows in cases:
        result = _build((manifest, accounting, replace(daily, days=rows)))
        assert result.status == "UNKNOWN"
        assert result.slots == ()
        assert "CHRONOLOGY_DAILY_IDENTITY_OR_CUTOFF_INVALID" in result.issues


def test_accounting_order_root_mapping_and_initial_side_are_checked():
    manifest, accounting, daily = _case()
    states = accounting.accounting.event_states
    cases = (
        (manifest, replace(accounting, accounting=replace(
            accounting.accounting, event_states=tuple(reversed(states))))),
        (replace(manifest, effective_root_execution_order=(
            manifest.roots[2].root_fill_event_id,
            manifest.roots[0].root_fill_event_id)), accounting),
        (manifest, replace(accounting, accounting=replace(
            accounting.accounting, event_states=(replace(states[0], side="SELL"),
                                                states[1])))),
    )
    for altered_manifest, altered_accounting in cases:
        result = _build((altered_manifest, altered_accounting, daily))
        assert result.status == "UNKNOWN"
        assert result.slots == ()


def test_duplicate_or_missing_effective_times_are_unknown():
    manifest, accounting, daily = _case()
    states = accounting.accounting.event_states
    for altered_at in (states[0].effective_at,
                       datetime(2026, 9, 22, 11)):
        changed = replace(accounting, accounting=replace(
            accounting.accounting, event_states=(states[0], replace(
                states[1], effective_at=altered_at))))
        result = _build((manifest, changed, daily))
        assert result.status == "UNKNOWN"
        assert result.slots == ()
        assert "CHRONOLOGY_EFFECTIVE_TIME_INVALID" in result.issues


@pytest.mark.parametrize(("owner", "bad_issues"), (
    ("manifest", None), ("accounting", ["BAD"]), ("daily", "BAD"),
    ("manifest", ("OK", None)), ("nested", None),
))
def test_malformed_issue_collections_return_unknown(owner, bad_issues):
    manifest, accounting, daily = _case()
    if owner == "manifest":
        manifest = replace(manifest, issues=bad_issues)
    elif owner == "accounting":
        accounting = replace(accounting, issues=bad_issues)
    elif owner == "daily":
        daily = replace(daily, issues=bad_issues)
    else:
        accounting = replace(accounting, accounting=replace(
            accounting.accounting, issues=bad_issues))

    result = _build((manifest, accounting, daily))

    assert result.status == "UNKNOWN"
    assert result.slots == ()
    assert ("CHRONOLOGY_ISSUES_INVALID" if owner != "nested"
            else "CHRONOLOGY_ACCOUNTING_UNKNOWN") in result.issues


@pytest.mark.parametrize("bad_accounting", (None, object(), {}, "CALCULATED"))
def test_malformed_nested_accounting_returns_unknown(bad_accounting):
    manifest, accounting, daily = _case()
    altered = replace(accounting, accounting=bad_accounting)

    result = _build((manifest, altered, daily))

    assert result.status == "UNKNOWN"
    assert result.slots == ()
    assert "CHRONOLOGY_ACCOUNTING_UNKNOWN" in result.issues
