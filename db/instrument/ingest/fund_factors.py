"""Backfill observed raw ETF factors against the same-basis local daily bars."""

from __future__ import annotations

import argparse
import logging
from datetime import date, timedelta

import exchange_calendars as xc
import numpy as np
import pandas as pd

from AI.dataflows.providers.base_provider import raise_if_network_access_denied
from db.instrument.dao.factor_daily import upsert_observed_bfq_factors
from db.instrument.ingest.guard import FATAL_INGEST_ERRORS, locked_ingestion

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ("ts_code", "trade_date", "close", "ma_bfq_5", "ma_bfq_20",
                    "ma_bfq_60", "ma_bfq_250", "atr_bfq")
CHUNK_DAYS = 5 * 366  # safely below the upstream 8,000-row limit per symbol
TRUNCATION_ROWS = 8000
REQUIRED_HISTORY_BARS = {
    "ma_bfq_5": 5, "ma_bfq_20": 20, "ma_bfq_60": 60,
    "ma_bfq_250": 250, "atr_bfq": 21,
}


def _local_day_bars(conn, trade_date: str) -> pd.DataFrame:
    rows = conn.execute(
        "SELECT d.ts_code, d.trade_date, d.close FROM market.instrument_daily d "
        "JOIN market.instrument i ON i.ts_code = d.ts_code "
        "WHERE i.instrument_type = 'fund' AND d.trade_date = %s ORDER BY d.ts_code",
        (trade_date,),
    ).fetchall()
    return pd.DataFrame(rows, columns=["ts_code", "trade_date", "local_close"])


def _source_warmup_bars(conn, provider, code: str, listed: date, day: str) -> int | None:
    """Count actual upstream samples only when all dates/prices match local history."""
    if provider is None:
        return None
    frame = provider.get_fund_daily_df(code, listed.isoformat(), day)
    raise_if_network_access_denied(provider)
    if frame is None or frame.empty or not {"ts_code", "trade_date", "close"} <= set(frame.columns):
        return None
    dates = pd.to_datetime(frame["trade_date"].astype(str), errors="coerce")
    if (len(frame) >= 8000 or dates.isna().any() or dates.duplicated().any()
            or not frame["ts_code"].eq(code).all()
            or not dates.dt.date.between(listed, date.fromisoformat(day)).all()):
        return None
    prices = pd.to_numeric(frame["close"], errors="coerce")
    if prices.isna().any() or not np.isfinite(prices).all() or (prices <= 0).any():
        return None
    source = dict(zip(dates.dt.date, prices))
    local = dict(conn.execute(
        "SELECT trade_date,close FROM market.instrument_daily "
        "WHERE ts_code=%s AND trade_date BETWEEN %s AND %s", (code, listed, day),
    ).fetchall())
    if source.keys() != local.keys() or any(
        not np.isclose(source[day], float(local[day]), rtol=0, atol=1e-4) for day in source
    ):
        return None
    return len(source)


def _indicator_gaps(conn, observed: pd.DataFrame, day: str, provider=None) -> tuple[list[str], list[str], list[str]]:
    """Separate source-null indicators inside a proven bar warmup from real gaps."""
    candidates = observed.loc[observed[list(REQUIRED_HISTORY_BARS)].isna().any(axis=1)]
    if candidates.empty:
        return [], [], []
    codes = candidates["ts_code"].astype(str).tolist()
    listings = dict(conn.execute(
        "SELECT ts_code,list_date FROM market.instrument "
        "WHERE instrument_type='fund' AND ts_code=ANY(%s)", (codes,),
    ).fetchall())
    target_day = date.fromisoformat(day)
    known_dates = [listed for listed in listings.values()
                   if listed is not None and listed <= target_day]
    sessions = (xc.get_calendar("XSHG", start=min(known_dates).isoformat(), end=day).sessions
                if known_dates else pd.DatetimeIndex([]))
    unresolved = []
    warmup = []
    source_warmup = []
    for _, row in candidates.iterrows():
        code = str(row["ts_code"])
        listed = listings.get(code)
        observed_sessions = (None if listed is None or listed > target_day else
                             len(sessions) - sessions.searchsorted(pd.Timestamp(listed)))
        if observed_sessions is None:
            unresolved.append(code)
        elif any(
            pd.isna(row[column]) and observed_sessions >= required
            for column, required in REQUIRED_HISTORY_BARS.items()
        ):
            source_count = _source_warmup_bars(conn, provider, code, listed, day)
            if source_count is not None and all(
                not pd.isna(row[column]) or source_count < required
                for column, required in REQUIRED_HISTORY_BARS.items()
            ):
                warmup.append(code)
                source_warmup.append(code)
            else:
                unresolved.append(code)
        else:
            warmup.append(code)
    return unresolved, warmup, source_warmup


