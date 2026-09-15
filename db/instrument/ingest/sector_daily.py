"""板块日线采集（板块概念Treemap方案 3.1：sector_daily dc 每日增量）。

dc_daily 是窗口型数据源（实测仅回最近 33 交易日、更早区间 0 行，2026-09-13）——
**无历史可回填**，只有每日增量积累一种形态：每次重拉最近窗口 DO UPDATE 覆盖，
库内历史自采集启动日起自然增长（+1 行/板块/日）。无回填模式、无 checkpoint
（断点续跑无意义：每日全窗口重拉天然自愈，漏采日由次日补回）。

- 窗口 70 自然日（恒覆盖 ≥33 交易日——45 自然日跨春节/国庆只剩 26–28 日，
  放宽到 70 后任何窗口都超端点上限，返回行数仍由端点封顶 33，成本为零）
- 写入列 source='dc' 恒值 + updated_at=采集时刻（DAO 不自动补——sector_daily
  的 updated_at 可空无默认，漏填则 DO UPDATE 会把 NULL 覆盖回已有行）
- 失败分层：单板块失败跳过（次日重拉自愈）；连续 5 板块失败熔断终止本步骤
  （防端点整体退化时 1031 次串行最坏 8.6 小时/日）；整体失败由调用方兜底
- 事务：每板块独立 commit（对齐 collect_sectors 的按工作单元提交约定，
  粒度为板块；SQL 级失败 rollback 恢复干净状态）
"""

import logging
import time
from datetime import date, timedelta

import pandas as pd

from db.instrument.dao.sector import get_sectors
from db.instrument.dao.sector_daily import bulk_upsert_sector_daily

logger = logging.getLogger(__name__)

REQUEST_INTERVAL = 0.2   # 逐板块请求间隔（同 collect_sectors）
MAX_CONSECUTIVE_FAILURES = 5  # 熔断阈值（仓库先例：get_all_concept_boards 连续 2 次）


def _ymd_to_tushare(day: str) -> str:
    """YYYY-MM-DD → YYYYMMDD（tushare 端点硬要求；格式由单测锁定）。"""
    return day.replace("-", "")


def collect_sector_daily_incremental(conn, provider, window_days: int = 70) -> dict:
    """板块日线每日增量（dc 源，DO UPDATE 覆盖最近窗口）。

    conn 由调用方托管生命周期（与 collect_incremental 同规则）。
    返回 {"boards": 完成数, "rows": 写入行数, "failed": [板块代码...]}。
    """
    result = {"boards": 0, "rows": 0, "failed": []}

    boards = get_sectors(conn, "dc")
    if boards is None or boards.empty:
        logger.warning("板块日线增量: 库内无 dc 板块（sector 表），跳过")
        return result
    codes = boards["sector_code"].astype(str).tolist()

    today = date.today()
    start_ymd = (today - timedelta(days=window_days)).isoformat()
    end_ymd = today.isoformat()
    start, end = _ymd_to_tushare(start_ymd), _ymd_to_tushare(end_ymd)

    consecutive_failures = 0
    aborted = False
    for code in codes:
        if aborted:
            result["failed"].append(code)
            continue
        time.sleep(REQUEST_INTERVAL)
        try:
            df = provider.get_sector_daily_df("dc", code, start, end)
        except Exception as e:
            logger.warning("板块日线增量: %s 拉取异常（跳过）: %s", code, e)
            df = None
        if df is None or df.empty:
            result["failed"].append(code)
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logger.warning(
                    "板块日线增量: 连续 %d 个板块失败，熔断终止本步骤（剩余板块记 failed）",
                    MAX_CONSECUTIVE_FAILURES)
                aborted = True
            continue
        consecutive_failures = 0
        try:
            out = _to_sector_daily_frame(df, code)
            n = bulk_upsert_sector_daily(conn, out, update=True)
            conn.commit()
            result["boards"] += 1
            result["rows"] += n
        except Exception as e:
            logger.warning("板块日线增量: %s 写入失败（回滚后继续）: %s", code, e)
            result["failed"].append(code)
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logger.warning(
                    "板块日线增量: 连续 %d 个板块失败，熔断终止本步骤（剩余板块记 failed）",
                    MAX_CONSECUTIVE_FAILURES)
                aborted = True
            try:
                conn.rollback()
            except Exception:
                pass
    logger.info("板块日线增量完成: 板块 %d / 行 %d / 失败 %d%s",
                result["boards"], result["rows"], len(result["failed"]),
                "（熔断终止）" if aborted else "")
    return result


def _to_sector_daily_frame(df: pd.DataFrame, sector_code: str) -> pd.DataFrame:
    """dc_daily 响应 → SECTOR_DAILY_COLS 列集（采集层列映射）。

    实测列（2026-09-13）：ts_code/trade_date/close/open/high/low/change/
    pct_change/vol/amount/swing/turnover_rate/category——
    pre_close 无此列恒 None；swing/category/ts_code 丢弃；pct_change→pct_chg。
    """
    out = pd.DataFrame({
        "trade_date": df["trade_date"],
        "open": df["open"],
        "high": df["high"],
        "low": df["low"],
        "close": df["close"],
        "pre_close": None,
        "change": df["change"],
        "pct_chg": df["pct_change"],
        "vol": df["vol"],
        "amount": df["amount"],
        "turnover_rate": df["turnover_rate"] if "turnover_rate" in df.columns else None,
    }, dtype=object)
    out["source"] = "dc"
    out["sector_code"] = sector_code
    out["updated_at"] = pd.Timestamp.now()
    # 双保险去重：同批次含相同 PK 行时 ON CONFLICT DO UPDATE 报
    # cannot affect row a second time（store-daily 实测踩坑 4）
    return out.drop_duplicates(
        subset=["source", "sector_code", "trade_date"]).reset_index(drop=True)
