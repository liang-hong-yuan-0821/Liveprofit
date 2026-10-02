# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle_initial_seed_replay（持仓生命周期、重放）：3aq first-BUY formula from explicit creation-time inputs, without DB facts.",
#   "keywords": [
#     "量化策略",
#     "策略族",
#     "成交",
#     "持仓生命周期",
#     "重放",
#     "风险",
#     "交易信号",
#     "来源证据",
#     "止损",
#     "lifecycle_initial_seed_replay",
#     "family",
#     "fill",
#     "lifecycle",
#     "replay",
#     "risk",
#     "signal",
#     "source",
#     "stop"
#   ],
#   "covers": [
#     "backend/modules/quant_strategy/application/lifecycle_initial_seed_replay.py",
#     "backend/modules/quant_strategy/application/lifecycle_revision_replay_manifest.py",
#     "backend/modules/quant_strategy/domain/family_management.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""3aq first-BUY formula from explicit creation-time inputs, without DB facts."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from backend.modules.quant_strategy.application.lifecycle_initial_seed_replay import (
    EffectiveInitialFill, FrozenCrossFlag, FrozenDecimalField,
    FrozenInitialSeedInputs, FrozenIntField, FrozenLegacyTrailing,
    FrozenTrailingField, replay_initial_buy_seed,
)
from backend.modules.quant_strategy.application.lifecycle_revision_replay_manifest import (
    RevisionReplayManifest, RevisionReplayRoot,
)
from backend.modules.quant_strategy.domain.family_management import TREND_3ATR


D = Decimal
DAY = date(2026, 9, 28)


def _case():
    root = uuid4()
    terminal = uuid4()
    manifest = RevisionReplayManifest(
        "LOCAL_MAPPING", "RESEED_REQUIRED", DAY,
        (RevisionReplayRoot(root, terminal, "CORRECT", None, None, False),),
        (root,), (), (), ("REPLAY_BLOCKED:RESEED_REQUIRED",),
    )
    frozen = FrozenInitialSeedInputs(
        template_id="ma_trend_cross_v1", management_policy=None,
        order_quantity=D(500), initial_stop_price=D("9.5"),
        initial_exposure=D("0.50"),
        signal_planned_shares=FrozenDecimalField(True, D(1300)),
        total_assets=FrozenDecimalField(True, D(100000)),
        risk_per_trade_pct=FrozenDecimalField(True, D("0.01")),
        config_reward_multiple=FrozenDecimalField(False, None),
        template_reward_multiple=FrozenDecimalField(False, None),
        legacy_trailing_stop=FrozenTrailingField(False, None),
        arc_neckline_price=FrozenDecimalField(False, None),
        ma5_confirmed_cross=FrozenCrossFlag(False, None),
        confirmation_window_trading_days=FrozenIntField(False, None),
    )
    fill = EffectiveInitialFill(terminal, "BUY", 0, D(10), D(500), DAY)
    return root, manifest, frozen, fill


def _replay(*, manifest=None, frozen=None, fill=None, root=None):
    default_root, default_manifest, default_frozen, default_fill = _case()
    return replay_initial_buy_seed(
        manifest=manifest if manifest is not None else default_manifest,
        initial_root_fill_id=root if root is not None else default_root,
        frozen=frozen if frozen is not None else default_frozen,
        effective_fill=fill if fill is not None else default_fill,
    )


def test_planned_capacity_branch_matches_creation_formula_and_remains_uncertified():
    root, manifest, frozen, fill = _case()
    result = _replay(root=root, manifest=manifest, frozen=frozen, fill=fill)

    assert result.status == "PROVISIONAL_INITIAL_SEED"
    assert result.source_status == "UNCERTIFIED"
    assert "INITIAL_SEED_INPUTS_AND_FILL_SOURCE_UNCERTIFIED" in result.issues
    assert "REPLAY_BLOCKED:CAUSAL_POLICY_INTENT_REPLAY_REQUIRED" in result.issues
    seed = result.seed
    assert seed.initial_fill_event_id == fill.event_id
    assert seed.planned_capacity_shares == D(1300)
    assert seed.risk_budget == D(1000)
    assert seed.repriced_capacity_shares == D(2000)
    assert seed.risk_capacity_shares == D(1300)
    assert seed.target_exposure_pct == D("0.5")
    assert seed.target_shares == D(600)
    assert seed.phase == "ENTRY_PENDING"
    assert seed.profit_take_price == D(11)
    assert seed.trailing is seed.expectation is None


