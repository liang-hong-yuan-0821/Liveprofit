"""
审核模块数据访问层（方案 3.2）

- 待审核草稿存 Redis（events:pending:{draft_id}），审核通过/忽略后写入 PG
- 影响结果草稿存 Redis（event_impacts:draft:{event_id}），人工勾选后落表 PG
- 审核日志写入 Redis 全局列表 event_review_log（LPUSH JSON，C2）
"""

import json
import logging
from datetime import datetime

from AI.eventStudy.collectors.config import (
    KEY_PENDING_EVENT, KEY_REVIEW_LOG, get_redis_client, is_redis_available,
)
from AI.eventStudy.processing import impact_writer

logger = logging.getLogger(__name__)


# ==================== 审核日志 ====================

def _log_review(event_ref, operator: str, action: str, changed_fields: dict = None):
    """LPUSH 审核动作 JSON 到 event_review_log（仅追溯保险，不建审计表）。"""
    if not is_redis_available():
        return
    entry = {
        "event_ref": str(event_ref),
        "operator": operator,
        "time": datetime.now().isoformat(),
        "action": action,
        "changed_fields": changed_fields or {},
    }
    try:
        get_redis_client().lpush(KEY_REVIEW_LOG, json.dumps(entry, ensure_ascii=False))
    except Exception as e:
        logger.warning(f"审核日志写入失败: {e}")


# ==================== 待审核事件 ====================

def get_pending_events() -> list[dict]:
    """从 Redis 读取待审核事件草稿（3.2.1 接口）。"""
    if not is_redis_available():
        return []
    events = []
    for key in get_redis_client().scan_iter("events:pending:*", count=100):
        raw = get_redis_client().get(key)
        if raw is None:
            continue
        try:
            draft = json.loads(raw)
            draft["draft_id"] = int(key.split(":")[-1])
            events.append(draft)
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    events.sort(key=lambda e: e.get("announced_at", ""), reverse=True)
    return events


def get_pending_event(draft_id: int):
    """读取单条待审核草稿。"""
    if not is_redis_available():
        return None
    raw = get_redis_client().get(KEY_PENDING_EVENT.format(draft_id=draft_id))
    if raw is None:
        return None
    try:
        draft = json.loads(raw)
        draft["draft_id"] = draft_id
        return draft
    except json.JSONDecodeError:
        return None


def _insert_event(conn, draft: dict, review: dict, status: str) -> int:
    """将草稿 + 审核字段写入 PG events 表，返回新 event_id。"""
    # 注意：数值 0 是合法值（如 CPI 环比 0.0），不能用 `or` 兜底
    actual = review.get("actual_value")
    if actual is None:
        actual = draft.get("actual_value")
    previous = review.get("previous_value")
    if previous is None:
        previous = draft.get("previous_value")
    row = conn.execute(
        """
        INSERT INTO events (
            title, content, event_type, event_subtype, event_condition,
            announced_at, expected_value, actual_value, previous_value,
            importance, status, source_url
        )
        VALUES (%s, %s, %s, %s, %s, %s::timestamptz, %s, %s, %s, %s, %s, %s)
        RETURNING event_id
        """,
        (
            draft.get("title", ""),
            draft.get("content") or "",
            review.get("event_type") or None,
            review.get("event_subtype") or None,
            review.get("event_condition") or None,
            draft.get("announced_at"),
            review.get("expected_value"),
            actual,
            previous,
            review.get("importance", draft.get("importance_hint") or 3),
            status,
            draft.get("source_url") or None,
        ),
    ).fetchone()
    conn.commit()
    return int(row[0])


def _delete_draft(draft_id: int):
    if is_redis_available():
        get_redis_client().delete(KEY_PENDING_EVENT.format(draft_id=draft_id))


def approve_event(conn, draft_id: int, review_fields: dict, operator: str = "admin") -> int:
    """审核通过：写入 PG（status='approved'），删除 Redis 草稿，记日志（3.2.1）。"""
    draft = get_pending_event(draft_id)
    if draft is None:
        raise ValueError(f"待审草稿不存在或已过期: {draft_id}")
    event_id = _insert_event(conn, draft, review_fields, "approved")
    _delete_draft(draft_id)
    _log_review(draft_id, operator, "approve", review_fields)
    logger.info(f"事件审核通过: draft={draft_id} → event_id={event_id}")
    return event_id


def ignore_event(conn, draft_id: int, operator: str = "admin") -> int:
    """忽略事件：写入 PG（status='ignored'，用于爬虫去重），删除草稿，记日志。"""
    draft = get_pending_event(draft_id)
    if draft is None:
        raise ValueError(f"待审草稿不存在或已过期: {draft_id}")
    event_id = _insert_event(conn, draft, {}, "ignored")
    _delete_draft(draft_id)
    _log_review(draft_id, operator, "ignore")
    logger.info(f"事件已忽略: draft={draft_id} → event_id={event_id}")
    return event_id


# ==================== 影响结果确认 ====================

def get_impact_drafts() -> list[dict]:
    """列出全部影响结果草稿（审核界面读取展示）。"""
    return impact_writer.list_impact_drafts()


def get_event_title(conn, event_id: int) -> str:
    row = conn.execute(
        "SELECT title FROM events WHERE event_id = %s", (event_id,)
    ).fetchone()
    return row[0] if row else f"事件 {event_id}"


def confirm_impacts(conn, event_id: int, selected_tickers: list,
                    operator: str = "admin") -> int:
    """人工勾选确认：仅将选中资产的窗口结果落表 PG event_impacts（B5）。

    Returns: 写入记录数。
    """
    draft = impact_writer.read_impact_draft(event_id)
    if draft is None:
        raise ValueError(f"影响草稿不存在或已过期: event_id={event_id}（可重算）")
    assets = draft.get("assets", {})
    count = 0
    records = []
    for ticker in selected_tickers:
        if ticker not in assets:
            logger.warning(f"草稿中无该资产结果: {ticker}")
            continue
        row = conn.execute(
            "SELECT asset_id FROM assets WHERE ticker = %s", (ticker,)
        ).fetchone()
        if row is None:
            logger.warning(f"资产未初始化: {ticker}")
            continue
        asset_id = row[0]
        for wt, r in assets[ticker].items():
            if r.get("error"):
                continue  # 计算失败的窗口不落表
            records.append((
                event_id, asset_id, wt,
                r.get("window_days"),
                r.get("cumulative_abnormal_return"),
                r.get("t_stat"),
                r.get("direction", 0),
                bool(r.get("is_contaminated", False)),
            ))
    if records:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO event_impacts (
                    event_id, asset_id, window_type, window_days,
                    cumulative_abnormal_return, t_stat, direction, is_contaminated
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_id, asset_id, window_type) DO NOTHING
                """,
                records,
            )
        count = len(records)
    conn.commit()
    impact_writer.delete_impact_draft(event_id)
    _log_review(event_id, operator, "confirm_impacts", {"assets": selected_tickers})
    logger.info(f"影响结果确认落表: event={event_id}，{count} 条记录")
    return count
