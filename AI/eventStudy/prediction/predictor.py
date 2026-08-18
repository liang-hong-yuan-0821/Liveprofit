"""
AI 预测模块（方案 3.7，模板匹配 + 向量检索加权融合）

逻辑（LangGraph 工具 / REST API / 离线回测共用同一套规则，保证确定性）：
1. 模板 = event_type + event_subtype + event_condition；模板匹配输出该模板
   历史事件（仅人工确认的 event_impacts）的平均 CAR、胜率（上涨占比）、样本数
2. 向量补充始终执行：新事件文本向量化 → pgvector 检索跨模板语义相似事件
   top K（相似度 < 0.5 不纳入），权重 = 相似度 × 0.5 折扣
3. 合并加权：模板内事件权重 1.0；方向按加权平均 CAR 符号判定
   （正 → 利好，负 → 利空，|CAR| <= 0.1% 近零 → 中性）；
   置信度基于样本数量与胜率极端程度
4. 落库仅两种情况：显式保存（save=True）/ 离线回测批量写入；日常调用不落库
"""

import logging

from AI.eventStudy.collectors.config import (
    SIMILARITY_MIN, SIMILARITY_TOP_K, TEMPLATE_WEIGHT, VECTOR_SUPPLEMENT_DISCOUNT,
)
from AI.eventStudy.processing.event_vectorizer import encode_text
from AI.eventStudy.prediction import similarity_search

logger = logging.getLogger(__name__)

NEAR_ZERO_CAR = 0.001  # |CAR| <= 0.1% 视为中性


def _template_cars(conn, asset_ticker: str, window_type: str,
                   event_type, event_subtype, event_condition, as_of=None) -> list:
    """模板匹配：查该模板下目标资产 × 窗口的全部历史 CAR（3.7.1 步骤 2）。"""
    fields = {
        "event_type": event_type,
        "event_subtype": event_subtype,
        "event_condition": event_condition,
    }
    present = {k: v for k, v in fields.items() if v}
    if not present:  # 模板未定义（无任何标签），模板样本为空
        return []
    conds = ["e.status = 'approved'"]
    params = [asset_ticker, window_type]
    for col, val in present.items():
        conds.append(f"e.{col} = %s")
        params.append(val)
    if as_of is not None:
        conds.append("e.announced_at < %s::timestamptz")
        params.append(as_of)
    sql = f"""
        SELECT ei.cumulative_abnormal_return
        FROM event_impacts ei
        JOIN events e ON e.event_id = ei.event_id
        JOIN assets a ON a.asset_id = ei.asset_id
        WHERE a.ticker = %s AND ei.window_type = %s AND {' AND '.join(conds)}
    """
    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception as e:
        logger.error(f"模板匹配查询失败: {e}")
        return []
    return [float(r[0]) for r in rows]


def _supplement_samples(conn, asset_ticker: str, window_type: str,
                        query_embedding: list, as_of=None) -> list[dict]:
    """向量补充（3.7.1 步骤 3）：检索语义相似事件及其对目标资产的影响。"""
    if not query_embedding:
        return []
    sims = similarity_search.search_similar_events(
        conn, query_embedding, top_k=SIMILARITY_TOP_K,
        min_similarity=SIMILARITY_MIN, before_ts=as_of,
    )
    samples = []
    for s in sims:
        row = conn.execute(
            """
            SELECT ei.cumulative_abnormal_return, ei.direction
            FROM event_impacts ei
            JOIN assets a ON a.asset_id = ei.asset_id
            WHERE ei.event_id = %s AND a.ticker = %s AND ei.window_type = %s
            LIMIT 1
            """,
            (s["event_id"], asset_ticker, window_type),
        ).fetchone()
        if row is None:
            continue  # 该相似事件无此资产/窗口的影响记录，不纳入
        car = float(row[0])
        weight = round(s["similarity"] * VECTOR_SUPPLEMENT_DISCOUNT, 4)
        samples.append({
            "event_id": s["event_id"],
            "title": s["title"],
            "similarity": s["similarity"],
            "weight": weight,
            "car": car,
            "direction": int(row[1]),
        })
    return samples


def _stats(cars: list) -> dict:
    """平均 CAR / 胜率（上涨占比）/ 样本数。"""
    if not cars:
        return {"sample_count": 0, "avg_car": None, "win_rate": None}
    n = len(cars)
    avg = sum(cars) / n
    win = sum(1 for c in cars if c > 0) / n
    return {"sample_count": n, "avg_car": round(avg, 6), "win_rate": round(win, 4)}


def _judge(car: float) -> int:
    """方向：正 → 利好，负 → 利空，|CAR| <= 0.1% 近零 → 中性。"""
    if abs(car) <= NEAR_ZERO_CAR:
        return 0
    return 1 if car > 0 else -1


