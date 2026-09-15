"""统一每日增量入口（证券市场数据库统一方案 3.4.1）。

collect_incremental(conn, provider_factory, fallback_provider_factory=None)：
1. trade_cal 取最近 3 个交易日
2. instrument + 信息表刷新：stock_basic/fund_basic → instrument 通用列 +
   stock_info / fund_info（exchange/market/area 原文直迁、fund_info 21 差异列）
3. 指数日线+因子：INDEX_TARGETS 9 个（前端写死清单 7 CN ∪ TARGET_ASSETS 四指数
   去重）——前置自举 upsert_instrument；主源失败/空数据换兜底源重试
   （双源兜底，迁自 market_data_collector 的 Tushare↔AKShare 互换逻辑）；
   source 写实际成功源
4. 个股基金日线+复权：fetch_day_frames 单日 4 接口 + DO UPDATE（覆盖日终修正）
5. 板块日线增量：collect_sector_daily_incremental 每日执行（dc_daily 窗口型
   数据源，无历史回填，自启动日起积累；约 15–25 分钟/日）
6. 板块体系周刷（周一自动/显式开关）

每步独立 commit（单日提交、来源独立提交、失败 rollback 不丢成果）。

两份硬编码清单的同步不变式：前端 MARKET_INDEX_CATALOG 是 11 个（7 CN + US 3 +
KS11 均 AVAILABLE）、INDEX_TARGETS 是 13 个（CN 9 + US 3 + KS11——CN 7 与前端
清单的 CN 子集必须一致 + 000300.SH/000698.SH 仅采集不上平台）——变更 CN 项时
两处同步；US/KR 项变更需实测验收 + 两处同步（2026-09-14 US/KR 上线：
KOSDAQ 无可用源不采集、前端已移除）。采集目标守卫：CN 格式或
NON_CN_INDEX_TARGETS 白名单（实测验收通过），未知非 CN 目标拒绝。
"""

import logging
from datetime import datetime, timedelta

import pandas as pd

from db.instrument.dao import fund_info, stock_info
from db.instrument.dao import instrument as instrument_dao
from db.instrument.dao.instrument_daily import bulk_upsert_daily, latest_trade_date
from db.instrument.dao.adj_factor import bulk_upsert_factor
from db.instrument.dao.factor_daily import bulk_upsert_factor_daily
from db.instrument.ingest.frames import fetch_day_frames
from db.instrument.ingest.sector_daily import collect_sector_daily_incremental
from db.instrument.ingest.sectors import collect_sectors

logger = logging.getLogger(__name__)

_WINDOW_DAYS = 3       # 增量窗口：最近 3 个交易日（覆盖日终修正）
_CAL_BACK_DAYS = 30    # 交易日历回溯窗口（周末+节假日兜底）

# 采集目标 9 个（决策 13 前置改造后：前端写死清单 CN 7 ∪ TARGET_ASSETS 四指数去重）。
# 名称口径：CN 7 与前端 MARKET_INDEX_CATALOG 同串（000001.SH=上证综指，平台口径）；
# 000300.SH/000698.SH 用 TARGET_ASSETS 名称。自举 upsert 的 DO UPDATE 会按此
# 名称刷新 instrument 行——两清单重叠行以平台口径为准。
INDEX_TARGETS = {
    "000001.SH": "上证综指",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
    "000016.SH": "上证50",
    "000852.SH": "中证1000",
    "000015.SH": "上证红利",
    "000300.SH": "沪深300",
    "000698.SH": "科创100",
    # 2026-09-14 US/KR 上线（实测验收：tushare index_global 主源 +
    # 新浪兜底；KOSDAQ 无可用源不采集）——必须保持在末尾（回填测试首帧断言依赖 dict 序）
    ".INX": "标普500",
    ".DJI": "道琼斯工业指数",
    ".IXIC": "纳斯达克综合指数",
    "KS11": "韩国综合指数",
}

# 实测验收通过的非 CN 采集目标白名单（2026-09-14 US/KR 上线；KOSDAQ 无可用源不入列）
NON_CN_INDEX_TARGETS = {".INX", ".DJI", ".IXIC", "KS11"}


def _is_cn_index_code(code: str) -> bool:
    """CN 指数代码格式判定（.SH/.SZ/.BJ 后缀）。"""
    return code.endswith((".SH", ".SZ", ".BJ"))


# 指数日线合并 mapper 列集（tushare 扩列后 10 列 + source/updated_at 采集层填）
_INDEX_DAILY_COLS = ["ts_code", "trade_date", "open", "high", "low", "close",
                     "pre_close", "change", "pct_chg", "vol", "amount"]


