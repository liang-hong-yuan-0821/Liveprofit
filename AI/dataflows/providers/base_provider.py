"""
YoHo 基础数据提供器 (简化版)
仅保留同步抽象方法，移除异步和多源兼容层。
"""

from abc import ABC, abstractmethod


class BaseStockDataProvider(ABC):
    """股票数据提供器抽象基类"""

    def __init__(self, name: str):
        self.name = name
        self.connected = False

    @abstractmethod
    def get_stock_data(self, code: str, start_date: str, end_date: str) -> str:
        """获取股票日线行情数据，返回格式化的文本字符串"""
        pass

    @abstractmethod
    def get_stock_info(self, code: str) -> dict:
        """获取股票基本信息，返回字典"""
        pass