def _confidence(n_samples: int, win_rate) -> float:
    """置信度 = 样本量饱和度与胜率极端程度的均值（0-1）。"""
    if n_samples == 0 or win_rate is None:
        return 0.0
    conf_n = min(1.0, n_samples / 20.0)
    conf_ext = min(1.0, 2.0 * abs(win_rate - 0.5))
    return round(0.5 * conf_n + 0.5 * conf_ext, 4)


def predict_impact(
    conn,
    new_event_text: str,
    asset_ticker: str,
    window_type: str = "post_event_5d",
    event_type: str = None,
    event_subtype: str = None,
    event_condition: str = None,
    save: bool = False,
    event_id: int = None,
    as_of=None,
) -> dict:
    """对新事件输出历史影响统计及预测（3.7.1 接口）。

    Args:
        conn: PG 连接
        new_event_text: 新事件文本（标题/摘要）
        asset_ticker: 目标资产（如 000001.SH）
        window_type: 预测窗口（默认 post_event_5d）
        event_type / event_subtype / event_condition: 模板标签（人工确认后传入；
            全空时模板样本为空，仅向量补充）
        save: True 时落库 predictions 表（显式保存 / 回测）
        event_id: 关联事件 ID（save=True 时）
        as_of: 回测时点——仅用该时间之前的事件（防前视偏差）

    Returns:
        {prediction, template_stats, supplement_events, note}
    """
    template_cars = _template_cars(
        conn, asset_ticker, window_type,
        event_type, event_subtype, event_condition, as_of,
    )
    tpl_stats = _stats(template_cars)
    tpl_stats.update({
        "event_type": event_type,
        "event_subtype": event_subtype,
        "event_condition": event_condition,
        "weight": TEMPLATE_WEIGHT,
    })

    # 向量补充：始终执行（B6，不设样本门槛）
    query_vec = encode_text(new_event_text)
    supplement = []
    note = ""
    if not query_vec:
        note = "向量模型不可用，本次仅模板匹配"
        logger.warning(note)
    else:
        supplement = _supplement_samples(
            conn, asset_ticker, window_type, query_vec, as_of=as_of,
        )
    sup_stats = _stats([s["car"] for s in supplement])

    # 合并加权（模板 1.0 / 补充 相似度×0.5）
    total_weight = (
        TEMPLATE_WEIGHT * tpl_stats["sample_count"]
        + sum(s["weight"] for s in supplement)
    )
    if total_weight > 0:
        weighted_car = (
            sum(c * TEMPLATE_WEIGHT for c in template_cars)
            + sum(s["weight"] * s["car"] for s in supplement)
        ) / total_weight
    else:
        weighted_car = None

    if weighted_car is None:
        direction, predicted_return, confidence = 0, None, 0.0
        note = (note + "；" if note else "") + "事件库暂无相似事件/影响样本"
    else:
        direction = _judge(weighted_car)
        predicted_return = round(weighted_car, 6)
        n_all = tpl_stats["sample_count"] + sup_stats["sample_count"]
        # 注意：win_rate = 0 是合法值（样本全部下跌），不能用 `or` 兜底
        tpl_wr = tpl_stats["win_rate"] if tpl_stats["win_rate"] is not None else 0.5
        sup_wr = sup_stats["win_rate"] if sup_stats["win_rate"] is not None else 0.5
        win_rate = (
            tpl_wr * tpl_stats["sample_count"] + sup_wr * sup_stats["sample_count"]
        ) / n_all if n_all else None
        confidence = _confidence(n_all, win_rate)

    prediction = {
        "window_type": window_type,
        "asset_ticker": asset_ticker,
        "predicted_direction": direction,
        "predicted_return": predicted_return,
        "confidence": confidence,
    }

    # 落库策略（3.7.1）：仅显式保存 / 离线回测写入；
    # 无样本（predicted_return=None）时不落库——predictions.predicted_return 为 NOT NULL
    if save and predicted_return is not None:
        _save_prediction(conn, prediction, event_id, supplement)
    elif save:
        logger.warning("无样本预测不落库（predicted_return 为空）")

    return {
        "prediction": prediction,
        "template_stats": tpl_stats,
        "supplement_events": supplement,
        "note": note,
    }


def _save_prediction(conn, prediction: dict, event_id: int, supplement: list):
    """写入 predictions 表（仅显式保存/回测时调用）。

    失败时回滚并记录错误——不能让异常事务毒化后续批量写入（离线回测场景）。
    """
    import json
    try:
        row = conn.execute(
            "SELECT asset_id FROM assets WHERE ticker = %s",
            (prediction["asset_ticker"],),
        ).fetchone()
        if row is None:
            logger.warning(f"资产未初始化: {prediction['asset_ticker']}，预测不落库")
            return
        conn.execute(
            """
            INSERT INTO predictions (
                event_id, asset_id, window_type, predicted_direction,
                predicted_return, confidence, similar_event_ids
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                event_id,
                row[0],
                prediction["window_type"],
                prediction["predicted_direction"],
                prediction["predicted_return"],
                prediction["confidence"],
                json.dumps([s["event_id"] for s in supplement]),
            ),
        )
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.error(f"预测落库失败（已回滚）: {e}")
