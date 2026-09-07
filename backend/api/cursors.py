"""keyset 游标编解码（不透明 base64url；元组 (updated_at, id)）。"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime


def encode_cursor(updated_at: datetime, task_id: uuid.UUID) -> str:
    payload = json.dumps({"u": updated_at.isoformat(), "id": str(task_id)})
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(raw: str) -> tuple[datetime, uuid.UUID]:
    """解码失败抛 ValueError（Router 映射 422 VALIDATION_ERROR）。"""
    padded = raw + "=" * (-len(raw) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    updated_at = datetime.fromisoformat(payload["u"])
    task_id = uuid.UUID(payload["id"])
    return updated_at, task_id
