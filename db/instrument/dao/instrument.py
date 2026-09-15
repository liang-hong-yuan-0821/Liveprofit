"""market.instrument 标的主表 DAO（指数/股票/基金统一目录）。

instrument_type 为显式列（store 前缀函数 is_fund_ts_code 退役，方案 2.2）。
"""

import pandas as pd

from db.instrument.dao._common import _clean_frame, _normalize_date_col, _bulk_upsert

TABLE = "market.instrument"
PK = ["ts_code"]

# 通用列（不含 updated_at——DB DEFAULT now() 维护）
INSTRUMENT_COLS = [
    "ts_code", "name", "instrument_type", "list_status",
    "list_date", "delist_date", "data_source",
]
DATE_COLS = ["list_date", "delist_date"]


def upsert_instrument(conn, df: pd.DataFrame, update: bool = True) -> int:
    """instrument 表 ON CONFLICT (ts_code) upsert 通用列。

    增量刷新用 DO UPDATE（覆盖更名/状态）；迁移步骤 7 多源先后写用
    DO NOTHING（先到者胜）——update 参数由调用方控制。
    """
    if df is None or df.empty:
        return 0
    df = df[pd.notna(df["ts_code"])].copy()
    for col in DATE_COLS:
        df = _normalize_date_col(df, col)
    # updated_at 纳入 DO UPDATE 列集：行最后写入时间语义（p1——DB DEFAULT
    # 只在 INSERT 生效，DO UPDATE 不刷新则该列失真）
    if "updated_at" not in df.columns:
        df["updated_at"] = pd.Timestamp.now()
    df = _clean_frame(df, INSTRUMENT_COLS + ["updated_at"])
    if df.empty:
        return 0
    return _bulk_upsert(conn, TABLE, INSTRUMENT_COLS + ["updated_at"], PK, df, update)


def get_instrument(conn, ts_code: str) -> dict | None:
    """单标的目录行，返回 dict 或 None。"""
    rows = conn.execute(
        "SELECT ts_code, name, instrument_type, list_status, list_date, "
        "delist_date, data_source FROM market.instrument WHERE ts_code = %s",
        (ts_code,),
    ).fetchall()
    if not rows:
        return None
    return dict(zip(INSTRUMENT_COLS, rows[0]))


def list_instruments(conn, instrument_type: str = None) -> pd.DataFrame:
    """目录列表（instrument_type=None 全类型），ORDER BY ts_code。"""
    sql = f"SELECT {', '.join(INSTRUMENT_COLS)} FROM market.instrument"
    params = ()
    if instrument_type is not None:
        sql += " WHERE instrument_type = %s"
        params = (instrument_type,)
    sql += " ORDER BY ts_code"
    rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame(rows, columns=INSTRUMENT_COLS)
