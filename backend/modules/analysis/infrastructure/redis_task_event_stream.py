"""TaskEventStreamPort 的 Redis Stream 实现（§2.4 / §3.1.4）。

- Key 规范：task:{task_id}:events；仅六种业务事件持久化（reset/heartbeat 不写 Stream）。
- MAXLEN 近似裁剪；时间保留由定期 XTRIM MINID 执行（T10 reconciliation 接入）。
- 写失败向上抛出（调用方记录告警但不回滚 PG）。
"""

from __future__ import annotations

import json
import uuid

import redis


class RedisTaskEventStream:
    def __init__(self, client: redis.Redis, *, maxlen: int = 1000) -> None:
        self._client = client
        self._maxlen = maxlen

    @staticmethod
    def _key(task_id: uuid.UUID) -> str:
        return f"task:{task_id}:events"

    def append(self, task_id: uuid.UUID, event_type: str, payload: dict) -> str:
        fields = {
            "event": event_type,
            "data": json.dumps(payload, ensure_ascii=False, default=str),
        }
        return self._client.xadd(self._key(task_id), fields, maxlen=self._maxlen, approximate=True)

    def read(self, task_id: uuid.UUID, after: str | None) -> list[tuple[str, str, dict]]:
        key = self._key(task_id)
        if after:
            entries = self._client.xrange(key, min=f"({after}", max="+")
        else:
            entries = self._client.xrange(key, min="-", max="+")
        return [
            (entry_id, fields["event"], json.loads(fields["data"]))
            for entry_id, fields in entries
            if "event" in fields
        ]

    def trim(self, task_id: uuid.UUID) -> None:
        self._client.xtrim(self._key(task_id), maxlen=self._maxlen, approximate=True)
