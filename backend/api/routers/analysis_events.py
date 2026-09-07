"""SSE 事件流路由（§3.2.3：回放 + 阻塞续读 + reset/heartbeat 控制帧）。

- 游标优先级：Last-Event-ID > after > 0-0；仅回放六种业务帧。
- 游标早于保留窗口：发送一次固定字段 reset 后关闭连接。
- PG 已终态而 Stream 缺终态业务帧：先经 TaskEventService 幂等补写（线程池、sync Redis）；
  补写失败/Redis 不可用则不发送任何替代事件并关闭，由客户端 REST 回退对账。
- 空闲心跳 30s；任务终态帧送达后关闭连接。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse

from backend.api.schemas.events import SSEContract
from backend.modules.analysis.domain.enums import TaskEventType

router = APIRouter(prefix="/api/v1", tags=["analysis-events"])

BUSINESS_EVENT_TYPES = {e.value for e in TaskEventType if e.value not in {"reset", "heartbeat"}}
TERMINAL_EVENT_TYPES = {TaskEventType.COMPLETED.value, TaskEventType.FAILED.value, TaskEventType.CANCELLED.value}

HEARTBEAT_INTERVAL_SECONDS = 30.0
_BLOCK_MS = 15000

_sse_connections = 0


def active_sse_connections() -> int:
    return _sse_connections


def _frame(event: str, data: dict, event_id: str | None) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data, ensure_ascii=False, default=str)}")
    return "\n".join(lines) + "\n\n"


def _business_frame(event_id: str, fields: dict) -> str | None:
    """只输出六种业务帧；结构不合法的帧丢弃（协议错误由客户端断开处理）。"""
    event_type = fields.get("event")
    if event_type not in BUSINESS_EVENT_TYPES:
        return None
    try:
        data = json.loads(fields["data"])
        if data.get("schema_version") != "v1":
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return _frame(event_type, data, event_id)


@router.get(
    "/analysis-tasks/{task_id}/events",
    # response_model 仅用于 OpenAPI 文档（把 SSE 协议帧 Schema 注册进 components）；
    # 运行时返回 StreamingResponse，不经过 JSON 序列化。
    response_model=SSEContract,
)
async def task_events(
    request: Request,
    task_id: uuid.UUID,
    after: str | None = None,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    cursor = last_event_id or after or "0-0"
    services = request.app.state.analysis_services
    redis_client = request.app.state.container.redis
    stream_key = f"task:{task_id}:events"
    task_url = f"/api/v1/analysis-tasks/{task_id}/events"

    async def event_stream():
        global _sse_connections
        _sse_connections += 1
        connection_id = uuid.uuid4().hex
        try:
            async for frame in _event_stream_body(
                request, services, redis_client, stream_key, task_id, task_url, cursor, connection_id
            ):
                yield frame
        finally:
            _sse_connections = max(0, _sse_connections - 1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _event_stream_body(request, services, redis_client, stream_key, task_id, task_url, cursor, connection_id):
        # 1) PG 已终态而 Stream 缺终态帧 → 幂等补写（sync 服务，线程池内执行）
        try:
            def _ensure():
                with services.open() as bundle:
                    return bundle.events.ensure_terminal_business_event(task_id)

            await services.run(_ensure)
        except Exception:  # noqa: BLE001 - 补写失败只关闭连接，客户端按 REST 对账
            return

        # 2) 回放（cursor 之后）
        try:
            if cursor == "0-0":
                replayed = await redis_client.xrange(stream_key, min="-", max="+")
            else:
                replayed = await redis_client.xrange(stream_key, min=f"({cursor}", max="+")
            earliest = await redis_client.xrange(stream_key, min="-", max="+", count=1)
        except Exception:  # noqa: BLE001 - Redis 不可用：不发送任何替代事件，关闭连接
            return

        if cursor != "0-0" and earliest:
            earliest_id = earliest[0][0]
            if cursor < earliest_id:
                # 游标早于保留窗口：发送一次固定字段 reset 后关闭
                yield _frame(
                    "reset",
                    {
                        "task_url": task_url,
                        "earliest_event_id": earliest_id,
                        "occurred_at": datetime.now(timezone.utc).isoformat(),
                        "schema_version": "v1",
                    },
                    None,
                )
                return

        last_id: str | None = None
        for entry_id, fields in replayed:
            frame = _business_frame(entry_id, fields)
            if frame is None:
                last_id = entry_id
                continue
            yield frame
            last_id = entry_id
            if fields.get("event") in TERMINAL_EVENT_TYPES:
                return  # 终态帧送达后关闭连接

        if not replayed and cursor != "0-0":
            last_id = cursor

        # 3) 阻塞续读 + 空闲心跳
        last_heartbeat = 0.0
        while True:
            if await request.is_disconnected():
                return
            try:
                start_id = "$" if last_id is None else last_id
                result = await redis_client.xread({stream_key: start_id}, block=_BLOCK_MS)
            except Exception:  # noqa: BLE001 - Redis 故障：关闭连接，客户端 REST 回退
                return
            now = time.monotonic()
            if result:
                for _key, entries in result:
                    for entry_id, fields in entries:
                        last_id = entry_id
                        frame = _business_frame(entry_id, fields)
                        if frame is not None:
                            yield frame
                        if fields.get("event") in TERMINAL_EVENT_TYPES:
                            return
            elif now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                yield _frame(
                    "heartbeat",
                    {
                        "connection_id": connection_id,
                        "sent_at": datetime.now(timezone.utc).isoformat(),
                        "schema_version": "v1",
                    },
                    None,
                )
                last_heartbeat = now
