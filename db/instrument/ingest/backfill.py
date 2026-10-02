"""全历史回填（证券市场数据库统一方案 3.5 + store 回填路径迁移）。

用法：
  python -m db.instrument.ingest.backfill [--start 2016-01-01] [--end 今天]
                                           [--retry-missing] [--skip-concepts]

设计要点（store 回填经验逐条沿用）：
- 指数全历史为内置分项（INDEX_TARGETS 15 个：CN 11 + US 3 + KS11，2026-09-14
  US/KR 上线）：get_index_data_df 全历史 →
  instrument_daily DO UPDATE（补齐迁移行缺失的 pre_close/change/pct_chg 三列，
  store 决策 5 的显式例外）；get_index_factor_df → factor_daily DO UPDATE
  （close 按上游覆盖全历史段）；instrument 行自举前置
- 个股基金日线回填：单日 4 接口全部成功后单日提交；失败重试 3 次（间隔 5s）
  后跳过记失败清单 var/logs/stock_backfill_failures.json；DO NOTHING（决策 5）
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
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from AI.dataflows.providers.base_provider import (
    ProviderNetworkAccessDenied,
    raise_if_network_access_denied,
)
from db.instrument.ingest.guard import ALL_RESOURCES, FATAL_INGEST_ERRORS, locked_ingestion, guarded_provider

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
from db.instrument.ingest.stock_factors import REQUIRED_QFQ, collect_stock_quant_day

logger = logging.getLogger(__name__)

# 仓库根（db/instrument/ingest/ 三级子目录）；脚本路径按仓库根解析、与 CWD 无关
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

RETRY_COUNT = 3           # 单日失败重试次数
RETRY_INTERVAL = 5.0      # 重试间隔（秒）
FAILURE_LIST_PATH = _PROJECT_ROOT / "var" / "logs" / "stock_backfill_failures.json"
PROGRESS_LOG_PATH = _PROJECT_ROOT / "var" / "logs" / "stock_backfill.log"

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
                           fallback_provider=None, *, codes=None) -> dict:
    """指数全历史回填内置分项（自举前置 + DO UPDATE + 5 年分段 + 断点续跑）。

    断点续跑按库内已入库交易日集合跳过（同个股基金回填口径：存在即完整）；
    双源兜底：主源段拉取失败/空时换 fallback_provider 重试（CR BLOCKER 2）。
    """
    result = {"bars": 0, "factors": 0}
    _assert_index_targets_valid()
    selected = list(INDEX_TARGETS if codes is None else codes)
    _bootstrap_instruments(conn, selected)
    conn.commit()

    for code in selected:
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
                raise_if_network_access_denied(provider)
            except FATAL_INGEST_ERRORS:
                raise
            except ProviderNetworkAccessDenied:
                raise
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
                        raise_if_network_access_denied(fallback_provider)
                        bars_source = fallback_provider
                    except FATAL_INGEST_ERRORS:
                        raise
                    except ProviderNetworkAccessDenied:
                        raise
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
                    raise_if_network_access_denied(provider)
                except FATAL_INGEST_ERRORS:
                    raise
                except ProviderNetworkAccessDenied:
                    raise
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
    try:
        quant = collect_stock_quant_day(conn, provider, trade_date, stock_codes)
        logger.info("回填: %s 量化数据=%s", trade_date, quant.get("status"))
    except FATAL_INGEST_ERRORS:
        raise
    except ProviderNetworkAccessDenied:
        raise
    except Exception as exc:
        logger.warning("回填: %s 量化因子/交易状态失败（保留日线成果）: %s", trade_date, exc)
        try:
            conn.rollback()
        except FATAL_INGEST_ERRORS:
            raise
        except ProviderNetworkAccessDenied:
            raise
        except Exception:
            pass


def _run_day_with_retry(conn, provider, trade_date: str, stock_codes: list,
                        fund_codes: list) -> bool:
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            _run_day(conn, provider, trade_date, stock_codes, fund_codes)
            return True
        except FATAL_INGEST_ERRORS:
            raise
        except ProviderNetworkAccessDenied:
            raise
        except Exception as e:
            logger.warning("回填: %s 第 %d/%d 次失败: %s",
                           trade_date, attempt, RETRY_COUNT, e)
            try:
                conn.rollback()
            except FATAL_INGEST_ERRORS:
                raise
            except ProviderNetworkAccessDenied:
                raise
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
                 skip_daily: bool = False, force_existing_days: bool = False) -> dict:
    """历史回填主流程（conn 由调用方托管生命周期，与 collect_incremental 同规则）。

    fallback_provider_factory：指数分项双源兜底（CR BLOCKER 2——生产入口
    经公共装配传入）；skip_index/skip_daily 供 market_ingest wrapper 的
    --skip-bars 映射（内部参数，不暴露 CLI）。force_existing_days 用于定向修复
    已有日线但缺少复权因子的历史日期。返回汇总 dict。
    """
    summary = {"index": {}, "days_done": 0, "failed_days": [],
               "retry_ok": 0, "retry_failed": 0}
    if provider_factory is None:
        provider_factory, fallback_provider_factory = _providers_from_env()
    provider = guarded_provider(conn, provider_factory())
    _ = provider.connected
    raise_if_network_access_denied(provider)

    # ---- 板块体系（失败域分层：整体失败不阻断回填；--skip-concepts 跳过） ----
    if not skip_concepts:
        try:
            sectors_result = collect_sectors(conn, provider)
            conn.commit()
            logger.info("板块体系采集完成: ths=%s dc=%s",
                        sectors_result.get("ths", {}).get("error", "ok"),
                        sectors_result.get("dc", {}).get("error", "ok"))
        except FATAL_INGEST_ERRORS:
            raise
        except ProviderNetworkAccessDenied:
            raise
        except Exception as e:
            logger.warning("板块体系采集失败（不阻断回填）: %s", e)
            try:
                conn.rollback()
            except FATAL_INGEST_ERRORS:
                raise
            except ProviderNetworkAccessDenied:
                raise
            except Exception:
                pass

    # ---- 指数全历史内置分项 ----
    if not skip_index:
        try:
            summary["index"] = backfill_index_history(
                conn, provider, start, end,
                fallback_provider=fallback_provider_factory()
                if fallback_provider_factory else None)
        except FATAL_INGEST_ERRORS:
            raise
        except ProviderNetworkAccessDenied:
            raise
        except Exception as e:
            logger.warning("指数回填失败（不阻断个股基金回填）: %s", e)
            try:
                conn.rollback()
            except FATAL_INGEST_ERRORS:
                raise
            except ProviderNetworkAccessDenied:
                raise
            except Exception:
                pass

    if skip_daily:
        return summary

    connected = provider.connected
    raise_if_network_access_denied(provider)
    if not connected:
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
            raise_if_network_access_denied(provider)
            if df is not None and not df.empty:
                (stock_codes if label == "stock" else fund_codes).extend(
                    df["ts_code"].astype(str).tolist())
        except FATAL_INGEST_ERRORS:
            raise
        except ProviderNetworkAccessDenied:
            raise
        except Exception as e:
            logger.warning("基本信息: %s 刷新失败（截断降级将不可用）: %s", label, e)

    # ---- 交易日历 ----
    try:
        cal = provider.get_trade_cal(start, end)
        raise_if_network_access_denied(provider)
    except FATAL_INGEST_ERRORS:
        raise
    except ProviderNetworkAccessDenied:
        raise
    except Exception as e:
        logger.error("交易日历获取失败 [%s ~ %s]，终止: %s", start, end, e)
        return summary
    if cal is None or cal.empty:
        logger.error("交易日历不可用 [%s ~ %s]，终止", start, end)
        return summary
    trade_days = sorted(
        pd.to_datetime(cal["trade_date"]).dt.strftime("%Y%m%d").unique().tolist())

    # ---- 断点续跑：按库内已入库交易日集合跳过（存在即完整） ----
    existing = (set() if force_existing_days else
                _existing_trade_dates(conn, instrument_types=["stock", "fund"]))
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


# ==================== 个股因子历史回填（2026-09-22 用户拍板） ====================
# 默认窗口与本文件的指数回填完全共用同一起点、全量上市股票、pacing 0.5s/只。
# 断点判定以 instrument_daily 为真值，逐交易日
# 检查 factor_daily 是否存在同键行；不能只看 min/max，否则区间内部缺洞也会误判完成。

BACKFILL_START_DEFAULT = "2016-01-01"
STOCK_FACTOR_START_DEFAULT = BACKFILL_START_DEFAULT
STOCK_FACTOR_FAILURE_LIST_PATH = _PROJECT_ROOT / "var" / "logs" / "stock_factor_backfill_failures.json"
STOCK_FACTOR_PROGRESS_EVERY = 100
STOCK_FACTOR_MAX_CONSECUTIVE_FAILURES = 5
REQUIRED_BFQ = [
    "ma_bfq_5", "ma_bfq_10", "ma_bfq_20", "ma_bfq_60",
    "boll_mid_bfq", "boll_upper_bfq", "boll_lower_bfq",
    "macd_dif_bfq", "macd_dea_bfq", "macd_bfq",
]


def _stock_factor_codes(conn, start: str, end: str) -> list[tuple[str, str | None]]:
    """Stocks listed during the requested window, including later delistings."""
    rows = conn.execute(
        "SELECT ts_code, list_date FROM market.instrument "
        "WHERE instrument_type = 'stock' AND list_date <= %s::date "
        "AND (list_status = 'L' OR (list_status = 'D' AND delist_date >= %s::date)) "
        "ORDER BY ts_code", (end, start)
    ).fetchall()
    return [(r[0], str(r[1]) if r[1] else None) for r in rows]


def _factor_cover_snapshot(conn, start: str, end: str) -> dict[str, int | None]:
    """返回含退市股票在本地日线窗口内缺失的必需因子日期数。

    instrument_daily 是策略执行实际消费的交易日序列；按 (ts_code, trade_date)
    精确反连接 factor_daily 并检查全部必需QFQ列，既支持新股（从 max(start, list_date) 起算），也能识别
    min/max 看不出的区间内部缺洞。无本地日线时返回 None，不能误判成 0（完整）；
    主循环仍会尝试上游，满足“全量上市股票”口径。
    """
    missing_fields = " OR ".join(f"f.{column} IS NULL" for column in REQUIRED_QFQ)
    rows = conn.execute(
        "WITH target AS ("
        " SELECT ts_code, greatest(%s::date, coalesce(list_date, %s::date)) AS lower_bound "
        " FROM market.instrument WHERE instrument_type = 'stock' "
        " AND list_date <= %s::date "
        " AND (list_status = 'L' OR (list_status = 'D' "
        " AND delist_date >= %s::date))"
        ") "
        "SELECT t.ts_code, CASE WHEN count(d.trade_date) = 0 THEN NULL ELSE count(*) FILTER ("
        f" WHERE d.trade_date IS NOT NULL AND (f.ts_code IS NULL OR {missing_fields})"
        ") END AS missing_rows "
        "FROM target t "
        "LEFT JOIN market.instrument_daily d ON d.ts_code = t.ts_code "
        " AND d.trade_date BETWEEN t.lower_bound AND %s::date "
        "LEFT JOIN market.factor_daily f ON f.ts_code = d.ts_code "
        " AND f.trade_date = d.trade_date "
        "GROUP BY t.ts_code",
        (start, start, end, start, end),
    ).fetchall()
    return {r[0]: (int(r[1]) if r[1] is not None else None) for r in rows}


def _snapshot_covers(missing_rows: int | None) -> bool:
    """精确缺口快照中 0 行缺失才算已覆盖；快照无该代码按未覆盖处理。"""
    return missing_rows == 0


def _verified_stock_factor_warmup(
    conn, ts_code: str, list_date: str | None, start: str, end: str, frame: pd.DataFrame,
) -> bool:
    """仅当上市以来全部交易日的本地/上游日线齐全且ATR尚未出现时豁免。"""
    if not list_date or list_date < start or list_date > end:
        return False
    import exchange_calendars as xc

    end_day = date.fromisoformat(end)
    sessions = xc.get_calendar(
        "XSHG", start=list_date, end=(end_day + timedelta(days=1)).isoformat(),
    ).sessions
    expected = {day.date() for day in sessions if day.date() <= end_day}
    # stk_factor_pro 样本在第21根才首次给ATR20（前收盘参与首根TR）。
    if not expected or len(expected) > 20:
        return False
    source_days = pd.to_datetime(frame["trade_date"], errors="coerce")
    if source_days.isna().any() or len(source_days) != len(expected):
        return False
    if set(source_days.dt.date) != expected:
        return False
    local_days = conn.execute(
        "SELECT trade_date FROM market.instrument_daily "
        "WHERE ts_code = %s AND trade_date BETWEEN %s::date AND %s::date",
        (ts_code, list_date, end),
    ).fetchall()
    return len(local_days) == len(expected) and {row[0] for row in local_days} == expected


def _backfill_one_stock(
    conn, provider, ts_code: str, start: str, end: str, list_date: str | None = None,
) -> bool | None:
    """单票因子回填：True=入库、None=可证明暖机、False=失败。"""
    for attempt in range(RETRY_COUNT):
        try:
            df = provider.get_stock_factor_df(ts_code, start, end)
            raise_if_network_access_denied(provider)
            if df is not None and df.empty:
                return False  # 上游明确无数据：重试无意义
            if df is None:
                # provider 内部吞异常返回 None（未连接/调用失败）——按瞬态失败重试
                raise RuntimeError("上游返回 None")
            missing = [c for c in (*REQUIRED_QFQ, *REQUIRED_BFQ) if c not in df.columns]
            if missing:
                logger.warning("个股 %s 因子列缺失 %s（跳过）", ts_code, missing)
                return False
            df = df.copy()
            atr = pd.to_numeric(df["atr_qfq"], errors="coerce")
            if (df["atr_qfq"].notna() & atr.isna()).any() or (
                atr.notna().any() and (not np.isfinite(atr.dropna().to_numpy(dtype=float)).all()
                                         or (atr.dropna() <= 0).any())
            ):
                logger.warning("个股 %s ATR 含非法数值（跳过）", ts_code)
                return False
            if not atr.notna().any():
                if _verified_stock_factor_warmup(
                    conn, ts_code, list_date, start, end, df,
                ):
                    logger.info("个股 %s ATR20 暖机：上市后至多20交易日，日线证据完整", ts_code)
                    return None
                logger.warning("个股 %s ATR 全空（跳过）", ts_code)
                return False
            df["ts_code"] = ts_code
            df["updated_at"] = pd.Timestamp.now(tz="UTC")
            bulk_upsert_factor_daily(conn, df, update=True)
            conn.commit()  # get_connection 不自管 commit，缓存必须落盘
            return True
        except FATAL_INGEST_ERRORS:
            raise
        except ProviderNetworkAccessDenied:
            raise
        except Exception as e:  # 网络/入库瞬态异常：重试耗尽才记清单
            try:
                conn.rollback()
            except FATAL_INGEST_ERRORS:
                raise
            except ProviderNetworkAccessDenied:
                raise
            except Exception:
                pass
            logger.warning("个股 %s 因子回填失败（第 %d 次）: %s", ts_code, attempt + 1, e)
            if attempt < RETRY_COUNT - 1:
                time.sleep(RETRY_INTERVAL)
    return False


def _read_failed_stock_codes() -> list[str]:
    """失败清单读取（JSON 数组，坏文件按空清单处理）。"""
    if not STOCK_FACTOR_FAILURE_LIST_PATH.exists():
        return []
    try:
        data = json.loads(STOCK_FACTOR_FAILURE_LIST_PATH.read_text(encoding="utf-8"))
        return [str(c) for c in data] if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("个股因子失败清单读取失败（按空清单处理）: %s", e)
        return []


def _write_failed_stock_codes(codes: list[str]) -> None:
    """失败清单原子写（temp+rename，与指数失败清单同模式）。"""
    STOCK_FACTOR_FAILURE_LIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STOCK_FACTOR_FAILURE_LIST_PATH.with_name(
        f"{STOCK_FACTOR_FAILURE_LIST_PATH.stem}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(sorted(codes), ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(STOCK_FACTOR_FAILURE_LIST_PATH)


def run_stock_factor_backfill(
    conn, start: str, end: str, sleep_seconds: float = 0.5,
    retry_failed: bool = False, provider_factory=None, limit: int | None = None,
) -> dict:
    """个股因子历史回填主流程（conn 由调用方托管生命周期，与 run_backfill 同规则）。

    retry_failed=True 时只处理失败清单中的代码（忽略断点覆盖判定）；其余模式
    按本地日线逐日检查因子键，完整覆盖区间才跳过。返回汇总 dict。
    """
    if provider_factory is None:
        # 个股技术因子只有 Tushare stk_factor_pro 提供；不能跟随
        # LIVEPROFIT_DATA_SOURCE=akshare，否则会用基类“不支持”实现空跑全市场。
        from AI.dataflows.providers.cn.tushare import TushareProvider
        provider_factory = TushareProvider
    provider = guarded_provider(conn, provider_factory())
    connected = getattr(provider, "connected", True)
    raise_if_network_access_denied(provider)
    if connected is False:
        raise RuntimeError("Tushare 个股因子数据源未连接，已中止回填（不会逐票空跑）")
    previous_failed = set(_read_failed_stock_codes())
    if retry_failed:
        codes = sorted(previous_failed)
        snapshot: dict[str, int | None] = {}
        logger.info("个股因子回填 --retry-failed：失败清单 %d 只", len(codes))
    else:
        codes = _stock_factor_codes(conn, start, end)
        snapshot = _factor_cover_snapshot(conn, start, end)
        conn.commit()  # 释放覆盖扫描的读事务，避免首个长网络请求期间持有旧快照
        logger.info(
            "个股因子回填：全市场 %d 只，区间 [%s, %s]，本地日线缺因子 %d 行，pacing %.1fs",
            len(codes), start, end, sum(v or 0 for v in snapshot.values()), sleep_seconds,
        )
    if limit is not None:
        # A bounded run must advance to the next deficient codes. Slicing the
        # full sorted universe first replays the same covered prefix forever.
        if not retry_failed:
            codes = [entry for entry in codes
                     if not _snapshot_covers(snapshot.get(entry[0]))]
        codes = codes[:limit]

    failed: list[str] = []
    done = 0
    skipped = 0
    warmup = 0
    consecutive_failures = 0
    for i, entry in enumerate(codes, 1):
        # 常规模式 = (code, list_date) 元组；retry_failed 模式 = 裸 code 字符串
        code, _list_date = entry if isinstance(entry, tuple) else (entry, None)
        if not retry_failed and _snapshot_covers(snapshot.get(code)):
            skipped += 1
            continue
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
        outcome = _backfill_one_stock(conn, provider, code, start, end, _list_date)
        if outcome is True:
            done += 1
            consecutive_failures = 0
        elif outcome is None:
            conn.commit()  # 暖机证据的只读查询也要结束事务，后续网络请求不持旧快照
            warmup += 1
            consecutive_failures = 0
        else:
            conn.rollback()  # 校验失败可能已查询本地日期，重试前恢复干净连接
            failed.append(code)
            consecutive_failures += 1
            if consecutive_failures >= STOCK_FACTOR_MAX_CONSECUTIVE_FAILURES:
                processed_codes = {
                    item[0] if isinstance(item, tuple) else item
                    for item in codes[:i]
                }
                outstanding = sorted((previous_failed - processed_codes) | set(failed))
                _write_failed_stock_codes(outstanding)
                raise RuntimeError(
                    f"个股因子连续 {consecutive_failures} 只失败，疑似上游故障或限流；"
                    "已保存失败清单并中止，请检查数据源后重跑"
                )
        if i % STOCK_FACTOR_PROGRESS_EVERY == 0:
            logger.info("个股因子回填进度 %d/%d（完成 %d 跳过 %d 失败 %d）",
                        i, len(codes), done, skipped, len(failed))

    processed_codes = {
        entry[0] if isinstance(entry, tuple) else entry
        for entry in codes
    }
    outstanding_failed = sorted((previous_failed - processed_codes) | set(failed))
    _write_failed_stock_codes(outstanding_failed)
    return {"total": len(codes), "done": done, "skipped": skipped, "warmup": warmup,
            "failed": len(outstanding_failed), "failed_codes": outstanding_failed}



# Public entrypoints acquire once; nested collectors reuse the guarded connection.
_backfill_index_history_unlocked = backfill_index_history
backfill_index_history = locked_ingestion("CN_INDEX_BARS", "CN_INDEX_FACTORS", "US_INDEX_BARS", "KR_INDEX_BARS")(_backfill_index_history_unlocked)
_run_backfill_unlocked = run_backfill
run_backfill = locked_ingestion(*ALL_RESOURCES)(_run_backfill_unlocked)
_run_stock_factor_backfill_unlocked = run_stock_factor_backfill
run_stock_factor_backfill = locked_ingestion("CN_STOCK_DAILY")(_run_stock_factor_backfill_unlocked)


def _providers_from_env():
    """CLI 直跑：按 LIVEPROFIT_DATA_SOURCE 延迟 import 装配（主源, 兜底源）工厂对。"""
    from AI.dataflows.providers.cn.akshare import AKShareProvider
    from AI.dataflows.providers.cn.tushare import TushareProvider
    ds = os.getenv("LIVEPROFIT_DATA_SOURCE", "tushare").lower()
    if ds == "akshare":
        return AKShareProvider, TushareProvider
    return TushareProvider, AKShareProvider


def main():
    from db.instrument.ingest.guard import IngestGuard
    from db.instrument.ingest.notifications import market_changed_notifier_from_env
    parser = argparse.ArgumentParser(description="market schema 全历史回填")
    parser.add_argument("--start", default=None,
                        help="起始日期 YYYY-MM-DD（指数和个股因子模式默认均为 2016-01-01）")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"),
                        help="截止日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--retry-missing", action="store_true",
                        help="忽略断点，仅补拉失败清单中的交易日")
    parser.add_argument("--skip-concepts", action="store_true",
                        help="跳过板块体系采集（板块数据已新鲜时用，省 ~35 分钟）")
    parser.add_argument("--stock-factors", action="store_true",
                        help="个股因子历史回填（与 --retry-missing/--skip-concepts 互斥）")
    parser.add_argument("--sleep", type=float, default=0.5,
                        help="个股因子回填每票 pacing 秒数，默认 0.5")
    parser.add_argument("--retry-failed", action="store_true",
                        help="个股因子回填：忽略断点，仅补拉失败清单中的代码")
    parser.add_argument("--limit", type=int,
                        help="个股因子回填最多处理前 N 只（小样本验收用；默认全量）")
    args = parser.parse_args()

    (_PROJECT_ROOT / "var" / "logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(PROGRESS_LOG_PATH, encoding="utf-8"),
        ],
    )

    if args.stock_factors:
        if args.retry_missing or args.skip_concepts:
            parser.error("--stock-factors 与 --retry-missing/--skip-concepts 互斥")
        if args.sleep < 0:
            parser.error("--sleep 不能为负数")
        if args.limit is not None and args.limit <= 0:
            parser.error("--limit 必须为正整数")
        from db.instrument.db import get_connection
        start = args.start or STOCK_FACTOR_START_DEFAULT
        try:
            start_date = datetime.strptime(start, "%Y-%m-%d").date()
            end_date = datetime.strptime(args.end, "%Y-%m-%d").date()
        except ValueError:
            parser.error("--start/--end 必须为 YYYY-MM-DD")
        if start_date > end_date:
            parser.error("--start 不能晚于 --end")
        with get_connection() as conn, IngestGuard(conn, changed=market_changed_notifier_from_env()) as guard:
            summary = run_stock_factor_backfill(
                conn, start, args.end,
                sleep_seconds=args.sleep,
                retry_failed=args.retry_failed,
                limit=args.limit,
                guard=guard,
            )
        logger.info("个股因子回填结束: 总数 %d / 完成 %d / 跳过 %d / 暖机 %d / 失败 %d",
                    summary["total"], summary["done"], summary["skipped"],
                    summary["warmup"], summary["failed"])
        if summary["failed_codes"]:
            logger.warning(
                "存在失败代码，可稍后用相同窗口执行 "
                "`python -m db.instrument.ingest.backfill --stock-factors --retry-failed "
                "--start %s --end %s` 补拉", start, args.end,
            )
            raise SystemExit(1)
        return

    if args.retry_failed or args.limit is not None or args.sleep != 0.5:
        parser.error("--retry-failed/--limit/--sleep 仅可与 --stock-factors 一起使用")

    start = args.start or BACKFILL_START_DEFAULT
    from db.instrument.db import get_connection
    with get_connection() as conn, IngestGuard(conn, changed=market_changed_notifier_from_env()) as guard:
        summary = run_backfill(conn, start, args.end,
                               retry_missing=args.retry_missing,
                               skip_concepts=args.skip_concepts, guard=guard)
    logger.info("回填结束: 指数=%s / 新入库 %d 日 / 失败 %d 日 %s",
                summary["index"], summary["days_done"],
                len(summary["failed_days"]), summary["failed_days"])
    if summary["failed_days"]:
        logger.warning("存在失败交易日，可稍后执行 "
                       "`python -m db.instrument.ingest.backfill --retry-missing` 补拉")


if __name__ == "__main__":
    main()
