"""
LiveProfit 文件缓存系统
参考 TradingAgents-CN，基于文件的缓存，按市场类型组织。
使用 JSON 文件存储，MD5 哈希键名，支持 TTL 过期。
"""

import os
import json
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class StockDataCache:
    """基于文件的股票数据缓存"""

    def __init__(self, cache_dir: str = None):
        if cache_dir is None:
            cache_dir = os.path.join(os.path.dirname(__file__), "data_cache")
        self.cache_dir = cache_dir
        self._ensure_dirs()

    def _ensure_dirs(self):
        """确保缓存目录存在"""
        for sub in ["china_stocks", "china_news", "china_fundamentals", "metadata"]:
            os.makedirs(os.path.join(self.cache_dir, sub), exist_ok=True)

    def _get_cache_key(self, *args) -> str:
        """生成缓存键（MD5 前16位）"""
        raw = "|".join(str(a) for a in args)
        return hashlib.md5(raw.encode()).hexdigest()[:16]

    def _get_cache_path(self, category: str, key: str) -> str:
        """获取缓存文件路径"""
        return os.path.join(self.cache_dir, category, f"{key}.json")

    def _save_metadata(self, key: str, metadata: dict):
        """保存元数据"""
        path = os.path.join(self.cache_dir, "metadata", f"{key}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False)

    def _load_metadata(self, key: str) -> Optional[dict]:
        """加载元数据"""
        path = os.path.join(self.cache_dir, "metadata", f"{key}.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def _is_cache_valid(self, cached_at: str, ttl_seconds: int) -> bool:
        """检查缓存是否在有效期内"""
        try:
            cached_time = datetime.fromisoformat(cached_at)
            return (datetime.now() - cached_time).total_seconds() < ttl_seconds
        except Exception:
            return False

    # ---- 行情 ----

    def save_stock_data(self, symbol: str, data: str, start_date: str = "",
                        end_date: str = "", data_source: str = "") -> bool:
        """保存股票行情数据"""
        try:
            # 生成缓存键（含日期范围，避免不同查询复用同一缓存）
            key = self._get_cache_key(symbol, data_source, start_date, end_date)
            path = self._get_cache_path("china_stocks", key)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"data": data, "created_at": datetime.now().isoformat()}, f, ensure_ascii=False)
            self._save_metadata(key, {
                "symbol": symbol, "data_source": data_source,
                "start_date": start_date, "end_date": end_date,
                "cached_at": datetime.now().isoformat(),
            })
            return True
        except Exception as e:
            logger.warning(f"保存行情缓存失败 [{symbol}]: {e}")
            return False

    def load_stock_data(self, symbol: str, data_source: str = "",
                        start_date: str = "", end_date: str = "") -> Optional[str]:
        """加载股票行情数据（TTL: 1小时）"""
        key = self._get_cache_key(symbol, data_source, start_date, end_date)
        path = self._get_cache_path("china_stocks", key)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    doc = json.load(f)
                # TTL = 3600s (1小时)
                if self._is_cache_valid(doc.get("created_at", ""), 3600):
                    return doc.get("data")
            except Exception:
                pass
        return None

    # ---- 新闻 ----

    def save_news_data(self, symbol: str, data: str, data_source: str = "") -> bool:
        """保存新闻数据"""
        try:
            key = self._get_cache_key(symbol, data_source)
            path = self._get_cache_path("china_news", key)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"data": data, "created_at": datetime.now().isoformat()}, f, ensure_ascii=False)
            return True
        except Exception as e:
            logger.warning(f"保存新闻缓存失败 [{symbol}]: {e}")
            return False

    def load_news_data(self, symbol: str, data_source: str = "") -> Optional[str]:
        """加载新闻数据（TTL: 4小时）"""
        key = self._get_cache_key(symbol, data_source)
        path = self._get_cache_path("china_news", key)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    doc = json.load(f)
                # TTL = 14400s (4小时)
                if self._is_cache_valid(doc.get("created_at", ""), 14400):
                    return doc.get("data")
            except Exception:
                pass
        return None

    # ---- 基本面 ----

    def save_fundamentals_data(self, symbol: str, data: str, data_source: str = "") -> bool:
        """保存基本面数据"""
        try:
            key = self._get_cache_key(symbol, data_source)
            path = self._get_cache_path("china_fundamentals", key)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"data": data, "created_at": datetime.now().isoformat()}, f, ensure_ascii=False)
            return True
        except Exception as e:
            logger.warning(f"保存基本面缓存失败 [{symbol}]: {e}")
            return False

    def load_fundamentals_data(self, symbol: str, data_source: str = "") -> Optional[str]:
        """加载基本面数据（TTL: 12小时）"""
        key = self._get_cache_key(symbol, data_source)
        path = self._get_cache_path("china_fundamentals", key)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    doc = json.load(f)
                # TTL = 43200s (12小时)
                if self._is_cache_valid(doc.get("created_at", ""), 43200):
                    return doc.get("data")
            except Exception:
                pass
        return None

    # ---- 清理 ----

    def clear_old_cache(self, max_age_days: int = 30):
        """清理过期缓存（按文件修改时间判断）"""
        cutoff = datetime.now() - timedelta(days=max_age_days)
        for category in ["china_stocks", "china_news", "china_fundamentals", "metadata"]:
            cat_dir = os.path.join(self.cache_dir, category)
            if not os.path.exists(cat_dir):
                continue
            for fname in os.listdir(cat_dir):
                fpath = os.path.join(cat_dir, fname)
                try:
                    mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
                    if mtime < cutoff:
                        os.remove(fpath)
                except Exception:
                    pass