def test_repriced_risk_budget_and_corrected_fill_economics_change_seed():
    root, manifest, frozen, fill = _case()
    constrained = replace(frozen, total_assets=FrozenDecimalField(True, D(10000)))
    result = _replay(root=root, manifest=manifest, frozen=constrained, fill=fill)

    assert result.status == "PROVISIONAL_INITIAL_SEED"
    assert result.seed.risk_budget == D(100)
    assert result.seed.repriced_capacity_shares == D(200)
    assert result.seed.risk_capacity_shares == D(200)
    assert result.seed.target_shares == D(100)
    assert result.seed.phase == "INITIALIZED"

    corrected = replace(fill, price=D("10.5"), quantity=D(50),
                        trade_date=date(2026, 9, 29))
    changed = _replay(root=root, manifest=manifest, frozen=constrained,
                      fill=corrected)
    assert changed.status == "UNKNOWN"
    assert changed.issues == ("INITIAL_SEED_TARGET_ZERO",)
    # With a larger budget the same correction has a real target and carries
    # its own economic price, quantity and trade date into the seed.
    larger = replace(constrained, total_assets=FrozenDecimalField(True, D(30000)))
    changed = _replay(root=root, manifest=manifest, frozen=larger,
                      fill=corrected)
    assert changed.status == "PROVISIONAL_INITIAL_SEED"
    assert changed.seed.risk_capacity_shares == D(300)
    assert changed.seed.target_shares == D(100)
    assert changed.seed.phase == "ENTRY_PENDING"
    assert changed.seed.initial_fill_price == D("10.5")
    assert changed.seed.fill_trade_date == date(2026, 9, 29)
    assert changed.seed.profit_take_price == D("12.5")


def test_missing_and_explicit_none_signal_or_risk_values_follow_service_branches():
    root, manifest, frozen, fill = _case()
    for shares in (FrozenDecimalField(False, None), FrozenDecimalField(True, None)):
        changed = replace(frozen, signal_planned_shares=shares,
                          total_assets=FrozenDecimalField(True, None))
        result = _replay(root=root, manifest=manifest, frozen=changed, fill=fill)
        assert result.status == "PROVISIONAL_INITIAL_SEED"
        assert result.seed.planned_capacity_shares == D(1000)  # 500 / 0.5
        assert result.seed.risk_budget is None
        assert result.seed.repriced_capacity_shares is None
        assert result.seed.risk_capacity_shares == D(1000)
        assert result.seed.target_shares == D(500)
        assert result.seed.phase == "INITIALIZED"

    zero_shares = replace(frozen,
                          signal_planned_shares=FrozenDecimalField(True, D(0)))
    result = _replay(root=root, manifest=manifest, frozen=zero_shares, fill=fill)
    assert result.status == "UNKNOWN"
    assert result.issues == ("INITIAL_SEED_FROZEN_VALUE_INVALID",)


def test_rounding_target_zero_and_legacy_reward_precedence():
    root, manifest, frozen, fill = _case()
    rounded = replace(
        frozen, signal_planned_shares=FrozenDecimalField(True, D(359)),
        total_assets=FrozenDecimalField(False, None),
        risk_per_trade_pct=FrozenDecimalField(False, None),
        config_reward_multiple=FrozenDecimalField(True, D("2.5")),
        template_reward_multiple=FrozenDecimalField(True, D(4)),
    )
    result = _replay(root=root, manifest=manifest, frozen=rounded, fill=fill)
    assert result.seed.risk_capacity_shares == D(300)
    assert result.seed.target_shares == D(100)
    assert result.seed.profit_take_price == D("11.25")

    from_template = _replay(root=root, manifest=manifest,
                            frozen=replace(rounded,
                                           config_reward_multiple=FrozenDecimalField(False, None)),
                            fill=fill)
    assert from_template.seed.profit_take_price == D(12)
    zero_target = _replay(root=root, manifest=manifest,
                          frozen=replace(rounded,
                                         signal_planned_shares=FrozenDecimalField(True, D(199))),
                          fill=fill)
    assert zero_target.status == "UNKNOWN"
    assert zero_target.issues == ("INITIAL_SEED_TARGET_ZERO",)


