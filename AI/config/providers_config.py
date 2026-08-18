"""
LiveProfit 数据源配置管理
参考 TradingAgents-CN，管理 Tushare 和 AKShare 的配置和速率限制。
"""

import os
import logging

from .env_utils import parse_bool_env, parse_int_env, parse_float_env

logger = logging.getLogger(__name__)


class DataSourceConfig:
    """数据源配置管理器"""

    @staticmethod
    def get_provider_config(provider: str) -> dict:
        """获取指定提供者的配置"""
        provider = provider.lower()
        if provider == "tushare":
            return {
                "enabled": parse_bool_env("TUSHARE_ENABLED", True),
                "token": os.getenv("TUSHARE_TOKEN", ""),
                "timeout": parse_int_env("TUSHARE_TIMEOUT", 30),
                "rate_limit": parse_float_env("TUSHARE_RATE_LIMIT", 0.1),
                "max_retries": parse_int_env("TUSHARE_MAX_RETRIES", 3),
                "cache_enabled": parse_bool_env("TUSHARE_CACHE_ENABLED", True),
                "cache_ttl": parse_int_env("TUSHARE_CACHE_TTL", 3600),
            }
        elif provider == "akshare":
            return {
                "enabled": parse_bool_env("AKSHARE_ENABLED", True),
                "timeout": parse_int_env("AKSHARE_TIMEOUT", 60),
                "max_retries": parse_int_env("AKSHARE_MAX_RETRIES", 2),
                "cache_enabled": parse_bool_env("AKSHARE_CACHE_ENABLED", True),
                "cache_ttl": parse_int_env("AKSHARE_CACHE_TTL", 1800),
                "request_delay": parse_float_env("AKSHARE_REQUEST_DELAY", 0.5),
            }
        else:
            return {"enabled": False}

    @staticmethod
    def is_provider_enabled(provider: str) -> bool:
        config = DataSourceConfig.get_provider_config(provider)
        return config.get("enabled", False)

    @staticmethod
    def get_all_enabled_providers() -> list:
        providers = []
        for p in ["tushare", "akshare"]:
            if DataSourceConfig.is_provider_enabled(p):
                providers.append(p)
        return providers


# 全局单例
_data_source_config = DataSourceConfig()


def get_provider_config(provider: str) -> dict:
    """获取提供者配置"""
    return _data_source_config.get_provider_config(provider)


def is_provider_enabled(provider: str) -> bool:
    """检查提供者是否启用"""
    return _data_source_config.is_provider_enabled(provider)
