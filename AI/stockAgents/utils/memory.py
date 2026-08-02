"""
YoHo 记忆系统
参考 TradingAgents-CN 架构：
- ChromaDBManager 单例模式（线程安全）
- FinancialSituationMemory 使用 ChromaDB 持久存储
- 嵌入：OpenAI text-embedding-3-small（通过同一 YOHO_API_KEY）
- Windows 10/11 自动适配
"""

import os
import threading
import logging
from typing import List, Dict, Optional

from .chromadb_config import get_optimal_chromadb_client

logger = logging.getLogger(__name__)

try:
    import chromadb
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


# ==================== ChromaDBManager 单例 ====================

class ChromaDBManager:
    """线程安全的 ChromaDB 单例管理器，参考 TradingAgents-CN"""

    _instance = None
    _lock = threading.Lock()
    _collections: Dict[str, any] = {}
    _client = None

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(ChromaDBManager, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        try:
            self._client = get_optimal_chromadb_client()
            logger.info("ChromaDB 客户端初始化成功")
        except Exception as e:
            logger.warning(f"ChromaDB 标准初始化失败: {e}，尝试回退...")
            try:
                self._client = chromadb.PersistentClient(path="./chroma_db")
                logger.info("ChromaDB 回退初始化成功")
            except Exception as be:
                logger.warning(f"ChromaDB 回退也失败: {be}")
                self._client = chromadb.Client()
                logger.info("ChromaDB 最简配置初始化")
        self._initialized = True

    def get_or_create_collection(self, name: str):
        """线程安全地获取或创建集合"""
        with self._lock:
            if name in self._collections:
                return self._collections[name]
            try:
                collection = self._client.get_collection(name=name)
                logger.info(f"ChromaDB: 获取现有集合 '{name}'")
            except Exception:
                try:
                    collection = self._client.create_collection(name=name)
                    logger.info(f"ChromaDB: 创建新集合 '{name}'")
                except Exception as e:
                    try:
                        collection = self._client.get_collection(name=name)
                        logger.info(f"ChromaDB: 并发后获取集合 '{name}'")
                    except Exception as fe:
                        logger.error(f"ChromaDB: 集合操作失败 '{name}': {fe}")
                        raise fe
            self._collections[name] = collection
            return collection


# ==================== FinancialSituationMemory ====================

class FinancialSituationMemory:
    """基于 ChromaDB 的金融情境记忆"""

    def __init__(self, name: str, config: Optional[dict] = None):
        self.name = name
        self.config = config or {}
        self._disabled = False

        # 检查启用状态
        memory_enabled = os.getenv("YOHO_MEMORY_ENABLED", "true").lower() == "true"
        if not memory_enabled:
            self._disabled = True
            logger.info(f"记忆 '{name}': 已禁用")
            return

        # 初始化 OpenAI 嵌入客户端
        api_key = os.getenv("YOHO_API_KEY", "")
        base_url = os.getenv("YOHO_BASE_URL", "https://api.openai.com/v1")
        self._openai = None
        if OPENAI_AVAILABLE and api_key:
            try:
                self._openai = OpenAI(api_key=api_key, base_url=base_url)
            except Exception as e:
                logger.warning(f"记忆 '{name}': OpenAI 客户端初始化失败: {e}")

        # 初始化 ChromaDB
        self._collection = None
        if CHROMADB_AVAILABLE:
            try:
                manager = ChromaDBManager()
                self._collection = manager.get_or_create_collection(name=name)
                logger.info(f"记忆 '{name}': 初始化成功")
            except Exception as e:
                logger.warning(f"记忆 '{name}': ChromaDB 初始化失败: {e}")
                self._disabled = True
        else:
            self._disabled = True
            logger.warning(f"记忆 '{name}': ChromaDB 未安装")

    def _get_embedding(self, text: str) -> List[float]:
        """获取文本嵌入向量"""
        if self._disabled or not self._openai:
            return [0.0] * 1024
        max_len = 8000 * 4  # 粗略中文长度上限
        if len(text) > max_len:
            text = text[:max_len]
        try:
            resp = self._openai.embeddings.create(
                model="text-embedding-3-small", input=text
            )
            return resp.data[0].embedding
        except Exception as e:
            logger.warning(f"记忆 '{self.name}': 嵌入失败: {e}")
            return [0.0] * 1024

    def add_situations(self, situations: List[tuple]):
        """添加情境到记忆"""
        if self._disabled or not self._collection:
            return
        for i, (situation, reflection) in enumerate(situations):
            if not situation or not reflection:
                continue
            embedding = self._get_embedding(situation)
            doc_id = f"{self.name}_{hash(situation) % 10**10}_{i}"
            try:
                self._collection.add(
                    ids=[doc_id],
                    embeddings=[embedding],
                    documents=[reflection],
                    metadatas=[{"situation": situation[:1000]}],
                )
            except Exception as e:
                logger.warning(f"记忆 '{self.name}': 添加失败: {e}")

    def get_memories(self, situation: str, n_matches: int = 1):
        """检索相关历史记忆"""
        if self._disabled or not self._collection:
            return []
        embedding = self._get_embedding(situation)
        if all(v == 0.0 for v in embedding):
            return []
        try:
            results = self._collection.query(
                query_embeddings=[embedding],
                n_results=min(n_matches, 5),
            )
        except Exception as e:
            logger.warning(f"记忆 '{self.name}': 查询失败: {e}")
            return []

        if not results or not results.get("documents") or not results["documents"][0]:
            return []

        memories = []
        distances = results.get("distances", [[]])[0]
        for i, doc in enumerate(results["documents"][0]):
            if doc:
                dist = distances[i] if i < len(distances) else 1.0
                memories.append({
                    "recommendation": doc,
                    "similarity": round(1.0 - dist, 4),
                    "distance": round(dist, 4),
                })
        return memories
