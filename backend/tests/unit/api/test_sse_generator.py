"""SSE 生成器 wire 级单测（§3.2.3：heartbeat 固定字段/无 id、终态帧关闭、协议外过滤）。

说明：starlette TestClient 的 ASGI 传输对长连接响应整体缓冲，无法在 HTTP 层
流式截取 heartbeat 帧（契约测试 test_sse_protocol.py 覆盖可终止的流；本文件
直接驱动 _event_stream_body 生成器，验证控制帧与终态行为）。
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from backend.api.routers import analysis_events as events_module


class FakeAsyncRedis:
    def __init__(self, replay: list, incoming: list) -> None:
        self._replay = replay
        self._incoming = list(incoming)
        self.xread_calls = 0

    async def xrange(self, key, min="-", max="+", count=None):
        return list(self._replay)

    async def xread(self, streams, block=None):
        self.xread_calls += 1
        if self._incoming:
            entry = self._incoming.pop(0)
            return [[None, [entry]]]
        return None


class FakeBundle:
    class _Events:
        def ensure_terminal_business_event(self, task_id):
            return None

    def __enter__(self):
        self.events = self._Events()
        return self

    def __exit__(self, *args):
        return None


class FakeServices:
    def __init__(self, bundle):
        self._bundle = bundle

    def open(self):
        return self._bundle

    async def run(self, fn):
        return fn()


class FakeRequest:
    async def is_disconnected(self):
        return False


def _entry(entry_id: str, event: str, **extra) -> tuple:
    payload = {
        "task_id": "t1",
        "attempt_no": 1,
        "occurred_at": "2026-09-05T09:10:00Z",
        "schema_version": "v1",
        **extra,
    }
    return entry_id, {"event": event, "data": json.dumps(payload)}


def _parse_frames(raw: list[str]) -> list[dict]:
    frames = []
    current = None
    for frame in raw:
        for line in frame.strip("\n").split("\n"):
            if line.startswith("id: "):
                if current:
                    frames.append(current)
                current = {"id": line[4:], "event": None, "data": None}
            elif line.startswith("event: "):
                if current is None or current["event"] is not None:
                    if current:
                        frames.append(current)
                    current = {"id": None, "event": None, "data": None}
                current["event"] = line[7:]
            elif line.startswith("data: "):
                current["data"] = json.loads(line[6:])
    if current:
        frames.append(current)
    return frames


_redis: FakeAsyncRedis | None = None


def test_heartbeat_fixed_fields_no_id():
    global _redis
    _redis = FakeAsyncRedis(replay=[_entry("1-0", "queued")], incoming=[])
    # 缩短心跳间隔：直接驱动生成器（不经过 HTTP 路由的模块常量）
    original = events_module.HEARTBEAT_INTERVAL_SECONDS
    events_module.HEARTBEAT_INTERVAL_SECONDS = 0.0
    try:
        raw = []

        async def scenario():
            generator = events_module._event_stream_body(
                FakeRequest(), FakeServices(FakeBundle()), _redis, "task:t", uuid.uuid4(),
                "/api/v1/analysis-tasks/t/events", "0-0", "conn-1",
            )
            async for frame in generator:
                raw.append(frame)
                parsed = _parse_frames(raw)
                if any(f.get("event") == "heartbeat" for f in parsed):
                    _redis._incoming.append(_entry("9-0", "completed", report_id="r9"))
                    # 终态帧送达后生成器关闭；排空剩余
                    continue
                if len(raw) > 20:
                    break

        asyncio.run(scenario())
    finally:
        events_module.HEARTBEAT_INTERVAL_SECONDS = original

    frames = _parse_frames(raw)
    events = [f["event"] for f in frames]
    assert "queued" in events
    heartbeats = [f for f in frames if f["event"] == "heartbeat"]
    assert heartbeats, f"未收到 heartbeat；events={events}"
    beat = heartbeats[0]
    assert beat["id"] is None
    assert set(beat["data"].keys()) == {"connection_id", "sent_at", "schema_version"}
    assert beat["data"]["schema_version"] == "v1"


def test_terminal_frame_closes_generator_and_keeps_wire_fields():
    global _redis
    _redis = FakeAsyncRedis(
        replay=[_entry("1-0", "queued"), _entry("2-0", "failed", error_code="FATAL", message="不可重试")],
        incoming=[],
    )
    raw = []

    async def scenario():
        generator = events_module._event_stream_body(
            FakeRequest(), FakeServices(FakeBundle()), _redis, "task:t", uuid.uuid4(),
            "/api/v1/analysis-tasks/t/events", "0-0", "conn-1",
        )
        async for frame in generator:
            raw.append(frame)

    asyncio.run(scenario())
    frames = _parse_frames(raw)
    assert [f["event"] for f in frames] == ["queued", "failed"]
    terminal = frames[-1]
    assert terminal["id"] == "2-0"
    assert terminal["data"]["error_code"] == "FATAL"
    assert terminal["data"]["schema_version"] == "v1"


def test_non_business_events_filtered_in_generator():
    global _redis
    _redis = FakeAsyncRedis(
        replay=[
            _entry("1-0", "queued"),
            ("2-0", {"event": "retrying", "data": "{}"}),
            _entry("3-0", "cancelled", message="已取消"),
        ],
        incoming=[],
    )
    raw = []

    async def scenario():
        generator = events_module._event_stream_body(
            FakeRequest(), FakeServices(FakeBundle()), _redis, "task:t", uuid.uuid4(),
            "/api/v1/analysis-tasks/t/events", "0-0", "conn-1",
        )
        async for frame in generator:
            raw.append(frame)

    asyncio.run(scenario())
    frames = _parse_frames(raw)
    assert [f["event"] for f in frames] == ["queued", "cancelled"]
    assert frames[-1]["id"] == "3-0"
    assert frames[-1]["data"]["message"] == "已取消"
