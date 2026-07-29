"""
YoHo 数据库配置
参考 TradingAgents-CN，提供 MongoDB 和 Redis 的配置读取。
"""

import os
import logging
from typing import Dict

from .env_utils import parse_bool_env

logger = logging.getLogger(__name__)


class DatabaseConfig:
    """数据库配置管理"""

    @staticmethod
    def get_mongodb_config() -> Dict:
        """获取 MongoDB 配置"""
        conn_str = os.getenv("MONGODB_CONNECTION_STRING") or os.getenv(
            "TRADINGAGENTS_MONGODB_URL"
        )
        if not conn_str:
            # 从组件构建连接字符串
            host = os.getenv("MONGODB_HOST", "localhost")
            port = os.getenv("MONGODB_PORT", "27017")
            user = os.getenv("MONGODB_USERNAME", "")
            password = os.getenv("MONGODB_PASSWORD", "")
            database = os.getenv("MONGODB_DATABASE", "yoho")
            auth_source = os.getenv("MONGODB_AUTH_SOURCE", "admin")

            if user and password:
                conn_str = f"mongodb://{user}:{password}@{host}:{port}/{database}?authSource={auth_source}"
            else:
                conn_str = f"mongodb://{host}:{port}/{database}"

        return {
            "connection_string": conn_str,
            "database": os.getenv("MONGODB_DATABASE", "yoho"),
            "host": os.getenv("MONGODB_HOST", "localhost"),
            "port": int(os.getenv("MONGODB_PORT", "27017")),
            "username": os.getenv("MONGODB_USERNAME", ""),
            "password": os.getenv("MONGODB_PASSWORD", ""),
            "auth_source": os.getenv("MONGODB_AUTH_SOURCE", "admin"),
        }

    @staticmethod
    def get_redis_config() -> Dict:
        """获取 Redis 配置"""
        conn_str = os.getenv("REDIS_CONNECTION_STRING") or os.getenv(
            "TRADINGAGENTS_REDIS_URL"
        )
        if not conn_str:
            host = os.getenv("REDIS_HOST", "localhost")
            port = os.getenv("REDIS_PORT", "6379")
            password = os.getenv("REDIS_PASSWORD", "")
            db = os.getenv("REDIS_DB", "0")

            if password:
                conn_str = f"redis://:{password}@{host}:{port}/{db}"
            else:
                conn_str = f"redis://{host}:{port}/{db}"

        return {
            "connection_string": conn_str,
            "host": os.getenv("REDIS_HOST", "localhost"),
            "port": int(os.getenv("REDIS_PORT", "6379")),
            "password": os.getenv("REDIS_PASSWORD", ""),
            "db": int(os.getenv("REDIS_DB", "0")),
        }

    @staticmethod
    def is_mongodb_enabled() -> bool:
        return parse_bool_env("MONGODB_ENABLED", False)

    @staticmethod
    def is_redis_enabled() -> bool:
        return parse_bool_env("REDIS_ENABLED", False)
