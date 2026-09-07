"""存活 / 就绪探测（不属于 /api/v1 业务 API，使用 health 标准格式）。

/health/live 只表示进程存活；/health/ready 校验 PG、Redis 与必要配置，不调用任何成本型 LLM。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request, Response

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


async def probe_postgres(engine, timeout_seconds: float = 2.0) -> None:
    """PG 连通性探测：SELECT 1，超时/异常即未就绪。"""
    from sqlalchemy import text

    async with engine.connect() as conn:
        await asyncio.wait_for(conn.execute(text("SELECT 1")), timeout=timeout_seconds)


async def probe_redis(client, timeout_seconds: float = 2.0) -> None:
    """Redis 连通性探测：PING。"""
    await asyncio.wait_for(client.ping(), timeout=timeout_seconds)


@router.get("/health/live")
async def health_live() -> dict:
    return {"status": "alive"}


@router.get("/health/ready")
async def health_ready(request: Request, response: Response) -> dict:
    container = request.app.state.container
    checks: dict[str, str] = {}

    for name, coro in (
        ("postgres", probe_postgres(container.engine)),
        ("redis", probe_redis(container.redis)),
    ):
        try:
            await coro
            checks[name] = "ok"
        except Exception as exc:  # noqa: BLE001 - 就绪探测失败一律映射为 not ready，但必须留诊断日志
            logger.warning("就绪探测失败：%s -> %s: %s", name, type(exc).__name__, exc)
            checks[name] = "not_ready"

    ready = all(value == "ok" for value in checks.values())
    if not ready:
        response.status_code = 503
    return {"status": "ready" if ready else "not_ready", "checks": checks}
