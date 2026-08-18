"""
LiveProfit 股票工具函数 (简化版)
面向 A 股市场，保留基础的市场识别功能。
"""

import re
from typing import Dict, Tuple
from enum import Enum


class StockMarket(Enum):
    """股票市场枚举"""
    CHINA_A = "china_a"
    UNKNOWN = "unknown"


class StockUtils:
    """股票工具类"""

    @staticmethod
    def identify_stock_market(ticker: str) -> StockMarket:
        """
        识别股票代码所属市场

        Args:
            ticker: 股票代码

        Returns:
            StockMarket: 股票市场类型
        """
        if not ticker:
            return StockMarket.UNKNOWN

        ticker = str(ticker).strip().upper()

        # 中国A股：6位数字，或带 .SZ/.SH 后缀
        if re.match(r"^\d{6}$", ticker):
            return StockMarket.CHINA_A
        if re.match(r"^\d{6}\.(SZ|SH)$", ticker):
            return StockMarket.CHINA_A

        return StockMarket.UNKNOWN

    @staticmethod
    def is_china_stock(ticker: str) -> bool:
        """判断是否为中国A股"""
        return StockUtils.identify_stock_market(ticker) == StockMarket.CHINA_A

    @staticmethod
    def get_currency_info(ticker: str) -> Tuple[str, str]:
        """
        获取货币信息

        Returns:
            (货币名称, 货币符号)
        """
        market = StockUtils.identify_stock_market(ticker)
        if market == StockMarket.CHINA_A:
            return "人民币", "¥"
        return "未知", "?"

    @staticmethod
    def normalize_code(ticker: str) -> str:
        """
        标准化股票代码：确保带有 .SZ 或 .SH 后缀

        Args:
            ticker: 原始股票代码

        Returns:
            标准化后的代码
        """
        ticker = str(ticker).strip().upper()
        if "." in ticker:
            return ticker
        if len(ticker) == 6:
            if ticker.startswith("6") or ticker.startswith("9"):
                return f"{ticker}.SH"
            return f"{ticker}.SZ"
        return ticker

    @staticmethod
    def get_market_info(ticker: str) -> Dict:
        """
        获取股票市场详细信息

        Returns:
            市场信息字典
        """
        market = StockUtils.identify_stock_market(ticker)
        currency_name, currency_symbol = StockUtils.get_currency_info(ticker)

        market_names = {
            StockMarket.CHINA_A: "中国A股",
            StockMarket.UNKNOWN: "未知市场",
        }

        return {
            "ticker": ticker,
            "market": market.value,
            "market_name": market_names[market],
            "currency_name": currency_name,
            "currency_symbol": currency_symbol,
            "is_china": market == StockMarket.CHINA_A,
        }


# 便捷函数
def is_china_stock(ticker: str) -> bool:
    """判断是否为中国A股"""
    return StockUtils.is_china_stock(ticker)


def get_stock_market_info(ticker: str) -> Dict:
    """获取股票市场信息"""
    return StockUtils.get_market_info(ticker)
