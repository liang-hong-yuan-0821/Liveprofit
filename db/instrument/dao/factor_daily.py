"""market.factor_daily 技术因子宽表 DAO（指数∪个股列并集，不自算原则）。

列集 = INDEX_FACTOR_FIELDS（10 因子 + close）∪ STOCK_FACTOR_FIELDS（14 因子）并集
（tushare.py 常量，方案 2.2）；stk_factor_pro 无 close 列，个股行 close 由
instrument_daily 反查填充（方案 3.7.1）。
"""

import pandas as pd

from db.instrument.dao._common import _bulk_upsert, _clean_frame

TABLE = "market.factor_daily"
PK = ["ts_code", "trade_date"]

FACTOR_DAILY_COLS = [
    "ts_code", "trade_date", "close",
    "ma_bfq_5", "ma_bfq_10", "ma_bfq_20", "ma_bfq_60", "ma_bfq_250",
    "boll_mid_bfq", "boll_upper_bfq", "boll_lower_bfq",
    "macd_dif_bfq", "macd_dea_bfq", "macd_bfq",
    "rsi_bfq_6", "rsi_bfq_12", "rsi_bfq_24",
    "ma_qfq_5", "ma_qfq_20", "ma_qfq_60",
    "boll_mid_qfq", "boll_upper_qfq", "boll_lower_qfq",
    "macd_dif_qfq", "macd_dea_qfq", "macd_qfq", "rsi_qfq_6",
    "updated_at",
]


def bulk_upsert_factor_daily(conn, df: pd.DataFrame, update: bool = False) -> int:
    """技术因子批量写入。update 由调用方控制（指数因子行 DO UPDATE、个股行 DO NOTHING）。"""
    if df is None or df.empty:
        return 0
    df = _clean_frame(df, FACTOR_DAILY_COLS)
    if df.empty:
        return 0
    return _bulk_upsert(conn, TABLE, FACTOR_DAILY_COLS, PK, df, update)


def upsert_observed_bfq_factors(conn, df: pd.DataFrame) -> int:
    """Update observed bfq columns only; never erase existing qfq or null-fill.

    Column selection happens before cleaning (which otherwise manufactures null
    columns), and SQL COALESCE protects existing observations on partial rows.
    """
    if df is None or df.empty:
        return 0
    allowed = [c for c in FACTOR_DAILY_COLS
               if c in PK or c in ("close", "updated_at") or "_bfq" in c]
    cols = [c for c in allowed if c in df.columns]
    if not all(c in cols for c in PK):
        raise ValueError("bfq observation requires ts_code and trade_date")
    if not any("_bfq" in c for c in cols):
        return 0
    frame = _clean_frame(df, cols).drop_duplicates(subset=PK, keep="last")
    return _bulk_upsert(conn, TABLE, cols, PK, frame, True,
                        preserve_null=tuple(c for c in cols if c not in PK))


def query_range(conn, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """单标的区间因子序列（backend indicator 组装消费），ORDER BY trade_date。"""
    rows = conn.execute(
        f"SELECT {', '.join(FACTOR_DAILY_COLS)} FROM market.factor_daily "
        "WHERE ts_code = %s AND trade_date BETWEEN %s AND %s ORDER BY trade_date",
        (ts_code, start_date, end_date),
    ).fetchall()
    return pd.DataFrame(rows, columns=FACTOR_DAILY_COLS)