def test_family_atr_and_legacy_trailing_seeds_match_creation_fields():
    root, manifest, frozen, fill = _case()
    family = replace(frozen, management_policy=TREND_3ATR,
                     template_id="ma_trend_cross_v1")
    result = _replay(root=root, manifest=manifest, frozen=family, fill=fill)
    assert result.status == "PROVISIONAL_INITIAL_SEED"
    assert result.seed.profit_take_price is None
    assert result.seed.trailing.mode == "HIGHEST_CLOSE_ATR"
    assert result.seed.trailing.atr_multiple == D(3)
    assert result.seed.trailing.initial_stop_price == D("9.5")
    assert result.seed.trailing.high_water_mark == D(10)
    assert result.seed.trailing.active_stop_price == D("9.5")
    assert result.seed.trailing.phase == "PROTECT"

    legacy = replace(frozen, legacy_trailing_stop=FrozenTrailingField(
        True, FrozenLegacyTrailing(D("0.02"), D("0.05"), D("0.10"))))
    result = _replay(root=root, manifest=manifest, frozen=legacy, fill=fill)
    assert result.status == "PROVISIONAL_INITIAL_SEED"
    assert result.seed.trailing.mode == "LEGACY_B_A_D"
    assert (result.seed.trailing.b, result.seed.trailing.a,
            result.seed.trailing.d) == (D("0.02"), D("0.05"), D("0.10"))
    assert _replay(root=root, manifest=manifest,
                   frozen=replace(family, legacy_trailing_stop=legacy.legacy_trailing_stop),
                   fill=fill).issues == ("INITIAL_SEED_TRAILING_CONFLICT",)


def test_ma5_expectation_uses_effective_fill_date_and_frozen_window():
    root, manifest, frozen, fill = _case()
    ma5 = replace(frozen, template_id="ma5_pre_cross_v1",
                  ma5_confirmed_cross=FrozenCrossFlag(True, 0),
                  confirmation_window_trading_days=FrozenIntField(True, 5))
    corrected = replace(fill, trade_date=date(2026, 9, 30))
    result = _replay(root=root, manifest=manifest, frozen=ma5,
                     fill=corrected)
    assert result.seed.expectation.fill_trade_date == date(2026, 9, 30)
    assert result.seed.expectation.window_trading_days == 5
    assert result.seed.expectation.observed_trading_days == 0
    assert result.seed.expectation.status == "PENDING"

    confirmed = replace(ma5, ma5_confirmed_cross=FrozenCrossFlag(True, 1))
    assert _replay(root=root, manifest=manifest, frozen=confirmed,
                   fill=corrected).seed.expectation is None
    assert _replay(root=root, manifest=manifest,
                   frozen=replace(ma5, ma5_confirmed_cross=FrozenCrossFlag(True, False)),
                   fill=corrected).seed.expectation is not None
    assert _replay(root=root, manifest=manifest,
                   frozen=replace(ma5, ma5_confirmed_cross=FrozenCrossFlag(True, True)),
                   fill=corrected).seed.expectation is None
    default = replace(ma5, confirmation_window_trading_days=FrozenIntField(False, None))
    assert _replay(root=root, manifest=manifest, frozen=default,
                   fill=corrected).seed.expectation.window_trading_days == 3


def test_arc_neckline_requires_frozen_positive_value():
    root, manifest, frozen, fill = _case()
    arc = replace(frozen, template_id="arc_bottom_75a_v1")
    assert _replay(root=root, manifest=manifest, frozen=arc,
                   fill=fill).issues == ("INITIAL_SEED_ARC_NECKLINE_MISSING",)
    present = replace(arc, arc_neckline_price=FrozenDecimalField(True, D("10.2")))
    assert _replay(root=root, manifest=manifest, frozen=present,
                   fill=fill).seed.arc_neckline_price == D("10.2")


@pytest.mark.parametrize("gate,side,rank,terminal_present,reason", [
    ("INITIAL_VOID", "BUY", 0, False, "INITIAL_SEED_VOID"),
    ("NONBUY_INITIAL", "SELL", 0, True, "INITIAL_SEED_NONBUY"),
    ("INITIAL_NOT_FIRST", "BUY", 1, True, "INITIAL_SEED_NOT_FIRST"),
])
def test_void_nonbuy_and_nonfirst_refuse_seed(gate, side, rank,
                                              terminal_present, reason):
    root, manifest, frozen, fill = _case()
    row = manifest.roots[0]
    manifest = replace(
        manifest, initial_gate=gate,
        roots=(replace(row,
                       disposition=row.disposition if terminal_present else "VOID",
                       terminal_fill_event_id=row.terminal_fill_event_id
                       if terminal_present else None),),
        effective_root_execution_order=(root,) if terminal_present else (),
    )
    result = _replay(root=root, manifest=manifest, frozen=frozen,
                     fill=replace(fill, side=side, execution_rank=rank))
    assert result.status == "UNKNOWN"
    assert result.seed is None
    assert result.issues == (reason,)


