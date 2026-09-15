"""db.instrument DAO 共享工具（迁移自 store.stock_daily_dao，3.1.1 写入约定）。

- 批量写入：COPY → 临时表（表名带 pid + 随机后缀防并发）→ INSERT ... ON CONFLICT，
  用完即删（finally drop）
- 表名一律带 `market.` 前缀（schema 限定约定——过渡窗口 public 与 market 同名表并存）
- 清洗顺序：close NaN 行显式 drop（计日志）→ 非法 ts_code/trade_date 行 drop →
  列序归一（缺失列置 None）→ NaN→None（须先转 object dtype）
"""

import logging
import os
import uuid

import pandas as pd

logger = logging.getLogger(__name__)


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
        logger.warning("写入清洗: %d 行 close 为 NaN 已 drop", dropped)
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


def _bulk_upsert(conn, table: str, cols: list, pk_cols: list,
                 df: pd.DataFrame, update: bool) -> int:
    """COPY → 临时表（表名带 pid + 随机后缀防并发）→ INSERT ... ON CONFLICT，用完即删。

    table 为带 schema 限定名（如 `market.instrument_daily`）——SQL 中不整体引号
    包裹（引号包裹会把限定名当单个标识符报错），列名仍逐个引号。

    update=True → DO UPDATE SET 全列（不含 PK）；全列均属 PK（如 sector_member）时
    退化为空更新（DO UPDATE SET 首个 PK 列 = EXCLUDED.自身，与 DO NOTHING 等价，
    保持"DO UPDATE"语义）。
    返回传入行数（清洗后 len(df)），非实际落库行数（DO NOTHING 冲突时落库更少）。
    """
    tmp = f"tmp_{table.replace('.', '_')}_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    col_list = ", ".join(f'"{c}"' for c in cols)
    pk_list = ", ".join(f'"{c}"' for c in pk_cols)
    with conn.cursor() as cur:
        # INCLUDING DEFAULTS 必带：LIKE 默认不复制 DEFAULT 表达式，否则带
        # DEFAULT now() 的 NOT NULL 列（updated_at）COPY 时被填 NULL 违例
        cur.execute(f'CREATE TEMP TABLE "{tmp}" (LIKE {table} INCLUDING DEFAULTS) '
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
                f'INSERT INTO {table} ({col_list}) '
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
