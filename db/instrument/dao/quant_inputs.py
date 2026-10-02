"""Read target-day facts used by all-market quant preflight and ingestion."""

from __future__ import annotations


def _finite_columns(columns: list[str], alias: str) -> str:
    return " AND ".join(
        f"{alias}.{column} IS NOT NULL AND {alias}.{column} NOT IN "
        "('NaN'::float8, 'Infinity'::float8, '-Infinity'::float8)"
        for column in columns
    )


def read_stock_quant_facts(conn, codes: list[str], trade_day, qfq_columns: list[str]) -> dict[str, set[str]]:
    """Return valid/seen code sets for four quant inputs on one fixed day.

    Coverage thresholds are deliberately left to callers. This common SQL
    projection keeps the refresh snapshot and the locked collector on the same
    finite-value and trusted-suspension contract.
    """
    if not codes:
        return {
            "daily_valid": set(), "daily_seen": set(), "suspended": set(),
            "qfq_valid": set(), "qfq_seen": set(), "adj_valid": set(),
            "adj_seen": set(), "status_valid": set(), "status_seen": set(),
        }
    params = (codes, trade_day)
    daily_rows = conn.execute(
        f"SELECT d.ts_code, ({_finite_columns(['open', 'high', 'low', 'close', 'pct_chg'], 'd')}) "
        "FROM market.instrument_daily d WHERE d.ts_code = ANY(%s) AND d.trade_date = %s",
        params,
    ).fetchall()
    status_rows = conn.execute(
        "SELECT ts_code, (is_suspended AND suspension_scope='full_day'), source "
        "FROM market.trade_status_effective "
        "WHERE ts_code = ANY(%s) AND trade_date = %s",
        params,
    ).fetchall()
    qfq_rows = conn.execute(
        f"SELECT f.ts_code, ({_finite_columns(qfq_columns, 'f')}) FROM market.factor_daily f "
        "WHERE f.ts_code = ANY(%s) AND f.trade_date = %s",
        params,
    ).fetchall()
    adj_rows = conn.execute(
        f"SELECT a.ts_code, ({_finite_columns(['adj_factor'], 'a')} AND a.adj_factor > 0) "
        "FROM market.adj_factor a WHERE a.ts_code = ANY(%s) AND a.trade_date = %s",
        params,
    ).fetchall()
    status_validity = (
        "is_suspended IS NOT NULL AND is_st IS NOT NULL AND market_board IS NOT NULL "
        "AND (is_suspended IS FALSE OR suspension_scope IN ('full_day','intraday')) "
        "AND btrim(market_board) <> '' "
        "AND (up_limit IS NULL OR (up_limit > 0 AND up_limit NOT IN "
        "('NaN'::float8, 'Infinity'::float8, '-Infinity'::float8))) "
        "AND (down_limit IS NULL OR (down_limit > 0 AND down_limit NOT IN "
        "('NaN'::float8, 'Infinity'::float8, '-Infinity'::float8))) "
        "AND (up_limit IS NULL OR down_limit IS NULL OR up_limit >= down_limit)"
    )
    status_facts = conn.execute(
        f"SELECT ts_code, ({status_validity}) FROM market.trade_status_effective "
        "WHERE ts_code = ANY(%s) AND trade_date = %s "
        "AND source IN ('tushare','tushare+baostock')",
        params,
    ).fetchall()
    status_valid = {code for code, valid in status_facts if valid}
    return {
        "daily_valid": {code for code, valid in daily_rows if valid},
        "daily_seen": {code for code, _ in daily_rows},
        "suspended": {code for code, flag, source in status_rows
                      if flag is True and source in ("tushare", "tushare+baostock")} & status_valid,
        "qfq_valid": {code for code, valid in qfq_rows if valid},
        "qfq_seen": {code for code, _ in qfq_rows},
        "adj_valid": {code for code, valid in adj_rows if valid},
        "adj_seen": {code for code, _ in adj_rows},
        "status_valid": status_valid,
        "status_seen": {code for code, _, _ in status_rows},
    }
