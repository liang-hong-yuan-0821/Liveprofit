"""持久新闻与事件判断版本仓储。

写入函数接受调用方事务连接并且不 commit/rollback。每日研究阶段可以复用同一
PostgreSQL 连接，把 assessment/event 业务结果与所属任务的状态更新原子提交。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID


class AssessmentRevisionConflict(RuntimeError):
    """事实判断已被其他提交修订，当前分析须重新核对。"""


@dataclass(frozen=True)
class NewsWriteResult:
    news_id: UUID
    source_revision: int
    content_hash: str
    inserted: bool


@dataclass(frozen=True)
class AssessmentWriteResult:
    assessment_id: UUID
    event_id: int
    fact_key: str
    revision: int
    review_status: str
    inserted: bool


def _json_value(value: Any) -> str:
    """确保 JSONB 参数只含 JSON 基本类型。"""
    return json.dumps(json.loads(json.dumps(value, ensure_ascii=False, default=str)), ensure_ascii=False)


def _as_datetime(value: datetime | str | None, *, default_now: bool = False) -> datetime | None:
    if value is None:
        return datetime.now(timezone.utc) if default_now else None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("时间戳必须包含时区")
    return value


def content_hash(title: str, raw_content: str) -> str:
    """用规范 UTF-8 内容计算来源版本身份，不因 URL 查询参数变化而改变。"""
    body = f"{title.strip()}\n{raw_content.strip()}".encode()
    return hashlib.sha256(body).hexdigest()


def persist_news(conn, item: dict[str, Any], *, first_seen_at: datetime | None = None) -> NewsWriteResult:
    """将一份来源原文写成不可变版本；同版本返回原 ID，同来源改文追加 revision。"""
    # source_key 是数据库身份；展示名可以调整，不应因此制造一个新来源。
    source = str(item.get("source_key") or item.get("source") or "").strip()
    title = str(item.get("title") or "").strip()
    raw_content = str(item.get("raw_content", item.get("content", "")) or "").strip()
    source_url = str(item.get("source_url") or "").strip() or None
    if not source or not title:
        raise ValueError("新闻来源和标题不能为空")

    digest = content_hash(title, raw_content)
    source_item_id = str(item.get("source_item_id") or source_url or f"content:{digest}").strip()
    if not source_item_id:
        source_item_id = f"content:{digest}"
    published_at = _as_datetime(item.get("published_at", item.get("announced_at")))
    first_seen = _as_datetime(first_seen_at, default_now=True)
    source_payload = dict(item.get("source_payload") or {})
    if item.get("source") and item.get("source_key"):
        source_payload.setdefault("source_label", str(item["source"]))
    payload = _json_value(source_payload)

    # 同一来源 ID 的相同内容由事务级 advisory lock 串行，异文按接收顺序分版本。
    lock_key = f"news:{source}:{source_item_id}"
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (lock_key,))
    existing = conn.execute(
        "SELECT news_id, source_revision FROM news "
        "WHERE source = %s AND source_item_id = %s AND content_hash = %s",
        (source, source_item_id, digest),
    ).fetchone()
    if existing:
        return NewsWriteResult(existing[0], int(existing[1]), digest, False)

    current = conn.execute(
        "SELECT COALESCE(MAX(source_revision), 0) FROM news "
        "WHERE source = %s AND source_item_id = %s",
        (source, source_item_id),
    ).fetchone()
    revision = int(current[0]) + 1
    row = conn.execute(
        "INSERT INTO news "
        "(source, source_item_id, source_revision, content_hash, published_at, first_seen_at, "
        " title, raw_content, source_url, source_payload) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
        "ON CONFLICT (source, source_item_id, content_hash) DO NOTHING "
        "RETURNING news_id, source_revision",
        (source, source_item_id, revision, digest, published_at, first_seen,
         title, raw_content, source_url, payload),
    ).fetchone()
    if row:
        return NewsWriteResult(row[0], int(row[1]), digest, True)

    # 守住唯一约束并发边界；正常情况下同一键已被上面的 advisory lock 串行。
    row = conn.execute(
        "SELECT news_id, source_revision FROM news "
        "WHERE source = %s AND source_item_id = %s AND content_hash = %s",
        (source, source_item_id, digest),
    ).fetchone()
    if not row:
        raise RuntimeError("新闻版本唯一键冲突后未找到既有记录")
    return NewsWriteResult(row[0], int(row[1]), digest, False)


def get_or_create_event(conn, *, canonical_key: str, title: str, content: str,
                        announced_at: datetime | str, source_url: str | None = None,
                        event_type: str | None = None, event_subtype: str | None = None,
                        event_condition: str | None = None, importance: int = 3,
                        event_scope: str = "market", affected_scope_refs: list[str] | None = None,
                        first_seen_at: datetime | None = None,
                        status: str = "approved") -> tuple[int, bool, str, str]:
    """在确定性归并键锁下创建或读取正式事件身份；不覆盖已有事件投影。

    返回 event_id、是否新建、当前审核状态与创建来源，供调用方避免覆盖人工忽略。
    """
    key = str(canonical_key or "").strip()
    if not key:
        raise ValueError("canonical_key 不能为空")
    if event_scope not in {"market", "sector", "stock"}:
        raise ValueError("event_scope 非法")
    refs = affected_scope_refs or []
    if event_scope == "market" and refs:
        raise ValueError("market 事件不允许带目标引用")
    if event_scope != "market" and not refs:
        raise ValueError("sector/stock 事件至少需要一个目标引用")
    if status not in {"approved", "pending"}:
        raise ValueError("自动事件状态只能是 approved 或 pending")

    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"event:{key}",))
    existing = conn.execute(
        "SELECT event_id, status, review_origin FROM events WHERE canonical_key = %s", (key,)
    ).fetchone()
    if existing:
        return int(existing[0]), False, str(existing[1]), str(existing[2])

    row = conn.execute(
        "INSERT INTO events (title, content, event_type, event_subtype, event_condition, "
        "announced_at, importance, status, source_url, canonical_key, review_origin, "
        "first_seen_at, event_scope, affected_scope_refs) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'ai', %s, %s, %s::jsonb) "
        "ON CONFLICT (canonical_key) DO NOTHING RETURNING event_id, status, review_origin",
        (title, content, event_type, event_subtype, event_condition,
         _as_datetime(announced_at), max(1, min(int(importance), 5)), status, source_url,
         key, _as_datetime(first_seen_at, default_now=True), event_scope,
         _json_value(refs)),
    ).fetchone()
    if row:
        return int(row[0]), True, str(row[1]), str(row[2])
    existing = conn.execute(
        "SELECT event_id, status, review_origin FROM events WHERE canonical_key = %s", (key,)
    ).fetchone()
    if not existing:
        raise RuntimeError("事件身份唯一键冲突后未找到既有事件")
    return int(existing[0]), False, str(existing[1]), str(existing[2])


def update_event_projection(conn, *, event_id: int, title: str, content: str,
                            event_type: str | None, event_subtype: str | None,
                            event_condition: str | None, importance: int,
                            source_url: str | None, event_scope: str,
                            affected_scope_refs: list[str], expected_value: float | None,
                            actual_value: float | None, previous_value: float | None,
                            status: str, review_origin: str = "ai",
                            embedding: list[float] | None = None) -> None:
    """仅将 accepted assessment 投影到 legacy events 当前视图，不改历史判断版本。"""
    if status != "accepted":
        return
    if review_origin not in {"ai", "human"}:
        raise ValueError("review_origin 非法")
    conn.execute(
        "UPDATE events SET title = %s, content = %s, event_type = %s, event_subtype = %s, "
        "event_condition = %s, importance = %s, source_url = %s, event_scope = %s, "
        "affected_scope_refs = %s::jsonb, expected_value = %s, actual_value = %s, "
        "previous_value = %s, embedding = %s::vector, status = 'approved', "
        "review_origin = %s, updated_at = now() "
        "WHERE event_id = %s AND merged_into_event_id IS NULL",
        (title, content, event_type, event_subtype, event_condition,
         max(1, min(int(importance), 5)), source_url, event_scope,
         _json_value(affected_scope_refs), expected_value, actual_value,
         previous_value,
         "[" + ",".join(f"{float(value):.8f}" for value in embedding) + "]" if embedding else None,
         review_origin, int(event_id)),
    )


def refresh_event_projection_status(conn, *, event_id: int) -> None:
    """Keep legacy readers aligned with the latest effective assessment versions.

    A disputed/rejected version does not erase a prior accepted fact. A retraction
    does. If an AI-owned event has no remaining accepted facts, mark its compatibility
    projection ignored so legacy event-study readers cannot keep using it.
    """
    accepted = conn.execute(
        "WITH effective AS ("
        " SELECT DISTINCT ON (fact_key) assessment_id, news_id, fact_key, labels, available_at "
        " FROM event_assessment WHERE event_id = %s AND review_status IN ('accepted', 'retracted') "
        " ORDER BY fact_key, revision DESC, available_at DESC, assessment_id DESC"
        ") SELECT e.assessment_id, e.news_id, e.labels, n.source_url, ea.embedding "
        " FROM effective e JOIN event_assessment ea ON ea.assessment_id = e.assessment_id "
        " JOIN news n ON n.news_id = e.news_id "
        " WHERE ea.review_status = 'accepted' "
        " ORDER BY e.available_at DESC, e.assessment_id DESC LIMIT 1",
        (int(event_id),),
    ).fetchone()
    if accepted is None:
        conn.execute(
            "UPDATE events SET status = 'ignored', updated_at = now() "
            "WHERE event_id = %s",
            (int(event_id),),
        )
        return
    labels = accepted[2] if isinstance(accepted[2], dict) else {}
    fact = labels.get("fact") if isinstance(labels.get("fact"), dict) else {}
    title = str(fact.get("title") or fact.get("fact_summary") or "事件事实")
    targets = labels.get("targets") if isinstance(labels.get("targets"), list) else []
    if not targets:
        return
    scope = str(targets[0].get("scope") or "market")
    refs = sorted({str(ref) for target in targets for ref in target.get("scope_refs", [])})
    conn.execute(
        "UPDATE events SET title = %s, content = %s, event_type = %s, event_subtype = %s, "
        "event_condition = %s, importance = %s, source_url = %s, event_scope = %s, "
        "affected_scope_refs = %s::jsonb, expected_value = %s, actual_value = %s, "
        "previous_value = %s, embedding = %s::vector, status = 'approved', updated_at = now() "
        "WHERE event_id = %s AND merged_into_event_id IS NULL",
        (title[:512], fact.get("fact_summary") or title, fact.get("event_type"),
         fact.get("event_subtype"), fact.get("event_condition"),
         max(1, min(int(fact.get("importance") or 3), 5)), accepted[3], scope,
         _json_value(refs), fact.get("expected_value"), fact.get("actual_value"),
         fact.get("previous_value"),
         "[" + ",".join(f"{float(value):.8f}" for value in accepted[4]) + "]"
         if accepted[4] is not None else None, int(event_id)),
    )


def list_event_candidates(conn, *, as_of: datetime, news_cutoff_at: datetime | None = None,
                          lookback_days: int = 90, limit: int = 50,
                          include_disputed: bool = False,
                          query_embedding: list[float] | None = None) -> list[dict[str, Any]]:
    """Return frozen assessment snapshots for prediction or novelty candidate recall.

    Only accepted/retracted versions participate in effective history. A later
    disputed/rejected version remains auditable but does not displace an accepted
    assessment. With ``include_disputed``, the latest unresolved fact is included
    only when no effective accepted version exists.
    """
    if lookback_days <= 0 or limit <= 0:
        raise ValueError("lookback_days 与 limit 必须为正")
    cutoff = _as_datetime(news_cutoff_at or as_of)
    query_vector = None
    if query_embedding:
        if len(query_embedding) != 1024:
            raise ValueError("event_assessment 向量必须为 1024 维")
        query_vector = "[" + ",".join(f"{float(value):.8f}" for value in query_embedding) + "]"
    disputed_cte = (
        " , disputed AS ("
        " SELECT ea.assessment_id, ea.event_id, ea.fact_key, ea.revision, ea.novelty, "
        " ea.review_status, ea.labels, ea.evidence, ea.available_at, ea.news_id, ea.embedding, "
        " ea.embedding_available_at, "
        " row_number() OVER (PARTITION BY ea.event_id, ea.fact_key "
        "   ORDER BY ea.revision DESC, ea.available_at DESC, ea.assessment_id DESC) AS rn "
        " FROM event_assessment ea JOIN news n ON n.news_id = ea.news_id "
        " WHERE ea.review_status = 'disputed' AND ea.available_at <= %s "
        " AND n.first_seen_at <= %s AND (n.published_at IS NULL OR n.published_at <= %s)"
        " ), combined AS ("
        " SELECT * FROM ranked WHERE rn = 1 AND review_status = 'accepted' "
        " UNION ALL "
        " SELECT d.* FROM disputed d WHERE d.rn = 1 AND NOT EXISTS ("
        "   SELECT 1 FROM ranked a WHERE a.event_id = d.event_id AND a.fact_key = d.fact_key "
        "   AND a.rn = 1 AND a.review_status = 'accepted'"
        " ) AND NOT EXISTS ("
        "   SELECT 1 FROM event_assessment later WHERE later.event_id = d.event_id "
        "   AND later.fact_key = d.fact_key AND later.revision > d.revision "
        "   AND later.review_status IN ('accepted', 'retracted')"
        " )"
        " )"
    ) if include_disputed else ""
    latest_filter = "r.review_status IN ('accepted', 'disputed')" if include_disputed else "r.review_status = 'accepted'"
    rank_statuses = "('accepted', 'retracted')"
    distance_order = (
        "(CASE WHEN r.has_embedding THEN r.embedding <=> %s::vector END) ASC NULLS LAST, "
        if query_vector else ""
    )
    params = [_as_datetime(as_of), cutoff, cutoff]
    if include_disputed:
        params.extend([_as_datetime(as_of), cutoff, cutoff])
    # Backfilled vectors are only usable from their own availability time.
    params.append(_as_datetime(as_of))
    params.extend([_as_datetime(as_of), int(lookback_days)])
    if query_vector:
        params.append(query_vector)
    params.append(_as_datetime(as_of))
    if query_vector:
        params.append(query_vector)
    params.append(min(int(limit), 1000))
    rows = conn.execute(
        "WITH ranked AS ("
        " SELECT ea.assessment_id, ea.event_id, ea.fact_key, ea.revision, ea.novelty, ea.review_status, "
        " ea.labels, ea.evidence, ea.available_at, ea.news_id, ea.embedding, ea.embedding_available_at, "
        " row_number() OVER (PARTITION BY ea.event_id, ea.fact_key "
        "   ORDER BY ea.revision DESC, ea.available_at DESC, ea.assessment_id DESC) AS rn "
        " FROM event_assessment ea JOIN news n ON n.news_id = ea.news_id "
        f" WHERE ea.review_status IN {rank_statuses} AND ea.available_at <= %s AND n.first_seen_at <= %s "
        " AND (n.published_at IS NULL OR n.published_at <= %s)"
        ") "
        + disputed_cte +
        ", effective AS ("
        " SELECT r.*, COALESCE(r.embedding IS NOT NULL AND r.embedding_available_at <= %s, FALSE) AS has_embedding, "
        " n.title AS source_title, n.raw_content AS source_content, "
        " n.published_at, n.first_seen_at, n.source_url "
        " FROM " + ("combined" if include_disputed else "ranked") + " r "
        " JOIN news n ON n.news_id = r.news_id "
        f" WHERE r.rn = 1 AND {latest_filter} "
        " AND r.available_at >= %s - (%s * INTERVAL '1 day')"
        "), counted AS (SELECT effective.*, count(*) OVER () AS total_count FROM effective) "
        "SELECT r.assessment_id, r.event_id, e.canonical_key, r.fact_key, r.revision, r.novelty, "
        " r.source_title, r.source_content, COALESCE(NULLIF(r.labels #>> '{fact,first_published_at}', ''), "
        " r.published_at::text, r.first_seen_at::text) AS announced_at, "
        " COALESCE(NULLIF(r.labels #>> '{fact,event_type}', ''), '') AS event_type, "
        " r.labels, r.evidence, "
        " COALESCE(NULLIF(r.labels #>> '{fact,title}', ''), NULLIF(r.labels #>> '{fact,fact_summary}', ''), r.source_title) "
        " AS event_title, COALESCE(NULLIF(r.labels #>> '{fact,fact_summary}', ''), r.source_content) AS event_content, "
        " r.available_at, r.total_count, r.has_embedding, "
        + ("CASE WHEN r.has_embedding THEN 1 - (r.embedding <=> %s::vector) ELSE NULL END AS vector_similarity "
           if query_vector else "NULL::double precision AS vector_similarity ")
        + " FROM counted r JOIN events e ON e.event_id = r.event_id "
        + " WHERE r.available_at <= %s "
        + " ORDER BY " + distance_order + " r.available_at DESC, r.event_id DESC, r.fact_key "
        + " LIMIT %s",
        tuple(params),
    ).fetchall()
    columns = ("assessment_id", "event_id", "canonical_key", "fact_key", "revision", "novelty",
               "title", "content", "announced_at", "event_type", "labels", "evidence",
               "event_title", "event_content", "available_at", "total_count", "has_embedding",
               "vector_similarity")
    return [dict(zip(columns, row)) for row in rows]


def count_unassessed_legacy_events(conn, *, as_of: datetime, lookback_days: int = 90) -> int:
    """Count recent approved legacy events with no structured assessment.

    Old first-seen times remain unknown; this count is disclosure only and such rows
    are never fabricated into historical scored facts.
    """
    cutoff = _as_datetime(as_of)
    row = conn.execute(
        "SELECT count(*) FROM events e WHERE "
        "e.status = 'approved' AND e.merged_into_event_id IS NULL "
        "AND e.announced_at <= %s AND e.announced_at >= %s - (%s * INTERVAL '1 day') "
        "AND (e.first_seen_at IS NULL OR e.first_seen_at <= %s) "
        "AND NOT EXISTS (SELECT 1 FROM event_assessment ea WHERE ea.event_id = e.event_id)",
        (cutoff, cutoff, int(lookback_days), cutoff),
    ).fetchone()
    return int(row[0] if row else 0)


def list_unprocessed_news(conn, *, as_of: datetime, limit: int = 500) -> list[dict[str, Any]]:
    """返回截至时点尚未形成正式判断或终结处理结果的原文版本。

    accepted/disputed assessments 是业务事实；只有跳过的转载/无关结果通过
    analysis_reports 中的批次处理结果去重。没有正式 assessment 的争议仍可重试。
    """
    if limit <= 0:
        raise ValueError("limit 必须为正")
    cutoff = _as_datetime(as_of)
    rows = conn.execute(
        "SELECT n.news_id, n.source, n.source_item_id, n.source_revision, n.content_hash, "
        "n.published_at, n.first_seen_at, n.title, n.raw_content, n.source_url, "
        "n.source_payload->>'source_label' AS source_label "
        "FROM news n "
        "WHERE n.first_seen_at <= %s AND (n.published_at IS NULL OR n.published_at <= %s) "
        "AND NOT EXISTS (SELECT 1 FROM event_assessment ea WHERE ea.news_id = n.news_id) "
        "AND NOT EXISTS ("
        " SELECT 1 FROM analysis_reports ar, LATERAL jsonb_array_elements("
        "   COALESCE(ar.report_json #> '{daily_research,news_results}', '[]'::jsonb)"
        " ) result "
        " WHERE result->>'news_id' = n.news_id::text "
        " AND result->>'status' IN ('accepted', 'skipped')"
        ") "
        "ORDER BY n.first_seen_at, n.news_id LIMIT %s",
        (cutoff, cutoff, min(int(limit), 5000)),
    ).fetchall()
    columns = ("news_id", "source", "source_item_id", "source_revision", "content_hash",
               "published_at", "first_seen_at", "title", "raw_content", "source_url", "source_label")
    return [dict(zip(columns, row)) for row in rows]


def count_unprocessed_news(conn, *, as_of: datetime) -> int:
    """Count the eligible backlog without loading full article bodies."""
    cutoff = _as_datetime(as_of)
    row = conn.execute(
        "SELECT count(*) FROM news n "
        "WHERE n.first_seen_at <= %s AND (n.published_at IS NULL OR n.published_at <= %s) "
        "AND NOT EXISTS (SELECT 1 FROM event_assessment ea WHERE ea.news_id = n.news_id) "
        "AND NOT EXISTS ("
        " SELECT 1 FROM analysis_reports ar, LATERAL jsonb_array_elements("
        "   COALESCE(ar.report_json #> '{daily_research,news_results}', '[]'::jsonb)"
        " ) result "
        " WHERE result->>'news_id' = n.news_id::text "
        " AND result->>'status' IN ('accepted', 'skipped')"
        ")",
        (cutoff, cutoff),
    ).fetchone()
    return int(row[0] if row else 0)


def append_assessment(conn, *, news_id: UUID | str, event_id: int, fact_key: str,
                      novelty: str, review_status: str, labels: dict[str, Any],
                      evidence: list[dict[str, Any]], model_version: str,
                      prompt_version: str, operation_id: str, task_id: UUID | str | None = None,
                      available_at: datetime | None = None,
                      expected_revision: int | None = None,
                      embedding: list[float] | None = None,
                      embedding_model: str | None = None,
                      embedding_text_hash: str | None = None) -> AssessmentWriteResult:
    """追加不可变的事实判断版本；幂等键与并发版本由 PostgreSQL 保证。"""
    if novelty not in {"new", "update"}:
        raise ValueError("正式 assessment 的 novelty 必须是 new 或 update")
    if review_status not in {"accepted", "disputed", "rejected", "retracted"}:
        raise ValueError("review_status 非法")
    key = str(fact_key or "").strip()
    operation = str(operation_id or "").strip()
    if not key or not operation:
        raise ValueError("fact_key 和 operation_id 不能为空")
    if embedding is not None and len(embedding) != 1024:
        raise ValueError("event_assessment embedding 必须为 1024 维")
    if embedding is not None and not embedding_model:
        raise ValueError("写入 embedding 时必须标记模型版本")

    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"assessment:{int(event_id)}:{key}",),
    )
    existing = conn.execute(
        "SELECT assessment_id, revision, review_status FROM event_assessment "
        "WHERE operation_id = %s AND event_id = %s AND fact_key = %s",
        (operation, int(event_id), key),
    ).fetchone()
    if existing:
        return AssessmentWriteResult(existing[0], int(event_id), key,
                                      int(existing[1]), existing[2], False)

    latest = conn.execute(
        "SELECT assessment_id, revision FROM event_assessment "
        "WHERE event_id = %s AND fact_key = %s ORDER BY revision DESC LIMIT 1 FOR UPDATE",
        (int(event_id), key),
    ).fetchone()
    latest_revision = int(latest[1]) if latest else 0
    if expected_revision is not None and latest_revision != expected_revision:
        raise AssessmentRevisionConflict(
            f"判断版本已变化：期望 {expected_revision}，当前 {latest_revision}"
        )
    revision = latest_revision + 1
    supersedes_id = latest[0] if latest else None
    available = _as_datetime(available_at, default_now=True)
    labels_json = _json_value(labels)
    vector_value = (
        "[" + ",".join(f"{float(value):.8f}" for value in embedding) + "]"
        if embedding is not None else None
    )
    text_hash = embedding_text_hash or hashlib.sha256(labels_json.encode("utf-8")).hexdigest()
    row = conn.execute(
        "INSERT INTO event_assessment (news_id, event_id, fact_key, revision, supersedes_id, "
        "novelty, review_status, labels, evidence, available_at, model_version, prompt_version, "
        "operation_id, task_id, text_hash, embedding, embedding_model, embedding_available_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, "
        "%s, %s::vector, %s, %s) "
        "ON CONFLICT (operation_id, event_id, fact_key) DO NOTHING "
        "RETURNING assessment_id, revision, review_status",
        (str(news_id), int(event_id), key, revision, str(supersedes_id) if supersedes_id else None,
         novelty, review_status, labels_json, _json_value(evidence),
         available, model_version, prompt_version, operation, str(task_id) if task_id else None,
         text_hash, vector_value, embedding_model if vector_value else None,
         available if vector_value else None),
    ).fetchone()
    if row:
        return AssessmentWriteResult(row[0], int(event_id), key, int(row[1]), row[2], True)

    existing = conn.execute(
        "SELECT assessment_id, revision, review_status FROM event_assessment "
        "WHERE operation_id = %s AND event_id = %s AND fact_key = %s",
        (operation, int(event_id), key),
    ).fetchone()
    if not existing:
        raise AssessmentRevisionConflict("事实判断并发修订冲突")
    return AssessmentWriteResult(existing[0], int(event_id), key,
                                 int(existing[1]), existing[2], False)


def list_latest_assessments(conn, *, as_of: datetime, event_id: int | None = None,
                            accepted_only: bool = False) -> list[dict[str, Any]]:
    """返回时点内每个 event/fact 最新判断；先选最新版本再过滤状态，防止旧版复活。"""
    params: list[Any] = [_as_datetime(as_of)]
    event_clause = ""
    if event_id is not None:
        event_clause = "event_id = %s"
        params.append(int(event_id))
    if accepted_only:
        event_clause = f"{event_clause} AND review_status = 'accepted'" if event_clause else "review_status = 'accepted'"
    where_clause = f"WHERE {event_clause}" if event_clause else ""
    rows = conn.execute(
        "WITH latest AS ("
        " SELECT DISTINCT ON (event_id, fact_key) assessment_id, news_id, event_id, fact_key, "
        " revision, supersedes_id, novelty, review_status, labels, evidence, available_at, "
        " model_version, prompt_version, operation_id "
        " FROM event_assessment WHERE available_at <= %s "
        " ORDER BY event_id, fact_key, revision DESC, available_at DESC, assessment_id DESC"
        ") SELECT assessment_id, news_id, event_id, fact_key, revision, supersedes_id, novelty, "
        "review_status, labels, evidence, available_at, model_version, prompt_version, operation_id "
        f"FROM latest {where_clause} ORDER BY available_at DESC, event_id, fact_key",
        tuple(params),
    ).fetchall()
    columns = ("assessment_id", "news_id", "event_id", "fact_key", "revision", "supersedes_id",
               "novelty", "review_status", "labels", "evidence", "available_at",
               "model_version", "prompt_version", "operation_id")
    return [dict(zip(columns, row)) for row in rows]


def _review_assessment_columns() -> tuple[str, ...]:
    return (
        "assessment_id", "news_id", "event_id", "fact_key", "revision", "novelty",
        "review_status", "labels", "evidence", "available_at", "title", "content",
        "source", "source_label", "source_url", "announced_at", "event_type",
        "event_subtype", "event_condition", "importance", "event_scope",
        "affected_scope_refs", "event_title", "event_content", "task_id",
    )


def _review_assessment(row) -> dict[str, Any] | None:
    return dict(zip(_review_assessment_columns(), row)) if row else None


def list_disputed_assessments(conn, *, limit: int = 100) -> list[dict[str, Any]]:
    """Return only the latest unresolved assessment for each event fact."""
    if limit <= 0:
        raise ValueError("limit 必须为正")
    rows = conn.execute(
        "WITH latest AS ("
        " SELECT DISTINCT ON (event_id, fact_key) assessment_id, news_id, event_id, fact_key, "
        " revision, novelty, review_status, labels, evidence, available_at, task_id "
        " FROM event_assessment "
        " ORDER BY event_id, fact_key, revision DESC, available_at DESC, assessment_id DESC"
        ") SELECT l.assessment_id, l.news_id, l.event_id, l.fact_key, l.revision, l.novelty, "
        " l.review_status, l.labels, l.evidence, l.available_at, n.title, n.raw_content, "
        " n.source, n.source_payload->>'source_label', n.source_url, e.announced_at, "
        " e.event_type, e.event_subtype, e.event_condition, e.importance, e.event_scope, "
        " e.affected_scope_refs, e.title, e.content, l.task_id "
        " FROM latest l JOIN news n ON n.news_id = l.news_id "
        " JOIN events e ON e.event_id = l.event_id "
        " WHERE l.review_status = 'disputed' AND e.merged_into_event_id IS NULL "
        " ORDER BY l.available_at DESC, l.event_id, l.fact_key LIMIT %s",
        (min(int(limit), 500),),
    ).fetchall()
    return [_review_assessment(row) for row in rows]


def get_assessment_for_review(conn, assessment_id: UUID | str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT ea.assessment_id, ea.news_id, ea.event_id, ea.fact_key, ea.revision, "
        " ea.novelty, ea.review_status, ea.labels, ea.evidence, ea.available_at, "
        " n.title, n.raw_content, n.source, n.source_payload->>'source_label', n.source_url, "
        " e.announced_at, e.event_type, e.event_subtype, e.event_condition, e.importance, "
        " e.event_scope, e.affected_scope_refs, e.title, e.content, ea.task_id "
        " FROM event_assessment ea JOIN news n ON n.news_id = ea.news_id "
        " JOIN events e ON e.event_id = ea.event_id WHERE ea.assessment_id = %s",
        (str(assessment_id),),
    ).fetchone()
    return _review_assessment(row)


def get_latest_assessment_for_fact(conn, *, event_id: int, fact_key: str,
                                   for_update: bool = False) -> dict[str, Any] | None:
    lock = " FOR UPDATE" if for_update else ""
    row = conn.execute(
        "SELECT ea.assessment_id, ea.news_id, ea.event_id, ea.fact_key, ea.revision, "
        " ea.novelty, ea.review_status, ea.labels, ea.evidence, ea.available_at, "
        " n.title, n.raw_content, n.source, n.source_payload->>'source_label', n.source_url, "
        " e.announced_at, e.event_type, e.event_subtype, e.event_condition, e.importance, "
        " e.event_scope, e.affected_scope_refs, e.title, e.content, ea.task_id "
        " FROM event_assessment ea JOIN news n ON n.news_id = ea.news_id "
        " JOIN events e ON e.event_id = ea.event_id "
        " WHERE ea.event_id = %s AND ea.fact_key = %s "
        " ORDER BY ea.revision DESC, ea.available_at DESC, ea.assessment_id DESC LIMIT 1" + lock,
        (int(event_id), str(fact_key)),
    ).fetchone()
    return _review_assessment(row)


def get_assessment_by_operation(conn, *, operation_id: str, event_id: int,
                                fact_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT assessment_id FROM event_assessment "
        "WHERE operation_id = %s AND event_id = %s AND fact_key = %s",
        (operation_id, int(event_id), str(fact_key)),
    ).fetchone()
    return get_assessment_for_review(conn, row[0]) if row else None
