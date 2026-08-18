"""
行情数据访问对象（market_data 表）

职责（方案 3.4）：
- insert_market_data: 批量插入指数日线（幂等 upsert）
- get_daily_returns: 返回日收益率（收盘价计算，指数无需复权）
- 其余查询接口：按资产/区间取行情、资产映射、事件窗口数据
"""

import logging

import pandas as pd
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)


def get_asset_id(conn, ticker: str):
    """按 ticker 查询资产 ID，不存在返回 None。"""
    row = conn.execute(
        "SELECT asset_id FROM assets WHERE ticker = %s", (ticker,)
    ).fetchone()
    return row[0] if row else None


def list_assets(conn) -> list[dict]:
    """列出全部资产。"""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT asset_id, ticker, name, asset_class, market FROM assets ORDER BY asset_id"
        )
        return cur.fetchall()


def insert_market_data(conn, asset_id: int, df: pd.DataFrame) -> int:
    """批量插入（upsert）行情数据，返回实际写入行数。

    df 标准列：trade_date(YYYY-MM-DD str) / open / high / low / close / vol / amount。
    已存在的主键 (asset_id, ts) 自动跳过（ON CONFLICT DO NOTHING）。
    """
    if df is None or df.empty:
        return 0
    records = []
    for _, row in df.iterrows():
        if pd.isna(row.get("close")):
            continue
        records.append((
            asset_id,
            str(row["trade_date"]),
            _to_num(row.get("open")),
            _to_num(row.get("high")),
            _to_num(row.get("low")),
            _to_num(row.get("close")),
            _to_num(row.get("vol")),
            _to_num(row.get("amount")),
        ))
    if not records:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO market_data (asset_id, ts, open, high, low, adj_close, vol, amount)
            VALUES (%s, %s::timestamptz, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (asset_id, ts) DO NOTHING
            """,
            records,
        )
    return len(records)


def _to_num(value):
    """NaN/None → None，其余转 float。"""
    if value is None or pd.isna(value):
        return None
    return float(value)


def get_market_data(conn, asset_id: int, start_date: str, end_date: str) -> pd.DataFrame:
    """按区间查询行情（含开高低收/量/额），按 ts 升序。"""
    rows = conn.execute(
        """
        SELECT ts, open, high, low, adj_close, vol, amount
        FROM market_data
        WHERE asset_id = %s AND ts >= %s::date AND ts <= %s::date + interval '1 day'
        ORDER BY ts
        """,
        (asset_id, start_date, end_date),
    ).fetchall()
    if not rows:
        return pd.DataFrame(columns=["ts", "adj_close"])
    return pd.DataFrame(
        rows, columns=["ts", "open", "high", "low", "adj_close", "vol", "amount"]
    )


def get_daily_returns(conn, asset_id: int, start_date: str, end_date: str) -> pd.DataFrame:
    """返回日收益率序列（含 ts 索引），使用收盘价计算（3.4 接口）。

    注意：首行为 NaN（无前收盘价），调用方计算时需 dropna。
    """
    df = get_market_data(conn, asset_id, start_date, end_date)
    if df.empty:
        return df
    df = df.copy()
    df["ret"] = df["adj_close"].pct_change()
    return df[["ts", "adj_close", "ret"]]
