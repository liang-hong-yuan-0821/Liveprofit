"""
YoHo 自适应缓存系统
根据可用后端自动选择：Redis > MongoDB > File
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

try:
    import pymongo
    PYMONGO_AVAILABLE = True
except ImportError:
    PYMONGO_AVAILABLE = False


class AdaptiveCacheSystem:
    """自适应缓存，自动选择最优后端"""

    def __init__(self):
        self._redis = None
        self._mongo_db = None
        self._backend = "file"

        # 检测 Redis
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

        # 检测 MongoDB
        if self._backend != "redis" and PYMONGO_AVAILABLE:
            try:
                uri = os.getenv("MONGODB_CONNECTION_STRING", f"mongodb://localhost:27017")
                client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=2000)
                client.admin.command("ping")
                self._mongo_db = client[os.getenv("MONGODB_DATABASE", "yoho")]
                self._backend = "mongodb"
                logger.info("AdaptiveCache: 使用 MongoDB 后端")
            except Exception:
                pass

        if self._backend == "file":
            logger.info("AdaptiveCache: 使用文件后端")

        # 文件缓存目录
        self._file_dir = os.path.join(os.path.dirname(__file__), "data_cache")
        os.makedirs(self._file_dir, exist_ok=True)

    def _get_ttl(self, data_type: str) -> int:
        ttls = {"stock": 3600, "news": 14400, "fundamentals": 43200}
        return ttls.get(data_type, 3600)

    def save_data(self, key: str, data, data_type: str = "stock") -> bool:
        ttl = self._get_ttl(data_type)
        try:
            if self._backend == "redis" and self._redis:
                pickled = pickle.dumps(data)
                self._redis.setex(f"yoho:{key}", ttl, pickled)
                return True
            elif self._backend == "mongodb" and self._mongo_db:
                doc = {"_id": key, "data": pickle.dumps(data).hex(),
                       "data_type": data_type,
                       "expires_at": datetime.utcnow() + timedelta(seconds=ttl)}
                self._mongo_db.cache.replace_one({"_id": key}, doc, upsert=True)
                return True
            else:
                # 文件回退
                path = os.path.join(self._file_dir, f"{key}.pkl")
                with open(path, "wb") as f:
                    pickle.dump({"data": data, "expires_at": datetime.now() + timedelta(seconds=ttl)}, f)
                return True
        except Exception as e:
            logger.warning(f"缓存保存失败 [{key}]: {e}")
            return False

    def load_data(self, key: str, data_type: str = "stock") -> Optional[any]:
        try:
            if self._backend == "redis" and self._redis:
                pickled = self._redis.get(f"yoho:{key}")
                if pickled:
                    return pickle.loads(pickled)
            elif self._backend == "mongodb" and self._mongo_db:
                doc = self._mongo_db.cache.find_one({"_id": key})
                if doc and doc.get("expires_at", datetime.min) > datetime.utcnow():
                    return pickle.loads(bytes.fromhex(doc["data"]))
            else:
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
        return self._backend
