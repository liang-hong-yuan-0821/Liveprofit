"""
全市场日线本地库 — 概念体系多来源采集（方案 3.3.1 concepts.py）

ths=同花顺 / dc=东方财富 双来源（决策 9）：概念列表 + 逐概念成分，落
concept / concept_member 两张表（决策 8）。backfill 与 incremental 共用
（incremental 周一自动周刷，决策 10）。

失败域分层（方案 3.4.1）：
- 单概念失败仅跳过该概念（记 failed 清单，后续周刷自然重试）
- 单来源失败不阻断另一来源
- 概念体系整体失败由调用方 try/except 兜底（不阻断日线回填/增量）
"""

import logging
import time
from datetime import datetime, timedelta

import pandas as pd

from .stock_daily_dao import upsert_concept_members, upsert_concepts

logger = logging.getLogger(__name__)

SOURCES = ("ths", "dc")
REQUEST_INTERVAL = 0.2   # 逐概念请求间隔（实测无强限频，保守余量）


def _latest_trade_date_str(provider) -> str:
    """最近交易日 YYYYMMDD（dc 快照口径需要）；trade_cal 不可用返回 None。"""
    try:
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        cal = provider.get_trade_cal(start, end)
        if cal is None or cal.empty:
            logger.warning("概念采集: 交易日历不可用，dc 来源无法取快照日")
            return None
        return pd.to_datetime(cal["trade_date"]).max().strftime("%Y%m%d")
    except Exception as e:
        logger.warning("概念采集: 最近交易日获取失败: %s", e)
        return None


def _collect_one_source(conn, provider, source: str, dc_trade_date: str) -> dict:
    """单来源采集：概念列表 upsert → 逐概念拉成分 upsert。

    返回 {"concepts": n, "members": n, "failed": [概念代码...], "error": str|None}。
    """
    result = {"concepts": 0, "members": 0, "failed": [], "error": None}
    try:
        list_df = provider.get_concept_list_df(source)
    except Exception as e:
        result["error"] = f"列表拉取异常: {e}"
        logger.warning("概念来源 %s 列表拉取异常（跳过该来源）: %s", source, e)
        return result
    if list_df is None or list_df.empty or "ts_code" not in list_df.columns:
        result["error"] = "列表不可用"
        logger.warning("概念来源 %s 列表不可用（None/空/缺 ts_code 列），跳过该来源", source)
        return result

    list_df = list_df.copy()
    list_df["source"] = source
    result["concepts"] = upsert_concepts(conn, list_df)
    logger.info("概念来源 %s: 列表 %d 行已 upsert，开始逐概念拉成分", source, len(list_df))

    if source == "dc" and dc_trade_date is None:
        result["error"] = "无最近交易日快照，成分采集跳过"
        logger.warning("概念来源 dc 无最近交易日（trade_cal 不可用），"
                       "列表已入库、成分采集跳过")
        return result

    for concept_code in list_df["ts_code"].astype(str).tolist():
        time.sleep(REQUEST_INTERVAL)
        try:
            members = provider.get_concept_members_df(
                concept_code, source,
                trade_date=dc_trade_date if source == "dc" else None,
            )
        except Exception as e:
            result["failed"].append(concept_code)
            logger.warning("概念来源 %s: %s 成分拉取异常，跳过该概念: %s",
                           source, concept_code, e)
            continue
        if members is None:
            result["failed"].append(concept_code)
            continue
        if members.empty:
            # 无 A 股成分不算失败：实测 ths 大量跨市场概念（港美股概念指数，
            # 如 864008"激光雷达"唯一成分是 MVIS.O），成分全在 V1 范围外被
            # Provider 过滤成空（周刷全量重采，端点瞬时空响应也会自然自愈）
            continue
        members = members.copy()
        members["source"] = source
        result["members"] += upsert_concept_members(conn, members)

    if result["failed"]:
        logger.warning("概念来源 %s: %d 个概念成分拉取失败已跳过: %s",
                       source, len(result["failed"]),
                       ", ".join(str(c) for c in result["failed"][:20]))
    return result


def collect_concepts(conn, provider) -> dict:
    """概念体系双来源采集（ths 899 + dc 1031 概念，逐概念拉成分 ≈ 2000 请求 ≈ 35 分钟）。

    单来源失败不阻断另一来源；整体失败由调用方兜底。
    返回 {"ths": {...}, "dc": {...}}（结构见 _collect_one_source）。
    """
    dc_trade_date = _latest_trade_date_str(provider)
    results = {}
    for source in SOURCES:
        try:
            results[source] = _collect_one_source(conn, provider, source, dc_trade_date)
            # 按来源独立提交：后一来源失败的回滚不得丢弃前一来源的成果
            # （两来源共用同一连接/事务，2026-08-30 实测踩坑：不提交时
            # dc 失败回滚把已完成的 ths 899 概念一并清空）
            conn.commit()
        except Exception as e:
            results[source] = {"concepts": 0, "members": 0,
                               "failed": [], "error": f"来源级异常: {e}"}
            logger.warning("概念来源 %s 采集整体失败（不阻断另一来源）: %s", source, e)
            try:
                conn.rollback()   # 回滚仅该来源的半截写入；事务可能已 abort，
                                  # 回滚同时恢复干净状态（下一来源 DB 写入不受污染）
            except Exception:
                pass
    return results
