"""单日全市场拉取（迁移自 store/backfill.py 的 fetch_day_frames，方案 3.4.1）。

- 单日 4 接口（daily/adj_factor/fund_daily/fund_adj）+ 6000 行截断检测降级分批补拉
- 全市场拉取禁止区间查询（代理端点 6000 行静默截断，fund_daily 区间甚至返回 0 行）
"""

import logging
import time

import pandas as pd

from AI.dataflows.providers.base_provider import (
    ProviderNetworkAccessDenied,
    raise_if_network_access_denied,
)
from db.instrument.ingest.guard import FATAL_INGEST_ERRORS

logger = logging.getLogger(__name__)

TRUNCATION_ROWS = 6000    # 单日行数 ≥ 6000 视为截断（实测单日 5547 距上限仅 ~8% 余量）
BATCH_SIZE = 100          # 分批补拉每批代码数（实测 200/批接近上限，100 为保守值）
REQUEST_INTERVAL = 0.2    # 请求间隔（秒）


class StoreFetchError(Exception):
    """单日采集失败（接口失败 / 截断无法降级 / 数据为空），整日不写库。"""


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
            raise_if_network_access_denied(provider)
        except FATAL_INGEST_ERRORS:
            raise
        except ProviderNetworkAccessDenied:
            raise
        except Exception as e:
            logger.warning("分批补拉 %s/%s 第 %d 批异常: %s",
                           market, kind, i // BATCH_SIZE, e)
            return None
        if df is None:
            logger.warning("分批补拉 %s/%s 第 %d 批超时/失败",
                           market, kind, i // BATCH_SIZE)
            return None
        if len(df) >= TRUNCATION_ROWS:
            logger.warning("分批补拉仍疑似截断: %s/%s", market, kind)
            return None
        if market == "stock" and not df.empty:
            if "ts_code" not in df.columns or not set(df["ts_code"].astype(str)) <= set(batch):
                logger.warning("分批补拉返回了请求批次以外的代码: %s/%s", market, kind)
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
    raise_if_network_access_denied(provider)
    if df is None:
        return None
    if len(df) >= TRUNCATION_ROWS:
        logger.warning(
            "%s/%s %s 单日 %d 行 ≥ 截断阈值 %d，降级分批补拉",
            trade_date, market, kind, len(df), TRUNCATION_ROWS)
        if not codes:
            logger.error("%s/%s 截断但无代码列表可分批补拉，整日失败",
                         market, kind)
            return None
        return _batched_pull(provider, market, kind, codes, trade_date)
    return df


def fetch_stock_daily_frame(provider, trade_date: str, codes: list[str]) -> pd.DataFrame:
    """Stock-only single-day pull, with bounded 100-code truncation fallback."""
    df = _pull_market_frame(provider, trade_date, "stock", "daily", codes)
    if df is None or df.empty:
        raise StoreFetchError("UPSTREAM_NOT_READY")
    required = ["ts_code", "trade_date", "open", "high", "low", "close", "pct_chg"]
    if any(c not in df.columns for c in required):
        raise StoreFetchError("UPSTREAM_COLUMNS_MISSING")
    frame = df.copy()
    days = pd.to_datetime(frame["trade_date"].astype(str), errors="coerce")
    if days.isna().any() or not days.dt.date.eq(pd.Timestamp(trade_date).date()).all():
        raise StoreFetchError("UPSTREAM_DATE_MISMATCH")
    if frame.duplicated(["ts_code", "trade_date"]).any():
        raise StoreFetchError("UPSTREAM_DUPLICATE_KEYS")
    frame = frame[frame["ts_code"].isin(codes)].copy()
    values = ["open", "high", "low", "close", "pct_chg"]
    frame[values] = frame[values].apply(pd.to_numeric, errors="coerce")
    frame[values] = frame[values].replace(
        [float("inf"), -float("inf")], None)
    frame = frame.dropna(subset=values)
    if frame.empty:
        raise StoreFetchError("UPSTREAM_NOT_READY")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"].astype(str)).dt.strftime("%Y-%m-%d")
    return frame


def fetch_stock_technical_factor_frame(provider, trade_date: str,
                                      codes: list[str]) -> pd.DataFrame:
    """Fetch one day's qfq factors, retrying a truncated full-market result by code.

    The technical-factor endpoint is ``stk_factor_pro``; it must not share the
    generic factor fallback, which calls the separate ``adj_factor`` endpoint.
    """
    time.sleep(REQUEST_INTERVAL)
    frame = provider.get_full_market_technical_factor_df(trade_date)
    raise_if_network_access_denied(provider)
    if frame is None:
        raise StoreFetchError("UPSTREAM_NOT_READY")
    if len(frame) >= TRUNCATION_ROWS:
        batches = []
        for i in range(0, len(codes), BATCH_SIZE):
            batch = codes[i:i + BATCH_SIZE]
            time.sleep(REQUEST_INTERVAL)
            part = provider.get_full_market_technical_factor_df(trade_date, ts_codes=batch)
            raise_if_network_access_denied(provider)
            if part is None or len(part) >= TRUNCATION_ROWS:
                raise StoreFetchError("UPSTREAM_INCOMPLETE")
            if not part.empty:
                if "ts_code" not in part.columns or not set(part["ts_code"].astype(str)) <= set(batch):
                    raise StoreFetchError("UPSTREAM_CODE_MISMATCH")
                batches.append(part)
        frame = pd.concat(batches, ignore_index=True) if batches else pd.DataFrame()
    if frame.empty:
        raise StoreFetchError("UPSTREAM_NOT_READY")
    return frame


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
