"""
YoHo 数据库缓存管理器 (MongoDB + Redis)
参考 TradingAgents-CN，双后端缓存，Redis 优先读取。
"""

import os
import json
import hashlib
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


class DatabaseCacheManager:
    """MongoDB + Redis 双后端缓存"""

    def __init__(self):
        self._redis = None
        self._mongo_db = None

        mongo_uri = os.getenv("MONGODB_CONNECTION_STRING") or os.getenv(
            "TRADINGAGENTS_MONGODB_URL"
        ) or f"mongodb://{os.getenv('MONGODB_HOST', 'localhost')}:{os.getenv('MONGODB_PORT', '27017')}/{os.getenv('MONGODB_DATABASE', 'yoho')}"

        redis_uri = os.getenv("REDIS_CONNECTION_STRING") or os.getenv(
            "TRADINGAGENTS_REDIS_URL"
        ) or f"redis://{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/{os.getenv('REDIS_DB', '0')}"

        self._mongo_available = False
        self._redis_available = False

        if PYMONGO_AVAILABLE:
            try:
                client = pymongo.MongoClient(mongo_uri, serverSelectionTimeoutMS=3000)
                client.admin.command("ping")
                db_name = os.getenv("MONGODB_DATABASE", "yoho")
                self._mongo_db = client[db_name]
                self._mongo_available = True
                self._ensure_indexes()
                logger.info("DatabaseCacheManager: MongoDB 已连接")
            except Exception as e:
                logger.warning(f"DatabaseCacheManager: MongoDB 不可用: {e}")

        if REDIS_AVAILABLE:
            try:
                self._redis = redis_lib.from_url(redis_uri, socket_connect_timeout=3)
                self._redis.ping()
                self._redis_available = True
                logger.info("DatabaseCacheManager: Redis 已连接")
            except Exception as e:
                logger.warning(f"DatabaseCacheManager: Redis 不可用: {e}")

    def _ensure_indexes(self):
        """创建 MongoDB 索引"""
        if not self._mongo_db:
            return
        try:
            self._mongo_db.stock_data.create_index(
                [("symbol", pymongo.ASCENDING), ("data_source", pymongo.ASCENDING)])
            self._mongo_db.stock_data.create_index("created_at")
            self._mongo_db.news_data.create_index(
                [("symbol", pymongo.ASCENDING), ("data_source", pymongo.ASCENDING)])
            self._mongo_db.fundamentals_data.create_index(
                [("symbol", pymongo.ASCENDING), ("data_source", pymongo.ASCENDING)])
        except Exception as e:
            logger.warning(f"索引创建失败: {e}")

    def _hash_key(self, *parts) -> str:
        raw = "|".join(str(p) for p in parts)
        return hashlib.md5(raw.encode()).hexdigest()[:16]

    # ---- 行情 ----

    def save_stock_data(self, symbol: str, data: str, start_date: str = "",
                        end_date: str = "", data_source: str = "") -> bool:
        key = f"stock:{symbol}:{self._hash_key(start_date, end_date, data_source)}"
        doc = {
            "symbol": symbol, "data": data, "data_source": data_source,
            "start_date": start_date, "end_date": end_date,
            "created_at": datetime.utcnow(),
        }
        # Redis (6h TTL)
        if self._redis_available:
            try:
                self._redis.setex(key, 21600, json.dumps(doc, ensure_ascii=False))
            except Exception:
                pass
        # MongoDB
        if self._mongo_available:
            try:
                self._mongo_db.stock_data.replace_one(
                    {"_id": key}, {"_id": key, **doc}, upsert=True)
            except Exception:
                pass
        return True

    def load_stock_data(self, symbol: str, data_source: str = "",
                        start_date: str = "", end_date: str = "") -> Optional[str]:
        key = f"stock:{symbol}:{self._hash_key(start_date, end_date, data_source)}"
        # Redis 优先
        if self._redis_available:
            try:
                raw = self._redis.get(key)
                if raw:
                    doc = json.loads(raw)
                    return doc.get("data")
            except Exception:
                pass
        # MongoDB 回退
        if self._mongo_available:
            try:
                doc = self._mongo_db.stock_data.find_one({"_id": key})
                if doc:
                    # 回填 Redis
                    if self._redis_available:
                        try:
                            doc.pop("_id", None)
                            self._redis.setex(key, 21600, json.dumps(doc, ensure_ascii=False))
                        except Exception:
                            pass
                    return doc.get("data")
            except Exception:
                pass
        return None

    # ---- 新闻 ----

    def save_news_data(self, symbol: str, data: str, data_source: str = "") -> bool:
        key = f"news:{symbol}:{data_source}"
        doc = {"symbol": symbol, "data": data, "data_source": data_source,
               "created_at": datetime.utcnow()}
        if self._redis_available:
            try:
                self._redis.setex(key, 86400, json.dumps(doc, ensure_ascii=False))
            except Exception:
                pass
        if self._mongo_available:
            try:
                self._mongo_db.news_data.replace_one({"_id": key}, {"_id": key, **doc}, upsert=True)
            except Exception:
                pass
        return True

    def load_news_data(self, symbol: str, data_source: str = "") -> Optional[str]:
        key = f"news:{symbol}:{data_source}"
        if self._redis_available:
            try:
                raw = self._redis.get(key)
                if raw:
                    return json.loads(raw).get("data")
            except Exception:
                pass
        if self._mongo_available:
            try:
                doc = self._mongo_db.news_data.find_one({"_id": key})
                if doc:
                    return doc.get("data")
            except Exception:
                pass
        return None

    # ---- 基本面 ----

    def save_fundamentals_data(self, symbol: str, data: str, data_source: str = "") -> bool:
        key = f"fina:{symbol}:{data_source}"
        doc = {"symbol": symbol, "data": data, "data_source": data_source,
               "created_at": datetime.utcnow()}
        if self._redis_available:
            try:
                self._redis.setex(key, 86400, json.dumps(doc, ensure_ascii=False))
            except Exception:
                pass
        if self._mongo_available:
            try:
                self._mongo_db.fundamentals_data.replace_one(
                    {"_id": key}, {"_id": key, **doc}, upsert=True)
            except Exception:
                pass
        return True

    def load_fundamentals_data(self, symbol: str, data_source: str = "") -> Optional[str]:
        key = f"fina:{symbol}:{data_source}"
        if self._redis_available:
            try:
                raw = self._redis.get(key)
                if raw:
                    return json.loads(raw).get("data")
            except Exception:
                pass
        if self._mongo_available:
            try:
                doc = self._mongo_db.fundamentals_data.find_one({"_id": key})
                if doc:
                    return doc.get("data")
            except Exception:
                pass
        return None

    def close(self):
        if self._redis:
            self._redis.close()