def test_initial_event_identity_and_source_proof_cannot_be_guessed():
    root, manifest, frozen, fill = _case()
    changed = _replay(root=root, manifest=manifest, frozen=frozen,
                      fill=replace(fill, event_id=uuid4()))
    assert changed.status == "UNKNOWN"
    assert changed.issues == ("INITIAL_SEED_EVENT_ID_MISMATCH",)
    wrong_root = _replay(root=uuid4(), manifest=manifest, frozen=frozen, fill=fill)
    assert wrong_root.status == "UNKNOWN"
    assert wrong_root.issues == ("INITIAL_SEED_ROOT_NOT_UNIQUE",)
    no_mapping = _replay(root=root, manifest=replace(manifest, status="NO_REVISION"),
                         frozen=frozen, fill=fill)
    assert no_mapping.status == "UNKNOWN"


def test_manifest_gate_and_effective_root_set_must_match_initial_path():
    root, manifest, frozen, fill = _case()
    bad_gate = replace(manifest, initial_gate="UNCHANGED_BUY_CANDIDATE")
    result = _replay(root=root, manifest=bad_gate, frozen=frozen, fill=fill)
    assert result.status == "UNKNOWN"
    assert result.issues == ("INITIAL_SEED_MANIFEST_GATE_MISMATCH",)

    stray_root = replace(manifest, effective_root_execution_order=(root, uuid4()))
    result = _replay(root=root, manifest=stray_root, frozen=frozen, fill=fill)
    assert result.status == "UNKNOWN"
    assert result.issues == ("INITIAL_SEED_MANIFEST_INVALID",)

    malformed = replace(manifest, roots=(None,))
    result = _replay(root=root, manifest=malformed, frozen=frozen, fill=fill)
    assert result.status == "UNKNOWN"
    assert result.seed is None
    assert result.issues == ("INITIAL_SEED_MANIFEST_INVALID",)

    unhashable_root = replace(manifest, roots=(replace(
        manifest.roots[0], root_fill_event_id=[]),))
    result = _replay(root=root, manifest=unhashable_root, frozen=frozen, fill=fill)
    assert result.status == "UNKNOWN"
    assert result.issues == ("INITIAL_SEED_MANIFEST_INVALID",)


@pytest.mark.parametrize("change,reason", [
    ({"initial_stop_price": D(10)}, "INITIAL_SEED_STOP_INVALID"),
    ({"initial_stop_price": D("NaN")}, "INITIAL_SEED_STOP_INVALID"),
    ({"order_quantity": D(0)}, "INITIAL_SEED_FILL_OR_ORDER_INVALID"),
    ({"signal_planned_shares": FrozenDecimalField(True, D("Infinity"))},
     "INITIAL_SEED_FROZEN_FIELD_INVALID"),
    ({"risk_per_trade_pct": FrozenDecimalField(True, D(-1))},
     "INITIAL_SEED_FROZEN_VALUE_INVALID"),
    ({"config_reward_multiple": FrozenDecimalField(True, None)},
     "INITIAL_SEED_REWARD_INVALID"),
    ({"legacy_trailing_stop": FrozenTrailingField(True, FrozenLegacyTrailing(
        D("0.1"), D("0.05"), D("0.2")))}, "INITIAL_SEED_TRAILING_INVALID"),
    ({"confirmation_window_trading_days": FrozenIntField(True, None),
      "template_id": "ma5_pre_cross_v1"}, "INITIAL_SEED_EXPECTATION_WINDOW_INVALID"),
])
def test_invalid_frozen_values_and_stop_fail_closed(change, reason):
    root, manifest, frozen, fill = _case()
    result = _replay(root=root, manifest=manifest,
                     frozen=replace(frozen, **change), fill=fill)
    assert result.status == "UNKNOWN"
    assert result.seed is None
    assert result.issues == (reason,)
