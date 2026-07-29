"""
YoHo MongoDB 存储适配器
参考 TradingAgents-CN 实现，管理 Token 使用记录的 MongoDB 存储。
"""

import os
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional

from .usage_models import UsageRecord

logger = logging.getLogger(__name__)

try:
    import pymongo
    PYMONGO_AVAILABLE = True
except ImportError:
    PYMONGO_AVAILABLE = False


class MongoDBStorage:
    """MongoDB 存储适配器，用于持久化使用记录"""

    def __init__(self, connection_string: Optional[str] = None,
                 database_name: Optional[str] = None,
                 collection_name: str = "token_usage"):
        if not PYMONGO_AVAILABLE:
            raise ImportError("pymongo 未安装，请运行: pip install pymongo")

        self.connection_string = connection_string or os.getenv(
            "MONGODB_CONNECTION_STRING",
            os.getenv("TRADINGAGENTS_MONGODB_URL", "")
        )
        if not self.connection_string:
            raise ValueError("未配置 MongoDB 连接字符串")

        self.database_name = database_name or os.getenv("MONGODB_DATABASE", "yoho")
        self.collection_name = collection_name
        self.client = None
        self.db = None
        self.collection = None
        self._connect()

    def _connect(self):
        """建立 MongoDB 连接"""
        connect_timeout = int(os.getenv("MONGO_CONNECT_TIMEOUT_MS", "30000"))
        socket_timeout = int(os.getenv("MONGO_SOCKET_TIMEOUT_MS", "60000"))
        server_timeout = int(os.getenv("MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000"))

        self.client = pymongo.MongoClient(
            self.connection_string,
            connectTimeoutMS=connect_timeout,
            socketTimeoutMS=socket_timeout,
            serverSelectionTimeoutMS=server_timeout,
        )
        # 验证连接
        self.client.admin.command("ping")
        self.db = self.client[self.database_name]
        self.collection = self.db[self.collection_name]
        self._create_indexes()
        logger.info(f"MongoDB 已连接: {self.database_name}.{self.collection_name}")

    def _create_indexes(self):
        """创建索引"""
        try:
            self.collection.create_index([
                ("timestamp", pymongo.DESCENDING),
                ("provider", pymongo.ASCENDING),
                ("model_name", pymongo.ASCENDING),
            ])
            self.collection.create_index("session_id")
            self.collection.create_index("analysis_type")
        except Exception as e:
            logger.warning(f"MongoDB 索引创建失败: {e}")

    def save_usage_record(self, record: UsageRecord) -> bool:
        """保存使用记录"""
        if self.collection is None:
            return False
        try:
            from dataclasses import asdict
            doc = asdict(record)
            doc["_created_at"] = datetime.now(timezone(timedelta(hours=8)))
            self.collection.insert_one(doc)
            return True
        except Exception as e:
            logger.error(f"保存使用记录失败: {e}")
            return False

    def load_usage_records(self, limit: int = 10000, days: Optional[int] = None) -> List[UsageRecord]:
        """加载使用记录"""
        if self.collection is None:
            return []
        try:
            query = {}
            if days:
                cutoff = datetime.now(timezone(timedelta(hours=8))) - timedelta(days=days)
                query["timestamp"] = {"$gte": cutoff}

            cursor = self.collection.find(query).sort("timestamp", pymongo.DESCENDING).limit(limit)
            records = []
            for doc in cursor:
                doc.pop("_id", None)
                doc.pop("_created_at", None)
                records.append(UsageRecord(**{
                    k: v for k, v in doc.items()
                    if k in UsageRecord.__dataclass_fields__
                }))
            return records
        except Exception as e:
            logger.error(f"加载使用记录失败: {e}")
            return []

    def get_usage_statistics(self, days: int = 30) -> Dict:
        """获取使用统计"""
        if self.collection is None:
            return {}
        try:
            cutoff = datetime.now(timezone(timedelta(hours=8))) - timedelta(days=days)
            pipeline = [
                {"$match": {"timestamp": {"$gte": cutoff}}},
                {"$group": {
                    "_id": None,
                    "total_cost": {"$sum": "$cost"},
                    "total_input_tokens": {"$sum": "$input_tokens"},
                    "total_output_tokens": {"$sum": "$output_tokens"},
                    "total_requests": {"$sum": 1},
                }}
            ]
            result = list(self.collection.aggregate(pipeline))
            if result:
                r = result[0]
                r.pop("_id", None)
                return r
            return {}
        except Exception as e:
            logger.error(f"获取使用统计失败: {e}")
            return {}

    def cleanup_old_records(self, days: int = 90) -> int:
        """清理旧记录"""
        if self.collection is None:
            return 0
        try:
            cutoff = datetime.now(timezone(timedelta(hours=8))) - timedelta(days=days)
            result = self.collection.delete_many({"timestamp": {"$lt": cutoff}})
            return result.deleted_count
        except Exception as e:
            logger.error(f"清理旧记录失败: {e}")
            return 0

    def close(self):
        """关闭连接"""
        if self.client:
            self.client.close()
            logger.info("MongoDB 连接已关闭")
