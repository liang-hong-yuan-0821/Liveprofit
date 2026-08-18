"""
事件向量化模块（方案 3.3）

使用 BAAI/bge-m3（sentence-transformers 加载）对审核通过的事件文本
（标题 + 内容）生成 1024 维向量，写入 events.embedding（唯一向量存储）。

- 模型约 2.2GB，进程内懒加载单例，一次加载常驻
- 国内网络可设 HF_ENDPOINT=https://hf-mirror.com 镜像下载
- CPU 推理每条约 2-5 秒
"""

import logging
import os

logger = logging.getLogger(__name__)

from AI.eventStudy.collectors.config import VECTOR_MODEL, VECTOR_DIM, HF_ENDPOINT

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
