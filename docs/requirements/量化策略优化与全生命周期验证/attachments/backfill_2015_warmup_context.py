"""Backfill the 2015 daily/adjustment context for 2016 warmup candidates only."""

import json
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from AI.dataflows.providers.base_provider import raise_if_network_access_denied
from AI.dataflows.providers.cn.tushare import TushareProvider
from db.instrument.dao.adj_factor import bulk_upsert_factor
from db.instrument.dao.instrument_daily import bulk_upsert_daily
from db.instrument.db import get_connection
from db.instrument.ingest.guard import IngestGuard

ROOT = Path(__file__).parent
END = date(2015, 12, 31)
DAILY_COLS = ("ts_code", "trade_date", "open", "high", "low", "close",
              "pre_close", "change", "pct_chg", "vol", "amount")


def _checked_frame(frame, code: str, listed: date, columns: tuple[str, ...]) -> pd.DataFrame:
    if frame is None or not set(columns) <= set(frame.columns):
        raise RuntimeError(f"source frame missing columns: {code}")
    result = frame.loc[:, columns].copy()
    if result.empty:
        raise RuntimeError(f"source frame empty for listed stock: {code}")
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="raise").dt.date
    if (result["ts_code"].ne(code).any() or result["trade_date"].duplicated().any()
            or result["trade_date"].lt(listed).any()
            or result["trade_date"].gt(END).any()):
        raise RuntimeError(f"source identity/date mismatch: {code}")
    return result.sort_values("trade_date").reset_index(drop=True)


def main():
    load_dotenv(".env")
    candidates = json.loads((ROOT / "factor_warmup_2016_verification.json").read_text(encoding="utf-8"))
    codes = candidates["untrusted_no_bar_codes"]
    if len(codes) != 32 or candidates["untrusted_no_bar_by_year"] != {"2015": 620, "2016": 0}:
        raise RuntimeError("2016 warmup context set changed; review before importing")
    with get_connection() as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        listed = dict(conn.execute(
            "SELECT ts_code,list_date FROM market.instrument "
            "WHERE ts_code=ANY(%s) AND instrument_type='stock'", (codes,),
        ).fetchall())
    if set(listed) != set(codes) or any(not date(2015, 1, 1) <= day <= END for day in listed.values()):
        raise RuntimeError("candidate identities/list dates changed")
    provider = TushareProvider()
    if not provider.connected:
        raise RuntimeError("Tushare provider unavailable")
    day_frames, factor_frames = [], []
    per_code = []
    for code in codes:
        start = listed[code].strftime("%Y%m%d")
        daily = _checked_frame(provider._api_call(provider.api.daily, ts_code=code,
                               start_date=start, end_date="20151231"), code,
                               listed[code], DAILY_COLS)
        raise_if_network_access_denied(provider)
        factor = _checked_frame(provider._api_call(provider.api.adj_factor, ts_code=code,
                                start_date=start, end_date="20151231"), code,
                                listed[code], ("ts_code", "trade_date", "adj_factor"))
        raise_if_network_access_denied(provider)
        daily_dates = set(daily["trade_date"])
        factor_dates = set(factor["trade_date"])
        if (not daily_dates <= factor_dates
                or daily[["open", "high", "low", "close"]].isna().any().any()
                or daily[["open", "high", "low", "close"]].le(0).any().any()
                or (daily["high"] < daily[["open", "low", "close"]].max(axis=1)).any()
                or (daily["low"] > daily[["open", "high", "close"]].min(axis=1)).any()
                or factor["adj_factor"].isna().any()
                or factor["adj_factor"].le(0).any()):
            raise RuntimeError(f"daily/factor quality mismatch: {code}")
        # The upstream adj_factor endpoint can return factors during a full-day
        # suspension. No daily bar exists on those dates, so only persist factors
        # joined to observed bars; record the extras for audit.
        factor_only = sorted(factor_dates - daily_dates)
        factor = factor[factor["trade_date"].isin(daily_dates)].copy()
        daily["source"] = "tushare"
        daily["updated_at"] = pd.Timestamp.now(tz="UTC")
        day_frames.append(daily)
        factor_frames.append(factor)
        per_code.append({"ts_code": code, "listed": str(listed[code]),
                         "daily_rows": len(daily), "factor_only_dates": [str(day) for day in factor_only]})
        time.sleep(0.2)
    with get_connection() as conn, IngestGuard(conn) as guard:
        guarded = guard.connection
        before_daily = guarded.execute(
            "SELECT count(*) FROM market.instrument_daily WHERE ts_code=ANY(%s) "
            "AND trade_date BETWEEN DATE '2015-01-01' AND DATE '2015-12-31'", (codes,),
        ).fetchone()[0]
        before_factor = guarded.execute(
            "SELECT count(*) FROM market.adj_factor WHERE ts_code=ANY(%s) "
            "AND trade_date BETWEEN DATE '2015-01-01' AND DATE '2015-12-31'", (codes,),
        ).fetchone()[0]
        submitted_daily = bulk_upsert_daily(guarded, pd.concat(day_frames, ignore_index=True))
        submitted_factor = bulk_upsert_factor(guarded, pd.concat(factor_frames, ignore_index=True))
        after_daily = guarded.execute(
            "SELECT count(*) FROM market.instrument_daily WHERE ts_code=ANY(%s) "
            "AND trade_date BETWEEN DATE '2015-01-01' AND DATE '2015-12-31'", (codes,),
        ).fetchone()[0]
        after_factor = guarded.execute(
            "SELECT count(*) FROM market.adj_factor WHERE ts_code=ANY(%s) "
            "AND trade_date BETWEEN DATE '2015-01-01' AND DATE '2015-12-31'", (codes,),
        ).fetchone()[0]
        guarded.commit()
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "32 stocks listed in 2015 with 2016 factor warmup gaps; not a full 2015 market backfill",
        "codes": per_code, "source_rows": sum(item["daily_rows"] for item in per_code),
        "factor_only_count": sum(len(item["factor_only_dates"]) for item in per_code),
        "submitted_daily": submitted_daily, "submitted_adj_factor": submitted_factor,
        "inserted_daily": after_daily - before_daily,
        "inserted_adj_factor": after_factor - before_factor,
    }
    output = ROOT / "warmup_context_2015_backfill.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print({key: report[key] for key in ("source_rows", "inserted_daily", "inserted_adj_factor")})


if __name__ == "__main__":
    main()
