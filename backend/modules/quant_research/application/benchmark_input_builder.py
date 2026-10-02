"""Construct a complete local HS300 trial series without granting source certification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

import pandas as pd

from backend.modules.quant_research.application.dataset_builder import ResearchDataset

BENCHMARK_CODE = "000300.SH"


@dataclass(frozen=True)
class BenchmarkInputBuild:
    as_of: date
    required_sessions: int
    bars: tuple[tuple[date, Decimal], ...]
    missing_days: tuple[date, ...]
    issues: tuple[str, ...]
    certification_issues: tuple[str, ...]

    @property
    def complete_local(self) -> bool:
        return len(self.bars) == self.required_sessions and not self.missing_days and not self.issues


def _positive_close(value) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def build_benchmark_input(
    dataset: ResearchDataset, *, as_of: date, trading_days: tuple[date, ...],
    required_sessions: int,
) -> BenchmarkInputBuild:
    """Return a full dated series or an empty series with explicit local gaps."""
    if not isinstance(dataset, ResearchDataset) or type(as_of) is not date:
        raise TypeError("frozen dataset and exact as-of date required")
    if dataset.quality.as_of != as_of:
        raise ValueError("benchmark date differs from research snapshot")
    if (not trading_days or trading_days[-1] != as_of
            or any(type(day) is not date for day in trading_days)
            or tuple(sorted(set(trading_days))) != trading_days):
        raise ValueError("verified increasing exchange calendar ending at as-of required")
    if type(required_sessions) is not int or required_sessions not in (60, 120, 200):
        raise ValueError("benchmark window must be a registered stock trial window")

    issues = list(dataset.quality.issues)
    if dataset.quality.status != "READY":
        issues.append("dataset: not READY")
    if "stock" not in dataset.quality.instrument_types:
        issues.append("dataset: stock universe not selected")
    certifications = tuple(sorted(set(dataset.quality.certification_issues) | {
        "benchmark_daily: source availability not verified",
    }))
    if len(trading_days) < required_sessions:
        issues.append("benchmark_daily: calendar window too short")
    frame = dataset.tables.get("benchmark_daily")
    if not isinstance(frame, pd.DataFrame) or not {"ts_code", "trade_date", "close", "source"} <= set(frame):
        issues.append("benchmark_daily: required source columns absent")
        frame = pd.DataFrame(columns=("ts_code", "trade_date", "close", "source"))

    calendar_set = set(trading_days)
    rows: dict[date, dict] = {}
    for row in frame.to_dict("records"):
        day = row["trade_date"]
        if type(day) is not date:
            issues.append("benchmark_daily: invalid trade date")
            continue
        if day > as_of:
            issues.append("benchmark_daily: future market fact")
        if not isinstance(row["ts_code"], str) or row["ts_code"] != BENCHMARK_CODE:
            issues.append("benchmark_daily: invalid index identity")
        if trading_days[0] <= day <= as_of and day not in calendar_set:
            issues.append("benchmark_daily: nonexchange date")
        if day in rows:
            issues.append("benchmark_daily: duplicate trade date")
        rows[day] = row

    expected = trading_days[-required_sessions:] if len(trading_days) >= required_sessions else trading_days
    missing: list[date] = []
    bars: list[tuple[date, Decimal]] = []
    for day in expected:
        row = rows.get(day)
        close = _positive_close(row["close"]) if row is not None else None
        if (close is None or not isinstance(row["ts_code"], str)
                or row["ts_code"] != BENCHMARK_CODE
                or not isinstance(row["source"], str) or not row["source"].strip()):
            missing.append(day)
            continue
        bars.append((day, close))
    if missing:
        issues.append("benchmark_daily: required window incomplete")
    if issues or len(bars) != required_sessions:
        bars = []
    return BenchmarkInputBuild(
        as_of=as_of, required_sessions=required_sessions, bars=tuple(bars),
        missing_days=tuple(missing), issues=tuple(sorted(set(issues))),
        certification_issues=certifications,
    )
