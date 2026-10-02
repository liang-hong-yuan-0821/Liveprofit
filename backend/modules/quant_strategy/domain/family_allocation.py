"""Deterministic family budgets and frozen-owner arbitration.

Inputs are trusted host facts, not sandbox declarations. This pure projection
does not grant strategy qualification, change ownership, or cancel orders.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from uuid import UUID

from .portfolio_targets import PortfolioTargetIntent

ZERO = Decimal(0)


def _amount(value):
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError("amount must be finite nonnegative Decimal")


@dataclass(frozen=True)
class FamilyAdmission:
    family_id: str
    strategy_id: UUID
    strategy_version_id: UUID
    net_expectancy_lower_bound: Decimal | None
    validated_at: datetime | None
    preregistered_rank: int

    def __post_init__(self):
        if not self.family_id or not all(isinstance(v, UUID) for v in (self.strategy_id, self.strategy_version_id)):
            raise ValueError("family and frozen strategy identity required")
        if type(self.preregistered_rank) is not int or self.preregistered_rank < 0:
            raise ValueError("preregistered rank must be nonnegative")
        if (self.net_expectancy_lower_bound is None) != (self.validated_at is None):
            raise ValueError("ranking evidence requires both value and availability time")
        if self.net_expectancy_lower_bound is not None:
            if (not isinstance(self.net_expectancy_lower_bound, Decimal)
                    or not self.net_expectancy_lower_bound.is_finite()
                    or not isinstance(self.validated_at, datetime)
                    or self.validated_at.utcoffset() is None):
                raise ValueError("ranking evidence must be finite and timezone-aware")


@dataclass(frozen=True)
class OwnedExposure:
    symbol: str
    family_id: str | None
    strategy_id: UUID | None
    strategy_version_id: UUID | None
    held_notional: Decimal
    pending_buy_notional: Decimal = ZERO

    def __post_init__(self):
        _amount(self.held_notional)
        _amount(self.pending_buy_notional)
        if not self.symbol or self.held_notional + self.pending_buy_notional <= 0:
            raise ValueError("ownership requires held or pending exposure")
        # Unknown imported/manual ownership remains occupied, never auto-adopted.
        identity = (self.family_id, self.strategy_id, self.strategy_version_id)
        if any(v is not None for v in identity) and not (
            bool(self.family_id) and isinstance(self.strategy_id, UUID)
            and isinstance(self.strategy_version_id, UUID)
        ):
            raise ValueError("owner identity must be complete or entirely unknown")


@dataclass(frozen=True)
class ResolvedFamilyTarget:
    symbol: str
    family_id: str | None
    strategy_id: UUID | None
    strategy_version_id: UUID | None
    target_notional: Decimal
    max_add_notional: Decimal
    reason: str


@dataclass(frozen=True)
class FamilyAllocation:
    family_budgets: tuple[tuple[str, Decimal], ...]
    targets: tuple[ResolvedFamilyTarget, ...]
    conflicts: tuple[tuple[str, str, str], ...]
    uncommitted_capacity: Decimal


def allocate_families(*, capital_budget: Decimal, decision_at: datetime,
                      admissions: tuple[FamilyAdmission, ...], intents: tuple[PortfolioTargetIntent, ...],
                      exposures: tuple[OwnedExposure, ...] = (),
                      protective_targets: dict[str, Decimal] | None = None,
                      research_cold_start: bool = False) -> FamilyAllocation:
    """Project target amounts and BUY ceilings without spending expected SELL cash.

    Equal family budgets are never redistributed when a candidate loses a
    conflict. Existing and pending exposure consumes both family and account
    capacity; a reduction only releases capacity after a confirmed fill.
    """
    _amount(capital_budget)
    if not isinstance(decision_at, datetime) or decision_at.utcoffset() is None:
        raise ValueError("decision time must be timezone-aware")
    if type(research_cold_start) is not bool:
        raise ValueError("cold start must be explicit")
    if len({a.family_id for a in admissions}) != len(admissions):
        raise ValueError("one admitted version per family required")
    if len({a.strategy_id for a in admissions}) != len(admissions):
        raise ValueError("a strategy cannot consume multiple family budgets")
    if len({a.strategy_version_id for a in admissions}) != len(admissions):
        raise ValueError("a frozen version cannot consume multiple family budgets")
    if len({e.symbol for e in exposures}) != len(exposures):
        raise ValueError("aggregate each symbol under exactly one owner before arbitration")
    for a in admissions:
        if a.validated_at is not None and a.validated_at >= decision_at:
            raise ValueError("ranking evidence must be available before the decision")
        if a.validated_at is None and not research_cold_start:
            raise ValueError("live ranking requires completed validation evidence")
    if research_cold_start and any(a.validated_at is not None for a in admissions):
        raise ValueError("cold start uses only preregistered order, never mixed evidence")
    ranked = sorted(admissions, key=lambda a: (
        a.preregistered_rank if research_cold_start else -a.net_expectancy_lower_bound,
        str(a.strategy_id),
    ))
    admitted = {a.family_id: a for a in ranked}
    owned = {e.symbol: e for e in exposures}
    protection = protective_targets or {}
    for symbol, target in protection.items():
        _amount(target)
        if symbol not in owned or target > owned[symbol].held_notional:
            raise ValueError("protection must only reduce an existing holding")
    # Round each equal currency budget down; the indivisible remainder stays
    # unallocated instead of appearing in several families through rounding.
    budget = (capital_budget / len(ranked)).quantize(Decimal("0.01"), rounding=ROUND_DOWN) if ranked else ZERO
    family_used = {a.family_id: sum((e.held_notional + e.pending_buy_notional for e in exposures
                                   if e.family_id == a.family_id), ZERO) for a in ranked}
    remaining = max(ZERO, capital_budget - sum((e.held_notional + e.pending_buy_notional
                                                for e in exposures), ZERO))
    by_family = {}
    for intent in intents:
        a = admitted.get(intent.family_id)
        if a is None or a.strategy_version_id != intent.strategy_version_id:
            raise ValueError("intent must match an admitted frozen family version")
        if intent.family_id in by_family:
            raise ValueError("one final intent per family required")
        if intent.decision_date != decision_at.date() or intent.valid_until < decision_at.date():
            raise ValueError("intent decision date must match this decision")
        by_family[intent.family_id] = intent
    results = {e.symbol: ResolvedFamilyTarget(
        e.symbol, e.family_id, e.strategy_id, e.strategy_version_id,
        protection.get(e.symbol, e.held_notional), ZERO,
        "PROTECTIVE_REDUCTION" if e.symbol in protection else "FROZEN_OWNER_HOLD",
    ) for e in exposures}
    conflicts = []
    claimed = set()
    for a in ranked:
        intent = by_family.get(a.family_id)
        if intent is None:
            continue  # Missing input never implies an exit.
        legs = {leg.ts_code: leg for leg in intent.legs}
        # A complete family target also represents exits from omitted holdings.
        symbols = sorted(set(legs) | {e.symbol for e in exposures
                                     if (e.family_id, e.strategy_id, e.strategy_version_id)
                                     == (a.family_id, a.strategy_id, a.strategy_version_id)})
        for symbol in symbols:
            owner = owned.get(symbol)
            if owner is not None and (
                owner.family_id, owner.strategy_id, owner.strategy_version_id
            ) != (a.family_id, a.strategy_id, a.strategy_version_id):
                conflicts.append((symbol, a.family_id, "FROZEN_OWNER_CONFLICT"))
                continue
            if symbol in claimed:
                conflicts.append((symbol, a.family_id, "RANKING_CONFLICT"))
                continue
            claimed.add(symbol)
            held = owner.held_notional if owner else ZERO
            pending = owner.pending_buy_notional if owner else ZERO
            desired = budget * legs[symbol].family_weight if symbol in legs else ZERO
            reason = "FAMILY_TARGET"
            if symbol in protection:
                desired = min(desired, protection[symbol])
                reason = "PROTECTIVE_REDUCTION"
            increment = min(max(ZERO, desired - held - pending),
                            max(ZERO, budget - family_used[a.family_id]), remaining)
            family_used[a.family_id] += increment
            remaining -= increment
            results[symbol] = ResolvedFamilyTarget(symbol, a.family_id, a.strategy_id,
                                                   a.strategy_version_id, desired, increment, reason)
    return FamilyAllocation(tuple(sorted((a.family_id, budget) for a in ranked)),
                            tuple(results[s] for s in sorted(results)), tuple(sorted(conflicts)), remaining)
