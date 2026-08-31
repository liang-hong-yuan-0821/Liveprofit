"""
全市场日线本地库 — 历史回填脚本（方案 3.4）

用法：
  python -m AI.dataflows.store.backfill [--start 2016-01-01] [--end 今天]
                                        [--retry-missing] [--skip-concepts]

设计要点：
- 直接实例化 TushareProvider（不经 get_provider()——store 数据只可能来自 tushare
  代理端点，与 LIVEPROFIT_DATA_SOURCE 无关，避免 akshare 环境下 4 方法全返回
  None、2600 交易日 × 3 重试空跑 9~10 小时）
- 单日 4 接口（daily/adj_factor/fund_daily/fund_adj）全部成功后单日提交；任一接口
  失败 / 完整性校验失败 / 写入抛异常 → 当日整体不写库（库内无"半截日"）→
  重试 3 次（间隔 5s）后跳过，写入失败清单 logs/stock_backfill_failures.json
- 断点续跑：按库内已入库交易日集合跳过（"完整日才入库"不变式保证存在即完整）。
  不用 max(trade_date) 截断——库内只有尾部几日时 max 断点会把全部历史误判为
  已入库跳过（2026-08-30 实测踩坑）
- 截断检测（硬性约束 3.2.2）：单日返回行数 ≥ 6000 → 视为截断，自动降级为分批补拉
  （全量代码列表、每批 100 个逗号分隔 + trade_date 逐批查询后 concat，实测可用）；
  全市场拉取只允许 trade_date 单日查询，不提供区间参数入口
- --retry-missing：忽略断点，仅按失败清单日期执行
  "4 接口拉取 + 完整性校验 + DO NOTHING upsert"，成功则从清单移除；
  与断点续跑写入同一主键空间，互不干扰（幂等）
- --skip-concepts：跳过概念体系采集（概念数据已新鲜时用，省 ~35 分钟）
- 请求间隔 0.2s（实测无强限频，保守余量）

fetch_day_frames() 供 incremental.py 复用（增量 DO UPDATE 口径同一采集路径）。
"""

import argparse
import json
import logging
import logging.handlers
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from AI.dataflows.providers.tushare_provider import TushareProvider

from . import concepts
from .db import get_connection, init_store_schema
from .stock_daily_dao import (
    bulk_upsert_daily, bulk_upsert_factor, is_fund_ts_code,
    upsert_fund_basic, upsert_stock_basic,
)

logger = logging.getLogger(__name__)

# ==================== 常量 ====================

TRUNCATION_ROWS = 6000    # 单日行数 ≥ 6000 视为截断（实测单日 5547 距上限仅 ~8% 余量）
BATCH_SIZE = 100          # 分批补拉每批代码数（实测 200/批接近上限，100 为保守值）
RETRY_COUNT = 3           # 单日失败重试次数
RETRY_INTERVAL = 5.0      # 重试间隔（秒）
REQUEST_INTERVAL = 0.2    # 请求间隔（秒）
FAILURE_LIST_PATH = Path("logs/stock_backfill_failures.json")
PROGRESS_LOG_PATH = Path("logs/stock_backfill.log")


class StoreFetchError(Exception):
    """单日采集失败（接口失败 / 截断无法降级 / 数据为空），整日不写库。"""


# ==================== 失败清单（JSON 数组 ["YYYYMMDD", ...]，temp+rename 原子写） ====================

def _read_failures() -> list:
    if not FAILURE_LIST_PATH.exists():
        return []
    try:
        data = json.loads(FAILURE_LIST_PATH.read_text(encoding="utf-8"))
        return [str(d) for d in data] if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("失败清单读取失败（按空清单处理）: %s", e)
        return []


