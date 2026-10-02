"""Build an as-of research input from historical market facts.

This boundary deliberately fails closed. It does not infer historical ST status,
industry membership, or tradability from the current instrument catalog.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd
import numpy as np

from backend.modules.quant_research.application.coverage_audit import (
    CoverageAudit,
    audit_historical_coverage,
)


@dataclass(frozen=True)
class DatasetQuality:
    status: str
    issues: tuple[str, ...]
    as_of: date
    source_rows: Mapping[str, int]
    certification_issues: tuple[str, ...] = ()
    coverage: CoverageAudit | None = None
    instrument_types: tuple[str, ...] = ()

    @property
    def certifiable(self) -> bool:
        return self.status == "READY" and not self.certification_issues


@dataclass(frozen=True)
class ResearchDataset:
    tables: Mapping[str, pd.DataFrame]
    quality: DatasetQuality


def _trade_date_timestamp(value):
    if isinstance(value, datetime):
        return pd.Timestamp(value.date()) if value.tzinfo is None else pd.NaT
    if isinstance(value, date):
        return pd.Timestamp(value)
    if isinstance(value, np.datetime64):
        parsed = pd.Timestamp(value)
        return pd.Timestamp(parsed.date()) if not pd.isna(parsed) else pd.NaT
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            return pd.NaT
        return pd.Timestamp(parsed) if parsed.isoformat() == value else pd.NaT
    return pd.NaT


class DatasetBuilder:
    """Filter source facts at their *historical availability* time."""

    REQUIRED_TABLES = ("instrument", "daily", "adj_factor", "factor", "trade_status")
    REQUIRED_DAILY = ("ts_code", "trade_date", "open", "high", "low", "close")

    def build(
        self,
        *,
        as_of: date,
        tables: Mapping[str, pd.DataFrame],
        require_historical_status: bool = True,
        verified_trading_days: tuple[date, ...] | None = None,
        instrument_types: tuple[str, ...] = ("stock", "fund"),
    ) -> ResearchDataset:
        if (not instrument_types or len(set(instrument_types)) != len(instrument_types)
                or not set(instrument_types) <= {"stock", "fund"}):
            raise ValueError("research instrument types must be unique stock/fund values")
        missing = set(self.REQUIRED_TABLES) - tables.keys()
        if missing:
            raise ValueError(f"missing source tables: {', '.join(sorted(missing))}")
        selected_codes = None
        if isinstance(tables["instrument"], pd.DataFrame) and {"ts_code", "instrument_type"} <= set(tables["instrument"]):
            selected_codes = set(tables["instrument"].loc[
                tables["instrument"]["instrument_type"].isin(instrument_types), "ts_code",
            ])
        frozen: dict[str, pd.DataFrame] = {}
        issues: list[str] = []
        certification_issues: list[str] = []
        # A table's presence does not prove complete historical dates, symbols,
        # point-in-time availability or corporate-action coverage. T5 must supply
        # an audited coverage certificate before any performance admission.
        certification_issues.append("coverage_audit: historical completeness not verified")
        table_names = (*self.REQUIRED_TABLES, *(
            name for name in ("etf_catalog", "suspension_evidence", "benchmark_daily")
            if name in tables and (name != "benchmark_daily" or "stock" in instrument_types)
        ))
        for name in table_names:
            source = tables[name]
            if not isinstance(source, pd.DataFrame):
                raise TypeError(f"{name} must be a DataFrame")
            frame = source.copy(deep=True)
            if name == "instrument" and "instrument_type" in frame:
                frame = frame.loc[frame["instrument_type"].isin(instrument_types)].copy()
            elif name != "benchmark_daily" and selected_codes is not None and "ts_code" in frame:
                frame = frame.loc[frame["ts_code"].isin(selected_codes)].copy()
            if "trade_date" in frame:
                trade_days = pd.Series(
                    (_trade_date_timestamp(value) for value in frame["trade_date"]),
                    index=frame.index, dtype="datetime64[ns]",
                )
                if trade_days.isna().any():
                    issues.append(f"{name}: invalid trade_date")
                frame = frame.loc[trade_days.notna() & (trade_days <= pd.Timestamp(as_of))].copy()
                frame["trade_date"] = trade_days.loc[frame.index].dt.date
            if "available_at" in frame:
                available = pd.to_datetime(frame["available_at"], utc=True, errors="coerce")
                if available.isna().any():
                    issues.append(f"{name}: unknown available_at")
                cutoff = pd.Timestamp(as_of).tz_localize("Asia/Shanghai") + pd.Timedelta(days=1)
                frame = frame.loc[available.notna() & (available < cutoff.tz_convert("UTC"))].copy()
            if name == "suspension_evidence":
                if "published_on" not in frame:
                    raise ValueError("official suspension publication date absent")
                published = pd.to_datetime(frame["published_on"], errors="coerce").dt.date
                if published.isna().any():
                    raise ValueError("invalid official suspension publication date")
                frame = frame.loc[published <= as_of].copy()
            if "ts_code" in frame:
                sort_cols = [column for column in ("ts_code", "trade_date") if column in frame]
                frame = frame.sort_values(sort_cols, kind="stable").reset_index(drop=True)
            frozen[name] = frame

        benchmark = frozen.get("benchmark_daily")
        if benchmark is not None and not benchmark.empty:
            if not {"ts_code", "trade_date", "close", "source"} <= set(benchmark):
                issues.append("benchmark_daily: required columns absent")
            else:
                closes = pd.to_numeric(benchmark["close"], errors="coerce").to_numpy(dtype=float)
                if (benchmark["ts_code"].ne("000300.SH").any()
                        or benchmark.duplicated(["ts_code", "trade_date"]).any()
                        or benchmark["close"].map(lambda value: isinstance(value, (bool, np.bool_))).any()
                        or not (np.isfinite(closes) & (closes > 0)).all()
                        or benchmark["source"].isna().any()
                        or benchmark["source"].astype(str).str.strip().eq("").any()):
                    issues.append("benchmark_daily: invalid index observations")
        if "stock" in instrument_types:
            certification_issues.append(
                "benchmark_daily: HS300 evidence absent" if benchmark is None or benchmark.empty
                else ("benchmark_daily: source availability not verified" if verified_trading_days is not None
                      else "benchmark_daily: exchange-calendar and source availability not verified")
            )

        instrument = frozen["instrument"]
        daily = frozen["daily"]
        if "ts_code" in instrument and instrument["ts_code"].duplicated().any():
            issues.append("instrument: duplicate identity")
        if not set(self.REQUIRED_DAILY) <= set(daily):
            issues.append("daily: required OHLC columns absent")
        if not {"ts_code", "instrument_type", "list_date", "delist_date"} <= set(instrument):
            issues.append("instrument: historical listing metadata absent")
        else:
            # A current D (delisted) status must not erase a stock from its past.
            listed = instrument.loc[instrument["instrument_type"].isin(("stock", "fund"))].copy()
            listed_dates = pd.to_datetime(listed["list_date"], errors="coerce")
            delist_dates = pd.to_datetime(listed["delist_date"], errors="coerce")
            if (listed_dates.isna().any()
                    or (listed["delist_date"].notna() & delist_dates.isna()).any()
                    or (delist_dates.notna() & (delist_dates <= listed_dates)).any()):
                issues.append("instrument: invalid historical listing interval")
            listed = listed.loc[listed_dates.notna() & (listed_dates <= pd.Timestamp(as_of))]
            if {"ts_code", "trade_date"} <= set(daily):
                historical = daily.merge(
                    listed[["ts_code", "list_date", "delist_date"]], on="ts_code", how="inner",
                )
                traded = pd.to_datetime(historical["trade_date"], errors="coerce")
                begins = pd.to_datetime(historical["list_date"], errors="coerce")
                ends = pd.to_datetime(historical["delist_date"], errors="coerce")
                historical = historical.loc[(traded >= begins) & (ends.isna() | (traded < ends))]
                frozen["daily"] = historical.drop(columns=["list_date", "delist_date"]).reset_index(drop=True)
                daily = frozen["daily"]
            # The current catalog can know a later delisting or name change.
            # Only time-stable listing facts cross into the strategy snapshot.
            listed["delist_date"] = listed["delist_date"].where(
                pd.to_datetime(listed["delist_date"], errors="coerce") <= pd.Timestamp(as_of),
            )
            frozen["instrument"] = listed[
                ["ts_code", "instrument_type", "list_date", "delist_date"]
            ].reset_index(drop=True)
            if daily.empty:
                issues.append("daily: no listed instrument observations")

        if {"ts_code", "instrument_type"} <= set(frozen["instrument"]) and "ts_code" in daily:
            fund_codes = set(frozen["instrument"].loc[
                frozen["instrument"]["instrument_type"].eq("fund"), "ts_code",
            ]) & set(daily["ts_code"])
            if fund_codes:
                catalog = frozen.get("etf_catalog")
                required_catalog = {"ts_code", "index_code", "etf_type", "list_date", "available_at"}
                if catalog is None or not required_catalog <= set(catalog):
                    issues.append("etf_catalog: dated classification evidence absent")
                else:
                    subset = catalog[catalog["ts_code"].isin(fund_codes)]
                    if (set(subset["ts_code"]) != fund_codes or subset["ts_code"].duplicated().any()
                            or subset[["index_code", "etf_type", "list_date"]].isna().any().any()
                            or subset["index_code"].astype(str).str.strip().eq("").any()
                            or subset["etf_type"].astype(str).str.upper().str.contains("QDII").any()):
                        issues.append("etf_catalog: historical ETF classification incomplete")

        if "ts_code" in daily and not daily.empty:
            certification_issues.append(
                "corporate_actions: historical adjustment evidence "
                + ("not verified" if "corporate_actions" in tables else "absent")
            )
            certification_issues.append(
                "instrument_rules: historical trading rules "
                + ("not verified" if "instrument_rules" in tables else "absent")
            )
            if ("instrument_type" in frozen["instrument"]
                    and frozen["instrument"]["instrument_type"].eq("stock").any()):
                certification_issues.append(
                    "industry_history: point-in-time membership "
                    + ("not verified" if "industry_history" in tables else "absent")
                )

        if require_historical_status:
            status = frozen["trade_status"]
            stock_codes = set()
            if {"ts_code", "instrument_type"} <= set(frozen["instrument"]):
                stock_codes = set(frozen["instrument"].loc[
                    frozen["instrument"]["instrument_type"].eq("stock"), "ts_code",
                ])
            stock_daily = daily.loc[daily["ts_code"].isin(stock_codes)] if "ts_code" in daily else daily.iloc[0:0]
            if stock_daily.empty:
                pass
            elif not {"ts_code", "trade_date", "is_st", "is_suspended"} <= set(status):
                issues.append("trade_status: historical ST/trading facts absent")
            elif status.empty or not {"ts_code", "trade_date"} <= set(stock_daily):
                issues.append("trade_status: historical coverage empty")
            else:
                key = ["ts_code", "trade_date"]
                uncovered = stock_daily[key].drop_duplicates().merge(
                    status[key].drop_duplicates(), on=key, how="left", indicator=True,
                )
                if uncovered["_merge"].eq("left_only").any():
                    issues.append("trade_status: daily observations lack historical status")
                covered = stock_daily[key].drop_duplicates().merge(status[key + ["is_st", "is_suspended"]],
                                                               on=key, how="inner")
                if (covered[["is_st", "is_suspended"]].isna().any().any()
                        or not covered["is_st"].map(lambda value: isinstance(value, (bool,))).all()
                        or not covered["is_suspended"].map(lambda value: isinstance(value, (bool,))).all()):
                    issues.append("trade_status: invalid historical flags")

        for name in ("daily", "adj_factor", "factor", "trade_status"):
            frame = frozen[name]
            if {"ts_code", "trade_date"} <= set(frame) and frame.duplicated(["ts_code", "trade_date"]).any():
                issues.append(f"{name}: duplicate instrument/day")
        if set(self.REQUIRED_DAILY) <= set(daily):
            numeric = daily[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
            if numeric.isna().any().any() or not numeric.map(lambda value: -float("inf") < value < float("inf")).all().all():
                issues.append("daily: invalid OHLC")
            elif ((numeric <= 0).any().any() or (numeric["high"] < numeric[["open", "close", "low"]].max(axis=1)).any()
                  or (numeric["low"] > numeric[["open", "close", "high"]].min(axis=1)).any()):
                issues.append("daily: impossible OHLC")
        if {"ts_code", "trade_date"} <= set(daily):
            keys = daily[["ts_code", "trade_date"]].drop_duplicates()
            for name in (("adj_factor", "factor") if verified_trading_days is None else ()):
                frame = frozen[name]
                if not {"ts_code", "trade_date"} <= set(frame):
                    issues.append(f"{name}: instrument/day keys absent")
                elif not keys.empty:
                    uncovered = keys.merge(
                        frame[["ts_code", "trade_date"]].drop_duplicates(),
                        on=["ts_code", "trade_date"], how="left", indicator=True,
                    )
                    if uncovered["_merge"].eq("left_only").any():
                        issues.append(f"{name}: daily observations lack matching rows")
        coverage = None
        if verified_trading_days is not None:
            coverage = audit_historical_coverage(
                trading_days=verified_trading_days, tables=frozen,
            )
            if not coverage.complete:
                for component, count in coverage.missing_counts.items():
                    if count:
                        issues.append(f"coverage_audit: {component} missing {count} symbol/day")
        return ResearchDataset(
            tables=frozen,
            quality=DatasetQuality(
                status="READY" if not issues else "INSUFFICIENT_EVIDENCE",
                issues=tuple(sorted(set(issues))),
                as_of=as_of,
                source_rows={name: len(tables[name]) for name in table_names},
                certification_issues=tuple(sorted(set(certification_issues))),
                coverage=coverage,
                instrument_types=instrument_types,
            ),
        )
