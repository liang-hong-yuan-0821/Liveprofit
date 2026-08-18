"""
行情采集模块（方案 3.1.1）

经 Provider 层（BaseStockDataProvider.get_index_data_df）拉取指数日线，
collector 仅负责持久化到 market_data 表，不直连任何数据源 SDK。
"""

import logging
from datetime import datetime, timedelta

import pandas as pd

from AI.eventStudy.collectors.config import TARGET_ASSETS, get_provider
from AI.eventStudy.db import market_data_dao

logger = logging.getLogger(__name__)


def fetch_market_data(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    """经 Provider 层获取日线行情（3.1.1 接口）。

    返回标准列：trade_date / open / high / low / close / vol / amount。
    数据源不支持或获取失败时返回空 DataFrame。
    """
    prov = get_provider()
    if not hasattr(prov, "get_index_data_df"):
        logger.warning(f"当前数据源不支持结构化指数行情接口（{type(prov).__name__}）")
        return pd.DataFrame()
    try:
        df = prov.get_index_data_df(ticker, start_date, end_date)
    except Exception as e:
        logger.error(f"行情获取失败 [{ticker}]: {e}")
        return pd.DataFrame()
    if df is None or df.empty:
        # 当前数据源不支持/无数据时，尝试切换另一数据源兜底
        return _fallback_fetch(ticker, start_date, end_date)
    return df


def _fallback_fetch(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    """当前数据源失败时尝试另一数据源（Tushare ↔ AKShare 互换）。"""
    import os
    current = os.getenv("LIVEPROFIT_DATA_SOURCE", "tushare").lower()
    other = "akshare" if current != "akshare" else "tushare"
    try:
        if other == "akshare":
            from AI.dataflows.providers.akshare_provider import AKShareProvider
            prov = AKShareProvider()
        else:
            from AI.dataflows.providers.tushare_provider import TushareProvider
            prov = TushareProvider()
        if not hasattr(prov, "get_index_data_df"):
            return pd.DataFrame()
        df = prov.get_index_data_df(ticker, start_date, end_date)
        return df if df is not None else pd.DataFrame()
    except Exception as e:
        logger.warning(f"备用数据源行情获取失败 [{ticker}]: {e}")
        return pd.DataFrame()


def collect_index_market_data(
    conn,
    asset_tickers: list = None,
    start_date: str = None,
    end_date: str = None,
) -> dict:
    """采集全部目标指数日线并持久化到 market_data 表。

    Args:
        conn: PG 连接
        asset_tickers: 目标指数列表，默认全部 4 个（config.TARGET_ASSETS）
        start_date / end_date: YYYY-MM-DD，默认过去 2 年（覆盖 120 日估计窗口）

    Returns:
        {ticker: 写入行数}
    """
    if asset_tickers is None:
        asset_tickers = list(TARGET_ASSETS.keys())
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")
    if start_date is None:
        start_date = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")

    result = {}
    for ticker in asset_tickers:
        asset_id = market_data_dao.get_asset_id(conn, ticker)
        if asset_id is None:
            logger.error(f"资产未初始化: {ticker}（请先执行 init_schema）")
            result[ticker] = -1
            continue
        df = fetch_market_data(ticker, start_date, end_date)
        if df.empty:
            logger.warning(f"[行情采集] {ticker} 未获取到数据")
            result[ticker] = 0
            continue
        rows = market_data_dao.insert_market_data(conn, asset_id, df)
        result[ticker] = rows
        logger.info(f"[行情采集] {ticker}: 处理 {len(df)} 条，写入 {rows} 条")
    conn.commit()
    return result