def _collect_fund_factor_day_unlocked(conn, provider, trade_date: str) -> dict:
    """Persist observed daily fund factors and expose every missing local bar.

    A PARTIAL result is never a successful coverage watermark. Price-basis or
    upstream identity errors reject the whole day before a database write.
    """
    day = date.fromisoformat(trade_date).isoformat()
    bars = _local_day_bars(conn, day)
    if bars.empty or bars["ts_code"].duplicated().any():
        raise ValueError("FUND_DAY_BARS_UNAVAILABLE")
    bars["trade_date"] = pd.to_datetime(bars["trade_date"]).dt.strftime("%Y-%m-%d")
    frame = provider.get_full_market_fund_factor_df(day)
    raise_if_network_access_denied(provider)
    if frame is None or frame.empty or len(frame) >= TRUNCATION_ROWS:
        raise ValueError("FUND_DAY_FACTORS_UNAVAILABLE_OR_TRUNCATED")
    if not set(REQUIRED_COLUMNS).issubset(frame.columns):
        raise ValueError("FUND_FACTOR_COLUMNS_MISSING")
    factors = frame.copy()
    dates = pd.to_datetime(factors["trade_date"].astype(str), errors="coerce")
    if dates.isna().any() or not dates.dt.date.eq(date.fromisoformat(day)).all():
        raise ValueError("FUND_FACTOR_DATE_MISMATCH")
    factors["trade_date"] = dates.dt.strftime("%Y-%m-%d")
    if factors["ts_code"].isna().any() or factors.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("FUND_FACTOR_DUPLICATE_KEYS")
    numeric = [col for col in factors.columns if col not in ("ts_code", "trade_date")]
    for col in numeric:
        raw = factors[col]
        factors[col] = pd.to_numeric(raw, errors="coerce")
        if (raw.notna() & factors[col].isna()).any():
            raise ValueError("FUND_FACTOR_INVALID_VALUE")
        if not np.isfinite(factors[col].dropna().to_numpy(dtype=float)).all():
            raise ValueError("FUND_FACTOR_NONFINITE_VALUE")
    if factors["close"].isna().any() or (factors["close"] <= 0).any():
        raise ValueError("FUND_FACTOR_INVALID_CLOSE")
    if (factors["atr_bfq"].dropna() <= 0).any():
        raise ValueError("FUND_FACTOR_INVALID_ATR")

    aligned = bars.merge(factors, on=["ts_code", "trade_date"], how="left",
                         validate="one_to_one", indicator=True)
    observed = aligned[aligned["_merge"].eq("both")].copy()
    if observed.empty:
        raise ValueError("FUND_FACTOR_BAR_COVERAGE_INCOMPLETE")
    if not np.isclose(observed["local_close"].astype(float), observed["close"].astype(float),
                      rtol=0, atol=1e-4).all():
        raise ValueError("FUND_FACTOR_PRICE_BASIS_MISMATCH")
    missing = aligned.loc[aligned["_merge"].ne("both"), "ts_code"].astype(str).tolist()
    incomplete, warmup, source_warmup = _indicator_gaps(conn, observed, day, provider)
    observed = observed[factors.columns].copy()
    observed["updated_at"] = pd.Timestamp.now(tz="UTC")
    try:
        rows = upsert_observed_bfq_factors(conn, observed)
        conn.commit()
    except FATAL_INGEST_ERRORS:
        raise
    except Exception:
        conn.rollback()
        raise
    return {"status": "PARTIAL" if missing or incomplete else "SUCCESS", "trade_date": day,
            "bar_rows": len(bars), "factor_rows": rows, "missing_codes": missing,
            "incomplete_indicator_codes": incomplete,
            "warmup_indicator_codes": warmup, "coverage": len(observed) / len(bars),
            "source_history_warmup_codes": source_warmup,
            "complete_indicator_coverage": (len(observed) - len(incomplete)) / len(bars)}