def _write_failures(dates: list) -> None:
    tmp = FAILURE_LIST_PATH.with_name(
        f"{FAILURE_LIST_PATH.stem}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(sorted(dates), ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(FAILURE_LIST_PATH)


def _append_failure(date_str: str) -> None:
    dates = _read_failures()
    if date_str not in dates:
        dates.append(date_str)
        _write_failures(dates)


# ==================== 单日采集（fetch_day_frames 供 incremental 复用） ====================

def _batched_pull(provider, market: str, kind: str, codes: list,
                  trade_date: str):
    """分批补拉：每批 BATCH_SIZE 个代码逗号分隔 + trade_date 逐批查询后 concat。

    任一失败返回 None（调用方走整日失败路径）。market: "stock"/"fund"，
    kind: "daily"/"factor"。
    """
    api_map = {
        ("stock", "daily"): provider.api.daily,
        ("fund", "daily"): provider.api.fund_daily,
        ("stock", "factor"): provider.api.adj_factor,
        ("fund", "factor"): provider.api.fund_adj,
    }
    fn = api_map[(market, kind)]
    frames = []
    for i in range(0, len(codes), BATCH_SIZE):
        batch = codes[i:i + BATCH_SIZE]
        time.sleep(REQUEST_INTERVAL)
        try:
            df = provider._api_call(fn, ts_code=",".join(batch),
                                    trade_date=trade_date)
        except Exception as e:
            logger.warning("store 分批补拉 %s/%s 第 %d 批异常: %s",
                           market, kind, i // BATCH_SIZE, e)
            return None
        if df is None:
            logger.warning("store 分批补拉 %s/%s 第 %d 批超时/失败",
                           market, kind, i // BATCH_SIZE)
            return None
        if not df.empty:
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _pull_market_frame(provider, trade_date: str, market: str, kind: str,
                       codes: list):
    """单接口拉取 + 截断检测降级分批补拉。返回 None 表示拉取失败。"""
    time.sleep(REQUEST_INTERVAL)
    if kind == "daily":
        df = provider.get_full_market_daily_df(trade_date, market)
    else:
        df = provider.get_full_market_factor_df(trade_date, market)
    if df is None:
        return None
    if len(df) >= TRUNCATION_ROWS:
        logger.warning(
            "store: %s/%s %s 单日 %d 行 ≥ 截断阈值 %d，降级分批补拉",
            trade_date, market, kind, len(df), TRUNCATION_ROWS)
        if not codes:
            logger.error("store: %s/%s 截断但无代码列表可分批补拉，整日失败",
                         market, kind)
            return None
        return _batched_pull(provider, market, kind, codes, trade_date)
    return df


def fetch_day_frames(provider, trade_date: str, stock_codes: list = None,
                     fund_codes: list = None) -> dict:
    """单交易日 4 接口全量拉取（含截断检测降级），内存合并股票/基金。

    返回 {"daily": 股票+基金日线 concat, "factor": 股票+基金因子 concat}；
    任一接口失败、两市场日线均为空 → 抛 StoreFetchError（整日不写库）。
    stock_codes/fund_codes：截断降级分批补拉所需代码列表（None 时无法降级）。
    """
    frames = {}
    for market, codes in (("stock", stock_codes), ("fund", fund_codes)):
        for kind in ("daily", "factor"):
            df = _pull_market_frame(provider, trade_date, market, kind, codes)
            if df is None:
                raise StoreFetchError(f"{market} {kind} 拉取失败")
            frames[f"{market}_{kind}"] = df
    if frames["stock_daily"].empty and frames["fund_daily"].empty:
        raise StoreFetchError("当日两市场日线均为空（交易日历开放日无任何行情，判定异常）")
    return {
        "daily": pd.concat([frames["stock_daily"], frames["fund_daily"]],
                           ignore_index=True),
        "factor": pd.concat([frames["stock_factor"], frames["fund_factor"]],
                            ignore_index=True),
    }


# ==================== 软性 sanity 检查 ====================

def _expected_stock_count(basic_df: pd.DataFrame, trade_date: str):
    """由 stock_basic 的 list_date/delist_date 重建当日应上市数（软性 sanity 用）。"""
    if basic_df is None or basic_df.empty or "list_date" not in basic_df.columns:
        return None
    d = trade_date
    list_dates = basic_df["list_date"]
    listed = list_dates.notna() & (list_dates.astype(str) <= d)
    if "delist_date" in basic_df.columns:
        delist = basic_df["delist_date"]
        delisted = delist.notna() & (delist.astype(str) <= d)
    else:
        delisted = pd.Series(False, index=basic_df.index)
    return int((listed & ~delisted).sum())


def _sanity_check(daily_df: pd.DataFrame, expected) -> None:
    """日线行数 vs 应上市数偏差超过 ±5% 记 warning（不阻断，覆盖新股扩容初期
    stock_basic 未及时更新的情况）。"""
    if expected is None or expected <= 0:
        return
    n = len(daily_df)
    if abs(n - expected) / expected > 0.05:
        logger.warning(
            "store sanity: 日线 %d 行 vs 应上市 %d，偏差 %.1f%% > 5%%"
            "（不阻断；可能新股扩容初期 stock_basic 未及时更新）",
            n, expected, abs(n - expected) / expected * 100)


# ==================== 基本信息与代码列表 ====================

def _refresh_basics(conn, provider):
    """upsert stock_basic + fund_basic（各 1 请求，覆盖退市股与历史基金）。

    返回 (stock_codes, fund_codes, stock_basic_df)；单表失败不阻断整体
    （代码列表缺失仅影响截断降级能力，basic_df 缺失仅跳过软性 sanity 检查）。
    """
    code_universe = {}
    basic_df = None
    for label, fetch_fn, upsert_fn in (
        ("stock", provider.get_stock_basic_df, upsert_stock_basic),
        ("fund", provider.get_fund_basic_df, upsert_fund_basic),
    ):
        try:
            df = fetch_fn()
            if df is not None and not df.empty:
                upsert_fn(conn, df)
                code_universe[label] = df["ts_code"].astype(str).tolist()
                if label == "stock":
                    basic_df = df
                logger.info("store 基本信息: %s %d 行已 upsert", label, len(df))
            else:
                logger.warning("store 基本信息: %s 拉取不可用（None/空），"
                               "截断降级将不可用", label)
        except Exception as e:
            logger.warning("store 基本信息: %s 刷新失败（不阻断日线回填）: %s",
                           label, e)
    return code_universe.get("stock", []), code_universe.get("fund", []), basic_df


# ==================== 逐日执行 ====================

def _run_day(conn, provider, trade_date: str, stock_codes: list,
             fund_codes: list, basic_df, update: bool) -> None:
    """单日执行：4 接口拉取 + sanity 检查 + DO NOTHING/UPDATE upsert + 单日提交。

    任一环节失败抛异常（由调用方重试/跳过），当日整体不写库。
    """
    frames = fetch_day_frames(provider, trade_date, stock_codes, fund_codes)
    # sanity 基线是纯股票应上市数（stock_basic 重建），须剔除合并帧中的基金行
    stock_daily = frames["daily"]
    if not stock_daily.empty and "ts_code" in stock_daily.columns:
        stock_daily = stock_daily[~stock_daily["ts_code"].map(is_fund_ts_code)]
    _sanity_check(stock_daily, _expected_stock_count(basic_df, trade_date))
    n_daily = bulk_upsert_daily(conn, frames["daily"], update=update)
    n_factor = bulk_upsert_factor(conn, frames["factor"], update=update)
    conn.commit()
    logger.info("store 回填: %s 完成 daily=%d factor=%d", trade_date, n_daily, n_factor)


def _run_day_with_retry(conn, provider, trade_date: str, stock_codes: list,
                        fund_codes: list, basic_df, update: bool) -> bool:
    """单日执行 + 失败重试 RETRY_COUNT 次（间隔 RETRY_INTERVAL 秒），
    仍失败返回 False（调用方记失败清单）。"""
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            _run_day(conn, provider, trade_date, stock_codes, fund_codes,
                     basic_df, update)
            return True
        except Exception as e:
            logger.warning("store 回填: %s 第 %d/%d 次失败: %s",
                           trade_date, attempt, RETRY_COUNT, e)
            try:
                conn.rollback()
            except Exception:
                pass
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_INTERVAL)
    logger.error("store 回填: %s 重试 %d 次后跳过，已记失败清单",
                 trade_date, RETRY_COUNT)
    return False


# ==================== 主流程 ====================

def _provider_ready(provider) -> bool:
    if not provider.connected:
        logger.error("Tushare 未连接（检查 TUSHARE_TOKEN），回填无法继续")
        return False
    return True


def _existing_trade_dates(conn) -> set:
    """库内已完整入库的交易日集合（YYYYMMDD）。

    断点续跑按存在性跳过：'完整日才入库'不变式保证存在即完整——
    不能用 max(trade_date) 截断（若库内只有尾部几日，max 断点会把
    全部历史误判为已入库跳过，2026-08-30 实测踩坑）。
    """
    rows = conn.execute("SELECT DISTINCT trade_date FROM stock_daily").fetchall()
    return {pd.Timestamp(r[0]).strftime("%Y%m%d") for r in rows}


def run_backfill(start: str, end: str, retry_missing: bool = False,
                 skip_concepts: bool = False) -> dict:
    """历史回填主流程（含 --retry-missing / --skip-concepts 模式）。返回汇总 dict。"""
    summary = {"days_done": 0, "failed_days": [], "retry_ok": 0, "retry_failed": 0}
    conn = None
    provider = None
    try:
        conn = get_connection()
        if not init_store_schema(conn):
            logger.error("store schema 初始化失败，终止")
            return summary
        provider = TushareProvider()

        # ---- 概念体系（失败域分层：整体失败不阻断日线回填） ----
        # --skip-concepts：概念数据已新鲜时跳过重采（每次 ~35 分钟，断点续跑冗余）
        if not skip_concepts:
            try:
                concepts_result = concepts.collect_concepts(conn, provider)
                conn.commit()   # 概念采集独立提交：后续逐日失败回滚不得静默丢弃采集成果
                logger.info("store 概念体系采集完成: ths=%s dc=%s",
                            concepts_result.get("ths", {}).get("error", "ok"),
                            concepts_result.get("dc", {}).get("error", "ok"))
            except Exception as e:
                logger.warning("store 概念体系采集失败（不阻断日线回填）: %s", e)
                try:
                    conn.rollback()   # 采集中途异常可能使事务 abort，回滚恢复干净状态
                except Exception:
                    pass

        if not _provider_ready(provider):
            return summary

        # ---- 基本信息（截断降级代码列表来源；失败不阻断） ----
        stock_codes, fund_codes, basic_df = _refresh_basics(conn, provider)
        try:
            conn.commit()   # 基本信息独立提交（同概念：逐日失败回滚不丢弃 basic 更新）
        except Exception as e:
            # _refresh_basics 吞掉 upsert 的 SQL 级异常后事务可能已 abort，
            # commit 会抛 InFailedSqlTransaction——回滚恢复干净状态继续日线回填
            logger.warning("store 基本信息提交失败（回滚后继续日线回填）: %s", e)
            try:
                conn.rollback()
            except Exception:
                pass

        if retry_missing:
            return _run_retry_missing(conn, provider, stock_codes, fund_codes,
                                      summary)

        # ---- 交易日历 ----
        cal = provider.get_trade_cal(start, end)
        if cal is None or cal.empty:
            logger.error("交易日历获取失败 [%s ~ %s]，终止", start, end)
            return summary
        trade_days = sorted(
            pd.to_datetime(cal["trade_date"]).dt.strftime("%Y%m%d").unique().tolist())

        # ---- 断点续跑：按库内已入库交易日集合跳过（存在即完整） ----
        existing = _existing_trade_dates(conn)
        skipped = [d for d in trade_days if d in existing]
        trade_days = [d for d in trade_days if d not in existing]
        if skipped:
            logger.info("store 断点续跑: 库内已有 %d 个交易日，跳过（含 %s ~ %s）",
                        len(skipped), skipped[0], skipped[-1])
        if not trade_days:
            logger.info("store 回填: 目标区间已全部入库，无待回填交易日")
            return summary

        # ---- 逐交易日（升序） ----
        total = len(trade_days)
        for i, d in enumerate(trade_days, 1):
            ok = _run_day_with_retry(conn, provider, d, stock_codes, fund_codes,
                                     basic_df, update=False)
            if ok:
                summary["days_done"] += 1
            else:
                summary["failed_days"].append(d)
                _append_failure(d)
            logger.info("store 回填进度: %d/%d 交易日", i, total)
        return summary
    finally:
        if conn is not None:
            conn.close()


def _run_retry_missing(conn, provider, stock_codes, fund_codes, summary) -> dict:
    """--retry-missing：忽略断点，仅按失败清单日期执行
    "4 接口拉取 + 完整性校验 + DO NOTHING upsert"，成功则从清单移除。"""
    dates = _read_failures()
    if not dates:
        logger.info("store --retry-missing: 失败清单为空")
        return summary
    logger.info("store --retry-missing: 失败清单 %d 个交易日待补拉", len(dates))
    remaining = []
    for d in dates:
        ok = _run_day_with_retry(conn, provider, d, stock_codes, fund_codes,
                                 basic_df=None, update=False)
        if ok:
            summary["retry_ok"] += 1
        else:
            summary["retry_failed"] += 1
            remaining.append(d)
    _write_failures(remaining)
    summary["failed_days"] = remaining
    return summary


# ==================== CLI ====================

def main():
    parser = argparse.ArgumentParser(description="全市场日线本地库历史回填")
    parser.add_argument("--start", default="2016-01-01",
                        help="起始日期 YYYY-MM-DD，默认 2016-01-01（近 10 年）")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"),
                        help="截止日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--retry-missing", action="store_true",
                        help="忽略断点，仅补拉失败清单中的交易日")
    parser.add_argument("--skip-concepts", action="store_true",
                        help="跳过概念体系采集（概念数据已新鲜时用，省 ~35 分钟）")
    args = parser.parse_args()

    Path("logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(PROGRESS_LOG_PATH, encoding="utf-8"),
        ],
    )
    summary = run_backfill(args.start, args.end, retry_missing=args.retry_missing,
                           skip_concepts=args.skip_concepts)
    logger.info("store 回填结束: 新入库 %d 日 / 失败 %d 日 %s / "
                "retry-missing 成功 %d 失败 %d",
                summary["days_done"], len(summary["failed_days"]),
                summary["failed_days"], summary["retry_ok"],
                summary["retry_failed"])
    if summary["failed_days"]:
        logger.warning("存在失败交易日，可稍后执行 "
                       "`python -m AI.dataflows.store.backfill --retry-missing` 补拉")


if __name__ == "__main__":
    main()
