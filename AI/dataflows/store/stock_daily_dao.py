"""
全市场日线本地库 DAO（方案 3.3.1）

六张表（public schema，与事件研究同库不同表）：
stock_basic / fund_basic / stock_daily / adj_factor / concept / concept_member

约定：
- 行情列 DOUBLE PRECISION、日期 DATE；stock_daily 与 adj_factor 股票/基金共用，
  分类靠 is_fund_ts_code() 前缀函数判定（不冗余 asset_type，方案 2.1 决策 1）
- 批量写入走 COPY → 临时表（表名带 pid 后缀防并发）→ INSERT ... ON CONFLICT，
  用完即删（finally drop）；清洗顺序：close NaN 行显式 drop（计日志）→ 其余列 NaN→None
- 写入幂等策略（决策 5）由调用方控制：回填用 DO NOTHING（历史静态）、
  增量最近 3 个交易日用 DO UPDATE（覆盖 tushare 日终修正）
- 单位约定：vol 手、amount 千元，与接口一致不换算
"""

import logging
import os
import uuid

import pandas as pd

logger = logging.getLogger(__name__)

# ==================== 表列（与 schema.sql 对齐） ====================

DAILY_COLS = [
    "ts_code", "trade_date", "open", "high", "low", "close",
    "pre_close", "change", "pct_chg", "vol", "amount",
]
FACTOR_COLS = ["ts_code", "trade_date", "adj_factor"]
STOCK_BASIC_COLS = [
    "ts_code", "name", "market", "exchange", "industry", "area",
    "list_status", "list_date", "delist_date",
]
# fund_basic 表列（fund_basic market='E' 全字段，updated_at 由 DB 默认值维护）
FUND_BASIC_COLS = [
    "ts_code", "name", "management", "custodian", "fund_type", "invest_type",
    "type", "found_date", "due_date", "list_date", "issue_date", "delist_date",
    "issue_amount", "m_fee", "c_fee", "duration_year", "p_value", "min_amount",
    "exp_return", "benchmark", "status", "trustee", "purc_startdate",
    "redm_startdate", "market",
]
FUND_BASIC_DATE_COLS = [
    "found_date", "due_date", "list_date", "issue_date", "delist_date",
    "purc_startdate", "redm_startdate",
]
CONCEPT_COLS = ["source", "concept_code", "name", "count", "exchange",
                "list_date", "type"]
CONCEPT_MEMBER_COLS = ["source", "concept_code", "ts_code"]


# ==================== 前缀分类 ====================

def is_fund_ts_code(ts_code: str) -> bool:
    """前缀规则判定场内基金代码：沪 5 开头；深 15/16/18 开头。

    股票 60/68/00/30/8/4/920 前缀不重叠；B 股（900/200）实测不存在于端点数据
    （方案 2.1 边界实测），该规则无 B 股边界问题。
    """
    code = str(ts_code).strip().upper()
    if "." not in code:
        return False
    base, suffix = code.split(".", 1)
    if suffix == "SH":
        return base.startswith("5")
    if suffix == "SZ":
        return base.startswith(("15", "16", "18"))
    return False


# ==================== 清洗 ====================

def _normalize_date_col(df: pd.DataFrame, col: str) -> pd.DataFrame:
    """YYYYMMDD 字符串/int/datetime → 'YYYY-MM-DD' 字符串（COPY 落 DATE 列）。"""
    if col in df.columns:
        df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%Y-%m-%d")
    return df


def _drop_close_nan(df: pd.DataFrame) -> pd.DataFrame:
    """close 为 NaN 的行显式 drop 并计日志（close 列 NOT NULL，不能靠 NaN→None 兜底）。"""
    if "close" not in df.columns:
        return df.copy()
    n_before = len(df)
    df = df[pd.notna(df["close"])].copy()
    dropped = n_before - len(df)
    if dropped:
        logger.warning("store 写入清洗: %d 行 close 为 NaN 已 drop", dropped)
    return df


