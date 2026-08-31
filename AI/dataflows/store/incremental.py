"""
全市场日线本地库 — 每日增量（方案 3.5.1）

daily_job 步骤 store 调用 collect_incremental(conn)：
1. trade_cal 取最近 3 个交易日
2. upsert_stock_basic + upsert_fund_basic（各 1 请求，覆盖新上市/更名）——
   独立提交且提前至逐日循环之前（basic 刷新不得随窗口覆盖/逐日失败而短路）
3. 逐日拉 4 接口（单日查询，复用 backfill.fetch_day_frames 同一采集路径），
   DO UPDATE 入库（覆盖 tushare 日终数据修正）；库内最新日期已覆盖目标窗口时
   跳过本循环（节假日重复运行）
4. 概念体系周刷（决策 10）：refresh_concepts=None 时自动按周一（weekday()==0）
   执行，显式传 True/False 覆盖（测试与手动触发用）；独立提交

返回 {date: {daily: n, factor: n}}；某接口失败仅记录，不阻断其他日。
Provider 经 eventStudy config.get_provider() 获取（与 LIVEPROFIT_DATA_SOURCE
联动）；akshare 环境下结构化方法返回 None → 记 warning 跳过，不阻断后续步骤。
"""

import logging
from datetime import datetime, timedelta

import pandas as pd

from AI.eventStudy.collectors.config import get_provider

from . import concepts
from .backfill import fetch_day_frames
from .db import init_store_schema
from .stock_daily_dao import (
    bulk_upsert_daily, bulk_upsert_factor, latest_trade_date,
    upsert_fund_basic, upsert_stock_basic,
)

logger = logging.getLogger(__name__)

_WINDOW_DAYS = 3       # 增量窗口：最近 3 个交易日（覆盖日终修正）
_CAL_BACK_DAYS = 30    # 交易日历回溯窗口（周末+节假日兜底）


def _last_trade_days(provider, n: int = _WINDOW_DAYS) -> list:
    """最近 n 个交易日（YYYYMMDD 升序）；交易日历不可用返回 []。"""
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=_CAL_BACK_DAYS)).strftime("%Y-%m-%d")
    try:
        cal = provider.get_trade_cal(start, end)
    except Exception as e:
        logger.warning("store 增量: 交易日历拉取异常: %s", e)
        return []
    if cal is None or cal.empty:
        logger.warning("store 增量: 交易日历不可用，跳过")
        return []
    days = sorted(pd.to_datetime(cal["trade_date"]).dt.strftime("%Y%m%d").unique())
    return days[-n:]


def _refresh_basics(conn, provider) -> tuple:
    """基本信息刷新（覆盖新上市/更名），失败不阻断日线。返回 (stock_codes, fund_codes)。"""
    codes = {}
    for label, fetch_fn, upsert_fn in (
        ("stock", provider.get_stock_basic_df, upsert_stock_basic),
        ("fund", provider.get_fund_basic_df, upsert_fund_basic),
    ):
        try:
            df = fetch_fn()
            if df is not None and not df.empty:
                upsert_fn(conn, df)
                codes[label] = df["ts_code"].astype(str).tolist()
            else:
                logger.warning("store 增量: %s 基本信息不可用（None/空）", label)
        except Exception as e:
            logger.warning("store 增量: %s 基本信息刷新失败（不阻断日线）: %s",
                           label, e)
    return codes.get("stock", []), codes.get("fund", [])


def collect_incremental(conn, refresh_concepts: bool = None) -> dict:
    """每日增量主流程。返回 {date: {daily: n, factor: n}}。"""
    result = {}
    if not init_store_schema(conn):
        logger.error("store 增量: schema 初始化失败，跳过")
        return result

    provider = get_provider()
    days = _last_trade_days(provider)
    if not days:
        return result

    # ---- 基本信息刷新（覆盖新上市/更名）——不随窗口覆盖/日线失败而短路 ----
    stock_codes, fund_codes = _refresh_basics(conn, provider)
    try:
        conn.commit()   # 独立提交：后续逐日失败回滚不得静默丢弃 basic 更新
    except Exception as e:
        # upsert 的 SQL 级异常被 _refresh_basics 吞掉后事务可能已 abort，
        # 回滚恢复干净状态（日线增量照常继续）
        logger.warning("store 增量: 基本信息提交失败（回滚后继续）: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass

    # ---- 窗口覆盖判定只作用于逐日拉取循环（节假日重复运行时 basic 仍刷新、
    # 概念周刷仍执行） ----
    latest = latest_trade_date(conn)
    window_covered = False
    if latest is not None:
        latest_dt = latest if hasattr(latest, "strftime") else pd.Timestamp(latest)
        last_target = pd.Timestamp(days[-1])
        if pd.Timestamp(latest_dt) >= last_target:
            window_covered = True
            logger.info("store 增量: 库内最新 %s 已覆盖目标窗口（最近交易日 %s），"
                        "跳过日线窗口", pd.Timestamp(latest_dt).date(), days[-1])

    if not window_covered:
        for d in days:
            try:
                frames = fetch_day_frames(provider, d, stock_codes, fund_codes)
            except Exception as e:
                logger.warning("store 增量: %s 拉取失败（跳过该日，不阻断其他日）: %s", d, e)
                continue
            try:
                n_daily = bulk_upsert_daily(conn, frames["daily"], update=True)
                n_factor = bulk_upsert_factor(conn, frames["factor"], update=True)
                conn.commit()
                result[d] = {"daily": n_daily, "factor": n_factor}
                logger.info("store 增量: %s 完成 daily=%d factor=%d", d, n_daily, n_factor)
            except Exception as e:
                logger.warning("store 增量: %s 写入失败（跳过该日）: %s", d, e)
                try:
                    conn.rollback()
                except Exception:
                    pass

    # ---- 概念体系周刷（决策 10） ----
    do_concepts = (datetime.now().weekday() == 0) if refresh_concepts is None \
        else bool(refresh_concepts)
    if do_concepts:
        try:
            concepts_result = concepts.collect_concepts(conn, provider)
            conn.commit()   # 概念周刷独立提交（35 分钟采集不得依赖后续步骤的偶然 commit）
            logger.info("store 增量: 概念体系周刷完成: ths=%s dc=%s",
                        concepts_result.get("ths", {}).get("error", "ok"),
                        concepts_result.get("dc", {}).get("error", "ok"))
        except Exception as e:
            logger.warning("store 增量: 概念体系周刷失败（不阻断）: %s", e)
            try:
                conn.rollback()   # 采集中途异常可能使事务 abort，回滚恢复干净状态
            except Exception:
                pass
    return result
