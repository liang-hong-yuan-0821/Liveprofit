"""
YoHo 基础数据提供器 (简化版)
仅保留同步抽象方法，移除异步和多源兼容层。
"""

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class BaseStockDataProvider(ABC):
    """股票数据提供器抽象基类"""

    def __init__(self, name: str):
        self.name = name
        self.connected = False

    @abstractmethod
    def get_stock_data(self, code: str, start_date: str, end_date: str) -> str:
        """
        获取股票日线行情数据

        Args:
            code: 股票代码
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            格式化的行情数据文本
        """
        pass

    @abstractmethod
    def get_stock_info(self, code: str) -> dict:
        """
        获取股票基本信息

        Args:
            code: 股票代码

        Returns:
            包含 name, industry, area, list_date 等字段的字典
        """
        pass
