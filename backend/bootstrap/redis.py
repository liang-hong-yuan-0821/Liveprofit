"""Redis 客户端生命周期。

API/SSE 使用 async Redis client；Worker/Dispatcher 使用 sync Redis client。
Redis 承担 Broker、事件流与短期缓存；业务真相在 PostgreSQL。
"""

from __future__ import annotations

import redis
import redis.asyncio as aioredis


def build_sync_redis(url: str) -> redis.Redis:
    return redis.Redis.from_url(url, decode_responses=True)


def build_async_redis(url: str) -> aioredis.Redis:
    return aioredis.from_url(url, decode_responses=True)
