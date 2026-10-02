"""Pure provisional first-BUY seed calculation from explicit frozen inputs.

This mirrors the successful creation formula in LifecycleOrderService.  The
caller must separately authenticate the creation-time inputs and the effective
fill economics.  Neither the manifest nor this result authorizes historical
research, lifecycle writes, or a production order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, DecimalException, ROUND_FLOOR
from typing import Literal
from uuid import UUID

from backend.modules.quant_strategy.domain.family_management import validate_family_policy
from backend.modules.quant_strategy.domain.management_policies import ManagementPolicy

from .lifecycle_revision_replay_manifest import (
    RevisionReplayManifest, RevisionReplayRoot,
)


LOT = Decimal(100)


@dataclass(frozen=True)
class FrozenDecimalField:
    """A snapshot key and its original value; present None differs from absent."""

    present: bool
    value: Decimal | None


@dataclass(frozen=True)
class FrozenCrossFlag:
    """Raw template_params confirmed_cross (the template normally emits 0/1)."""

    present: bool
    value: int | bool | None


@dataclass(frozen=True)
class FrozenIntField:
    present: bool
    value: int | None


@dataclass(frozen=True)
class FrozenLegacyTrailing:
    b: Decimal
    a: Decimal
    d: Decimal


@dataclass(frozen=True)
class FrozenTrailingField:
    present: bool
    value: FrozenLegacyTrailing | None


@dataclass(frozen=True)
class FrozenInitialSeedInputs:
    """Creation-time policy/order/signal/task values, supplied independently.

    The two reward fields correspond to policy.config and version.template_params.
    Missing config uses template_params; an explicitly present None is invalid.
    """

    template_id: str
    management_policy: ManagementPolicy | None
    order_quantity: Decimal
    initial_stop_price: Decimal
    initial_exposure: Decimal
    signal_planned_shares: FrozenDecimalField
    total_assets: FrozenDecimalField
    risk_per_trade_pct: FrozenDecimalField
    config_reward_multiple: FrozenDecimalField
    template_reward_multiple: FrozenDecimalField
    legacy_trailing_stop: FrozenTrailingField
    arc_neckline_price: FrozenDecimalField
    ma5_confirmed_cross: FrozenCrossFlag
    confirmation_window_trading_days: FrozenIntField


@dataclass(frozen=True)
class EffectiveInitialFill:
    event_id: UUID
    side: Literal["BUY", "SELL"]
    execution_rank: int
    price: Decimal
    quantity: Decimal
    trade_date: date


@dataclass(frozen=True)
class InitialTrailingSeed:
    mode: Literal["HIGHEST_CLOSE_ATR", "LEGACY_B_A_D"]
    initial_stop_price: Decimal
    high_water_mark: Decimal
    active_stop_price: Decimal
    phase: Literal["PROTECT"]
    atr_multiple: Decimal | None = None
    b: Decimal | None = None
    a: Decimal | None = None
    d: Decimal | None = None


@dataclass(frozen=True)
class InitialExpectationSeed:
    fill_trade_date: date
    window_trading_days: int
    observed_trading_days: Literal[0] = 0
    status: Literal["PENDING"] = "PENDING"


@dataclass(frozen=True)
class InitialPolicySeed:
    initial_fill_event_id: UUID
    initial_fill_price: Decimal
    fill_trade_date: date
    initial_stop_price: Decimal
    planned_capacity_shares: Decimal
    risk_budget: Decimal | None
    repriced_capacity_shares: Decimal | None
    risk_capacity_shares: Decimal
    target_exposure_pct: Decimal
    target_shares: Decimal
    phase: Literal["INITIALIZED", "ENTRY_PENDING"]
    profit_take_price: Decimal | None
    arc_neckline_price: Decimal | None
    trailing: InitialTrailingSeed | None
    expectation: InitialExpectationSeed | None


@dataclass(frozen=True)
class InitialSeedReplay:
    status: Literal["PROVISIONAL_INITIAL_SEED", "UNKNOWN"]
    seed: InitialPolicySeed | None
    source_status: Literal["UNCERTIFIED"]
    issues: tuple[str, ...]


def _decimal_field_valid(field: object) -> bool:
    return (isinstance(field, FrozenDecimalField)
            and type(field.present) is bool
            and (field.present or field.value is None)
            and (field.value is None or isinstance(field.value, Decimal)
                 and field.value.is_finite()))


def _positive(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def replay_initial_buy_seed(
    *, manifest: RevisionReplayManifest, initial_root_fill_id: UUID,
    frozen: FrozenInitialSeedInputs, effective_fill: EffectiveInitialFill,
) -> InitialSeedReplay:
    """Recompute only the creation seed, never its later policy transitions."""

    def unknown(issue: str) -> InitialSeedReplay:
        return InitialSeedReplay("UNKNOWN", None, "UNCERTIFIED", (issue,))

    if (not isinstance(manifest, RevisionReplayManifest)
            or not isinstance(initial_root_fill_id, UUID)
            or not isinstance(frozen, FrozenInitialSeedInputs)
            or not isinstance(effective_fill, EffectiveInitialFill)):
        return unknown("INITIAL_SEED_INPUT_INVALID")
    if (not isinstance(manifest.roots, tuple)
            or any(not isinstance(row, RevisionReplayRoot)
                   for row in manifest.roots)
            or not isinstance(manifest.effective_root_execution_order, tuple)
            or any(not isinstance(root, UUID)
                   for root in manifest.effective_root_execution_order)
            or not isinstance(manifest.issues, tuple)
            or any(not isinstance(issue, str) for issue in manifest.issues)):
        return unknown("INITIAL_SEED_MANIFEST_INVALID")
    if (not manifest.roots
            or any(not isinstance(row.root_fill_event_id, UUID)
                   or row.terminal_fill_event_id is not None
                      and not isinstance(row.terminal_fill_event_id, UUID)
                   or row.disposition not in ("UNCHANGED", "CORRECT", "VOID")
                   or (row.disposition == "VOID")
                      != (row.terminal_fill_event_id is None)
                   for row in manifest.roots)):
        return unknown("INITIAL_SEED_MANIFEST_INVALID")
    if (manifest.status != "LOCAL_MAPPING"
            or len({row.root_fill_event_id for row in manifest.roots}) != len(manifest.roots)
            or len(manifest.effective_root_execution_order)
               != len(set(manifest.effective_root_execution_order))
            or set(manifest.effective_root_execution_order)
               != {row.root_fill_event_id for row in manifest.roots
                   if row.terminal_fill_event_id is not None}):
        return unknown("INITIAL_SEED_MANIFEST_INVALID")
    roots = [row for row in manifest.roots
             if row.root_fill_event_id == initial_root_fill_id]
    if len(roots) != 1:
        return unknown("INITIAL_SEED_ROOT_NOT_UNIQUE")
    initial = roots[0]
    if initial.terminal_fill_event_id is None or manifest.initial_gate == "INITIAL_VOID":
        return unknown("INITIAL_SEED_VOID")
    if (manifest.initial_gate == "NONBUY_INITIAL"
            or effective_fill.side != "BUY"):
        return unknown("INITIAL_SEED_NONBUY")
    if (manifest.initial_gate == "INITIAL_NOT_FIRST"
            or not manifest.effective_root_execution_order
            or manifest.effective_root_execution_order[0] != initial_root_fill_id
            or type(effective_fill.execution_rank) is not int
            or effective_fill.execution_rank != 0):
        return unknown("INITIAL_SEED_NOT_FIRST")
    if (manifest.initial_gate not in ("RESEED_REQUIRED", "UNCHANGED_BUY_CANDIDATE")
            or initial.terminal_fill_event_id != effective_fill.event_id
            or not isinstance(effective_fill.event_id, UUID)):
        return unknown("INITIAL_SEED_EVENT_ID_MISMATCH")
    if (manifest.initial_gate == "RESEED_REQUIRED"
            and (initial.disposition != "CORRECT"
                 or initial.terminal_fill_event_id == initial_root_fill_id)
            or manifest.initial_gate == "UNCHANGED_BUY_CANDIDATE"
            and (initial.disposition != "UNCHANGED"
                 or initial.terminal_fill_event_id != initial_root_fill_id)):
        return unknown("INITIAL_SEED_MANIFEST_GATE_MISMATCH")
    if (type(effective_fill.trade_date) is not date
            or not _positive(effective_fill.price)
            or not _positive(effective_fill.quantity)
            or not _positive(frozen.order_quantity)
            or effective_fill.quantity > frozen.order_quantity):
        return unknown("INITIAL_SEED_FILL_OR_ORDER_INVALID")
    if (not _positive(frozen.initial_stop_price)
            or effective_fill.price <= frozen.initial_stop_price):
        return unknown("INITIAL_SEED_STOP_INVALID")
    if (not isinstance(frozen.template_id, str) or not frozen.template_id.strip()
            or not _positive(frozen.initial_exposure)
            or frozen.initial_exposure > 1):
        return unknown("INITIAL_SEED_POLICY_ID_OR_EXPOSURE_INVALID")
    if frozen.management_policy is not None:
        if not isinstance(frozen.management_policy, ManagementPolicy):
            return unknown("INITIAL_SEED_MANAGEMENT_INVALID")
        try:
            validate_family_policy(frozen.management_policy, frozen.template_id)
        except ValueError:
            return unknown("INITIAL_SEED_MANAGEMENT_TEMPLATE_MISMATCH")
        if frozen.initial_exposure != frozen.management_policy.initial_exposure:
            return unknown("INITIAL_SEED_MANAGEMENT_EXPOSURE_MISMATCH")
    elif frozen.initial_exposure != Decimal("0.50"):
        return unknown("INITIAL_SEED_LEGACY_EXPOSURE_MISMATCH")

    decimal_fields = (
        frozen.signal_planned_shares, frozen.total_assets,
        frozen.risk_per_trade_pct, frozen.config_reward_multiple,
        frozen.template_reward_multiple, frozen.arc_neckline_price,
    )
    if not all(_decimal_field_valid(field) for field in decimal_fields):
        return unknown("INITIAL_SEED_FROZEN_FIELD_INVALID")
    for field in (frozen.signal_planned_shares, frozen.total_assets,
                  frozen.risk_per_trade_pct, frozen.arc_neckline_price):
        if field.value is not None and not _positive(field.value):
            return unknown("INITIAL_SEED_FROZEN_VALUE_INVALID")
    if (not isinstance(frozen.ma5_confirmed_cross, FrozenCrossFlag)
            or type(frozen.ma5_confirmed_cross.present) is not bool
            or not frozen.ma5_confirmed_cross.present
               and frozen.ma5_confirmed_cross.value is not None
            or frozen.ma5_confirmed_cross.value is not None
               and (type(frozen.ma5_confirmed_cross.value) not in (int, bool)
                    or frozen.ma5_confirmed_cross.value not in (0, 1))
            or not isinstance(frozen.confirmation_window_trading_days, FrozenIntField)
            or type(frozen.confirmation_window_trading_days.present) is not bool
            or not frozen.confirmation_window_trading_days.present
               and frozen.confirmation_window_trading_days.value is not None
            or frozen.confirmation_window_trading_days.value is not None
               and (type(frozen.confirmation_window_trading_days.value) is not int
                    or frozen.confirmation_window_trading_days.value <= 0)):
        return unknown("INITIAL_SEED_FLAG_OR_WINDOW_INVALID")
    trailing_field = frozen.legacy_trailing_stop
    if (not isinstance(trailing_field, FrozenTrailingField)
            or type(trailing_field.present) is not bool
            or not trailing_field.present and trailing_field.value is not None
            or trailing_field.value is not None
               and not isinstance(trailing_field.value, FrozenLegacyTrailing)):
        return unknown("INITIAL_SEED_TRAILING_FIELD_INVALID")
    legacy_trailing = trailing_field.value
    if legacy_trailing is not None:
        if (not all(_positive(value) for value in (
                legacy_trailing.b, legacy_trailing.a, legacy_trailing.d))
                or legacy_trailing.b > legacy_trailing.a
                or legacy_trailing.d >= 1):
            return unknown("INITIAL_SEED_TRAILING_INVALID")
        if (frozen.management_policy is not None
                and frozen.management_policy.trailing_atr_multiple is not None):
            # The service would insert two rows for a unique lifecycle_id.
            return unknown("INITIAL_SEED_TRAILING_CONFLICT")
    if (frozen.template_id == "arc_bottom_75a_v1"
            and frozen.arc_neckline_price.value is None):
        return unknown("INITIAL_SEED_ARC_NECKLINE_MISSING")

    try:
        exposure = frozen.initial_exposure
        planned = (frozen.signal_planned_shares.value
                   if frozen.signal_planned_shares.value is not None
                   else frozen.order_quantity / exposure)
        budget = repriced = None
        if (frozen.total_assets.value is not None
                and frozen.risk_per_trade_pct.value is not None):
            budget = frozen.total_assets.value * frozen.risk_per_trade_pct.value
            repriced = budget / (effective_fill.price - frozen.initial_stop_price)
            capacity = min(planned, repriced)
        else:
            capacity = planned
        capacity = (capacity / LOT).to_integral_value(rounding=ROUND_FLOOR) * LOT
        target = (capacity * exposure / LOT).to_integral_value(rounding=ROUND_FLOOR) * LOT
        if not all(value.is_finite() for value in (
                planned, capacity, target,
                *(value for value in (budget, repriced) if value is not None))):
            return unknown("INITIAL_SEED_COMPUTATION_NONFINITE")
        if capacity <= 0:
            return unknown("INITIAL_SEED_CAPACITY_ZERO")
        if target <= 0:
            # Conservative gate: the old service did not explicitly reject zero.
            return unknown("INITIAL_SEED_TARGET_ZERO")
        if frozen.management_policy is None:
            if frozen.config_reward_multiple.present:
                reward = frozen.config_reward_multiple.value
            elif frozen.template_reward_multiple.present:
                reward = frozen.template_reward_multiple.value
            else:
                reward = Decimal(2)
            if not _positive(reward):
                return unknown("INITIAL_SEED_REWARD_INVALID")
            profit_take = effective_fill.price + reward * (
                effective_fill.price - frozen.initial_stop_price)
            if not profit_take.is_finite():
                return unknown("INITIAL_SEED_PROFIT_TAKE_NONFINITE")
        else:
            profit_take = None
    except DecimalException:
        return unknown("INITIAL_SEED_COMPUTATION_INVALID")

    trailing = None
    if (frozen.management_policy is not None
            and frozen.management_policy.trailing_atr_multiple is not None):
        trailing = InitialTrailingSeed(
            "HIGHEST_CLOSE_ATR", frozen.initial_stop_price, effective_fill.price,
            frozen.initial_stop_price, "PROTECT",
            atr_multiple=frozen.management_policy.trailing_atr_multiple,
        )
    elif legacy_trailing is not None:
        trailing = InitialTrailingSeed(
            "LEGACY_B_A_D", frozen.initial_stop_price, effective_fill.price,
            frozen.initial_stop_price, "PROTECT",
            b=legacy_trailing.b, a=legacy_trailing.a, d=legacy_trailing.d,
        )
    expectation = None
    if (frozen.template_id == "ma5_pre_cross_v1"
            and not frozen.ma5_confirmed_cross.value):
        window = (frozen.confirmation_window_trading_days.value
                  if frozen.confirmation_window_trading_days.present else 3)
        if window is None:
            return unknown("INITIAL_SEED_EXPECTATION_WINDOW_INVALID")
        expectation = InitialExpectationSeed(effective_fill.trade_date, window)
    seed = InitialPolicySeed(
        effective_fill.event_id, effective_fill.price, effective_fill.trade_date,
        frozen.initial_stop_price, planned, budget, repriced, capacity,
        exposure, target,
        "INITIALIZED" if effective_fill.quantity >= target else "ENTRY_PENDING",
        profit_take, frozen.arc_neckline_price.value, trailing, expectation,
    )
    return InitialSeedReplay(
        "PROVISIONAL_INITIAL_SEED", seed, "UNCERTIFIED",
        (*manifest.issues, "INITIAL_SEED_INPUTS_AND_FILL_SOURCE_UNCERTIFIED",
         "REPLAY_BLOCKED:CAUSAL_POLICY_INTENT_REPLAY_REQUIRED"),
    )