def _provider_source(provider) -> str:
    """provider 实例 → source 标签（base_provider.name: "Tushare"/"AKShare"）。

    身份启发式（fallback is not provider）会写错值：akshare 主源下兜底成
    功会写成 'tushare' 的反面（CR M1）。"""
    name = getattr(provider, "name", None)
    return name.lower() if isinstance(name, str) else "tushare"


def _assert_index_targets_valid() -> None:
    """采集目标守卫（原 CN-only，2026-09-14 US/KR 上线放宽）：
    INDEX_TARGETS 仅允许 CN 格式代码或 NON_CN_INDEX_TARGETS 白名单——
    未知非 CN 目标仍 ValueError（保留防御）。"""
    for code in INDEX_TARGETS:
        if _is_cn_index_code(code) or code in NON_CN_INDEX_TARGETS:
            continue
        raise ValueError(
            f"INDEX_TARGETS 含未认可目标：{code}（仅 CN 格式或 NON_CN_INDEX_TARGETS 白名单）")


def _last_trade_days(provider, n: int = _WINDOW_DAYS) -> list:
    """最近 n 个交易日（YYYYMMDD 升序）；交易日历不可用返回 []。"""
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=_CAL_BACK_DAYS)).strftime("%Y-%m-%d")
    try:
        cal = provider.get_trade_cal(start, end)
    except Exception as e:
        logger.warning("增量: 交易日历拉取异常: %s", e)
        return []
    if cal is None or cal.empty:
        logger.warning("增量: 交易日历不可用，跳过")
        return []
    days = sorted(pd.to_datetime(cal["trade_date"]).dt.strftime("%Y%m%d").unique())
    return days[-n:]


def _refresh_basics(conn, provider) -> tuple:
    """基本信息刷新（步骤 2）：stock_basic/fund_basic 拆两路写入。

    返回 (stock_codes, fund_codes)（截断降级代码列表）；失败不阻断后续步骤。
    """
    codes = {}
    for label, fetch_fn in (("stock", provider.get_stock_basic_df),
                            ("fund", provider.get_fund_basic_df)):
        try:
            df = fetch_fn()
            if df is None or df.empty:
                logger.warning("增量: %s 基本信息不可用（None/空）", label)
                continue
            if label == "stock":
                instrument_dao.upsert_instrument(
                    conn, _basic_to_instrument(df, "stock"))
                stock_info.upsert_stock_info(
                    conn, df[["ts_code", "exchange", "market", "area"]])
            else:
                instrument_dao.upsert_instrument(
                    conn, _basic_to_instrument(df, "fund"))
                fund_info.upsert_fund_info(conn, df)
            codes[label] = df["ts_code"].astype(str).tolist()
        except Exception as e:
            logger.warning("增量: %s 基本信息刷新失败（不阻断）: %s", label, e)
    return codes.get("stock", []), codes.get("fund", [])


def _basic_to_instrument(df: pd.DataFrame, itype: str) -> pd.DataFrame:
    """stock_basic/fund_basic → instrument 通用列（fund 无 list_status → 恒 NULL）。"""
    out = pd.DataFrame({
        "ts_code": df["ts_code"],
        "name": df["name"],
        "instrument_type": itype,
        "list_date": df["list_date"] if "list_date" in df.columns else None,
        "delist_date": df["delist_date"] if "delist_date" in df.columns else None,
        "data_source": "tushare",
    })
    if itype == "stock":
        out["list_status"] = df["list_status"]
    else:
        out["list_status"] = None
    return out


def _bootstrap_instruments(conn) -> None:
    """指数 instrument 行自举：按 INDEX_TARGETS 写/刷新（非迁移库无步骤 7 行来源，
    不自举则回填完成后 get_bars 存在性校验仍 404）。名称口径 = INDEX_TARGETS
    常量（CN 7 平台口径优先，DO UPDATE 按 EXCLUDED.name 覆盖）。"""
    df = pd.DataFrame({
        "ts_code": list(INDEX_TARGETS.keys()),
        "name": list(INDEX_TARGETS.values()),
        "instrument_type": "index",
        "list_status": None,
        "list_date": None,
        "delist_date": None,
        "data_source": "tushare",
    })
    instrument_dao.upsert_instrument(conn, df, update=True)


