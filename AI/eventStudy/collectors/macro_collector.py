"""
宏观环境采集模块（方案 3.1.1）

经 Provider 层宏观接口（BaseStockDataProvider.get_macro_context）
获取中国宏观指标（10 年期国债收益率等）。FRED 等海外源暂不接入。
"""

import logging

from AI.eventStudy.collectors.config import get_provider

logger = logging.getLogger(__name__)


def fetch_macro_context(date: str, market: str = "CN") -> dict:
    """获取当日宏观环境指标（3.1.1 接口）。

    返回如 {"rate_10y": 2.34}；数据源不支持或获取失败时返回空 dict
    （调用方对缺失指标置空，不阻塞）。
    """
    prov = get_provider()
    if not hasattr(prov, "get_macro_context"):
        logger.warning(f"当前数据源不支持宏观环境接口（{type(prov).__name__}）")
        return {}
    try:
        return prov.get_macro_context(date, market) or {}
    except Exception as e:
        logger.error(f"宏观指标获取失败 [{date}]: {e}")
        return {}
