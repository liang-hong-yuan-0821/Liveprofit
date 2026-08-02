"""
YoHo 集成缓存管理器
Facade 模式，统一文件缓存和自适应缓存的接口。
高性能后端可用时自动启用双写（自适应 + 文件），
确保在降级场景下文件缓存始终可用。
"""

import logging
from typing import Optional

from .file_cache import StockDataCache
from .adaptive import AdaptiveCacheSystem

logger = logging.getLogger(__name__)


class IntegratedCacheManager:
    """集成缓存管理器，自动选择最优缓存策略"""

    def __init__(self):
        # 文件缓存作为兜底（始终可用）
        self._legacy = StockDataCache()
        self._adaptive = None
        self._use_adaptive = False

        try:
            self._adaptive = AdaptiveCacheSystem()
            backend = self._adaptive.get_backend()
            if backend != "file":
                # 高性能后端可用时，启用双写模式
                self._use_adaptive = True
                logger.info(f"IntegratedCache: 使用 {backend.upper()} 高性能模式")
            else:
                logger.info("IntegratedCache: 使用文件缓存模式")
        except Exception as e:
            logger.warning(f"自适应缓存初始化失败: {e}")

    # ---- 行情 ----

    def save_stock_data(self, symbol: str, data: str, **kwargs) -> bool:
        """保存行情数据：自适应缓存 + 文件缓存双写"""
        success = True
        if self._use_adaptive and self._adaptive:
            success = self._adaptive.save_data(f"stock:{symbol}", data)
        # 文件缓存始终写入，作为降级保障
        self._legacy.save_stock_data(symbol, data, **kwargs)
        return success

    def load_stock_data(self, symbol: str, **kwargs) -> Optional[str]:
        """加载行情数据：自适应缓存优先，文件缓存回退"""
        if self._use_adaptive and self._adaptive:
            cached = self._adaptive.load_data(f"stock:{symbol}")
            if cached and isinstance(cached, str):
                return cached
        # 回退到文件缓存
        return self._legacy.load_stock_data(symbol, **kwargs)

    # ---- 新闻 ----

    def save_news_data(self, symbol: str, data: str, **kwargs) -> bool:
        """保存新闻数据：双写模式"""
        if self._use_adaptive and self._adaptive:
            self._adaptive.save_data(f"news:{symbol}", data, "news")
        return self._legacy.save_news_data(symbol, data, **kwargs)

    def load_news_data(self, symbol: str, **kwargs) -> Optional[str]:
        """加载新闻数据：自适应缓存优先，文件缓存回退"""
        if self._use_adaptive and self._adaptive:
            cached = self._adaptive.load_data(f"news:{symbol}", "news")
            if cached and isinstance(cached, str):
                return cached
        return self._legacy.load_news_data(symbol, **kwargs)

    # ---- 基本面 ----

    def save_fundamentals_data(self, symbol: str, data: str, **kwargs) -> bool:
        """保存基本面数据：双写模式"""
        if self._use_adaptive and self._adaptive:
            self._adaptive.save_data(f"fina:{symbol}", data, "fundamentals")
        return self._legacy.save_fundamentals_data(symbol, data, **kwargs)

    def load_fundamentals_data(self, symbol: str, **kwargs) -> Optional[str]:
        """加载基本面数据：自适应缓存优先，文件缓存回退"""
        if self._use_adaptive and self._adaptive:
            cached = self._adaptive.load_data(f"fina:{symbol}", "fundamentals")
            if cached and isinstance(cached, str):
                return cached
        return self._legacy.load_fundamentals_data(symbol, **kwargs)

    # ---- 清理 ----

    def clear_old_cache(self, max_age_days: int = 30):
        """清理过期缓存"""
        self._legacy.clear_old_cache(max_age_days)
