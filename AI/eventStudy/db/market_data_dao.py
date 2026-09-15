"""
行情数据访问对象（包装 db.instrument.dao.instrument_daily，统一方案 3.3.1）

分工定稿：
- get_market_data / get_daily_returns 包装 db.instrument（内部先经 assets.ticker
  映射 ts_code，读 market.instrument_daily）
- get_asset_id / list_assets 保持读 public.assets 不变（语义是 asset_id 维度——
  assets 表按方案 2.2 保留）
- insert_market_data 已删除（采集由 db.instrument.ingest 接管，退役后是指向
  已删表的死代码）
- 返回 DataFrame 列保持 ts/open/high/low/adj_close/vol/amount 兼容
  （adj_close=close 别名；ts 归一为 datetime64——消费方 .dt.date 依赖）
"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)

_COMPAT_COLS = ["ts", "open", "high", "low", "adj_close", "vol", "amount"]


def get_asset_id(conn, ticker: str):
    """按 ticker 查询资产 ID，不存在返回 None。"""
    row = conn.execute(
        "SELECT asset_id FROM assets WHERE ticker = %s", (ticker,)
    ).fetchone()
    return row[0] if row else None


def list_assets(conn) -> list[dict]:
    """列出全部资产。"""
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT asset_id, ticker, name, asset_class, market FROM assets ORDER BY asset_id"
        )
        return cur.fetchall()


def _to_frame(rows: list, cols: list) -> pd.DataFrame:
    """行集 → 兼容 DataFrame：ts 归一为 datetime64（消费方 .dt.date 依赖；
    market.instrument_daily.trade_date 是 DATE，psycopg 返 datetime.date →
    object dtype，不归一则 event_study.py:158 的 df["ts"].dt.date 抛
    AttributeError）。"""
    df = pd.DataFrame(rows, columns=cols)
    if not df.empty and "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"])
    return df


def get_market_data(conn, asset_id: int, start_date: str, end_date: str) -> pd.DataFrame:
    """按区间查询行情（内部先查 assets.ticker → market.instrument_daily），按 ts 升序。"""
    ticker = conn.execute(
        "SELECT ticker FROM assets WHERE asset_id = %s", (asset_id,)
    ).fetchone()
    if ticker is None:
        return pd.DataFrame(columns=_COMPAT_COLS)
    from db.instrument.dao import instrument_daily

    df = instrument_daily.query_range(conn, ticker[0], start_date, end_date)
    if df.empty:
        return pd.DataFrame(columns=_COMPAT_COLS)
    out = pd.DataFrame({
        "ts": df["trade_date"],
        "open": df["open"],
        "high": df["high"],
        "low": df["low"],
        "adj_close": df["close"],  # 指数无复权：adj_close=close 别名（兼容列）
        "vol": df["vol"],
        "amount": df["amount"],
    })
    return _to_frame(out.to_dict("records") or [], _COMPAT_COLS)


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
