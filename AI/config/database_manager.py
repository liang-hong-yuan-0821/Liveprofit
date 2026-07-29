"""
YoHo 数据库管理器
参考 TradingAgents-CN，智能检测 MongoDB 和 Redis 可用性，提供缓存后端选择。
"""

import logging
import os
from typing import Dict, Tuple, Optional

from .env_utils import parse_bool_env

logger = logging.getLogger(__name__)

try:
    import pymongo
    PYMONGO_AVAILABLE = True
except ImportError:
    PYMONGO_AVAILABLE = False

try:
    import redis as redis_lib
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False


class DatabaseManager:
    """智能数据库管理器，自动检测 MongoDB/Redis 可用性"""

    def __init__(self):
        self._config = self._load_env_config()
        self._mongodb_available = False
        self._redis_available = False
        self._mongodb_client = None
        self._redis_client = None
        self._primary_backend = "file"

        self._detect_and_initialize()

    def _load_env_config(self) -> Dict:
        """加载环境变量配置"""
        return {
            "mongodb": {
                "enabled": parse_bool_env("MONGODB_ENABLED", False),
                "host": os.getenv("MONGODB_HOST", "localhost"),
                "port": int(os.getenv("MONGODB_PORT", "27017")),
                "username": os.getenv("MONGODB_USERNAME", ""),
                "password": os.getenv("MONGODB_PASSWORD", ""),
                "database": os.getenv("MONGODB_DATABASE", "yoho"),
                "auth_source": os.getenv("MONGODB_AUTH_SOURCE", "admin"),
                "connection_string": os.getenv("MONGODB_CONNECTION_STRING", ""),
            },
            "redis": {
                "enabled": parse_bool_env("REDIS_ENABLED", False),
                "host": os.getenv("REDIS_HOST", "localhost"),
                "port": int(os.getenv("REDIS_PORT", "6379")),
                "password": os.getenv("REDIS_PASSWORD", ""),
                "db": int(os.getenv("REDIS_DB", "0")),
                "connection_string": os.getenv("REDIS_CONNECTION_STRING", ""),
            },
        }

    def _build_mongo_uri(self) -> str:
        """构建 MongoDB URI"""
        cfg = self._config["mongodb"]
        if cfg["connection_string"]:
            return cfg["connection_string"]
        if cfg["username"] and cfg["password"]:
            return (
                f"mongodb://{cfg['username']}:{cfg['password']}"
                f"@{cfg['host']}:{cfg['port']}/{cfg['database']}"
                f"?authSource={cfg['auth_source']}"
            )
        return f"mongodb://{cfg['host']}:{cfg['port']}/{cfg['database']}"

    def _build_redis_uri(self) -> str:
        """构建 Redis URI"""
        cfg = self._config["redis"]
        if cfg["connection_string"]:
            return cfg["connection_string"]
        if cfg["password"]:
            return f"redis://:{cfg['password']}@{cfg['host']}:{cfg['port']}/{cfg['db']}"
        return f"redis://{cfg['host']}:{cfg['port']}/{cfg['db']}"

    def _detect_mongodb(self) -> Tuple[bool, str]:
        """检测 MongoDB 可用性"""
        if not self._config["mongodb"]["enabled"]:
            return False, "MongoDB 未启用"
        if not PYMONGO_AVAILABLE:
            return False, "pymongo 未安装"
        try:
            uri = self._build_mongo_uri()
            client = pymongo.MongoClient(
                uri,
                connectTimeoutMS=3000,
                serverSelectionTimeoutMS=3000,
            )
            client.admin.command("ping")
            self._mongodb_client = client
            return True, "MongoDB 连接成功"
        except Exception as e:
            return False, f"MongoDB 连接失败: {e}"

    def _detect_redis(self) -> Tuple[bool, str]:
        """检测 Redis 可用性"""
        if not self._config["redis"]["enabled"]:
            return False, "Redis 未启用"
        if not REDIS_AVAILABLE:
            return False, "redis-py 未安装"
        try:
            uri = self._build_redis_uri()
            client = redis_lib.from_url(uri, socket_connect_timeout=3)
            client.ping()
            self._redis_client = client
            return True, "Redis 连接成功"
        except Exception as e:
            return False, f"Redis 连接失败: {e}"

    def _detect_and_initialize(self):
        """检测并初始化数据库"""
        mongo_ok, mongo_msg = self._detect_mongodb()
        self._mongodb_available = mongo_ok
        logger.info(f"MongoDB: {mongo_msg}")

        redis_ok, redis_msg = self._detect_redis()
        self._redis_available = redis_ok
        logger.info(f"Redis: {redis_msg}")

        # 确定缓存后端
        if self._redis_available:
            self._primary_backend = "redis"
        elif self._mongodb_available:
            self._primary_backend = "mongodb"
        else:
            self._primary_backend = "file"

        logger.info(f"缓存后端: {self._primary_backend}")

    # ---- 公共接口 ----

    def is_mongodb_available(self) -> bool:
        return self._mongodb_available

    def is_redis_available(self) -> bool:
        return self._redis_available

    def get_cache_backend(self) -> str:
        return self._primary_backend

    def get_mongodb_client(self):
        return self._mongodb_client

    def get_mongodb_db(self):
        if self._mongodb_client:
            return self._mongodb_client[self._config["mongodb"]["database"]]
        return None

    def get_redis_client(self):
        return self._redis_client

    def get_cache_ttl_config(self) -> Dict:
        """获取按市场分类的 TTL 配置"""
        return {
            "china_stock": 3600,        # A股数据 1h
            "china_news": 14400,        # A股新闻 4h
            "china_fundamentals": 43200, # A股基本面 12h
        }

    def get_status_report(self) -> str:
        lines = [
            "=== YoHo 数据库状态 ===",
            f"MongoDB: {'可用' if self._mongodb_available else '不可用'}",
            f"Redis: {'可用' if self._redis_available else '不可用'}",
            f"缓存后端: {self._primary_backend}",
        ]
        return "\n".join(lines)

    def close(self):
        """关闭所有数据库连接"""
        if self._mongodb_client:
            self._mongodb_client.close()
        if self._redis_client:
            self._redis_client.close()
        logger.info("数据库连接已关闭")


# 全局单例
_database_manager: Optional[DatabaseManager] = None


def get_database_manager() -> DatabaseManager:
    """获取全局数据库管理器单例"""
    global _database_manager
    if _database_manager is None:
        _database_manager = DatabaseManager()
    return _database_manager


# 便捷函数
def is_mongodb_available() -> bool:
    return get_database_manager().is_mongodb_available()


def is_redis_available() -> bool:
    return get_database_manager().is_redis_available()


def get_cache_backend() -> str:
    return get_database_manager().get_cache_backend()