def _fetch_index_daily(provider, code: str, start: str, end: str):
    """单指数区间日线（双源兜底在调用方）；返回 DataFrame 或 None。

    帧按 _INDEX_DAILY_COLS 归一：缺列置 None（tushare 扩列后 10 列；
    AKShare 兜底行 pre_close/change/pct_chg 恒 NULL 属事实）。
    """
    df = provider.get_index_data_df(code, start, end)
    if df is None or df.empty:
        return None
    out = pd.DataFrame({
        c: df[c] if c in df.columns else None for c in _INDEX_DAILY_COLS
    }, dtype=object)
    out["ts_code"] = code
    return out


def _ingest_index_bars_and_factors(conn, provider, fallback_provider,
                                   days: list) -> dict:
    """步骤 3：指数日线+因子（自举前置 + 双源兜底 + DO UPDATE）。"""
    result = {"bars": 0, "factors": 0}
    _bootstrap_instruments(conn)
    conn.commit()   # 自举独立提交（后续采集失败回滚不丢目录行）

    start, end = days[0], days[-1]
    for code in INDEX_TARGETS:
        source_used = None
        try:
            bars = _fetch_index_daily(provider, code, start, end)
        except Exception as e:
            logger.warning("增量: 指数 %s 主源拉取异常（换兜底源重试）: %s", code, e)
            bars = None
        if bars is None and fallback_provider is not None and fallback_provider is not provider:
            logger.warning("增量: 指数 %s 主源无数据，换兜底源重试", code)
            try:
                bars = _fetch_index_daily(fallback_provider, code, start, end)
            except Exception as e:
                # 与 backfill 对称：兜底源网络异常只跳过 bars，不吞掉整个步骤 3
                logger.warning("增量: 指数 %s 兜底源拉取异常（跳过 bars）: %s", code, e)
                bars = None
            if bars is not None:
                source_used = _provider_source(fallback_provider)
        if bars is None:
            next_step = "因子继续" if _is_cn_index_code(code) else "无因子"
            logger.warning("增量: 指数 %s 双源均无日线，跳过 bars（%s）", code, next_step)
        else:
            if source_used is None:
                source_used = _provider_source(provider)
            bars = bars.copy()
            bars["source"] = source_used
            bars["updated_at"] = pd.Timestamp.now()
            try:
                n = bulk_upsert_daily(conn, bars, update=True)  # 指数行 DO UPDATE（决策 5 例外）
                result["bars"] += n
            except Exception as e:
                logger.warning("增量: 指数 %s 日线写入失败（bars 跳过、因子继续）: %s",
                               code, e)
                try:
                    conn.rollback()
                except Exception:
                    pass
        # 因子（仅主源 CN——idx_factor_pro 为 tushare 端点；US/KR 无因子源跳过）
        if not _is_cn_index_code(code):
            continue
        try:
            factor_df = provider.get_index_factor_df(code, start, end)
        except Exception as e:
            logger.warning("增量: 指数 %s 因子拉取异常（跳过）: %s", code, e)
            factor_df = None
        if factor_df is None or factor_df.empty:
            logger.warning("增量: 指数 %s 因子不可用（None/空），跳过", code)
            continue
        missing = getattr(factor_df, "attrs", {}).get("missing_chunks")
        if missing:
            # 缺段拒绝部分入库（因子窗口语义：缺段行内因子失真）
            logger.warning("增量: 指数 %s 因子缺段 %s，拒绝部分入库", code, missing)
            continue
        factor_df = factor_df.copy()
        factor_df["ts_code"] = code  # get_index_factor_df 帧无 ts_code 列（按名读取）
        factor_df["updated_at"] = pd.Timestamp.now()
        try:
            n = bulk_upsert_factor_daily(conn, factor_df, update=True)
            result["factors"] += n
        except Exception as e:
            logger.warning("增量: 指数 %s 因子写入失败（跳过）: %s", code, e)
            try:
                conn.rollback()
            except Exception:
                pass
    conn.commit()
    return result


def _ingest_stock_fund_daily(conn, provider, days, stock_codes, fund_codes) -> dict:
    """步骤 4：个股基金日线+复权逐日循环（DO UPDATE，单日提交）。"""
    result = {}
    for d in days:
        try:
            frames = fetch_day_frames(provider, d, stock_codes, fund_codes)
        except Exception as e:
            logger.warning("增量: %s 拉取失败（跳过该日，不阻断其他日）: %s", d, e)
            continue
        frames["daily"] = frames["daily"].copy()
        frames["daily"]["source"] = _provider_source(provider)
        frames["daily"]["updated_at"] = pd.Timestamp.now()
        try:
            n_daily = bulk_upsert_daily(conn, frames["daily"], update=True)
            n_factor = bulk_upsert_factor(conn, frames["factor"], update=True)
            conn.commit()
            result[d] = {"daily": n_daily, "factor": n_factor}
            logger.info("增量: %s 完成 daily=%d factor=%d", d, n_daily, n_factor)
        except Exception as e:
            logger.warning("增量: %s 写入失败（跳过该日）: %s", d, e)
            try:
                conn.rollback()
            except Exception:
                pass
    return result


