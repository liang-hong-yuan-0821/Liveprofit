"""
YoHo 技术指标计算 (简化版)
使用 tushare 数据 + stockstats 计算技术指标，不再依赖 yfinance。
"""

import logging
from datetime import datetime

import pandas as pd
from stockstats import wrap

from AI.dataflows.interface import get_china_stock_data

logger = logging.getLogger(__name__)


class StockstatsUtils:
    """技术指标计算工具"""

    @staticmethod
    def get_stock_stats(
        symbol: str,
        indicator: str,
        curr_date: str,
        lookback_days: int = 365,
        online: bool = True,
    ):
        """
        计算指定股票的技术指标

        Args:
            symbol: 股票代码 (如 000001.SZ)
            indicator: 指标名称 (如 "close_50_sma", "rsi_14", "macd")
            curr_date: 当前日期 YYYY-mm-dd
            lookback_days: 回看天数，默认365天
            online: 保留参数（始终在线获取）

        Returns:
            指标值 或 "N/A"
        """
        curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
        start_dt = curr_dt - pd.DateOffset(days=lookback_days)
        start_date = start_dt.strftime("%Y-%m-%d")

        # 从 Tushare 获取行情数据
        raw_text = get_china_stock_data(symbol, start_date, curr_date)
        if not raw_text or raw_text.startswith("Tushare") or raw_text.startswith("获取"):
            logger.warning(f"无法获取 {symbol} 的行情数据用于技术指标计算")
            return "N/A: 无法获取行情数据"

        # tushare 输出格式解析
        # 构建 OHLCV DataFrame
        rows = []
        for line in raw_text.strip().split("\n"):
            parts = line.strip().split()
            if len(parts) < 7:
                continue
            try:
                rows.append(
                    {
                        "date": parts[0],
                        "open": float(parts[1]),
                        "high": float(parts[2]),
                        "low": float(parts[3]),
                        "close": float(parts[4]),
                        "volume": float(parts[5]),
                    }
                )
            except (ValueError, IndexError):
                continue

        if not rows:
            logger.warning(f"解析 {symbol} 的行情数据失败")
            return "N/A: 数据解析失败"

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")

        try:
            stock = wrap(df)
            stock[indicator]  # 触发 stockstats 计算指标
            matching = stock[stock["date"].dt.strftime("%Y-%m-%d") == curr_date]
            if not matching.empty:
                return matching[indicator].values[0]
            return "N/A: 非交易日 (周末或节假日)"
        except Exception as e:
            logger.warning(f"计算技术指标失败: {e}")
            return f"N/A: 计算失败 ({e})"

    @staticmethod
    def get_indicators_report(
        symbol: str,
        curr_date: str,
        lookback_days: int = 365,
    ) -> str:
        """
        生成综合技术指标报告

        Args:
            symbol: 股票代码
            curr_date: 当前日期
            lookback_days: 回看天数

        Returns:
            格式化的技术指标报告文本
        """
        indicators = [
            "close_5_sma", "close_10_sma", "close_20_sma", "close_50_sma",
            "close_200_sma",
            "rsi_6", "rsi_14", "rsi_28",
            "macd", "macds", "macdh",
            "boll", "boll_ub", "boll_lb",
            "volume_delta",
        ]

        results = []
        for ind in indicators:
            val = StockstatsUtils.get_stock_stats(
                symbol, ind, curr_date, lookback_days
            )
            results.append(f"  {ind}: {val}")

        header = f"技术指标报告 - {symbol} @ {curr_date}\n" + "=" * 50
        return header + "\n" + "\n".join(results)