collect_fund_factor_day = locked_ingestion("CN_STOCK_QUANT_INPUTS")(
    _collect_fund_factor_day_unlocked)


def _local_bars(conn, code: str, start: str, end: str) -> pd.DataFrame:
    rows = conn.execute(
        "SELECT d.trade_date, d.close FROM market.instrument_daily d "
        "JOIN market.instrument i ON i.ts_code = d.ts_code "
        "WHERE d.ts_code = %s AND i.instrument_type = 'fund' "
        "AND d.trade_date BETWEEN %s AND %s ORDER BY d.trade_date",
        (code, start, end),
    ).fetchall()
    return pd.DataFrame(rows, columns=["trade_date", "local_close"])


def _collect_fund_factors_unlocked(conn, provider, code: str, start: str, end: str) -> dict:
    """Commit only complete, price-aligned factor observations for one fund.

    A local daily-bar gap is reported by the dataset builder; it is not filled by
    this factor-only collector. A missing factor on any existing bar fails closed.
    """
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last or not code or not code.endswith((".SH", ".SZ")):
        raise ValueError("invalid fund code or date range")
    bars = _local_bars(conn, code, start, end)
    if bars.empty:
        raise ValueError("FUND_BARS_UNAVAILABLE")
    bars["trade_date"] = pd.to_datetime(bars["trade_date"]).dt.strftime("%Y-%m-%d")
    first = max(first, date.fromisoformat(bars["trade_date"].min()))
    last = min(last, date.fromisoformat(bars["trade_date"].max()))

    frames = []
    chunk_start = first
    while chunk_start <= last:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), last)
        if not bars["trade_date"].between(chunk_start.isoformat(), chunk_end.isoformat()).any():
            chunk_start = chunk_end + timedelta(days=1)
            continue
        frame = provider.get_fund_factor_df(code, chunk_start.isoformat(), chunk_end.isoformat())
        raise_if_network_access_denied(provider)
        if frame is None or frame.empty:
            raise ValueError("FUND_FACTORS_UNAVAILABLE")
        if not set(REQUIRED_COLUMNS).issubset(frame.columns):
            raise ValueError("FUND_FACTOR_COLUMNS_MISSING")
        frame = frame.copy()
        dates = pd.to_datetime(frame["trade_date"], errors="coerce")
        if dates.isna().any() or not dates.dt.date.between(chunk_start, chunk_end).all():
            raise ValueError("FUND_FACTOR_DATE_MISMATCH")
        frame["trade_date"] = dates.dt.strftime("%Y-%m-%d")
        if not frame["ts_code"].astype(str).eq(code).all():
            raise ValueError("FUND_FACTOR_CODE_MISMATCH")
        frames.append(frame)
        chunk_start = chunk_end + timedelta(days=1)

    factors = pd.concat(frames, ignore_index=True)
    if factors.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("FUND_FACTOR_DUPLICATE_KEYS")
    numeric = [col for col in factors.columns if col not in ("ts_code", "trade_date")]
    for col in numeric:
        raw = factors[col]
        factors[col] = pd.to_numeric(raw, errors="coerce")
        if (raw.notna() & factors[col].isna()).any():
            raise ValueError("FUND_FACTOR_INVALID_VALUE")
        if not np.isfinite(factors[col].dropna().to_numpy(dtype=float)).all():
            raise ValueError("FUND_FACTOR_NONFINITE_VALUE")
    if factors["close"].isna().any() or (factors["close"] <= 0).any():
        raise ValueError("FUND_FACTOR_INVALID_CLOSE")
    if (factors["atr_bfq"].dropna() <= 0).any():
        raise ValueError("FUND_FACTOR_INVALID_ATR")

    aligned = bars.merge(factors, on="trade_date", how="left", validate="one_to_one", indicator=True)
    if not aligned["_merge"].eq("both").all():
        raise ValueError("FUND_FACTOR_BAR_COVERAGE_INCOMPLETE")
    if not np.isclose(aligned["local_close"].astype(float), aligned["close"].astype(float),
                      rtol=0, atol=1e-4).all():
        raise ValueError("FUND_FACTOR_PRICE_BASIS_MISMATCH")
    observed = factors[factors["trade_date"].isin(set(bars["trade_date"]))].copy()
    unresolved_days = []
    warmup_days = []
    for day, day_frame in observed.groupby("trade_date", sort=True):
        unresolved, warmup, _ = _indicator_gaps(conn, day_frame, day, provider)
        if unresolved:
            unresolved_days.append(day)
        elif warmup:
            warmup_days.append(day)
    observed["updated_at"] = pd.Timestamp.now(tz="UTC")
    try:
        rows = upsert_observed_bfq_factors(conn, observed)
        conn.commit()
    except FATAL_INGEST_ERRORS:
        raise
    except Exception:
        conn.rollback()
        raise
    return {"status": "PARTIAL" if unresolved_days else "SUCCESS", "code": code,
            "bar_rows": len(bars), "factor_rows": rows, "start": start, "end": end,
            "incomplete_indicator_days": unresolved_days, "warmup_indicator_days": warmup_days}


