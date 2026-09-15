"""
相似事件检索（方案 3.7）

pgvector 余弦距离检索历史已通过事件（events.embedding），
跨模板语义相似事件补充预测样本。

- 相似度 = 1 - 余弦距离；低于 0.5 不纳入（B6）
- 事件量数千条级暂不建 HNSW 索引，全量余弦查询（毫秒级，3.7.2）
- 回测必须过滤 announced_at <= 事件自身时间，防止未来信息泄漏（3.7.3；
  边界包含，与 review_dao.list_approved_events_for_route 的 as_of 语义一致）
- 三级路由（方案第三章）：build_scope_filter 提供同作用域过滤谓词，与
  review_dao.list_approved_events_for_route 共用归一化与并集命中语义
"""

import logging
import re

from AI.eventStudy.review import review_dao

logger = logging.getLogger(__name__)

# 空路由（sector/stock 无有效目标引用）谓词：恒假 → 确定性返回零样本，
# 不落到全量事件（与 review_dao.list_approved_events_for_route 一致）
EMPTY_ROUTE_PREDICATE = "false"

# 引用字符串切分（与 review_dao._raw_ref_items 同规则：中英文逗号/分号/换行）
_RAW_REF_SPLIT_RE = re.compile(r"[,，;；\n]+")


def _raw_ref_items(value) -> list:
    """展开引用输入为原始项列表（与 review_dao 同规则，用于识别"部分非法"）。

    review_dao._raw_ref_items 为审核模块私有工具，此处本地镜像以保持预取/
    预测侧与 review_dao.build_scope_filter 的严格程度一致（非法引用不静默丢弃）。
    """
    if value is None:
        return []
    if isinstance(value, str):
        items = _RAW_REF_SPLIT_RE.split(value)
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = [value]
    return [i for i in items if str(i).strip()]


def build_scope_filter(scope=None, scope_refs=(), qualifier: str = "") -> tuple[str, list]:
    """同作用域过滤谓词（方案第三章「审核、查询与预取」）。

    返回 (SQL 谓词, 参数)：谓词为可直接放进 WHERE 的单个条件（不含 AND 前缀），
    调用方按 `if predicate: conds.append(predicate)` 组装。
    - scope 为 None/空 → ("", [])：不限定作用域（兼容旧调用；此时 scope_refs
      无路由语义，被忽略）
    - market → 命中 `event_scope = 'market'` 或历史 NULL 行（迁移前旧行只在市场层可见）
    - sector/stock → `event_scope` 精确匹配 + `affected_scope_refs ?| ARRAY[...]`
      （并集：命中任一目标引用即属该路由；单引用与包含查询等价。引用经同一
      归一化，可传裸代码；代码精确匹配不做前缀/模糊匹配）
    - sector/stock 无有效目标引用 → 空路由，返回 EMPTY_ROUTE_PREDICATE（零样本）

    作用域非法或引用格式非法时抛 ValueError（调用方编程错误，不静默降级为空样本）。
    qualifier：events 表别名（多表 JOIN 时传 "e"），单表查询留空。
    """
    resolved = review_dao.normalize_scope(scope)
    if resolved is None:
        if scope is None or not str(scope).strip():
            return "", []
        raise ValueError(
            f"作用域非法: {scope!r}（可选 {'/'.join(review_dao.SCOPE_VALUES)}）"
        )

    col = f"{qualifier}.event_scope" if qualifier else "event_scope"
    ref_col = f"{qualifier}.affected_scope_refs" if qualifier else "affected_scope_refs"
    if resolved == review_dao.SCOPE_MARKET:
        if _raw_ref_items(scope_refs):  # market 固定空引用（与审核侧组合校验一致）
            raise ValueError(f"market 作用域不允许目标引用: {scope_refs!r}")
        return f"({col} = %s OR {col} IS NULL)", [review_dao.SCOPE_MARKET]

    refs = review_dao.normalize_scope_refs(scope_refs)
    if len(refs) != len(_raw_ref_items(scope_refs)):
        raise ValueError(f"路由目标引用格式非法: {scope_refs!r}")
    if not refs:
        return EMPTY_ROUTE_PREDICATE, []
    return f"{col} = %s AND {ref_col} ?| %s::text[]", [resolved, refs]


def _vec_literal(embedding: list) -> str:
    """向量转 pgvector 字面量（浮点数列表，无注入风险）。"""
    return "[" + ",".join(str(float(x)) for x in embedding) + "]"


def search_similar_events(
    conn,
    query_embedding: list,
    top_k: int = 10,
    min_similarity: float = 0.5,
    before_ts=None,
    scope=None,
    scope_refs=(),
    errors: dict | None = None,
    exclude_event_id: int = None,
) -> list[dict]:
    """检索语义相似的历史已通过事件。

    Args:
        conn: PG 连接
        query_embedding: 查询文本向量（与 events.embedding 同维度）
        top_k: 返回上限
        min_similarity: 相似度下限（低于该值不纳入）
        before_ts: 回测用——只检索该时点（含）之前公布的事件（防前视偏差）
        scope / scope_refs: 三级路由同作用域过滤（None = 不限定作用域）
        errors: 可选错误收集器（通道名 → 原因）；查询异常时写入 "vector" 键，
            供上层区分 no_sample（无样本）与 api_unavailable（查询不可用）
        exclude_event_id: 排除的事件 ID（评审 m14）——预取按候选事件自身取
            历史统计时，候选事件已在库中（有 event_impacts 行），不排除会把
            「事件自身已实现的影响」当作历史样本（自相关污染，样本偏乐观）

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
        conds.append("announced_at <= %s::timestamptz")
        params.append(before_ts)
    if exclude_event_id is not None:
        conds.append("event_id != %s")
        params.append(exclude_event_id)
    predicate, scope_params = build_scope_filter(scope, scope_refs)  # 非法路由抛 ValueError
    if predicate:
        conds.append(predicate)
        params.extend(scope_params)
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
        if errors is not None:
            errors["vector"] = f"相似事件检索失败: {e}"
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
