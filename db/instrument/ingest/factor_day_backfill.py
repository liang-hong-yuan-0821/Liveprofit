"""Historical stock technical factors by trading day, with per-symbol evidence checks."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import date, timedelta
from pathlib import Path

import exchange_calendars as xc
import numpy as np
import pandas as pd

from AI.dataflows.providers.base_provider import (
    ProviderNetworkAccessDenied,
    raise_if_network_access_denied,
)
from db.instrument.dao.factor_daily import bulk_upsert_factor_daily
from db.instrument.db import get_connection
from db.instrument.ingest.backfill import REQUIRED_BFQ
from db.instrument.ingest.frames import fetch_stock_technical_factor_frame
from db.instrument.ingest.guard import (
    FATAL_INGEST_ERRORS,
    IngestGuard,
    locked_ingestion,
)
from db.instrument.ingest.notifications import market_changed_notifier_from_env
from db.instrument.ingest.stock_factors import REQUIRED_QFQ

logger = logging.getLogger(__name__)

# 仓库根（db/instrument/ingest/ 三级子目录）；脚本路径按仓库根解析、与 CWD 无关
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
FAILURE_LIST = _PROJECT_ROOT / "var" / "logs" / "stock_factor_day_failures.json"
REQUIRED = (*REQUIRED_QFQ, *REQUIRED_BFQ)


def _missing_codes(conn, day: date) -> dict[str, tuple[date, float]]:
    missing = " OR ".join(f"f.{column} IS NULL" for column in REQUIRED_QFQ)
    rows = conn.execute(
        "SELECT d.ts_code, i.list_date, d.close FROM market.instrument_daily d "
        "JOIN market.instrument i ON i.ts_code=d.ts_code "
        "LEFT JOIN market.factor_daily f ON f.ts_code=d.ts_code "
        "AND f.trade_date=d.trade_date "
        "WHERE i.instrument_type='stock' AND d.trade_date=%s "
        "AND i.list_date<=d.trade_date "
        "AND (i.delist_date IS NULL OR d.trade_date<i.delist_date) "
        f"AND (f.ts_code IS NULL OR {missing}) ORDER BY d.ts_code",
        (day,),
    ).fetchall()
    return {code: (listed, float(close)) for code, listed, close in rows}


def _verified_warmup(conn, code: str, listed: date, day: date,
                     provider=None) -> bool:
    if listed > day or (day - listed).days > 60:
        return False
    sessions = {session.date() for session in xc.get_calendar(
        "XSHG", start=listed.isoformat(), end=(day + timedelta(days=1)).isoformat(),
    ).sessions if session.date() <= day}
    if not sessions:
        return False
    local = conn.execute(
        "SELECT trade_date FROM market.instrument_daily "
        "WHERE ts_code=%s AND trade_date BETWEEN %s AND %s",
        (code, listed, day),
    ).fetchall()
    local_dates = {row[0] for row in local}
    if len(local_dates) != len(local) or len(local_dates) > 20 or not local_dates <= sessions:
        return False
    missing = sessions - local_dates
    if not missing:
        return True
    if provider is None:
        return False
    cache = getattr(provider, "_quant_verified_halt_cache", None)
    if cache is None:
        cache = {}
        provider._quant_verified_halt_cache = cache
    for missing_day in sorted(missing):
        if missing_day not in cache:
            frame = provider.get_verified_full_day_suspensions_df(missing_day.isoformat())
            raise_if_network_access_denied(provider)
            cache[missing_day] = (None if frame is None else
                                  set(frame["ts_code"].astype(str)))
        if cache[missing_day] is None or code not in cache[missing_day]:
            return False
    return True


def backfill_factor_day(conn, provider, day: date,
                        aliases: dict[str, str] | None = None) -> dict:
    """Commit valid missing rows only; return every unresolved symbol for the audit."""
    target = _missing_codes(conn, day)
    conn.commit()  # No read transaction may stay open during a network request.
    if not target:
        return {"day": day.isoformat(), "written": 0, "warmup": 0, "unresolved": []}
    frame = fetch_stock_technical_factor_frame(provider, day.isoformat(), list(target))
    raise_if_network_access_denied(provider)
    required = {"ts_code", "trade_date", *REQUIRED}
    if not required <= set(frame.columns):
        raise ValueError("FACTOR_DAY_COLUMNS_MISSING")
    parsed = pd.to_datetime(frame["trade_date"].astype(str), errors="coerce")
    if (parsed.isna().any() or not parsed.dt.date.eq(day).all()
            or frame["ts_code"].isna().any()
            or frame["ts_code"].astype(str).duplicated().any()):
        raise ValueError("FACTOR_DAY_INVALID_KEYS")
    frame = frame.copy()
    frame["source_code"] = frame["ts_code"].astype(str)
    frame["ts_code"] = frame["source_code"].map(aliases or {}).fillna(frame["source_code"])
    rows = frame.loc[frame["ts_code"].isin(target)].copy()
    # During the BSE transition stk_factor_pro publishes both identities on
    # the same day, with different adjusted indicator histories. The target
    # database identity is the new code, so its direct row takes precedence;
    # use the old-code row only when no direct row exists.
    direct_codes = set(rows.loc[rows["source_code"].eq(rows["ts_code"]), "ts_code"])
    rows = rows.loc[rows["source_code"].eq(rows["ts_code"])
                    | ~rows["ts_code"].isin(direct_codes)].copy()
    if rows["ts_code"].duplicated().any():
        raise ValueError("FACTOR_DAY_ALIAS_COLLISION")
    direct_rows = rows.loc[rows["source_code"].eq(rows["ts_code"])]
    if not direct_rows.empty:
        if "close" not in direct_rows:
            raise ValueError("FACTOR_DAY_CLOSE_MISSING")
        source_close = pd.to_numeric(direct_rows["close"], errors="coerce")
        local_close = direct_rows["ts_code"].map(
            {code: close for code, (_listed, close) in target.items()})
        if (source_close.isna().any() or not np.isfinite(source_close).all()
                or source_close.le(0).any()
                or not np.isclose(source_close, local_close, rtol=0, atol=0.0001).all()):
            raise ValueError("FACTOR_DAY_CLOSE_MISMATCH")
    if rows["source_code"].ne(rows["ts_code"]).any():
        if "close" not in rows:
            raise ValueError("FACTOR_DAY_ALIAS_CLOSE_MISSING")
        alias_rows = rows.loc[rows["source_code"].ne(rows["ts_code"])]
        source_close = pd.to_numeric(alias_rows["close"], errors="coerce")
        local_close = alias_rows["ts_code"].map(
            {code: close for code, (_listed, close) in target.items()})
        if (source_close.isna().any() or not np.isfinite(source_close).all()
                or not np.isclose(source_close, local_close, rtol=0, atol=0.0001).all()):
            raise ValueError("FACTOR_DAY_ALIAS_CLOSE_MISMATCH")
    rows["trade_date"] = day.isoformat()
    atr = pd.to_numeric(rows["atr_qfq"], errors="coerce")
    bad_atr = rows["atr_qfq"].notna() & atr.isna()
    bad_atr |= atr.notna() & (~np.isfinite(atr) | atr.le(0))
    if bad_atr.any():
        raise ValueError("FACTOR_DAY_INVALID_ATR")
    rows["atr_qfq"] = atr
    warmup = []
    source_absent = set(target) - set(rows["ts_code"].astype(str))
    warmup_candidates = source_absent | set(
        rows.loc[rows["atr_qfq"].isna(), "ts_code"].astype(str))
    for code in sorted(warmup_candidates):
        if _verified_warmup(conn, code, target[code][0], day, provider):
            warmup.append(code)
    writable = rows.loc[rows["atr_qfq"].notna()].copy()
    unresolved = sorted(set(target) - set(writable["ts_code"].astype(str)) - set(warmup))
    if not writable.empty:
        writable["updated_at"] = pd.Timestamp.now(tz="UTC")
        bulk_upsert_factor_daily(conn, writable, update=True)
    conn.commit()
    return {"day": day.isoformat(), "written": len(writable),
            "warmup": len(warmup), "unresolved": unresolved}


def _run_stock_factor_day_backfill(conn, provider_factory, start: date, end: date,
                                   limit_days: int | None = None) -> dict:
    if start > end or limit_days is not None and limit_days <= 0:
        raise ValueError("invalid factor day window or limit")
    provider = provider_factory()
    if getattr(provider, "connected", True) is False:
        raise RuntimeError("stock factor provider disconnected")
    mapping = provider.get_bse_mapping_df()
    raise_if_network_access_denied(provider)
    aliases = dict(zip(mapping["o_code"], mapping["n_code"])) if mapping is not None else {}
    sessions = [session for session in xc.get_calendar(
        "XSHG", start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
    ).sessions if session.date() <= end]
    results = []
    failed = []
    consecutive_source_failures = 0
    for session in sessions:
        day = session.date()
        if limit_days is not None and len(results) >= limit_days:
            break
        for attempt in range(3):
            try:
                result = backfill_factor_day(conn, provider, day, aliases)
                results.append(result)
                consecutive_source_failures = 0
                if result["unresolved"]:
                    failed.append(day.isoformat())
                break
            except FATAL_INGEST_ERRORS:
                raise
            except ProviderNetworkAccessDenied:
                raise
            except Exception as exc:  # noqa: BLE001 - a single day may fail in any provider/DB layer
                conn.rollback()
                if attempt == 2:
                    logger.warning("因子日 %s 三次失败: %s", day, exc)
                    failed.append(day.isoformat())
                    consecutive_source_failures += 1
                    results.append({"day": day.isoformat(), "written": 0,
                                    "warmup": 0, "unresolved": ["DAY_FAILED"]})
                else:
                    time.sleep(5)
        if len(results) % 20 == 0:
            logger.info("个股因子按日回填 %d/%d，写入 %d 行，失败日 %d",
                        len(results), len(sessions),
                        sum(item["written"] for item in results), len(failed))
        if consecutive_source_failures >= 5:
            logger.warning("最近五个交易日源调用均失败，停止请求上游")
            break
    FAILURE_LIST.parent.mkdir(parents=True, exist_ok=True)
    pending = FAILURE_LIST.with_name(f"{FAILURE_LIST.stem}.{os.getpid()}.tmp")
    pending.write_text(json.dumps(sorted(set(failed)), indent=2), encoding="utf-8")
    pending.replace(FAILURE_LIST)
    return {"days": len(results), "written": sum(r["written"] for r in results),
            "warmup": sum(r["warmup"] for r in results), "failed_days": sorted(set(failed)),
            "results": results}


run_stock_factor_day_backfill = locked_ingestion("CN_STOCK_DAILY")(
    _run_stock_factor_day_backfill)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--limit-days", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from AI.dataflows.providers.cn.tushare import TushareProvider

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with get_connection() as conn, IngestGuard(
        conn, changed=market_changed_notifier_from_env(),
    ) as guard:
        result = run_stock_factor_day_backfill(
            conn, TushareProvider, args.start, args.end,
            limit_days=args.limit_days, guard=guard,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print({key: result[key] for key in ("days", "written", "warmup", "failed_days")})
    if result["failed_days"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
