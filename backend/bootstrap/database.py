"""PostgreSQL engine / session 生命周期。

API/SSE 进程使用 async SQLAlchemy；Dramatiq Worker/Dispatcher 使用独立装配的 sync
SQLAlchemy。二者共享领域契约和表结构，但绝不跨线程/进程/event loop 传递 Session。
驱动统一为 psycopg3（postgresql+psycopg://），与事件研究既有 psycopg3 兼容。
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker


# psycopg 连接超时：防止目标不可达（如 IPv6 黑洞）时无限等待
_PSYCOPG_CONNECT_ARGS = {"connect_timeout": 5}


def build_sync_engine(url: str, *, echo: bool = False) -> Engine:
    return create_engine(url, pool_pre_ping=True, echo=echo, connect_args=_PSYCOPG_CONNECT_ARGS)


def build_async_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True, echo=echo, connect_args=_PSYCOPG_CONNECT_ARGS)


def build_sync_session_factory(engine: Engine) -> sessionmaker:
    """同步 Session 工厂（Worker/Dispatcher）。expire_on_commit=False 便于提交后读取投影。"""
    return sessionmaker(bind=engine, expire_on_commit=False)


def build_async_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    """异步 Session 工厂（API/SSE）。"""
    return async_sessionmaker(bind=engine, expire_on_commit=False)
