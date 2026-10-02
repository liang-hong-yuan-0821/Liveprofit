"""Reconcile local lifecycle version steps without inferring event timestamps.

The caller must separately prove the first BUY creates lifecycle version 1.
Only subsequent linked fills have frozen version bounds here. This proves
only a local write chain, not broker events or historical visibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID


@dataclass(frozen=True)
class FillVersionStep:
    fill_event_id: UUID
    before: int
    after: int


@dataclass(frozen=True)
class DailyVersionStep:
    trade_date: date
    before: int
    after: int


@dataclass(frozen=True)
class LocalVersionChain:
    status: Literal["PROVISIONAL", "UNKNOWN"]
    daily_advances: tuple[bool, ...]
    ending_version: int | None
    issues: tuple[str, ...]
    verified_days: tuple[DailyVersionStep, ...] = ()


def reconcile_local_version_chain(
    *, fills: tuple[FillVersionStep, ...], days: tuple[DailyVersionStep, ...],
    current_version: int,
) -> LocalVersionChain:
    """Require a contiguous creation(1) -> later fills/days -> current chain.

    A zero-advance day is a checkpoint at its version. The initial BUY must
    be excluded by the caller; it creates version 1 and has no 1 -> 2 step.
    Later fills are ordered by frozen version, not by event timestamp.
    """
    unknown = lambda issue: LocalVersionChain("UNKNOWN", (), None, (issue,))
    if (not isinstance(fills, tuple) or not isinstance(days, tuple) or not days
            or type(current_version) is not int or current_version < 1):
        return unknown("VERSION_CHAIN_INPUT_INVALID")
    if (any(not isinstance(fill, FillVersionStep)
            or not isinstance(fill.fill_event_id, UUID)
            or type(fill.before) is not int or type(fill.after) is not int
            or fill.before < 1 or fill.after != fill.before + 1
            for fill in fills)
            or len({fill.fill_event_id for fill in fills}) != len(fills)):
        return unknown("VERSION_CHAIN_FILL_INVALID")
    if (any(not isinstance(day, DailyVersionStep)
            or type(day.trade_date) is not date
            or type(day.before) is not int or type(day.after) is not int
            or day.before < 1 or day.after - day.before not in (0, 1)
            for day in days)
            or tuple(day.trade_date for day in days) != tuple(sorted({
                day.trade_date for day in days}))):
        return unknown("VERSION_CHAIN_DAY_INVALID")
    ordered_fills = sorted(fills, key=lambda fill: fill.before)
    cursor = 1
    index = 0
    advances: list[bool] = []
    for day in days:
        while index < len(ordered_fills) and ordered_fills[index].before < day.before:
            fill = ordered_fills[index]
            if fill.before != cursor:
                return unknown(f"VERSION_CHAIN_GAP_BEFORE_DAY:{day.trade_date}")
            cursor = fill.after
            index += 1
        if cursor != day.before:
            return unknown(f"VERSION_CHAIN_DAY_BOUNDARY_MISMATCH:{day.trade_date}")
        cursor = day.after
        advances.append(day.after > day.before)
    while index < len(ordered_fills):
        fill = ordered_fills[index]
        if fill.before != cursor:
            return unknown("VERSION_CHAIN_GAP_AFTER_DAILY")
        cursor = fill.after
        index += 1
    if cursor != current_version:
        return unknown("VERSION_CHAIN_CURRENT_MISMATCH")
    return LocalVersionChain("PROVISIONAL", tuple(advances), cursor,
                             ("VERSION_CHAIN_SOURCE_UNCERTIFIED",), days)
