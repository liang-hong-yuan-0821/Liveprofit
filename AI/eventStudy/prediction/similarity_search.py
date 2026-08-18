"""
相似事件检索（方案 3.7）

pgvector 余弦距离检索历史已通过事件（events.embedding），
跨模板语义相似事件补充预测样本。

- 相似度 = 1 - 余弦距离；低于 0.5 不纳入（B6）
- 事件量数千条级暂不建 HNSW 索引，全量余弦查询（毫秒级，3.7.2）
- 回测必须过滤 announced_at < 事件自身时间，防止未来信息泄漏（3.7.3）
"""

import logging

logger = logging.getLogger(__name__)


def _vec_literal(embedding: list) -> str:
    """向量转 pgvector 字面量（浮点数列表，无注入风险）。"""
    return "[" + ",".join(str(float(x)) for x in embedding) + "]"


def search_similar_events(
    conn,
    query_embedding: list,
    top_k: int = 10,
    min_similarity: float = 0.5,
    before_ts=None,
) -> list[dict]:
    """检索语义相似的历史已通过事件。

    Args:
        conn: PG 连接
        query_embedding: 查询文本向量（与 events.embedding 同维度）
        top_k: 返回上限
        min_similarity: 相似度下限（低于该值不纳入）
        before_ts: 回测用——只检索该时间之前公布的事件（防前视偏差）

    Returns:
        [{event_id, title, event_type, event_subtype, event_condition,
          announced_at, importance, similarity}]，按相似度降序
    """
    if not query_embedding:
        return []
    vec = _vec_literal(query_embedding)
    conds = [
        "status = 'approved'",
        "embedding IS NOT NULL",
    ]
    params = []
    if before_ts is not None:
        conds.append("announced_at < %s::timestamptz")
        params.append(before_ts)
    where = " AND ".join(conds)
    sql = f"""
        SELECT event_id, title, event_type, event_subtype, event_condition,
               announced_at, importance,
               1 - (embedding <=> '{vec}'::vector) AS similarity
        FROM events
        WHERE {where}
        ORDER BY embedding <=> '{vec}'::vector
        LIMIT %s
    """
    params.append(top_k)
    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception as e:
        logger.error(f"相似事件检索失败: {e}")
        return []
    results = []
    for r in rows:
        similarity = float(r[7])
        if similarity < min_similarity:
            continue
        results.append({
            "event_id": r[0],
            "title": r[1],
            "event_type": r[2],
            "event_subtype": r[3],
            "event_condition": r[4],
            "announced_at": r[5],
            "importance": r[6],
            "similarity": round(similarity, 4),
        })
    return results
