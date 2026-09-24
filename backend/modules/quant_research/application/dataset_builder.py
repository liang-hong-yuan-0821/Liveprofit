"""Build an as-of research input from historical market facts.

This boundary deliberately fails closed. It does not infer historical ST status,
industry membership, or tradability from the current instrument catalog.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

import pandas as pd


@dataclass(frozen=True)
class DatasetQuality:
    status: str
    issues: tuple[str, ...]
    as_of: date
    source_rows: Mapping[str, int]


@dataclass(frozen=True)
class ResearchDataset:
    tables: Mapping[str, pd.DataFrame]
    quality: DatasetQuality


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
    ) -> ResearchDataset:
        missing = set(self.REQUIRED_TABLES) - tables.keys()
        if missing:
            raise ValueError(f"missing source tables: {', '.join(sorted(missing))}")
        frozen: dict[str, pd.DataFrame] = {}
        issues: list[str] = []
        for name in self.REQUIRED_TABLES:
            source = tables[name]
            if not isinstance(source, pd.DataFrame):
                raise TypeError(f"{name} must be a DataFrame")
            frame = source.copy(deep=True)
            if "trade_date" in frame:
                trade_days = pd.to_datetime(frame["trade_date"], errors="coerce").dt.date
                if trade_days.isna().any():
                    issues.append(f"{name}: invalid trade_date")
                frame = frame.loc[trade_days.notna() & (trade_days <= as_of)].copy()
            if "available_at" in frame:
                available = pd.to_datetime(frame["available_at"], utc=True, errors="coerce")
                if available.isna().any():
                    issues.append(f"{name}: unknown available_at")
                cutoff = pd.Timestamp(as_of).tz_localize("Asia/Shanghai") + pd.Timedelta(days=1)
                frame = frame.loc[available.notna() & (available < cutoff.tz_convert("UTC"))].copy()
            if "ts_code" in frame:
                sort_cols = [column for column in ("ts_code", "trade_date") if column in frame]
                frame = frame.sort_values(sort_cols, kind="stable").reset_index(drop=True)
            frozen[name] = frame

        instrument = frozen["instrument"]
        daily = frozen["daily"]
        if not set(self.REQUIRED_DAILY) <= set(daily):
            issues.append("daily: required OHLC columns absent")
        if not {"ts_code", "instrument_type", "list_date", "delist_date"} <= set(instrument):
            issues.append("instrument: historical listing metadata absent")
        else:
            # A current D (delisted) status must not erase a stock from its past.
            listed = instrument.loc[instrument["instrument_type"].isin(("stock", "fund"))].copy()
            listed_dates = pd.to_datetime(listed["list_date"], errors="coerce").dt.date
            listed = listed.loc[listed_dates.notna() & (listed_dates <= as_of)]
            if {"ts_code", "trade_date"} <= set(daily):
                historical = daily.merge(
                    listed[["ts_code", "list_date", "delist_date"]], on="ts_code", how="inner",
                )
                traded = pd.to_datetime(historical["trade_date"], errors="coerce").dt.date
                begins = pd.to_datetime(historical["list_date"], errors="coerce").dt.date
                ends = pd.to_datetime(historical["delist_date"], errors="coerce").dt.date
                historical = historical.loc[(traded >= begins) & (ends.isna() | (traded < ends))]
                frozen["daily"] = historical.drop(columns=["list_date", "delist_date"]).reset_index(drop=True)
                daily = frozen["daily"]
            # The current catalog can know a later delisting or name change.
            # Only time-stable listing facts cross into the strategy snapshot.
            listed["delist_date"] = listed["delist_date"].where(
                pd.to_datetime(listed["delist_date"], errors="coerce").dt.date <= as_of,
            )
            frozen["instrument"] = listed[
                ["ts_code", "instrument_type", "list_date", "delist_date"]
            ].reset_index(drop=True)
            if daily.empty:
                issues.append("daily: no listed instrument observations")

        if require_historical_status:
            status = frozen["trade_status"]
            if not {"ts_code", "trade_date", "is_st", "is_suspended"} <= set(status):
                issues.append("trade_status: historical ST/trading facts absent")
            elif daily.empty or status.empty or not {"ts_code", "trade_date"} <= set(daily):
                issues.append("trade_status: historical coverage empty")
            else:
                key = ["ts_code", "trade_date"]
                uncovered = daily[key].drop_duplicates().merge(
                    status[key].drop_duplicates(), on=key, how="left", indicator=True,
                )
                if uncovered["_merge"].eq("left_only").any():
                    issues.append("trade_status: daily observations lack historical status")

        for name in ("daily", "adj_factor", "factor", "trade_status"):
            frame = frozen[name]
            if {"ts_code", "trade_date"} <= set(frame) and frame.duplicated(["ts_code", "trade_date"]).any():
                issues.append(f"{name}: duplicate instrument/day")
        if set(self.REQUIRED_DAILY) <= set(daily):
            numeric = daily[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
            if numeric.isna().any().any() or not numeric.map(lambda value: -float("inf") < value < float("inf")).all().all():
                issues.append("daily: invalid OHLC")
        if {"ts_code", "trade_date"} <= set(daily):
            keys = daily[["ts_code", "trade_date"]].drop_duplicates()
            for name in ("adj_factor", "factor"):
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
        return ResearchDataset(
            tables=frozen,
            quality=DatasetQuality(
                status="READY" if not issues else "INSUFFICIENT_EVIDENCE",
                issues=tuple(sorted(set(issues))),
                as_of=as_of,
                source_rows={name: len(tables[name]) for name in self.REQUIRED_TABLES},
            ),
        )
