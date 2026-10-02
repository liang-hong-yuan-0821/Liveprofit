"""Read a bounded historical market window without modifying source facts."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from db.instrument.dao.etf_catalog import query_as_of

STOCK_BENCHMARK_CODE = "000300.SH"

_SOURCES = {
    "daily": (
        "market.instrument_daily",
        "d.ts_code, d.trade_date, d.open, d.high, d.low, d.close, d.amount, d.source",
        "",
        ("ts_code", "trade_date", "open", "high", "low", "close", "amount", "source"),
    ),
    "adj_factor": (
        "market.adj_factor", "d.ts_code, d.trade_date, d.adj_factor", "",
        ("ts_code", "trade_date", "adj_factor"),
    ),
    "factor": (
        "market.factor_daily",
        ("d.ts_code, d.trade_date, d.ma_qfq_5, d.ma_qfq_20, d.ma_qfq_60, "
         "d.atr_qfq, d.ma_bfq_5, d.ma_bfq_20, d.ma_bfq_60, d.ma_bfq_250, d.atr_bfq"),
        "",
        ("ts_code", "trade_date", "ma_qfq_5", "ma_qfq_20", "ma_qfq_60",
         "atr_qfq", "ma_bfq_5", "ma_bfq_20", "ma_bfq_60", "ma_bfq_250", "atr_bfq"),
    ),
    "trade_status": (
        "market.trade_status_effective",
        "d.ts_code, d.trade_date, d.is_st, d.is_suspended, d.suspension_scope, "
        "d.suspend_timing, d.market_board, d.source",
        " AND i.instrument_type = 'stock'",
        ("ts_code", "trade_date", "is_st", "is_suspended", "suspension_scope",
         "suspend_timing", "market_board", "source"),
    ),
}


def read_research_source(conn, *, start: date, end: date) -> dict[str, pd.DataFrame]:
    """Return at most 400 calendar days ending at the decision date.

    The caller supplies an independent verified calendar to DatasetBuilder. The
    reader itself never infers sessions from stored bars and never writes to PG.
    """
    if type(start) is not date or type(end) is not date or end < start:
        raise ValueError("valid research date interval required")
    if (end - start).days >= 400:
        raise ValueError("research source window exceeds 400 calendar days")
    rows = conn.execute(
        "SELECT ts_code, instrument_type, "
        "CASE WHEN ts_code LIKE '%%.BJ' "
        "THEN greatest(list_date, DATE '2021-11-15') ELSE list_date END AS list_date, "
        "delist_date "
        "FROM market.instrument WHERE instrument_type IN ('stock', 'fund') "
        "AND (list_date IS NULL OR "
        "(CASE WHEN ts_code LIKE '%%.BJ' "
        "THEN greatest(list_date, DATE '2021-11-15') ELSE list_date END) <= %s) "
        "AND (delist_date IS NULL OR delist_date > %s) "
        "ORDER BY ts_code",
        (end, start),
    ).fetchall()
    tables = {"instrument": pd.DataFrame(
        rows, columns=("ts_code", "instrument_type", "list_date", "delist_date"),
    )}
    for name, (table, columns_sql, extra, columns) in _SOURCES.items():
        rows = conn.execute(
            f"SELECT {columns_sql} FROM {table} d "
            "JOIN market.instrument i ON i.ts_code = d.ts_code "
            "WHERE i.instrument_type IN ('stock', 'fund') "
            "AND i.list_date <= d.trade_date "
            "AND (i.delist_date IS NULL OR d.trade_date < i.delist_date) "
            "AND (i.ts_code NOT LIKE '%%.BJ' OR d.trade_date >= DATE '2021-11-15') "
            f"AND d.trade_date BETWEEN %s AND %s{extra} "
            "ORDER BY d.ts_code, d.trade_date",
            (start, end),
        ).fetchall()
        tables[name] = pd.DataFrame(rows, columns=columns)
    benchmark_rows = conn.execute(
        "SELECT d.ts_code, d.trade_date, d.close, d.source "
        "FROM market.instrument_daily d "
        "JOIN market.instrument i ON i.ts_code=d.ts_code "
        "WHERE d.ts_code=%s AND i.instrument_type='index' "
        "AND i.list_date IS NOT NULL AND i.list_date<=d.trade_date "
        "AND (i.delist_date IS NULL OR d.trade_date<i.delist_date) "
        "AND d.trade_date BETWEEN %s AND %s "
        "ORDER BY d.trade_date",
        (STOCK_BENCHMARK_CODE, start, end),
    ).fetchall()
    tables["benchmark_daily"] = pd.DataFrame(
        benchmark_rows, columns=("ts_code", "trade_date", "close", "source"),
    )
    # A notice published after the snapshot date must not repair past research
    # inputs. DISTINCT permits multiple independent notices of one scope;
    # contradictory scopes remain separate rows and fail coverage validation.
    rows = conn.execute(
        "SELECT e.ts_code,e.trade_date,e.scope,min(e.published_on) AS published_on "
        "FROM market.suspension_evidence e "
        "JOIN market.instrument i ON i.ts_code=e.ts_code "
        "WHERE i.instrument_type='stock' AND i.list_date<=e.trade_date "
        "AND (i.delist_date IS NULL OR e.trade_date<i.delist_date) "
        "AND (i.ts_code NOT LIKE '%%.BJ' OR e.trade_date>=DATE '2021-11-15') "
        "AND e.trade_date BETWEEN %s AND %s AND e.published_on<=%s "
        "GROUP BY e.ts_code,e.trade_date,e.scope "
        "ORDER BY e.ts_code,e.trade_date,e.scope",
        (start, end, end),
    ).fetchall()
    tables["suspension_evidence"] = pd.DataFrame(
        rows, columns=("ts_code", "trade_date", "scope", "published_on"),
    )
    cutoff = datetime.combine(end, time.max, tzinfo=ZoneInfo("Asia/Shanghai"))
    tables["etf_catalog"] = query_as_of(conn, cutoff)
    return tables
