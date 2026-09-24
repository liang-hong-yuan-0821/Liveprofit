"""
事件向量化模块（方案 3.3）

使用 BAAI/bge-m3（sentence-transformers 加载）生成 1024 维向量。
兼容事件检索写入 `events.embedding`；不可变判断版本的候选召回向量写入
`event_assessment.embedding`，只允许补填一次并记录实际可用时间。

- 模型约 2.2GB，进程内懒加载单例，一次加载常驻
- 国内网络可设 HF_ENDPOINT=https://hf-mirror.com 镜像下载
- CPU 推理每条约 2-5 秒
"""

import hashlib
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from AI.eventStudy.collectors.config import HF_ENDPOINT, VECTOR_DIM, VECTOR_MODEL

_model = None
_model_available = None  # None=未尝试, True/False=尝试结果


def _ensure_hf_endpoint():
    """配置 HuggingFace 镜像（国内网络）。已显式设置时不覆盖。"""
    if HF_ENDPOINT and "HF_ENDPOINT" not in os.environ:
        os.environ["HF_ENDPOINT"] = HF_ENDPOINT


def get_model():
    """懒加载 bge-m3 模型单例。加载失败返回 None。"""
    global _model, _model_available
    if _model_available is None:
        _ensure_hf_endpoint()
        try:
            from sentence_transformers import SentenceTransformer
            logger.info(f"加载向量模型 {VECTOR_MODEL}（首次加载约 10-60 秒）...")
            _model = SentenceTransformer(VECTOR_MODEL)
            _model_available = True
            dim = getattr(_model, "get_embedding_dimension", None) or \
                getattr(_model, "get_sentence_embedding_dimension", None)
            logger.info(f"向量模型加载完成，维度: {dim() if dim else '未知'}")
        except Exception as e:
            _model_available = False
            logger.error(f"向量模型加载失败: {e}（请确认已 pip install sentence-transformers "
                         f"且可访问模型仓库/HF_ENDPOINT）")
    return _model if _model_available else None


def encode_text(text: str) -> list:
    """文本 → 1024 维向量。模型不可用/文本为空返回空列表。"""
    if not text or not text.strip():
        return []
    model = get_model()
    if model is None:
        return []
    try:
        vec = model.encode(text.strip(), normalize_embeddings=True)
        return vec.tolist()
    except Exception as e:
        logger.error(f"向量化失败: {e}")
        return []


def encode_texts(texts: list[str]) -> list[list[float]]:
    """Batch text encoding for assessment candidate backfill."""
    values = [str(text or "").strip() for text in texts]
    if not values:
        return []
    model = get_model()
    if model is None:
        return []
    try:
        matrix = model.encode(values, normalize_embeddings=True)
        rows = matrix.tolist()
        if len(rows) != len(values) or any(len(row) != VECTOR_DIM for row in rows):
            return []
        return [[float(value) for value in row] for row in rows]
    except Exception as exc:  # noqa: BLE001 - optional vector search must degrade safely
        logger.error("批量事件向量化失败: %s", exc)
        return []


def vectorize_unembedded_assessments(conn, *, limit: int = 1000,
                                     lookback_days: int = 90) -> int:
    """Fill optional vectors for recent auditable assessments without rewriting labels."""
    if limit <= 0 or lookback_days <= 0:
        raise ValueError("limit 和 lookback_days 必须为正")
    if get_model() is None:
        return 0
    rows = conn.execute(
        "SELECT assessment_id, labels FROM event_assessment "
        "WHERE embedding IS NULL AND review_status IN ('accepted', 'disputed') "
        "AND available_at >= now() - (%s * INTERVAL '1 day') "
        "ORDER BY available_at DESC, assessment_id LIMIT %s",
        (int(lookback_days), int(limit)),
    ).fetchall()
    texts: list[str] = []
    kept = []
    for assessment_id, raw_labels in rows:
        labels = raw_labels if isinstance(raw_labels, dict) else {}
        fact = labels.get("fact") if isinstance(labels.get("fact"), dict) else {}
        identity = fact.get("identity") if isinstance(fact.get("identity"), dict) else {}
        text = " ".join((
            str(identity.get("entity") or ""), str(identity.get("action") or ""),
            str(identity.get("reference_period") or ""), str(fact.get("title") or ""),
            str(fact.get("fact_summary") or ""),
        )).strip()
        if text:
            kept.append((assessment_id, text))
            texts.append(text)
    vectors = encode_texts(texts)
    if len(vectors) != len(kept):
        return 0
    now = datetime.now(timezone.utc)
    updated = 0
    for (assessment_id, text), vector in zip(kept, vectors):
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        vector_literal = "[" + ",".join(f"{value:.8f}" for value in vector) + "]"
        result = conn.execute(
            "UPDATE event_assessment SET text_hash = %s, embedding = %s::vector, "
            "embedding_model = %s, embedding_available_at = %s "
            "WHERE assessment_id = %s AND embedding IS NULL",
            (digest, vector_literal, VECTOR_MODEL, now, assessment_id),
        )
        updated += int(bool(result.rowcount))
    conn.commit()
    return updated


def check_dimension() -> bool:
    """检查模型输出维度与表结构 VECTOR(1024) 是否一致（3.3.3 验证项）。"""
    model = get_model()
    if model is None:
        return False
    getter = getattr(model, "get_embedding_dimension", None) or \
        getattr(model, "get_sentence_embedding_dimension", None)
    dim = getter() if getter else None
    if dim is None or dim != VECTOR_DIM:
        logger.error(f"模型维度 {dim} 与表结构 VECTOR({VECTOR_DIM}) 不一致，"
                     f"需同步改表并全库重算向量")
        return False
    return True


def vectorize_event(conn, event_id: int) -> bool:
    """查询事件标题+内容，生成向量并更新数据库（3.3.1 接口）。

    Returns: 是否成功写入。
    """
    row = conn.execute(
        "SELECT title, content FROM events WHERE event_id = %s", (event_id,)
    ).fetchone()
    if row is None:
        logger.warning(f"事件 {event_id} 不存在，跳过向量化")
        return False
    title, content = row
    text = f"{title}\n{content}" if content else title
    vec = encode_text(text)
    if not vec:
        logger.warning(f"事件 {event_id} 向量化失败（模型不可用或文本为空）")
        return False
    conn.execute(
        "UPDATE events SET embedding = %s::vector, updated_at = now() WHERE event_id = %s",
        (vec, event_id),
    )
    conn.commit()
    logger.info(f"事件 {event_id} 向量化完成")
    return True


def vectorize_unembedded(conn) -> int:
    """为全部已通过且未向量化的事件生成向量（每日批处理用）。

    Returns: 成功向量化的事件数。
    """
    if get_model() is None:
        logger.warning("向量模型不可用，跳过批量向量化")
        return 0
    rows = conn.execute(
        "SELECT event_id FROM events WHERE status = 'approved' AND embedding IS NULL"
    ).fetchall()
    count = 0
    for (event_id,) in rows:
        if vectorize_event(conn, event_id):
            count += 1
    return count
