"""market.fund_info 基金信息表 DAO（fund_basic 差异列，21 个）。

通用列（ts_code/name/list_date/delist_date）在 instrument 主表——fund_basic 行
拆两路写入（迁移步骤 7 / 增量步骤 2）。
"""

import pandas as pd

from db.instrument.dao._common import _normalize_date_col, _clean_frame, _bulk_upsert

TABLE = "market.fund_info"
PK = ["ts_code"]

FUND_INFO_COLS = [
    "ts_code", "management", "custodian", "trustee", "fund_type", "invest_type",
    "type", "found_date", "due_date", "issue_date", "issue_amount", "m_fee",
    "c_fee", "duration_year", "p_value", "min_amount", "exp_return", "benchmark",
    "status", "market", "purc_startdate", "redm_startdate",
]
DATE_COLS = ["found_date", "due_date", "issue_date", "purc_startdate", "redm_startdate"]


def upsert_fund_info(conn, df: pd.DataFrame, update: bool = True) -> int:
    """fund_info 表 ON CONFLICT (ts_code) upsert（market='E' 差异列全字段）。"""
    if df is None or df.empty:
        return 0
    df = df[pd.notna(df["ts_code"])].copy()
    for col in DATE_COLS:
        df = _normalize_date_col(df, col)
    if "updated_at" not in df.columns:
        df["updated_at"] = pd.Timestamp.now()
    df = _clean_frame(df, FUND_INFO_COLS + ["updated_at"])
    if df.empty:
        return 0
    return _bulk_upsert(conn, TABLE, FUND_INFO_COLS + ["updated_at"], PK, df, update)