collect_fund_factors = locked_ingestion("CN_STOCK_QUANT_INPUTS")(_collect_fund_factors_unlocked)


def main(argv: list[str] | None = None) -> int:
    """Explicit, resumable symbol backfill; no implicit all-fund request."""
    parser = argparse.ArgumentParser(description="Backfill observed raw ETF factors")
    parser.add_argument("--codes", required=True, help="Comma-separated exchange-qualified fund codes")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    args = parser.parse_args(argv)
    codes = tuple(dict.fromkeys(code.strip().upper() for code in args.codes.split(",")))
    if not codes or any(not code for code in codes):
        parser.error("--codes requires nonempty exchange-qualified codes")
    try:
        if date.fromisoformat(args.start) > date.fromisoformat(args.end):
            parser.error("--start must not be after --end")
    except ValueError:
        parser.error("--start/--end must be YYYY-MM-DD")

    from AI.dataflows.providers.base_provider import ProviderNetworkAccessDenied
    from AI.dataflows.providers.cn.tushare import TushareProvider
    from db.instrument.db import get_connection
    from db.instrument.ingest.guard import IngestGuard
    from db.instrument.ingest.notifications import market_changed_notifier_from_env

    failures = []
    with get_connection() as conn, IngestGuard(conn, changed=market_changed_notifier_from_env()) as guard:
        provider = TushareProvider()
        for code in codes:
            try:
                result = collect_fund_factors(conn, provider, code, args.start, args.end, guard=guard)
                if result["status"] != "SUCCESS":
                    failures.append(code)
                logger.info("fund factor complete: %s %s", code, result)
            except (ProviderNetworkAccessDenied, *FATAL_INGEST_ERRORS):
                raise
            except Exception as exc:  # noqa: BLE001 - one symbol must not erase other commits
                conn.rollback()
                failures.append(code)
                logger.error("fund factor failed: %s: %s", code, exc)
    if failures:
        logger.error("retry failed fund codes with the same arguments: %s", ",".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
