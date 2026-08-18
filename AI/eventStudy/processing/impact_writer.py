"""
影响结果写入模块（方案 3.6.4）

事件研究结果先写入 Redis 草稿（event_impacts:draft:{event_id}），
经人工审核勾选后由 review 模块落表 PG event_impacts。

- 草稿设 TTL，过期可重算（幂等）
- PG event_impacts 只含人工确认过的正式记录，不加 status 字段（B5）
"""

import json
import logging
from datetime import datetime

from AI.eventStudy.collectors.config import (
    IMPACT_DRAFT_TTL, KEY_IMPACT_DRAFT, get_redis_client, is_redis_available,
)

logger = logging.getLogger(__name__)


def write_impact_draft(event_id: int, draft: dict) -> bool:
    """写入/覆盖事件影响草稿（TTL 过期自动清理，可重算）。

    draft 结构（compute_all_windows 生成）：
    {"event_id", "t0", "computed_at", "assets": {ticker: {window_type: {...}}}}
    """
    if not is_redis_available():
        logger.warning("Redis 不可用，影响草稿写入失败")
        return False
    draft.setdefault("event_id", event_id)
    draft.setdefault("computed_at", datetime.now().isoformat())
    get_redis_client().set(
        KEY_IMPACT_DRAFT.format(event_id=event_id),
        json.dumps(draft, ensure_ascii=False, default=str),
        ex=IMPACT_DRAFT_TTL,
    )
    return True


def read_impact_draft(event_id: int):
    """读取事件影响草稿；不存在或已过期返回 None。"""
    if not is_redis_available():
        return None
    raw = get_redis_client().get(KEY_IMPACT_DRAFT.format(event_id=event_id))
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"影响草稿 JSON 解析失败: event={event_id}")
        return None


def delete_impact_draft(event_id: int):
    """删除事件影响草稿（落表确认后清理）。"""
    if not is_redis_available():
        return
    get_redis_client().delete(KEY_IMPACT_DRAFT.format(event_id=event_id))


def list_impact_drafts() -> list[dict]:
    """列出全部影响草稿（审核界面用）。"""
    if not is_redis_available():
        return []
    drafts = []
    for key in get_redis_client().scan_iter(
        "event_impacts:draft:*", count=100
    ):
        raw = get_redis_client().get(key)
        if raw is None:
            continue
        try:
            drafts.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    drafts.sort(key=lambda d: d.get("t0", ""), reverse=True)
    return drafts
