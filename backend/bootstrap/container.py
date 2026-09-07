"""DI 装配边界（组合根）。

API 进程装配 async engine/redis；Worker/Dispatcher 装配 sync engine/redis（T4 接入）。
容器只负责基础设施生命周期，不承载业务用例；具体 Service/Port 装配在 T3+ 按模块补齐。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.bootstrap.database import (
    build_async_engine,
    build_async_session_factory,
    build_sync_engine,
    build_sync_session_factory,
)
from backend.bootstrap.redis import build_async_redis, build_sync_redis
from backend.bootstrap.settings import Settings

ProcessKind = Literal["api", "worker", "dispatcher", "market_ingestion"]


@dataclass
class ApiContainer:
    """API 进程容器。

    - async engine/redis：SSE 阻塞读取与健康探测。
    - sync engine/redis：领域服务调用（ApiAnalysisServices 线程池内使用，
      每次调用全新 Session，绝不跨线程/event loop 传递）。
    """

    settings: Settings
    engine: object  # sqlalchemy.ext.asyncio.AsyncEngine
    session_factory: object  # async_sessionmaker
    redis: object  # redis.asyncio.Redis
    sync_engine: object  # sqlalchemy.Engine
    sync_session_factory: object  # sessionmaker
    redis_sync: object  # redis.Redis

    async def dispose(self) -> None:
        await self.engine.dispose()
        await self.redis.aclose()
        self.sync_engine.dispose()
        self.redis_sync.close()


@dataclass
class SyncContainer:
    """Worker / Dispatcher 进程容器（sync）。"""

    settings: Settings
    engine: object  # sqlalchemy.Engine
    session_factory: object  # sessionmaker
    redis: object  # redis.Redis

    def dispose(self) -> None:
        self.engine.dispose()
        self.redis.close()


def build_api_container(settings: Settings) -> ApiContainer:
    """仅创建客户端对象，不做连接探测（依赖连通性由 /health/ready 报告）。"""
    database_url = settings.core.resolved_database_url()
    redis_url = settings.core.resolved_redis_url()
    if not database_url or not redis_url:
        raise ValueError("API 容器装配缺少 DATABASE_URL / REDIS_URL")
    engine = build_async_engine(database_url)
    sync_engine = build_sync_engine(database_url)
    return ApiContainer(
        settings=settings,
        engine=engine,
        session_factory=build_async_session_factory(engine),
        redis=build_async_redis(redis_url),
        sync_engine=sync_engine,
        sync_session_factory=build_sync_session_factory(sync_engine),
        redis_sync=build_sync_redis(redis_url),
    )


def build_sync_container(settings: Settings, process: ProcessKind) -> SyncContainer:
    """Worker / Dispatcher 容器装配（T4 接入使用）。"""
    settings.validate_for_process(process)
    database_url = settings.core.resolved_database_url()
    redis_url = settings.core.resolved_redis_url()
    if not database_url or not redis_url:
        raise ValueError(f"{process} 容器装配缺少 DATABASE_URL / REDIS_URL")
    engine = build_sync_engine(database_url)
    return SyncContainer(
        settings=settings,
        engine=engine,
        session_factory=build_sync_session_factory(engine),
        redis=build_sync_redis(redis_url),
    )