def collect_incremental(conn, provider_factory, fallback_provider_factory=None,
                        refresh_sectors: bool = None) -> dict:
    """每日增量主流程（provider 工厂注入；CLI 入口按 LIVEPROFIT_DATA_SOURCE 实例化）。"""
    _assert_index_targets_valid()
    provider = provider_factory()
    fallback = fallback_provider_factory() if fallback_provider_factory else None
    days = _last_trade_days(provider)
    if not days:
        return {"error": "交易日历不可用"}

    summary = {}

    # ---- 步骤 2：instrument + 信息表刷新（提前且独立提交） ----
    stock_codes, fund_codes = _refresh_basics(conn, provider)
    try:
        conn.commit()
    except Exception as e:
        logger.warning("增量: 基本信息提交失败（回滚后继续）: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass

    # ---- 步骤 3：指数日线+因子（独立于个股基金窗口覆盖判定） ----
    try:
        summary["index"] = _ingest_index_bars_and_factors(
            conn, provider, fallback, days)
    except Exception as e:
        logger.warning("增量: 指数采集失败（不阻断）: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass

    # ---- 步骤 4：个股基金日线+复权（窗口覆盖判定只作用于本步骤；
    # 门控按数据域判定——过滤 instrument_type IN ('stock','fund')，否则
    # 步骤 3 先写入的指数行抬高全局 max 后本步骤被静默跳过，CR B1） ----
    summary["daily"] = {}
    latest = latest_trade_date(conn, instrument_types=("stock", "fund"))
    window_covered = False
    if latest is not None:
        latest_dt = latest if hasattr(latest, "strftime") else pd.Timestamp(latest)
        if pd.Timestamp(latest_dt) >= pd.Timestamp(days[-1]):
            window_covered = True
            logger.info("增量: 库内个股基金最新 %s 已覆盖目标窗口（最近交易日 %s），"
                        "跳过日线窗口", pd.Timestamp(latest_dt).date(), days[-1])
    if not window_covered:
        summary["daily"] = _ingest_stock_fund_daily(
            conn, provider, days, stock_codes, fund_codes)

    # ---- 步骤 5：板块日线增量（每日常规、非周一限定——板块概念Treemap方案 3.1；
    # dc_daily 窗口型数据源无历史回填，历史自启动日起每日 +1 行积累；
    # 耗时约 15–25 分钟/日：1031 板块 × (0.6s 请求 + 0.2s 间隔)，与周刷同量级） ----
    try:
        summary["sector_daily"] = collect_sector_daily_incremental(conn, provider)
    except Exception as e:
        logger.warning("增量: 板块日线采集失败（不阻断）: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass

    # ---- 步骤 6：板块体系周刷（周一自动/显式开关） ----
    do_sectors = (datetime.now().weekday() == 0) if refresh_sectors is None \
        else bool(refresh_sectors)
    if do_sectors:
        try:
            sectors_result = collect_sectors(conn, provider)
            conn.commit()
            logger.info("增量: 板块体系周刷完成: ths=%s dc=%s",
                        sectors_result.get("ths", {}).get("error", "ok"),
                        sectors_result.get("dc", {}).get("error", "ok"))
            summary["sectors"] = sectors_result
        except Exception as e:
            logger.warning("增量: 板块体系周刷失败（不阻断）: %s", e)
            try:
                conn.rollback()
            except Exception:
                pass
    return summary


# ==================== CLI ====================

def _providers_from_env():
    """按 LIVEPROFIT_DATA_SOURCE 延迟 import 装配（主源, 兜底源）工厂对
    （CLI 直跑路径；双源兜底生产接线，CR M2）。"""
    import os
    from AI.dataflows.providers.cn.akshare import AKShareProvider
    from AI.dataflows.providers.cn.tushare import TushareProvider
    ds = os.getenv("LIVEPROFIT_DATA_SOURCE", "tushare").lower()
    if ds == "akshare":
        return AKShareProvider, TushareProvider
    return TushareProvider, AKShareProvider


def main():
    from db.instrument.db import get_connection
    provider_factory, fallback_factory = _providers_from_env()
    with get_connection() as conn:
        summary = collect_incremental(conn, provider_factory, fallback_factory)
    logger.info("增量完成: %s", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