def _clean_frame(df: pd.DataFrame, cols: list) -> pd.DataFrame:
    """通用清洗：非法 ts_code/trade_date 行 drop → 列序归一（缺失列置 None）→ NaN→None。

    NaN→None 必须先转 object dtype：float64 列上 where(cond, None) 会把 None
    压回 NaN（pandas 无法在 float64 中存 None），导致 NaN 写入 DOUBLE 列而非 NULL。
    """
    df = _normalize_date_col(df.copy(), "trade_date") if "trade_date" in cols else df.copy()
    if "ts_code" in cols:
        df = df[pd.notna(df["ts_code"])]
    if "trade_date" in cols:
        df = df[pd.notna(df["trade_date"])]
    out = pd.DataFrame({c: df[c] if c in df.columns else None for c in cols},
                       dtype=object)
    return out.where(pd.notna(out), None)


# ==================== 批量写入 ====================

def _bulk_upsert(conn, table: str, cols: list, pk_cols: list,
                 df: pd.DataFrame, update: bool) -> int:
    """COPY → 临时表（表名带 pid + 随机后缀防并发）→ INSERT ... ON CONFLICT，用完即删。

    update=True → DO UPDATE SET 全列（不含 PK）；全列均属 PK（如 concept_member）时
    退化为空更新（DO UPDATE SET 首个 PK 列 = EXCLUDED.自身，与 DO NOTHING 等价，
    保持"DO UPDATE"语义）。
    返回传入行数（清洗后 len(df)），非实际落库行数（DO NOTHING 冲突时落库更少）。
    """
    tmp = f"tmp_{table}_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    col_list = ", ".join(f'"{c}"' for c in cols)
    pk_list = ", ".join(f'"{c}"' for c in pk_cols)
    with conn.cursor() as cur:
        # INCLUDING DEFAULTS 必带：LIKE 默认不复制 DEFAULT 表达式，否则带
        # DEFAULT now() 的 NOT NULL 列（updated_at）COPY 时被填 NULL 违例
        cur.execute(f'CREATE TEMP TABLE "{tmp}" (LIKE "{table}" INCLUDING DEFAULTS) '
                    f'ON COMMIT DROP')
        try:
            with cur.copy(f'COPY "{tmp}" ({col_list}) FROM STDIN') as copier:
                for row in df[cols].itertuples(index=False, name=None):
                    copier.write_row(row)
            if update:
                settable = [c for c in cols if c not in pk_cols]
                if settable:
                    sets = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in settable)
                else:
                    # 全列均属 PK：空更新（保持 DO UPDATE 语义，实际等价 DO NOTHING）
                    sets = f'"{pk_cols[0]}" = EXCLUDED."{pk_cols[0]}"'
                on_conflict = f"DO UPDATE SET {sets}"
            else:
                on_conflict = "DO NOTHING"
            cur.execute(
                f'INSERT INTO "{table}" ({col_list}) '
                f'SELECT {col_list} FROM "{tmp}" '
                f'ON CONFLICT ({pk_list}) {on_conflict}'
            )
        finally:
            try:
                cur.execute(f'DROP TABLE IF EXISTS "{tmp}"')
            except Exception:
                # 事务已 abort（InFailedSqlTransaction）时 DROP 抛错——不掩盖原始
                # 异常。表名含随机后缀，残留 temp 表不会与下次调用冲突；
                # ON COMMIT DROP 仅 commit 时清理，abort 残留随会话结束回收
                pass
    return len(df)


def upsert_stock_basic(conn, df: pd.DataFrame) -> int:
    """stock_basic 表 ON CONFLICT (ts_code) DO UPDATE 全列（覆盖退市股/更名）。"""
    if df is None or df.empty:
        return 0
    df = df[pd.notna(df["ts_code"])].copy()
    df = _normalize_date_col(df, "list_date")
    df = _normalize_date_col(df, "delist_date")
    df = _clean_frame(df, STOCK_BASIC_COLS)
    if df.empty:
        return 0
    return _bulk_upsert(conn, "stock_basic", STOCK_BASIC_COLS, ["ts_code"], df, update=True)


def upsert_fund_basic(conn, df: pd.DataFrame) -> int:
    """fund_basic 表 ON CONFLICT (ts_code) DO UPDATE 全列（market='E' 全字段）。"""
    if df is None or df.empty:
        return 0
    df = df[pd.notna(df["ts_code"])].copy()
    for col in FUND_BASIC_DATE_COLS:
        df = _normalize_date_col(df, col)
    df = _clean_frame(df, FUND_BASIC_COLS)
    if df.empty:
        return 0
    return _bulk_upsert(conn, "fund_basic", FUND_BASIC_COLS, ["ts_code"], df, update=True)


