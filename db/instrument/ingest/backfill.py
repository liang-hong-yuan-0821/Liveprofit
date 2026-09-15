"""全历史回填（证券市场数据库统一方案 3.5 + store 回填路径迁移）。

用法：
  python -m db.instrument.ingest.backfill [--start 2016-01-01] [--end 今天]
                                           [--retry-missing] [--skip-concepts]

设计要点（store 回填经验逐条沿用）：
- 指数全历史为内置分项（INDEX_TARGETS 13 个：CN 9 + US 3 + KS11，2026-09-14
  US/KR 上线）：get_index_data_df 全历史 →
  instrument_daily DO UPDATE（补齐迁移行缺失的 pre_close/change/pct_chg 三列，
  store 决策 5 的显式例外）；get_index_factor_df → factor_daily DO UPDATE
  （close 按上游覆盖全历史段）；instrument 行自举前置
- 个股基金日线回填：单日 4 接口全部成功后单日提交；失败重试 3 次（间隔 5s）
  后跳过记失败清单 logs/stock_backfill_failures.json；DO NOTHING（决策 5）
- 断点续跑：按库内已入库交易日集合跳过（"完整日才入库"不变式保证存在即完整）。
  不用 max(trade_date) 截断（库内只有尾部几日时会把全部历史误判为已入库，
  2026-08-30 实测踩坑）
- 全市场拉取只允许 trade_date 单日查询（禁区间，6000 行静默截断）
- provider 工厂注入；CLI 入口函数内延迟 import AI.dataflows.providers.cn
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from db.instrument.dao.adj_factor import bulk_upsert_factor
from db.instrument.dao.factor_daily import bulk_upsert_factor_daily
from db.instrument.dao.instrument_daily import bulk_upsert_daily
from db.instrument.ingest.frames import (
    TRUNCATION_ROWS, REQUEST_INTERVAL, fetch_day_frames, StoreFetchError,
)
from db.instrument.ingest.incremental import (
    INDEX_TARGETS, _bootstrap_instruments, _INDEX_DAILY_COLS,
    _assert_index_targets_valid, _is_cn_index_code, _provider_source,
)
from db.instrument.ingest.sectors import collect_sectors

logger = logging.getLogger(__name__)

RETRY_COUNT = 3           # 单日失败重试次数
RETRY_INTERVAL = 5.0      # 重试间隔（秒）
FAILURE_LIST_PATH = Path("logs/stock_backfill_failures.json")
PROGRESS_LOG_PATH = Path("logs/stock_backfill.log")

# 指数回填分段（5 年 ≈ 1220 行/段，≪ idx_factor_pro 官方 8000 行上限）
INDEX_CHUNK_DAYS = 1825


# ==================== 失败清单（JSON 数组，temp+rename 原子写） ====================

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


# ==================== 指数全历史分项（3.5.1） ====================

def _iter_date_chunks(start: str, end: str):
    """按 INDEX_CHUNK_DAYS 分段（YYYY-MM-DD 闭区间）。"""
    s = pd.Timestamp(start)
    e = pd.Timestamp(end)
    while s <= e:
        chunk_end = min(s + timedelta(days=INDEX_CHUNK_DAYS - 1), e)
        yield s.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")
        s = chunk_end + timedelta(days=1)


def backfill_index_history(conn, provider, start: str, end: str,
                           fallback_provider=None) -> dict:
    """指数全历史回填内置分项（自举前置 + DO UPDATE + 5 年分段 + 断点续跑）。

    断点续跑按库内已入库交易日集合跳过（同个股基金回填口径：存在即完整）；
    双源兜底：主源段拉取失败/空时换 fallback_provider 重试（CR BLOCKER 2）。
    """
    result = {"bars": 0, "factors": 0}
    _assert_index_targets_valid()
    _bootstrap_instruments(conn)
    conn.commit()

    for code in INDEX_TARGETS:
        # 断点集合按指数单独计算（CR M3：跨代码并集会漏掉"零行目标"——
        # 库内无该指数任何行时 existing_set 命中其他指数日期、缺列探测返回
        # None → 误跳过，该指数永不回填）
        existing = conn.execute(
            "SELECT DISTINCT trade_date FROM market.instrument_daily "
            "WHERE ts_code = %s", (code,),
        ).fetchall()
        existing_set = {pd.Timestamp(r[0]).strftime("%Y%m%d") for r in existing}
        for chunk_start, chunk_end in _iter_date_chunks(start, end):
            # 请求间隔（QPS 保护：tushare 限速，远低于 500/s）
            time.sleep(REQUEST_INTERVAL)
            bars_source = provider
            try:
                bars = provider.get_index_data_df(code, chunk_start, chunk_end)
            except Exception as e:
                logger.warning("指数回填: %s [%s ~ %s] 拉取异常（换兜底源重试）: %s",
                               code, chunk_start, chunk_end, e)
                bars = None
            if bars is None or bars.empty:
                if fallback_provider is not None and fallback_provider is not provider:
                    time.sleep(REQUEST_INTERVAL)
                    try:
                        bars = fallback_provider.get_index_data_df(
                            code, chunk_start, chunk_end)
                        bars_source = fallback_provider
                    except Exception as e:
                        logger.warning("指数回填: %s [%s ~ %s] 兜底源也失败（跳过该段）: %s",
                                       code, chunk_start, chunk_end, e)
                        continue
                if bars is None or bars.empty:
                    continue
            # 断点续跑：整段已入库**且无缺列行**则跳过（升序帧首末日都已在库 =
            # 段完整）。缺列行判定：迁移迁入行 pre_close 恒 NULL（eventStudy 源
            # 无此列）——日期存在但缺列时必须重跑 DO UPDATE 补齐（3.5.1 定稿
            # "回填 DO UPDATE 既是同源幂等覆盖，又把三列补齐"的落点）
            date_strs = pd.to_datetime(bars["trade_date"]).dt.strftime("%Y%m%d")
            if date_strs.isin(list(existing_set)).all():
                # 缺列探测限定到本段日期区间：各指数首行 pre_close 合法 NULL
                # （000688.SH 基日/399001.SZ 上市日），全历史探测会让这两个
                # 指数的所有分段每次都重拉重写（三轮 review minor 1b）
                # 且限定 source='tushare'：akshare 兜底行 pre_close 恒 NULL
                # 属事实（2026-09-14 兜底首次真正可用后暴露），不应被判缺列
                # 而永久重拉重写（幂等但耗 API 配额）
                segment_dates = [pd.Timestamp(d).date() for d in date_strs]
                missing = conn.execute(
                    "SELECT 1 FROM market.instrument_daily "
                    "WHERE ts_code = %s AND trade_date = ANY(%s) "
                    "AND pre_close IS NULL AND source = 'tushare' LIMIT 1",
                    (code, segment_dates),
                ).fetchone()
                if missing is None:
                    continue
            bars = bars.copy()
            out = pd.DataFrame({
                c: bars[c] if c in bars.columns else None
                for c in _INDEX_DAILY_COLS
            }, dtype=object)
            out["ts_code"] = code
            out["source"] = _provider_source(bars_source)
            out["updated_at"] = pd.Timestamp.now()
            n = bulk_upsert_daily(conn, out, update=True)  # DO UPDATE 补齐三列
            result["bars"] += n
            time.sleep(REQUEST_INTERVAL)
            # 因子仅 CN（主源 idx_factor_pro 端点；US/KR 无因子源，2026-09-14）
            if _is_cn_index_code(code):
                try:
                    factor_df = provider.get_index_factor_df(code, chunk_start, chunk_end)
                except Exception as e:
                    logger.warning("指数回填: %s [%s ~ %s] 因子拉取异常（跳过）: %s",
                                   code, chunk_start, chunk_end, e)
                    factor_df = None
                if factor_df is not None and not factor_df.empty:
                    missing = getattr(factor_df, "attrs", {}).get("missing_chunks")
                    if missing:
                        logger.warning("指数回填: %s 因子缺段 %s，拒绝部分入库",
                                       code, missing)
                    else:
                        factor_df = factor_df.copy()
                        factor_df["ts_code"] = code  # get_index_factor_df 帧无 ts_code 列
                        factor_df["updated_at"] = pd.Timestamp.now()
                        result["factors"] += bulk_upsert_factor_daily(
                            conn, factor_df, update=True)
            conn.commit()
            logger.info("指数回填: %s [%s ~ %s] bars=%d", code, chunk_start,
                        chunk_end, n)
    return result


# ==================== 个股基金日线回填（store 路径迁移） ====================

def _run_day(conn, provider, trade_date: str, stock_codes: list,
             fund_codes: list) -> None:
    """单日执行：4 接口拉取 + DO NOTHING upsert + 单日提交。"""
    frames = fetch_day_frames(provider, trade_date, stock_codes, fund_codes)
    frames["daily"] = frames["daily"].copy()
    frames["daily"]["source"] = _provider_source(provider)
    frames["daily"]["updated_at"] = pd.Timestamp.now()
    n_daily = bulk_upsert_daily(conn, frames["daily"], update=False)
    n_factor = bulk_upsert_factor(conn, frames["factor"], update=False)
    conn.commit()
    logger.info("回填: %s 完成 daily=%d factor=%d", trade_date, n_daily, n_factor)


def _run_day_with_retry(conn, provider, trade_date: str, stock_codes: list,
                        fund_codes: list) -> bool:
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            _run_day(conn, provider, trade_date, stock_codes, fund_codes)
            return True
        except Exception as e:
            logger.warning("回填: %s 第 %d/%d 次失败: %s",
                           trade_date, attempt, RETRY_COUNT, e)
            try:
                conn.rollback()
            except Exception:
                pass
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_INTERVAL)
    logger.error("回填: %s 重试 %d 次后跳过，已记失败清单", trade_date, RETRY_COUNT)
    return False


def _existing_trade_dates(conn, instrument_types: list[str] = None) -> set:
    """库内已入库交易日集合。默认按数据域过滤 instrument_type=('stock','fund')
    ——指数全历史先写入会把全局 max 抬高，不过滤则个股基金日线被静默跳过
    100%（CR 二轮 BLOCKER 1，B1 同类根因的回填侧）。"""
    if instrument_types is None:
        rows = conn.execute(
            "SELECT DISTINCT trade_date FROM market.instrument_daily").fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT d.trade_date FROM market.instrument_daily d "
            "JOIN market.instrument i ON i.ts_code = d.ts_code "
            "WHERE i.instrument_type = ANY(%s)",
            (list(instrument_types),),
        ).fetchall()
    return {pd.Timestamp(r[0]).strftime("%Y%m%d") for r in rows}


# ==================== 主流程 ====================

def run_backfill(conn, start: str, end: str, retry_missing: bool = False,
                 skip_concepts: bool = False, provider_factory=None,
                 fallback_provider_factory=None, skip_index: bool = False,
                 skip_daily: bool = False) -> dict:
    """历史回填主流程（conn 由调用方托管生命周期，与 collect_incremental 同规则）。

    fallback_provider_factory：指数分项双源兜底（CR BLOCKER 2——生产入口
    经公共装配传入）；skip_index/skip_daily 供 market_ingest wrapper 的
    --skip-bars 映射（内部参数，不暴露 CLI）。返回汇总 dict。
    """
    summary = {"index": {}, "days_done": 0, "failed_days": [],
               "retry_ok": 0, "retry_failed": 0}
    if provider_factory is None:
        provider_factory, fallback_provider_factory = _providers_from_env()
    provider = provider_factory()

    # ---- 板块体系（失败域分层：整体失败不阻断回填；--skip-concepts 跳过） ----
    if not skip_concepts:
        try:
            sectors_result = collect_sectors(conn, provider)
            conn.commit()
            logger.info("板块体系采集完成: ths=%s dc=%s",
                        sectors_result.get("ths", {}).get("error", "ok"),
                        sectors_result.get("dc", {}).get("error", "ok"))
        except Exception as e:
            logger.warning("板块体系采集失败（不阻断回填）: %s", e)
            try:
                conn.rollback()
            except Exception:
                pass

    # ---- 指数全历史内置分项 ----
    if not skip_index:
        try:
            summary["index"] = backfill_index_history(
                conn, provider, start, end,
                fallback_provider=fallback_provider_factory()
                if fallback_provider_factory else None)
        except Exception as e:
            logger.warning("指数回填失败（不阻断个股基金回填）: %s", e)
            try:
                conn.rollback()
            except Exception:
                pass

    if skip_daily:
        return summary

    if not provider.connected:
        logger.error("Tushare 未连接（检查 TUSHARE_TOKEN），回填无法继续")
        return summary

    if retry_missing:
        return _run_retry_missing(conn, provider, summary)

    # ---- 基本信息（截断降级代码列表来源；失败不阻断） ----
    stock_codes, fund_codes = [], []
    for label, fetch_fn in (("stock", provider.get_stock_basic_df),
                            ("fund", provider.get_fund_basic_df)):
        try:
            df = fetch_fn()
            if df is not None and not df.empty:
                (stock_codes if label == "stock" else fund_codes).extend(
                    df["ts_code"].astype(str).tolist())
        except Exception as e:
            logger.warning("基本信息: %s 刷新失败（截断降级将不可用）: %s", label, e)

    # ---- 交易日历 ----
    try:
        cal = provider.get_trade_cal(start, end)
    except Exception as e:
        logger.error("交易日历获取失败 [%s ~ %s]，终止: %s", start, end, e)
        return summary
    if cal is None or cal.empty:
        logger.error("交易日历不可用 [%s ~ %s]，终止", start, end)
        return summary
    trade_days = sorted(
        pd.to_datetime(cal["trade_date"]).dt.strftime("%Y%m%d").unique().tolist())

    # ---- 断点续跑：按库内已入库交易日集合跳过（存在即完整） ----
    existing = _existing_trade_dates(conn, instrument_types=["stock", "fund"])
    skipped = [d for d in trade_days if d in existing]
    trade_days = [d for d in trade_days if d not in existing]
    if skipped:
        logger.info("断点续跑: 库内已有 %d 个交易日，跳过（含 %s ~ %s）",
                    len(skipped), skipped[0], skipped[-1])
    if not trade_days:
        logger.info("回填: 目标区间已全部入库，无待回填交易日")
        return summary

    # ---- 逐交易日（升序） ----
    total = len(trade_days)
    for i, d in enumerate(trade_days, 1):
        ok = _run_day_with_retry(conn, provider, d, stock_codes, fund_codes)
        if ok:
            summary["days_done"] += 1
        else:
            summary["failed_days"].append(d)
            _append_failure(d)
        logger.info("回填进度: %d/%d 交易日", i, total)
    return summary


def _run_retry_missing(conn, provider, summary) -> dict:
    """--retry-missing：忽略断点，仅按失败清单日期执行，成功则从清单移除。"""
    dates = _read_failures()
    if not dates:
        logger.info("--retry-missing: 失败清单为空")
        return summary
    remaining = []
    for d in dates:
        ok = _run_day_with_retry(conn, provider, d, [], [])
        if ok:
            summary["retry_ok"] += 1
        else:
            summary["retry_failed"] += 1
            remaining.append(d)
    _write_failures(remaining)
    summary["failed_days"] = remaining
    return summary


def _providers_from_env():
    """CLI 直跑：按 LIVEPROFIT_DATA_SOURCE 延迟 import 装配（主源, 兜底源）工厂对。"""
    from AI.dataflows.providers.cn.akshare import AKShareProvider
    from AI.dataflows.providers.cn.tushare import TushareProvider
    ds = os.getenv("LIVEPROFIT_DATA_SOURCE", "tushare").lower()
    if ds == "akshare":
        return AKShareProvider, TushareProvider
    return TushareProvider, AKShareProvider


def main():
    parser = argparse.ArgumentParser(description="market schema 全历史回填")
    parser.add_argument("--start", default="2016-01-01",
                        help="起始日期 YYYY-MM-DD，默认 2016-01-01")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"),
                        help="截止日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--retry-missing", action="store_true",
                        help="忽略断点，仅补拉失败清单中的交易日")
    parser.add_argument("--skip-concepts", action="store_true",
                        help="跳过板块体系采集（板块数据已新鲜时用，省 ~35 分钟）")
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
    from db.instrument.db import get_connection
    with get_connection() as conn:
        summary = run_backfill(conn, args.start, args.end,
                               retry_missing=args.retry_missing,
                               skip_concepts=args.skip_concepts)
    logger.info("回填结束: 指数=%s / 新入库 %d 日 / 失败 %d 日 %s",
                summary["index"], summary["days_done"],
                len(summary["failed_days"]), summary["failed_days"])
    if summary["failed_days"]:
        logger.warning("存在失败交易日，可稍后执行 "
                       "`python -m db.instrument.ingest.backfill --retry-missing` 补拉")


if __name__ == "__main__":
    main()
