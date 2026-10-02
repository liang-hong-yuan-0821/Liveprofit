"""Diagnose stock trial inputs from a frozen research snapshot.

The output is local input consistency evidence, not production admission.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Mapping

import pandas as pd
import numpy as np

from backend.modules.quant_research.application.dataset_builder import ResearchDataset
from backend.modules.quant_research.application.target_universe_audit import (
    StockUniverseAudit, audit_stock_target_universe,
)
from backend.modules.quant_strategy.domain.stock_inputs import StockCandidate


@dataclass(frozen=True)
class StockAccountFacts:
    held: bool
    cooldown_until: date | None = None

    def __post_init__(self):
        if type(self.held) is not bool or (
            self.cooldown_until is not None and type(self.cooldown_until) is not date
        ):
            raise TypeError("explicit held and dated cooldown facts required")


@dataclass(frozen=True)
class StockCandidateBuild:
    candidates: tuple[StockCandidate, ...]
    gaps: Mapping[str, tuple[str, ...]]
    universe: StockUniverseAudit


def _positive(value) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def _index(frame: pd.DataFrame, code: str) -> tuple[dict[date, dict], bool]:
    if not {"ts_code", "trade_date"} <= set(frame):
        return {}, True
    selected = frame.loc[frame["ts_code"].eq(code)]
    if selected["trade_date"].map(lambda day: type(day) is not date).any():
        return {}, True
    if selected["trade_date"].duplicated().any():
        return {}, True
    return {row["trade_date"]: row for row in selected.to_dict("records")}, False


def build_stock_candidates(
    dataset: ResearchDataset, *, as_of: date, trading_days: tuple[date, ...],
    account_facts: Mapping[str, StockAccountFacts],
) -> StockCandidateBuild:
    """Build only fully evidenced local candidates; report every omitted code."""
    if not isinstance(dataset, ResearchDataset) or type(as_of) is not date:
        raise TypeError("frozen dataset and exact as-of date required")
    if dataset.quality.as_of != as_of:
        raise ValueError("candidate date differs from research snapshot")
    if (not trading_days or trading_days[-1] != as_of
            or any(type(day) is not date for day in trading_days)
            or tuple(sorted(set(trading_days))) != trading_days):
        raise ValueError("verified increasing exchange calendar ending at as-of required")
    if not isinstance(account_facts, Mapping) or any(
        not isinstance(code, str) or not isinstance(facts, StockAccountFacts)
        for code, facts in account_facts.items()
    ):
        raise TypeError("explicit account facts mapping required")
    required = {"instrument", "daily", "adj_factor", "trade_status"}
    if not required <= dataset.tables.keys() or any(
        not isinstance(dataset.tables[name], pd.DataFrame) for name in required
    ):
        raise ValueError("research stock input tables absent")
    instrument = dataset.tables["instrument"]
    if not {"ts_code", "instrument_type", "list_date", "delist_date"} <= set(instrument):
        raise ValueError("dated stock identities absent")
    stock = instrument.loc[instrument["instrument_type"].eq("stock")]
    if stock["ts_code"].isna().any() or stock["ts_code"].duplicated().any():
        raise ValueError("invalid stock identity")
    active: dict[str, date] = {}
    for row in stock.itertuples(index=False):
        if (not isinstance(row.ts_code, str) or not row.ts_code
                or type(row.list_date) is not date
                or not pd.isna(row.delist_date) and type(row.delist_date) is not date):
            raise ValueError("dated stock listing evidence invalid")
        if row.list_date <= as_of and (pd.isna(row.delist_date) or as_of < row.delist_date):
            active[row.ts_code] = row.list_date
    candidates: list[StockCandidate] = []
    gaps: dict[str, tuple[str, ...]] = {}
    for code in sorted(active):
        problems: list[str] = []
        if not code.endswith((".SH", ".SZ")):
            problems.append("unsupported stock market")
        daily, bad_daily = _index(dataset.tables["daily"], code)
        factor, bad_factor = _index(dataset.tables["adj_factor"], code)
        status, bad_status = _index(dataset.tables["trade_status"], code)
        if bad_daily or bad_factor or bad_status:
            problems.append("invalid or duplicate source date")
        if any(day > as_of for day in (*daily, *factor, *status)):
            problems.append("future market fact")
        if as_of not in daily or as_of not in factor:
            problems.append("as-of price or adjustment absent")
        if as_of not in status:
            problems.append("as-of stock status absent")
        account = account_facts.get(code)
        if account is None:
            problems.append("account facts absent")
        if problems:
            gaps[code] = tuple(problems)
            continue

        latest_factor = _positive(factor[as_of].get("adj_factor"))
        if latest_factor is None:
            gaps[code] = ("invalid as-of adjustment",)
            continue
        bars: list[tuple[date, Decimal]] = []
        for day in trading_days:
            if day < active[code]:
                continue
            raw = daily.get(day)
            adj = factor.get(day)
            if raw is None or adj is None:
                continue
            close = _positive(raw.get("close"))
            multiplier = _positive(adj.get("adj_factor"))
            if close is None or multiplier is None:
                problems.append("invalid price or adjustment")
                break
            bars.append((day, close * multiplier / latest_factor))
        if len(bars) < 250:
            problems.append("fewer than 250 evidenced bars")
        if not bars or bars[-1][0] != as_of:
            problems.append("as-of adjusted bar absent")

        last20 = trading_days[-20:]
        if len(last20) < 20:
            problems.append("ADV20 calendar shorter than 20 sessions")
        amounts = [_positive(daily[day].get("amount")) if day in daily else None for day in last20]
        if any(amount is None for amount in amounts):
            problems.append("ADV20 amount incomplete")
        if any(day not in daily or day not in factor
               or _positive(daily[day].get("close")) is None
               or _positive(factor[day].get("adj_factor")) is None
               for day in last20):
            problems.append("recent adjusted-price window incomplete")
        current = status[as_of]
        is_st, suspended = current.get("is_st"), current.get("is_suspended")
        if (not isinstance(is_st, (bool, np.bool_))
                or not isinstance(suspended, (bool, np.bool_))
                or not isinstance(current.get("market_board"), str)
                or not current["market_board"].strip()
                or current.get("source") not in ("tushare", "tushare+baostock")
                or (bool(suspended) and current.get("suspension_scope") not in ("full_day", "intraday"))):
            problems.append("as-of stock status invalid")
        if problems:
            gaps[code] = tuple(dict.fromkeys(problems))
            continue
        candidates.append(StockCandidate(
            ts_code=code, adjusted_bars=tuple(bars), listed_sessions=len(bars),
            adv20_cny=sum(amounts, Decimal(0)) * Decimal(1000) / Decimal(20),
            is_st=bool(is_st), suspended=bool(suspended), held=account.held,
            cooldown_until=account.cooldown_until,
        ))
    frozen = tuple(candidates)
    return StockCandidateBuild(
        candidates=frozen, gaps=gaps,
        universe=audit_stock_target_universe(dataset, frozen, as_of=as_of),
    )
