"""
YoHo 缓存层
支持三级缓存后端：Redis > MongoDB > File。
通过 TA_CACHE_STRATEGY 环境变量控制。
"""

import os
import logging

logger = logging.getLogger(__name__)


def get_cache():
    """
    获取缓存管理器

    根据 TA_CACHE_STRATEGY 环境变量：
    - "integrated" (需显式设置): 使用 IntegratedCacheManager (自适应选择后端)
    - "file" (默认): 使用纯文件缓存

    集成模式初始化失败时自动回退到文件缓存。
    """
    strategy = os.getenv("TA_CACHE_STRATEGY", "file").lower()

    if strategy == "integrated":
        try:
            from .integrated import IntegratedCacheManager
            return IntegratedCacheManager()
        except Exception as e:
            logger.warning(f"集成缓存初始化失败，回退到文件缓存: {e}")

    # 默认：文件缓存
    from .file_cache import StockDataCache
    return StockDataCache()
