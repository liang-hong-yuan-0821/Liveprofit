"""
YoHo 自适应缓存系统
根据可用后端自动选择：Redis > File
使用 pickle 序列化，支持任意 Python 对象。
"""

import os
import pickle
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import redis as redis_lib
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False


class AdaptiveCacheSystem:
    """自适应缓存，自动选择最优后端"""

    def __init__(self):
        self._redis = None
        self._backend = "file"  # 默认文件后端

        # 检测 Redis — 最高优先级
        if REDIS_AVAILABLE:
            try:
                uri = os.getenv("REDIS_CONNECTION_STRING") or os.getenv(
                    "TRADINGAGENTS_REDIS_URL"
                ) or f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/0"
                self._redis = redis_lib.from_url(uri, socket_connect_timeout=2)
                self._redis.ping()
                self._backend = "redis"
                logger.info("AdaptiveCache: 使用 Redis 后端")
            except Exception:
                pass

        # 兜底：文件后端
        if self._backend == "file":
            logger.info("AdaptiveCache: 使用文件后端")

        # 文件缓存目录
        self._file_dir = os.path.join(os.path.dirname(__file__), "data_cache")
        os.makedirs(self._file_dir, exist_ok=True)

    def _get_ttl(self, data_type: str) -> int:
        """获取数据类型对应的 TTL"""
        ttls = {"stock": 3600, "news": 14400, "fundamentals": 43200}
        return ttls.get(data_type, 3600)

    def save_data(self, key: str, data, data_type: str = "stock") -> bool:
        """保存数据到当前后端"""
        ttl = self._get_ttl(data_type)
        try:
            if self._backend == "redis" and self._redis:
                # Redis：pickle 序列化后写入，带 TTL
                pickled = pickle.dumps(data)
                self._redis.setex(f"yoho:{key}", ttl, pickled)
                return True
            else:
                # 文件回退：pickle 二进制文件 + 过期时间
                path = os.path.join(self._file_dir, f"{key}.pkl")
                with open(path, "wb") as f:
                    pickle.dump({"data": data, "expires_at": datetime.now() + timedelta(seconds=ttl)}, f)
                return True
        except Exception as e:
            logger.warning(f"缓存保存失败 [{key}]: {e}")
            return False

    def load_data(self, key: str, data_type: str = "stock") -> Optional[any]:
        """从当前后端加载数据"""
        try:
            if self._backend == "redis" and self._redis:
                pickled = self._redis.get(f"yoho:{key}")
                if pickled:
                    return pickle.loads(pickled)
            else:
                # 文件回退
                path = os.path.join(self._file_dir, f"{key}.pkl")
                if os.path.exists(path):
                    with open(path, "rb") as f:
                        doc = pickle.load(f)
                    if doc.get("expires_at", datetime.min) > datetime.now():
                        return doc["data"]
        except Exception as e:
            logger.warning(f"缓存加载失败 [{key}]: {e}")
        return None

    def get_backend(self) -> str:
        """获取当前使用的后端名称"""
        return self._backend