def bulk_upsert_daily(conn, df: pd.DataFrame, update: bool = False) -> int:
    """stock_daily 批量写入。df 为 Provider 归一后标准列（3.2.1）。

    清洗顺序：close NaN 行 drop（计日志）→ 非法 ts_code/trade_date 行 drop →
    列序归一 → NaN→None。update 参数由调用方控制（决策 5）。
    """
    if df is None or df.empty:
        return 0
    df = _drop_close_nan(df)
    if df.empty:
        return 0
    df = _clean_frame(df, DAILY_COLS)
    if df.empty:
        return 0
    return _bulk_upsert(conn, "stock_daily", DAILY_COLS,
                        ["ts_code", "trade_date"], df, update)


def bulk_upsert_factor(conn, df: pd.DataFrame, update: bool = False) -> int:
    """adj_factor 批量写入（同构，无 NOT NULL 列，仅 NaN→None）。"""
    if df is None or df.empty:
        return 0
    df = _clean_frame(df, FACTOR_COLS)
    if df.empty:
        return 0
    return _bulk_upsert(conn, "adj_factor", FACTOR_COLS,
                        ["ts_code", "trade_date"], df, update)


# ==================== 查询 ====================

def latest_trade_date(conn):
    """stock_daily 最大交易日（增量起点 / 回填断点），无数据返回 None。"""
    row = conn.execute("SELECT max(trade_date) FROM stock_daily").fetchone()
    return row[0] if row and row[0] is not None else None


