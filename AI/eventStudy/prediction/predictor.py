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
5. 三级路由与防前视（方案第三章）：as_of 只取 announced_at <= as_of 的已审核
   样本（边界包含）；event_scope/scope_refs 传入时同作用域过滤（sector/stock
   用并集命中 affected_scope_refs）；输出 sample_metadata 元数据（样本数 /
   覆盖状态 / 污染提示），区分「无同层样本」no_sample 与「查询不可用」
   api_unavailable，供上层预取器降级（historical_impact=null + 原因）
"""

import logging
from datetime import datetime

from AI.eventStudy.collectors.config import (
    SIMILARITY_MIN, SIMILARITY_TOP_K, TEMPLATE_WEIGHT, VECTOR_SUPPLEMENT_DISCOUNT,
)
from AI.eventStudy.processing.event_vectorizer import encode_text
from AI.eventStudy.prediction import similarity_search
from AI.eventStudy.review import review_dao

logger = logging.getLogger(__name__)

NEAR_ZERO_CAR = 0.001  # |CAR| <= 0.1% 视为中性

# 查询通道 → 可读标签（sample_metadata.unavailable_channels / reason 用）
_CHANNEL_LABELS = {
    "template": "模板匹配",
    "vector": "向量检索",
    "supplement": "相似样本影响查询",
}


def _as_of_str(as_of) -> str | None:
    """时点回显归一（datetime → ISO8601；其余原样保留）。"""
    if as_of is None:
        return None
    if isinstance(as_of, datetime):
        return as_of.isoformat()
    return str(as_of)


_DATE_ONLY_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d")


def _as_of_sql_param(as_of):
    """时点上界 → SQL 绑定值：仅日期形态归一为当日 23:59:59.999999。

    口径与 `AI/eventStudy/review/review_dao._as_of_param` 一致（Code Review
    第 2 轮 finding 5）：`announced_at` 是带时刻的时间戳，裸日期直绑会被 PG
    解析为当日 00:00，把同日已公布事件（如 09:30）整片排除——与
    `is_visible_at` 对 datetime 精度「同日可见」的口径矛盾。带时刻输入
    （datetime / ISO 串）原样保留，不扩大到当日末；「时分秒微秒全零」的
    datetime（如 `datetime.combine(d, time())`，语义为日边界）按日期形态
    归一为当日末；None / 空串 / 纯空白 → None（调用方跳过时间条件，不把
    空串绑进 `timestamptz` 触发类型错误而静默丢历史）。
    """
    if as_of is None:
        return None
    if isinstance(as_of, datetime):
        if (as_of.hour, as_of.minute, as_of.second, as_of.microsecond) == (0, 0, 0, 0):
            return f"{as_of.date().isoformat()} 23:59:59.999999"
        return as_of
    text = str(as_of).strip()
    if not text:
        return None
    for fmt in _DATE_ONLY_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except (TypeError, ValueError):
            continue
        return parsed.strftime("%Y-%m-%d 23:59:59.999999")
    return as_of


def _template_samples(conn, asset_ticker: str, window_type: str,
                      event_type, event_subtype, event_condition,
                      as_of=None, scope_predicate: str = "", scope_params=None,
                      errors: dict | None = None,
                      exclude_event_id: int = None) -> list[dict]:
    """模板匹配：查该模板下目标资产 × 窗口的全部历史样本（3.7.1 步骤 2）。

    as_of 非空时只取 announced_at <= as_of 的已审核事件（边界包含 = 该时点已
    公布的事件是合法样本，防前视）；scope_predicate/scope_params 为
    similarity_search.build_scope_filter 产物（同作用域路由）。
    exclude_event_id 非空时排除该事件自身（评审 m14：预取按候选事件取统计时，
    候选事件已在事件库中，不排除会把事件自身已实现的影响当作历史样本）。

    Returns: [{"car": float, "contaminated": bool}]；查询异常记入 errors["template"]
    并返回空列表（不抛，由上层区分 no_sample / api_unavailable）。
    """
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
        conds.append("e.announced_at <= %s::timestamptz")
        params.append(as_of)
    if exclude_event_id is not None:
        conds.append("e.event_id != %s")  # 排除事件自身（评审 m14）
        params.append(exclude_event_id)
    if scope_predicate:
        conds.append(scope_predicate)
        params.extend(scope_params or [])
    sql = f"""
        SELECT ei.cumulative_abnormal_return, ei.is_contaminated
        FROM event_impacts ei
        JOIN events e ON e.event_id = ei.event_id
        JOIN assets a ON a.asset_id = ei.asset_id
        WHERE a.ticker = %s AND ei.window_type = %s AND {' AND '.join(conds)}
    """
    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception as e:
        logger.error(f"模板匹配查询失败: {e}")
        if errors is not None:
            errors["template"] = f"模板匹配查询失败: {e}"
        return []
    return [{"car": float(r[0]), "contaminated": bool(r[1])} for r in rows]


def _supplement_samples(conn, asset_ticker: str, window_type: str,
                        query_embedding: list, as_of=None,
                        scope=None, scope_refs=(),
                        errors: dict | None = None,
                        exclude_event_id: int = None) -> list[dict]:
    """向量补充（3.7.1 步骤 3）：检索语义相似事件及其对目标资产的影响。

    scope/scope_refs 传入时只检索同作用域命中事件（过滤在相似事件检索层完成，
    后续影响查询继承该候选集）；as_of 只取 announced_at <= as_of 的已审核事件；
    exclude_event_id 排除事件自身（评审 m14，避免自相关污染）。
    """
    if not query_embedding:
        return []
    sims = similarity_search.search_similar_events(
        conn, query_embedding, top_k=SIMILARITY_TOP_K,
        min_similarity=SIMILARITY_MIN, before_ts=as_of,
        scope=scope, scope_refs=scope_refs, errors=errors,
        exclude_event_id=exclude_event_id,
    )
    samples = []
    for s in sims:
        try:
            row = conn.execute(
                """
                SELECT ei.cumulative_abnormal_return, ei.direction, ei.is_contaminated
                FROM event_impacts ei
                JOIN assets a ON a.asset_id = ei.asset_id
                WHERE ei.event_id = %s AND a.ticker = %s AND ei.window_type = %s
                LIMIT 1
                """,
                (s["event_id"], asset_ticker, window_type),
            ).fetchone()
        except Exception as e:
            logger.error(f"相似事件影响查询失败: event_id={s['event_id']}: {e}")
            if errors is not None:
                errors["supplement"] = f"相似事件影响查询失败: {e}"
            continue
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
            "contaminated": bool(row[2]),
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


def _asset_registered(conn, asset_ticker: str) -> bool | None:
    """资产是否已登记（事件研究覆盖范围，V1 仅 4 个市场指数）；异常返回 None。

    覆盖判定 = assets 表登记（无法登记则事件研究无法为其产出 CAR），与
    「已登记但暂无样本」（no_sample，含冷启动）语义区分。
    """
    try:
        return conn.execute(
            "SELECT 1 FROM assets WHERE ticker = %s LIMIT 1", (asset_ticker,)
        ).fetchone() is not None
    except Exception as e:
        logger.error(f"资产覆盖查询失败: {e}")
        return None


def _resolve_history_match_status(conn, asset_ticker: str, n_samples: int,
                                  errors: dict | None = None) -> tuple[str, str | None]:
    """样本覆盖/命中状态（供预取器写 history_match_status 并降级）。

    - ok：有样本（通道部分失败时状态仍为 ok，失败通道见 unavailable_channels）
    - api_unavailable：无样本且查询通道失败（事件研究不可用，不得当作冷启动）
    - coverage_missing：资产未登记（首期仅 4 个市场指数，行业/个股无覆盖）
    - no_sample：资产已登记但该模板/窗口/作用域下无已审核样本（含冷启动）

    Returns: (status, reason)；ok 时 reason 为 None。
    """
    if n_samples > 0:
        return "ok", None
    if errors:
        labels = "、".join(_CHANNEL_LABELS.get(c, c) for c in errors)
        return "api_unavailable", f"事件研究查询不可用（{labels}）"
    registered = _asset_registered(conn, asset_ticker)
    if registered is None:
        return "api_unavailable", "事件研究查询不可用（资产覆盖查询失败）"
    if not registered:
        return (
            "coverage_missing",
            f"资产 {asset_ticker} 未登记（事件研究首期仅覆盖 4 个市场指数）",
        )
    return "no_sample", "事件库暂无该资产/作用域下的已审核样本"


def _build_sample_metadata(conn, asset_ticker: str, as_of, event_scope, scope_refs,
                           template_samples: list, supplement: list,
                           errors: dict | None = None) -> dict:
    """样本元数据（方案第三章「分层消费与隔离」；预取器据此降级）。"""
    contaminated_count = (
        sum(1 for s in template_samples if s["contaminated"])
        + sum(1 for s in supplement if s["contaminated"])
    )
    n_template = len(template_samples)
    n_supplement = len(supplement)
    status, reason = _resolve_history_match_status(
        conn, asset_ticker, n_template + n_supplement, errors,
    )
    return {
        "as_of": _as_of_str(as_of),
        "event_scope": review_dao.normalize_scope(event_scope),
        "scope_refs": review_dao.normalize_scope_refs(scope_refs),
        "sample_count": n_template + n_supplement,
        "template_sample_count": n_template,
        "supplement_sample_count": n_supplement,
        "contaminated_sample_count": contaminated_count,
        "contaminated": contaminated_count > 0,
        "history_match_status": status,
        "unavailable_channels": list(errors) if errors else [],
        "reason": reason,
    }


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
    event_scope: str = None,
    scope_refs=(),
    exclude_event_id: int = None,
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
        as_of: 回测时点——仅用该时点（含）之前公布的事件（announced_at <= as_of，
            防前视偏差）；None = 不做时间过滤（兼容旧调用）
        event_scope: 三级路由作用域（market/sector/stock）；None = 不限定作用域
            （兼容旧调用，混用全部作用域样本）
        scope_refs: 层内目标引用（sector/stock 必填，market 忽略）；并集命中语义，
            与 review_dao.list_approved_events_for_route 一致；空引用 = 空路由（零样本），
            不落到全量事件
        exclude_event_id: 排除的事件 ID（评审 m14）——预取按候选事件取历史统计时
            传候选自身 ID：候选事件已在库中（含 event_impacts 行），不排除会把
            「事件自身已实现的影响」当作历史样本（自相关污染）；None = 不排除

    Returns:
        {prediction, template_stats, supplement_events, note, sample_metadata}
        sample_metadata 含样本数 / 覆盖状态 history_match_status（ok / no_sample /
        coverage_missing / api_unavailable）/ 污染提示 / 截止时点

    Raises:
        ValueError: event_scope / scope_refs 非法（调用方编程错误，不静默降级为空样本）
    """
    as_of = _as_of_sql_param(as_of)  # 裸日期归一当日末（与 review_dao 口径一致）
    scope_predicate, scope_params = similarity_search.build_scope_filter(
        event_scope, scope_refs, qualifier="e",
    )
    errors: dict = {}

    template_samples = _template_samples(
        conn, asset_ticker, window_type,
        event_type, event_subtype, event_condition,
        as_of=as_of, scope_predicate=scope_predicate, scope_params=scope_params,
        errors=errors, exclude_event_id=exclude_event_id,
    )
    template_cars = [s["car"] for s in template_samples]
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
        errors["vector"] = note
    else:
        supplement = _supplement_samples(
            conn, asset_ticker, window_type, query_vec,
            as_of=as_of, scope=event_scope, scope_refs=scope_refs, errors=errors,
            exclude_event_id=exclude_event_id,
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

    contaminated_count = (
        sum(1 for s in template_samples if s["contaminated"])
        + sum(1 for s in supplement if s["contaminated"])
    )
    if contaminated_count:
        note = (note + "；" if note else "") + (
            f"部分样本被其他重大事件污染（{contaminated_count} 条）"
        )

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
        "sample_metadata": _build_sample_metadata(
            conn, asset_ticker, as_of, event_scope, scope_refs,
            template_samples, supplement, errors,
        ),
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
