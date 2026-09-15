"""market.instrument_daily 统一日线 DAO（指数+个股+基金同构一张表）。

含前复权查询（qfq_x = x × factor_t / factor_latest）与横截面查询
（instrument_type 显式列过滤，替代退役的 is_fund_ts_code 前缀函数）。
"""

import pandas as pd

from db.instrument.dao._common import _clean_frame, _drop_close_nan, _bulk_upsert

TABLE = "market.instrument_daily"
PK = ["ts_code", "trade_date"]

# 行情列 + source（NOT NULL 必填）+ updated_at（覆盖式写入 = 最近一次入库，调用方填）
DAILY_COLS = [
    "ts_code", "trade_date", "open", "high", "low", "close",
    "pre_close", "change", "pct_chg", "vol", "amount", "source", "updated_at",
]
QUERY_COLS = DAILY_COLS[:-1]  # 查询返回列不含 updated_at


def bulk_upsert_daily(conn, df: pd.DataFrame, update: bool = False) -> int:
    """统一日线批量写入。df 为 Provider 归一后标准列（含 source，NOT NULL 必填）。

    清洗顺序：close NaN 行 drop（计日志）→ 非法 ts_code/trade_date 行 drop →
    列序归一 → NaN→None。update 参数由调用方控制（store 决策 5：
    回填 DO NOTHING、增量 DO UPDATE；指数行例外 DO UPDATE 见方案 3.5.1）。
    """
    if df is None or df.empty:
        return 0
    df = _drop_close_nan(df)
    if df.empty:
        return 0
    df = _clean_frame(df, DAILY_COLS)
    if df.empty:
        return 0
    return _bulk_upsert(conn, TABLE, DAILY_COLS, PK, df, update)


def latest_trade_date(conn, instrument_types: list[str] | tuple[str, ...] = None):
    """统一日线最大交易日（增量起点 / 回填断点），无数据返回 None。

    instrument_types 给定时按 instrument 主表显式列过滤（如 ('stock', 'fund')）——
    增量步骤 4 的窗口门控必须按数据域判定：步骤 3 先写入的指数行会抬高
    全局 max(trade_date)，不过滤则步骤 4 被自身步骤 3 静默跳过（CR B1）。
    """
    if instrument_types is None:
        row = conn.execute(
            "SELECT max(trade_date) FROM market.instrument_daily").fetchone()
    else:
        row = conn.execute(
            "SELECT max(d.trade_date) FROM market.instrument_daily d "
            "JOIN market.instrument i ON i.ts_code = d.ts_code "
            "WHERE i.instrument_type = ANY(%s)",
            (list(instrument_types),),
        ).fetchone()
    return row[0] if row and row[0] is not None else None


def get_daily(conn, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """单标的区间日线序列，ORDER BY trade_date，列同表结构（不含 updated_at）。"""
    rows = conn.execute(
        "SELECT ts_code, trade_date, open, high, low, close, pre_close, "
        "change, pct_chg, vol, amount, source FROM market.instrument_daily "
        "WHERE ts_code = %s AND trade_date BETWEEN %s AND %s ORDER BY trade_date",
        (ts_code, start_date, end_date),
    ).fetchall()
    return pd.DataFrame(rows, columns=QUERY_COLS)


def query_range(conn, ts_code: str, start_date: str, end_date: str,
                limit: int = None) -> pd.DataFrame:
    """backend get_bars 消费的区间查询（与 get_daily 同语义，limit 为可选截断）。"""
    sql = (
        "SELECT ts_code, trade_date, open, high, low, close, pre_close, "
        "change, pct_chg, vol, amount, source FROM market.instrument_daily "
        "WHERE ts_code = %s AND trade_date BETWEEN %s AND %s ORDER BY trade_date"
    )
    params = [ts_code, start_date, end_date]
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame(rows, columns=QUERY_COLS)


def get_cross_section(conn, trade_date: str,
                      instrument_type: str = None) -> pd.DataFrame:
    """单交易日全市场横截面；instrument_type='stock'/'fund' 时按
    instrument 主表显式列过滤（前缀函数退役，方案 2.2）。

    不 JOIN instrument 主表做白名单过滤会漏退市股——这里仅按类型过滤，
    无其他过滤条件，语义与旧前缀过滤等价。
    """
    sql = ("SELECT d.ts_code, d.trade_date, d.open, d.high, d.low, d.close, "
           "d.pre_close, d.change, d.pct_chg, d.vol, d.amount, d.source "
           "FROM market.instrument_daily d "
           "WHERE d.trade_date = %s")
    params = [trade_date]
    if instrument_type is not None:
        sql += (" AND EXISTS (SELECT 1 FROM market.instrument i "
                "WHERE i.ts_code = d.ts_code AND i.instrument_type = %s)")
        params.append(instrument_type)
    sql += " ORDER BY d.ts_code"
    rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame(rows, columns=QUERY_COLS)


def get_qfq_daily(conn, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """单标的前复权区间序列：qfq_x = x × adj_factor_t / adj_factor_latest。

    基准为区间内最新因子（factor 序列升序最后一个非 NaN 值）；
    因子缺失日（停牌/新上市）qfq 列为 None，调用方自行处理。
    返回原始列 + qfq_open/qfq_high/qfq_low/qfq_close。
    """
    daily = get_daily(conn, ts_code, start_date, end_date)
    factor_rows = conn.execute(
        "SELECT trade_date, adj_factor FROM market.adj_factor "
        "WHERE ts_code = %s AND trade_date BETWEEN %s AND %s ORDER BY trade_date",
        (ts_code, start_date, end_date),
    ).fetchall()
    factor = pd.DataFrame(factor_rows, columns=["trade_date", "adj_factor"])
    if daily.empty:
        result = daily.copy()
        for c in ("open", "high", "low", "close"):
            result[f"qfq_{c}"] = None
        return result
    merged = daily.merge(factor, on="trade_date", how="left")
    valid = factor[pd.notna(factor["adj_factor"])]["adj_factor"]
    base = float(valid.iloc[-1]) if not valid.empty else None
    if base is not None and base > 0:
        for c in ("open", "high", "low", "close"):
            merged[f"qfq_{c}"] = merged[c] * merged["adj_factor"] / base
    else:
        for c in ("open", "high", "low", "close"):
            merged[f"qfq_{c}"] = None
    return merged.drop(columns=["adj_factor"]).reset_index(drop=True)
