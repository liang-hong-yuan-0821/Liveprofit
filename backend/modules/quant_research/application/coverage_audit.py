"""Historical symbol/day coverage audit against an explicit exchange calendar."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CoverageGap:
    component: str
    ts_code: str
    trade_date: date


@dataclass(frozen=True)
class CoverageAudit:
    expected_symbol_days: int
    missing_counts: Mapping[str, int]
    not_applicable_counts: Mapping[str, int]
    sample_gaps: tuple[CoverageGap, ...]
    sample_truncated: bool

    @property
    def complete(self) -> bool:
        return self.expected_symbol_days > 0 and not any(self.missing_counts.values())


def _keys(frame: pd.DataFrame) -> set[tuple[str, date]]:
    if not {"ts_code", "trade_date"} <= set(frame):
        return set()
    dates = pd.to_datetime(frame["trade_date"].astype(str), errors="coerce")
    normalized = pd.DataFrame({"ts_code": frame["ts_code"].astype(str), "trade_date": dates.dt.date})
    if dates.isna().any() or frame["ts_code"].isna().any() or normalized.duplicated().any():
        raise ValueError("invalid or duplicate historical symbol/day identity")
    return set(zip(normalized["ts_code"], normalized["trade_date"]))


def _valid_values(frame: pd.DataFrame, columns: tuple[str, ...]) -> set[tuple[str, date]]:
    if not set(columns) <= set(frame):
        return set()
    numeric = frame[list(columns)].apply(pd.to_numeric, errors="coerce")
    values = numeric.to_numpy(dtype=float)
    valid = np.isfinite(values).all(axis=1) & (values > 0).all(axis=1)
    return _keys(frame.loc[valid])


def audit_historical_coverage(
    *, trading_days: tuple[date, ...], tables: Mapping[str, pd.DataFrame],
    sample_limit: int = 100,
) -> CoverageAudit:
    """Audit listed stock/fund observations without inventing a missing market day.

    ``trading_days`` must come from an independently verified calendar. A date
    inferred from stored bars would hide an entire missing trading day.
    """
    if (not trading_days or any(type(day) is not date for day in trading_days)
            or tuple(sorted(set(trading_days))) != trading_days):
        raise ValueError("verified, unique, increasing trading calendar required")
    if sample_limit < 0:
        raise ValueError("sample_limit must be nonnegative")
    required = {"instrument", "daily", "adj_factor", "factor", "trade_status"}
    if not required <= tables.keys():
        raise ValueError("historical coverage inputs incomplete")
    instrument = tables["instrument"]
    if not {"ts_code", "instrument_type", "list_date", "delist_date"} <= set(instrument):
        raise ValueError("historical listing evidence absent")
    if instrument["ts_code"].isna().any() or instrument["ts_code"].duplicated().any():
        raise ValueError("duplicate instrument identity")

    for name in ("daily", "adj_factor", "factor", "trade_status"):
        _keys(tables[name])  # validate all identities, even rows with bad values
    daily_frame = tables["daily"]
    daily = _valid_values(daily_frame, ("open", "high", "low", "close"))
    if {"open", "high", "low", "close"} <= set(daily_frame):
        numeric = daily_frame[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
        valid_range = (numeric["high"] >= numeric[["open", "close", "low"]].max(axis=1)) & (
            numeric["low"] <= numeric[["open", "close", "high"]].min(axis=1))
        daily &= _keys(daily_frame.loc[valid_range])
    adjustment = _valid_values(tables["adj_factor"], ("adj_factor",))
    stock_factors = _valid_values(tables["factor"], (
        "ma_qfq_5", "ma_qfq_20", "ma_qfq_60", "atr_qfq",
    ))
    fund_factors = _valid_values(tables["factor"], (
        "ma_bfq_5", "ma_bfq_20", "ma_bfq_60", "ma_bfq_250", "atr_bfq",
    ))
    status_frame = tables["trade_status"]
    statuses: set[tuple[str, date]] = set()
    suspended: set[tuple[str, date]] = set()
    vendor_intraday: set[tuple[str, date]] = set()
    status_columns = {"ts_code", "trade_date", "is_st", "is_suspended",
                      "suspension_scope", "market_board", "source"}
    if status_columns <= set(status_frame):
        trusted = status_frame.loc[
            status_frame["is_st"].map(lambda value: isinstance(value, (bool, np.bool_)))
            & status_frame["is_suspended"].map(lambda value: isinstance(value, (bool, np.bool_)))
            & status_frame["market_board"].notna()
            & status_frame["market_board"].astype(str).str.strip().ne("")
            & status_frame["source"].isin(("tushare", "tushare+baostock"))
            & (~status_frame["is_suspended"].eq(True)
               | status_frame["suspension_scope"].isin(("full_day", "intraday")))
        ]
        statuses = _keys(trusted)
        suspended = _keys(trusted.loc[
            trusted["is_suspended"].eq(True)
            & trusted["suspension_scope"].eq("full_day")
        ])
        vendor_intraday = _keys(trusted.loc[
            trusted["is_suspended"].eq(True)
            & trusted["suspension_scope"].eq("intraday")
        ])
    official_frame = tables.get("suspension_evidence")
    if official_frame is not None:
        if not {"ts_code", "trade_date", "scope"} <= set(official_frame):
            raise ValueError("official suspension evidence columns missing")
        official_keys = _keys(official_frame)
        if not official_frame["scope"].isin(("full_day", "intraday")).all():
            raise ValueError("invalid official suspension scope")
        official_full_day = _keys(official_frame.loc[official_frame["scope"].eq("full_day")])
        official_intraday = official_keys - official_full_day
        if (official_full_day & daily or official_intraday - daily
                or official_full_day & vendor_intraday
                or official_intraday & suspended):
            raise ValueError("official suspension scope conflicts with daily bar")
        suspended |= official_full_day

    listed = instrument[instrument["instrument_type"].isin(("stock", "fund"))].copy()
    begins = pd.to_datetime(listed["list_date"].astype(str), errors="coerce")
    ends = pd.to_datetime(listed["delist_date"].astype(str), errors="coerce")
    if begins.isna().any() or (listed["delist_date"].notna() & ends.isna()).any():
        raise ValueError("invalid listing dates")
    counts: dict[str, int] = {key: 0 for key in (
        "daily", "adj_factor", "factor", "stock_trade_status", "fund_trade_rules",
    )}
    benchmark_frame = tables.get("benchmark_daily")
    if benchmark_frame is not None and listed["instrument_type"].eq("stock").any():
        counts["benchmark_daily"] = 0
    not_applicable = {"trusted_suspension": 0, "factor_warmup": 0}
    samples: list[CoverageGap] = []
    expected = 0

    def gap(component: str, code: str, day: date) -> None:
        counts[component] += 1
        if len(samples) < sample_limit:
            samples.append(CoverageGap(component, code, day))

    if "benchmark_daily" in counts:
        all_benchmark_keys = _keys(benchmark_frame)
        numeric_benchmark = benchmark_frame.loc[
            ~benchmark_frame["close"].map(lambda value: isinstance(value, (bool, np.bool_)))
        ] if "close" in benchmark_frame else benchmark_frame
        benchmark_keys = _valid_values(numeric_benchmark, ("close",))
        calendar_set = set(trading_days)
        if any(code != "000300.SH" or (
            trading_days[0] <= day <= trading_days[-1] and day not in calendar_set
        ) for code, day in all_benchmark_keys):
            raise ValueError("benchmark index identity or exchange date invalid")
        for day in trading_days:
            if ("000300.SH", day) not in benchmark_keys:
                gap("benchmark_daily", "000300.SH", day)

    for row, begin, end in zip(listed.itertuples(index=False), begins.dt.date, ends.dt.date):
        code = str(row.ts_code)
        kind = row.instrument_type
        if kind == "stock" and code.endswith(".BJ"):
            begin = max(begin, date(2021, 11, 15))
        # A warmup exemption is provable only when this calendar includes the
        # listing date. A window starting after listing cannot infer prior bars.
        warmup_known = begin >= trading_days[0]
        observed_bars = 0
        for day in trading_days:
            if not (begin <= day and (pd.isna(end) or day < end)):
                continue
            expected += 1
            key = (code, day)
            # An S event may be intraday. A real bar proves this symbol/day
            # still needs adjustment and indicators for historical research.
            trusted_suspension = kind == "stock" and key in suspended and key not in daily
            if trusted_suspension:
                not_applicable["trusted_suspension"] += 1
            elif key not in daily:
                gap("daily", code, day)
            if kind == "stock" and key not in statuses:
                gap("stock_trade_status", code, day)
            if kind == "fund":
                # ETF-specific suspension/limit/rule evidence has its own source.
                # Until supplied, fund history cannot be certified by stock status.
                gap("fund_trade_rules", code, day)
            # No actual bar means no price basis for an adjustment or indicator.
            # Its missing daily observation is already counted above.
            if trusted_suspension or key not in daily:
                continue
            observed_bars += 1
            if key not in adjustment:
                gap("adj_factor", code, day)
            minimum_bars = 60 if kind == "stock" else 250
            if key not in (stock_factors if kind == "stock" else fund_factors):
                if warmup_known and observed_bars < minimum_bars:
                    not_applicable["factor_warmup"] += 1
                else:
                    gap("factor", code, day)
    return CoverageAudit(
        expected_symbol_days=expected, missing_counts=counts,
        not_applicable_counts=not_applicable,
        sample_gaps=tuple(samples),
        sample_truncated=sum(counts.values()) > len(samples),
    )
