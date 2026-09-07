"""SSE 协议契约测试（§2.6/§3.2.3：八事件白名单、固定 reset/heartbeat、终态帧关闭）。"""

from __future__ import annotations

import json
import uuid

from backend.tests.contract.api.test_analysis_tasks import CREATE_BODY, _create


def _write_business_event(redis_client, task_id: str, entry_id: str, event: str, **extra) -> None:
    payload = {
        "task_id": task_id,
        "attempt_no": 1,
        "occurred_at": "2026-09-05T09:10:00Z",
        "schema_version": "v1",
        **extra,
    }
    redis_client.xadd(
        f"task:{task_id}:events",
        {"event": event, "data": json.dumps(payload)},
        id=entry_id,
    )


def _read_frames(response) -> list[dict]:
    """按 SSE 帧组装（业务帧 id/event/data；控制帧无 id）。"""
    frames: list[dict] = []
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if current is not None:
            frames.append(current)
            current = None

    for line in response.iter_lines():
        if line.startswith("id: "):
            flush()
            current = {"id": line[4:], "event": None, "data": None}
        elif line.startswith("event: "):
            if current is None or current["event"] is not None:
                flush()
                current = {"id": None, "event": None, "data": None}
            current["event"] = line[7:]
        elif line.startswith("data: "):
            if current is None:
                current = {"id": None, "event": None, "data": None}
            current["data"] = json.loads(line[6:])
        elif line == "":
            flush()
    flush()
    return frames


def test_replay_three_business_frames_then_close_on_terminal(client):
    task_id = _create(client, f"sse-{uuid.uuid4().hex[:8]}").json()["data"]["task_id"]
    redis_client = client.redis
    # 写入六种业务事件之外的 bogus 帧：服务端必须过滤
    _write_business_event(redis_client, task_id, "1-0", "queued")
    _write_business_event(redis_client, task_id, "2-0", "bogus")
    _write_business_event(redis_client, task_id, "3-0", "started", worker_id="w1")
    _write_business_event(redis_client, task_id, "4-0", "progress", sequence=1, phase="market", message="市场层完成")
    _write_business_event(redis_client, task_id, "5-0", "completed", report_id="r1", duration_ms=100)

    with client.http.stream("GET", f"/api/v1/analysis-tasks/{task_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        frames = _read_frames(response)

    events = [frame["event"] for frame in frames]
    assert events == ["queued", "started", "progress", "completed"]  # bogus 被过滤
    assert [frame["id"] for frame in frames] == ["1-0", "3-0", "4-0", "5-0"]
    for frame in frames:
        assert frame["data"]["schema_version"] == "v1"
        assert frame["data"]["task_id"] == task_id
        assert frame["data"]["attempt_no"] == 1
    assert frames[-1]["data"]["report_id"] == "r1"


def test_cursor_before_window_gets_single_reset_then_close(client):
    task_id = _create(client, f"sse-reset-{uuid.uuid4().hex[:8]}").json()["data"]["task_id"]
    redis_client = client.redis
    _write_business_event(redis_client, task_id, "10-0", "queued")

    # 游标 0-1 早于最早保留 ID 10-0 → 一次 reset（固定四字段）后关闭
    with client.http.stream("GET", f"/api/v1/analysis-tasks/{task_id}/events?after=0-1") as response:
        frames = _read_frames(response)

    assert len(frames) == 1
    reset = frames[0]
    assert reset["id"] is None
    assert reset["event"] == "reset"
    assert set(reset["data"].keys()) == {"task_url", "earliest_event_id", "occurred_at", "schema_version"}
    assert reset["data"]["earliest_event_id"] == "10-0"
    assert reset["data"]["schema_version"] == "v1"


def test_after_cursor_resumes_from_later_frames(client):
    task_id = _create(client, f"sse-after-{uuid.uuid4().hex[:8]}").json()["data"]["task_id"]
    redis_client = client.redis
    _write_business_event(redis_client, task_id, "1-0", "queued")
    _write_business_event(redis_client, task_id, "2-0", "started", worker_id="w1")
    _write_business_event(redis_client, task_id, "3-0", "completed", report_id="r1")

    with client.http.stream("GET", f"/api/v1/analysis-tasks/{task_id}/events?after=1-0") as response:
        frames = _read_frames(response)
    assert [frame["id"] for frame in frames] == ["2-0", "3-0"]


def test_unknown_event_type_never_emitted(client):
    task_id = _create(client, f"sse-bogus-{uuid.uuid4().hex[:8]}").json()["data"]["task_id"]
    redis_client = client.redis
    _write_business_event(redis_client, task_id, "1-0", "queued")
    _write_business_event(redis_client, task_id, "2-0", "retrying")  # 协议外事件
    _write_business_event(redis_client, task_id, "3-0", "completed")

    with client.http.stream("GET", f"/api/v1/analysis-tasks/{task_id}/events") as response:
        frames = _read_frames(response)
    assert [frame["event"] for frame in frames] == ["queued", "completed"]

def test_failed_and_cancelled_terminal_frames_wire_and_close(client):
    """failed/cancelled 终态业务帧：id/event/data 齐全、专属字段存在、送达后连接关闭。"""
    for event_type, extra in (("failed", {"error_code": "FATAL", "message": "不可重试"}), ("cancelled", {"message": "已取消"})):
        task_id = _create(client, f"sse-term-{uuid.uuid4().hex[:8]}").json()["data"]["task_id"]
        _write_business_event(client.redis, task_id, "1-0", "queued")
        _write_business_event(client.redis, task_id, "2-0", event_type, **extra)
        with client.http.stream("GET", f"/api/v1/analysis-tasks/{task_id}/events") as response:
            frames = _read_frames(response)
        assert [frame["event"] for frame in frames] == ["queued", event_type]
        terminal = frames[-1]
        assert terminal["id"] == "2-0"
        assert terminal["data"]["schema_version"] == "v1"
        for key in extra:
            assert terminal["data"][key] == extra[key]

