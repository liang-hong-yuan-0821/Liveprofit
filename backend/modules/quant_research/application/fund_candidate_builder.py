"""Diagnose ETF trial inputs from a frozen research snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Mapping

import pandas as pd

from backend.modules.quant_research.application.dataset_builder import ResearchDataset
from backend.modules.quant_strategy.domain.etf_dual_momentum import (
    ELIGIBLE_CATEGORIES, FundCandidate,
)


@dataclass(frozen=True)
class FundClassificationFacts:
    tracking_index: str
    category: str
    suspended: bool

    def __post_init__(self):
        if (not isinstance(self.tracking_index, str) or not self.tracking_index.strip()
                or self.category not in ELIGIBLE_CATEGORIES | {"OTHER"}
                or type(self.suspended) is not bool):
            raise ValueError("explicit ETF classification and suspension facts required")


@dataclass(frozen=True)
class FundCandidateBuild:
    candidates: tuple[FundCandidate, ...]
    gaps: Mapping[str, tuple[str, ...]]
    excluded_codes: tuple[str, ...]
    certification_issues: tuple[str, ...]


def _positive(value) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return result if result.is_finite() and result > 0 else None


def _rows(frame: pd.DataFrame, code: str) -> tuple[dict[date, dict], bool]:
    if not {"ts_code", "trade_date"} <= set(frame):
        return {}, True
    selected = frame.loc[frame["ts_code"].eq(code)]
    if selected["trade_date"].map(lambda value: type(value) is not date).any():
        return {}, True
    if selected["trade_date"].duplicated().any():
        return {}, True
    return {row["trade_date"]: row for row in selected.to_dict("records")}, False


def _available_before(value, cutoff: pd.Timestamp) -> bool:
    if not isinstance(value, (datetime, str)):
        return False
    try:
        observed = pd.Timestamp(value)
    except (TypeError, ValueError):
        return False
    return not pd.isna(observed) and observed.tzinfo is not None and observed < cutoff


def build_fund_candidates(
    dataset: ResearchDataset, *, as_of: date, trading_days: tuple[date, ...],
    classification_facts: Mapping[str, FundClassificationFacts],
) -> FundCandidateBuild:
    """Return local ETF candidates and omissions, never a production certificate."""
    if not isinstance(dataset, ResearchDataset) or type(as_of) is not date:
        raise TypeError("frozen dataset and exact as-of date required")
    if dataset.quality.as_of != as_of:
        raise ValueError("ETF candidate date differs from research snapshot")
    if (not trading_days or trading_days[-1] != as_of
            or any(type(day) is not date for day in trading_days)
            or tuple(sorted(set(trading_days))) != trading_days):
        raise ValueError("verified increasing exchange calendar ending at as-of required")
    if not isinstance(classification_facts, Mapping) or any(
        not isinstance(code, str) or not isinstance(facts, FundClassificationFacts)
        for code, facts in classification_facts.items()
    ):
        raise TypeError("explicit ETF classification mapping required")
    required = {"instrument", "daily", "adj_factor", "factor"}
    if not required <= dataset.tables.keys() or any(
        not isinstance(dataset.tables[name], pd.DataFrame) for name in required
    ):
        raise ValueError("research ETF input tables absent")
    instrument = dataset.tables["instrument"]
    if not {"ts_code", "instrument_type", "list_date", "delist_date"} <= set(instrument):
        raise ValueError("dated fund identities absent")
    fund = instrument.loc[instrument["instrument_type"].eq("fund")]
    if fund["ts_code"].isna().any() or fund["ts_code"].duplicated().any():
        raise ValueError("invalid fund identity")
    catalog = dataset.tables.get("etf_catalog")
    required_catalog = {"ts_code", "index_code", "etf_type", "list_date", "list_status", "available_at"}
    if not isinstance(catalog, pd.DataFrame) or not required_catalog <= set(catalog):
        catalog = pd.DataFrame(columns=tuple(required_catalog))
    catalog_rows: dict[str, list[dict]] = {}
    for row in catalog.to_dict("records"):
        code = row["ts_code"]
        if isinstance(code, str):
            catalog_rows.setdefault(code, []).append(row)

    active: dict[str, date] = {}
    for row in fund.itertuples(index=False):
        if (not isinstance(row.ts_code, str) or not row.ts_code
                or type(row.list_date) is not date
                or not pd.isna(row.delist_date) and type(row.delist_date) is not date):
            raise ValueError("dated fund listing evidence invalid")
        if row.list_date <= as_of and (pd.isna(row.delist_date) or as_of < row.delist_date):
            active[row.ts_code] = row.list_date
    cutoff = pd.Timestamp(as_of).tz_localize("Asia/Shanghai") + pd.Timedelta(days=1)
    candidates: list[FundCandidate] = []
    gaps: dict[str, tuple[str, ...]] = {}
    excluded: list[str] = []
    for code in sorted(active):
        problems: list[str] = []
        if not code.endswith((".SH", ".SZ")):
            problems.append("unsupported ETF market")
        entries = catalog_rows.get(code, [])
        if len(entries) != 1:
            problems.append("dated ETF catalog absent or duplicate")
        facts = classification_facts.get(code)
        if facts is None:
            problems.append("ETF category or suspension fact absent")
        if problems:
            gaps[code] = tuple(problems)
            continue
        entry = entries[0]
        if (not _available_before(entry["available_at"], cutoff)
                or type(entry["list_date"]) is not date
                or entry["list_date"] != active[code]
                or not isinstance(entry["index_code"], str)
                or not entry["index_code"].strip()
                or entry["index_code"] != facts.tracking_index
                or not isinstance(entry["list_status"], str)
                or entry["list_status"] != "L"
                or not isinstance(entry["etf_type"], str)
                or not entry["etf_type"].strip()
                or "QDII" in entry["etf_type"].upper()):
            gaps[code] = ("ETF catalog identity or availability invalid",)
            continue
        if facts.category == "OTHER":
            excluded.append(code)
            continue
        daily, bad_daily = _rows(dataset.tables["daily"], code)
        adjustment, bad_adjustment = _rows(dataset.tables["adj_factor"], code)
        factors, bad_factor = _rows(dataset.tables["factor"], code)
        if bad_daily or bad_adjustment or bad_factor:
            problems.append("invalid or duplicate ETF source date")
        if any(day > as_of for day in (*daily, *adjustment, *factors)):
            problems.append("future ETF market fact")
        if as_of not in daily or as_of not in adjustment or as_of not in factors:
            problems.append("as-of ETF price, adjustment or ATR absent")
        if problems:
            gaps[code] = tuple(problems)
            continue
        latest_factor = _positive(adjustment[as_of].get("adj_factor"))
        atr = _positive(factors[as_of].get("atr_bfq"))
        if latest_factor is None or atr is None:
            gaps[code] = ("as-of ETF adjustment or ATR invalid",)
            continue
        bars: list[tuple[date, Decimal]] = []
        for day in trading_days:
            if day < active[code] or day not in daily or day not in adjustment:
                continue
            close = _positive(daily[day].get("close"))
            multiplier = _positive(adjustment[day].get("adj_factor"))
            if close is None or multiplier is None:
                problems.append("ETF price or adjustment invalid")
                break
            bars.append((day, close * multiplier / latest_factor))
        if len(bars) < 250:
            problems.append("fewer than 250 evidenced ETF bars")
        if not bars or bars[-1][0] != as_of:
            problems.append("as-of adjusted ETF bar absent")
        last20 = trading_days[-20:]
        if len(last20) < 20:
            problems.append("ETF ADV20 calendar shorter than 20 sessions")
        amounts = [_positive(daily[day].get("amount")) if day in daily else None for day in last20]
        if any(value is None for value in amounts):
            problems.append("ETF ADV20 amount incomplete")
        if any(day not in daily or day not in adjustment
               or _positive(daily[day].get("close")) is None
               or _positive(adjustment[day].get("adj_factor")) is None
               for day in last20):
            problems.append("ETF recent adjusted-price window incomplete")
        if problems:
            gaps[code] = tuple(dict.fromkeys(problems))
            continue
        candidates.append(FundCandidate(
            ts_code=code, tracking_index=facts.tracking_index, category=facts.category,
            trade_date=as_of, listed_sessions=len(bars),
            adv20_cny=sum(amounts, Decimal(0)) * Decimal(1000) / Decimal(20),
            adjusted_bars=tuple(bars), atr20_raw=atr, suspended=facts.suspended,
        ))
    certificates = set(dataset.quality.certification_issues) | {
        "etf_classification: independent category source not verified",
        "etf_status: suspension source not verified",
        "etf_rules: historical trading rules not verified",
    }
    return FundCandidateBuild(tuple(candidates), gaps, tuple(excluded), tuple(sorted(certificates)))
