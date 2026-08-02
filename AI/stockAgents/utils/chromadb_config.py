"""
YoHo ChromaDB 配置
参考 TradingAgents-CN，Windows 10/11 兼容，禁用遥测。
"""

import platform
import logging
from chromadb.config import Settings

logger = logging.getLogger(__name__)


def is_windows_11() -> bool:
    """检测是否为 Windows 11"""
    if platform.system() != "Windows":
        return False
    try:
        build = int(platform.version().split(".")[-1]) if platform.version() else 0
        return build >= 22000
    except (ValueError, IndexError):
        return False


def get_win10_chromadb_client():
    """Windows 10 兼容的 ChromaDB 客户端"""
    try:
        import chromadb
        settings = Settings(
            chroma_db_impl="duckdb+parquet",
            chroma_api_impl="chromadb.api.segment.SegmentAPI",
            is_persistent=True,
            anonymized_telemetry=False,
        )
        return chromadb.PersistentClient(
            path="./chroma_db",
            settings=settings,
        )
    except Exception:
        pass
    # 回退
    import chromadb
    return chromadb.PersistentClient(path="./chroma_db")


def get_win11_chromadb_client():
    """Windows 11 优化的 ChromaDB 客户端"""
    try:
        import chromadb
        settings = Settings(
            is_persistent=True,
            anonymized_telemetry=False,
        )
        return chromadb.PersistentClient(
            path="./chroma_db",
            settings=settings,
        )
    except Exception as e:
        logger.warning(f"Windows 11 ChromaDB 初始化失败: {e}")
        return get_win10_chromadb_client()


def get_optimal_chromadb_client():
    """获取最适合当前操作系统的 ChromaDB 客户端"""
    import chromadb

    if platform.system() == "Windows":
        if is_windows_11():
            logger.info("ChromaDB: Windows 11 优化配置")
            return get_win11_chromadb_client()
        else:
            logger.info("ChromaDB: Windows 10 兼容配置")
            return get_win10_chromadb_client()
    else:
        # Linux/macOS 标准配置
        settings = Settings(
            is_persistent=True,
            anonymized_telemetry=False,
        )
        logger.info(f"ChromaDB: {platform.system()} 标准配置")
        return chromadb.PersistentClient(
            path="./chroma_db",
            settings=settings,
        )