def get_daily(conn, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """单标的区间日线序列，ORDER BY trade_date，列同表结构。"""
    rows = conn.execute(
        "SELECT ts_code, trade_date, open, high, low, close, pre_close, "
        "change, pct_chg, vol, amount FROM stock_daily "
        "WHERE ts_code = %s AND trade_date BETWEEN %s AND %s ORDER BY trade_date",
        (ts_code, start_date, end_date),
    ).fetchall()
    return pd.DataFrame(rows, columns=DAILY_COLS)


def get_cross_section(conn, trade_date: str, market: str = None) -> pd.DataFrame:
    """单交易日全市场横截面；market='stock'/'fund' 时按 is_fund_ts_code 前缀函数过滤。

    不 JOIN 基本信息表（避免退市股、异常代码被白名单静默漏掉——2.1 决策 1）。
    market 为 None 时不分类（股票 + 基金混合返回）。
    """
    rows = conn.execute(
        "SELECT ts_code, trade_date, open, high, low, close, pre_close, "
        "change, pct_chg, vol, amount FROM stock_daily WHERE trade_date = %s",
        (trade_date,),
    ).fetchall()
    df = pd.DataFrame(rows, columns=DAILY_COLS)
    if market is not None and market not in ("stock", "fund"):
        logger.warning("get_cross_section: 未知 market=%s（不做分类过滤）", market)
        return df
    if market is not None and not df.empty:
        want_fund = market == "fund"
        df = df[df["ts_code"].map(is_fund_ts_code) == want_fund].reset_index(drop=True)
    return df


def get_qfq_daily(conn, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """单标的前复权区间序列：qfq_x = x × adj_factor_t / adj_factor_latest。

    基准为区间内最新因子（factor 序列升序最后一个非 NaN 值）；
    因子缺失日（停牌/新上市）qfq 列为 None，调用方自行处理。
    返回原始列 + qfq_open/qfq_high/qfq_low/qfq_close。
    """
    daily = get_daily(conn, ts_code, start_date, end_date)
    factor_rows = conn.execute(
        "SELECT trade_date, adj_factor FROM adj_factor "
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


def get_basic(conn, ts_code: str):
    """单标的基本信息：先查 stock_basic 再查 fund_basic，返回 dict 或 None。"""
    with conn.cursor() as cur:
        for table in ("stock_basic", "fund_basic"):
            cur.execute(f"SELECT * FROM {table} WHERE ts_code = %s", (ts_code,))
            row = cur.fetchone()
            if row is not None:
                return dict(zip((d.name for d in cur.description), row))
    return None


# ==================== 概念体系（concept / concept_member，多来源） ====================

def upsert_concepts(conn, df: pd.DataFrame) -> int:
    """concept 表 ON CONFLICT (source, concept_code) DO UPDATE。

    df 标准列 [ts_code(概念代码), name, count, exchange, list_date, type] + source
    （source 由采集层附加）；落表时 ts_code 重命名为 concept_code。
    """
    if df is None or df.empty:
        return 0
    df = df.copy()
    if "source" not in df.columns:
        raise ValueError("upsert_concepts: df 缺少 source 列")
    df = df[pd.notna(df["ts_code"])]
    df = df.rename(columns={"ts_code": "concept_code"})
    df = _normalize_date_col(df, "list_date")
    if "count" in df.columns:
        # count 列为 INTEGER，tushare 实测返回 float（如 300.0）——
        # 不转 Python int 会 COPY 报 invalid input syntax for type integer。
        # 必须显式 dtype=object：Series.apply 的 dtype 推断在多行含 None 时
        # 会把 int 整体压回 float64（单行均匀时又推成 int64，单测骗过了）
        df["count"] = pd.Series(
            [None if pd.isna(v) else int(float(v)) for v in df["count"]],
            dtype=object, index=df.index)
    df = _clean_frame(df, CONCEPT_COLS)
    if df.empty:
        return 0
    return _bulk_upsert(conn, "concept", CONCEPT_COLS,
                        ["source", "concept_code"], df, update=True)


def upsert_concept_members(conn, df: pd.DataFrame) -> int:
    """concept_member 表 ON CONFLICT (source, concept_code, ts_code) DO UPDATE。

    df 标准列 [concept_code, ts_code] + source（source 由采集层附加）。
    全列均属 PK，DO UPDATE 退化为空更新（幂等，等价 DO NOTHING）。
    """
    if df is None or df.empty:
        return 0
    df = df.copy()
    if "source" not in df.columns:
        raise ValueError("upsert_concept_members: df 缺少 source 列")
    df = _clean_frame(df, CONCEPT_MEMBER_COLS)
    # 双保险去重：同一批次含相同 PK 行时 INSERT ... ON CONFLICT DO UPDATE
    # 报 cannot affect row a second time（PG 约束，实测踩坑）
    df = df.drop_duplicates(subset=CONCEPT_MEMBER_COLS)
    if df.empty:
        return 0
    return _bulk_upsert(conn, "concept_member", CONCEPT_MEMBER_COLS,
                        CONCEPT_MEMBER_COLS, df, update=True)


def get_concepts(conn, source: str = None) -> pd.DataFrame:
    """概念列表（source=None 全来源），ORDER BY source, concept_code。"""
    sql = "SELECT source, concept_code, name, count, exchange, list_date, type FROM concept"
    params = ()
    if source is not None:
        sql += " WHERE source = %s"
        params = (source,)
    sql += " ORDER BY source, concept_code"
    rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame(rows, columns=CONCEPT_COLS)


def get_concept_members(conn, concept_code: str, source: str = None) -> pd.DataFrame:
    """按概念查成分（source=None 全来源）。"""
    sql = ("SELECT source, concept_code, ts_code FROM concept_member "
           "WHERE concept_code = %s")
    params = [concept_code]
    if source is not None:
        sql += " AND source = %s"
        params.append(source)
    sql += " ORDER BY source, ts_code"
    rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame(rows, columns=CONCEPT_MEMBER_COLS)


def get_stock_concepts(conn, ts_code: str, source: str = None) -> pd.DataFrame:
    """按股票查所属概念（全来源，走 idx_concept_member_ts 反查索引）。"""
    sql = ("SELECT source, concept_code, ts_code FROM concept_member "
           "WHERE ts_code = %s")
    params = [ts_code]
    if source is not None:
        sql += " AND source = %s"
        params.append(source)
    sql += " ORDER BY source, concept_code"
    rows = conn.execute(sql, params).fetchall()
    return pd.DataFrame(rows, columns=CONCEPT_MEMBER_COLS)
