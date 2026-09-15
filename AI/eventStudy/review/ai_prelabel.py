"""
AI 预填写模块（审核辅助，2026-08-18）

用 LLM（项目 quick 模型）对爬虫事件草稿做结构化预分类：
event_type / event_subtype / event_condition / importance /
expected_value / actual_value / previous_value（仅原文明确提到数字时提取）/
event_scope / affected_scope_refs（三级路由作用域与目标引用，2026-09-11 扩产）。

- 预填结果写回 Redis 草稿的 ai_suggestions 字段，审核界面作为表单
  默认值展示（标注"AI 预填，请确认"），人工可改
- LLM 不可用 / 输出解析失败 → 无建议，人工照旧填，不阻塞
- 只预填尚无 ai_suggestions 的草稿（幂等）
- 作用域/引用经同一归一化（review_dao）+ 存在性初筛（market schema 表
  stock_info / sector(dc) + 行业表 industry）：格式非法或代码不存在的
  引用不写回建议；存在性数据源不可用时仅做格式清洗（fail-open），服务端
  approve 的严格校验为最终防线
"""

import json
import logging
import re

from AI.eventStudy.collectors.config import (
    KEY_PENDING_EVENT, get_redis_client, is_redis_available,
)
from AI.eventStudy.review.review_dao import (
    SCOPE_SECTOR, SCOPE_STOCK, RouteExistence,
    filter_existing_refs, normalize_scope, normalize_scope_refs,
)

logger = logging.getLogger(__name__)

_llm = None

_SYSTEM_PROMPT = (
    "你是金融事件分类助手。对给定财经快讯，判断其事件分类与作用范围，并提取关键数值。\n"
    "输出规则：\n"
    "1. 只输出一个 JSON 对象，不要输出任何其他文字或代码块标记\n"
    "2. event_type 从以下选：宏观数据 / 央行 / 地缘 / 产业政策 / 公司 / 市场行情 / 其他\n"
    "3. event_subtype：事件二级分类（如 CPI、LPR、降准、关税、并购），"
    "没有明确子类时用空字符串\n"
    "4. event_condition 从以下选：超预期 / 符合预期 / 低于预期 / 利好 / 利空 / 中性；"
    "仅当原文含预期对比信息时才选\"超预期/符合预期/低于预期\"\n"
    "5. importance 为 1-5 整数：央行降准降息 5、重要经济数据/重大地缘 4、"
    "一般政策/行业 3、普通公司 2、噪音 1\n"
    "6. expected_value / actual_value / previous_value：仅当原文明确提到对应数字时"
    "提取为数值，否则 null（不要编造）\n"
    "7. event_scope 从以下选：market / sector / stock——"
    "影响整个市场（宏观数据、央行、地缘、交易制度、大盘行情）选 market；"
    "影响特定行业或概念板块（行业政策、题材催化）选 sector；"
    "影响特定上市公司（公司公告、并购、业绩）选 stock；"
    "无法确定时选 market\n"
    "8. affected_scope_refs：目标引用数组——event_scope=market 时固定为 []；"
    "sector 填申万一级行业代码 SW:加 6 位数字（如 SW:801080 电子）"
    "或东财概念代码 CONCEPT:加 BK 四位数字加 .DC（如 CONCEPT:BK1753.DC）；"
    "stock 填带市场后缀的股票代码 stock:加 6 位数字加 .SH/.SZ/.BJ"
    "（如 stock:600519.SH）；只填原文明确指向的代码，禁止编造代码，"
    "无法确定时 event_scope=market、affected_scope_refs=[]\n"
    '输出 JSON 格式：{"event_type": "...", "event_subtype": "...", '
    '"event_condition": "...", "importance": 3, '
    '"expected_value": null, "actual_value": null, "previous_value": null, '
    '"event_scope": "market", "affected_scope_refs": []}'
)


def get_llm():
    """懒加载 quick 模型（与主程序一致的 OpenAI 兼容配置）。"""
    global _llm
    if _llm is None:
        try:
            from langchain_openai import ChatOpenAI
            from AI.default_config import load_config
            cfg = load_config()
            if not cfg.get("api_key"):
                logger.warning("未配置 LIVEPROFIT_API_KEY，AI 预填不可用")
                return None
            _llm = ChatOpenAI(
                model=cfg.get("quick_think_llm", "gpt-4o-mini"),
                base_url=cfg.get("base_url"),
                api_key=cfg["api_key"],
                temperature=0.2,  # 分类任务低温度
                max_tokens=500,
                timeout=60,
            )
        except Exception as e:
            logger.warning(f"AI 预填 LLM 初始化失败: {e}")
            return None
    return _llm


def _parse_json_output(text: str):
    """容错解析 LLM 输出：容忍代码块标记与前后杂质。"""
    if not text:
        return None
    # 尝试直接解析；失败则提取首个 {...} 块
    for candidate in (text.strip(), _extract_json_block(text)):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _extract_json_block(text: str):
    """提取文本中的首个 JSON 对象（含 ```json 代码块）。"""
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    return text[start:end + 1]


def _sanitize(suggestion: dict, existence=None) -> dict:
    """清洗 LLM 输出：类型校验 + 值域收敛，非法字段置 None。

    路由字段（2026-09-11）：
    - event_scope 收敛三值；非法/缺失 → None（表单回退 market）
    - affected_scope_refs 走同一归一化（review_dao.normalize_scope_refs），
      existence 可用时再做存在性初筛剔除幻觉引用（数据源不可用则保留全部
      格式合法引用，服务端二次校验为最终防线）；scope 非 sector/stock 时
      目标强制为空（market 固定 []，非法 scope 不留悬空引用）
    """
    clean = {}
    for field in ("event_type", "event_subtype", "event_condition"):
        value = suggestion.get(field)
        clean[field] = str(value).strip() if value else None
    try:
        importance = int(suggestion.get("importance", 3))
        clean["importance"] = max(1, min(5, importance))
    except (TypeError, ValueError):
        clean["importance"] = None
    for field in ("expected_value", "actual_value", "previous_value"):
        value = suggestion.get(field)
        try:
            clean[field] = float(value) if value is not None else None
        except (TypeError, ValueError):
            clean[field] = None

    scope = normalize_scope(suggestion.get("event_scope"))
    refs = normalize_scope_refs(suggestion.get("affected_scope_refs"))
    if scope in (SCOPE_SECTOR, SCOPE_STOCK):
        refs = filter_existing_refs(refs, existence)
    else:
        refs = []
    clean["event_scope"] = scope
    clean["affected_scope_refs"] = refs
    return clean


def prelabel_one(draft: dict, existence=None) -> dict:
    """对单条事件草稿生成 AI 预填建议（不写 Redis）。

    existence：RouteExistence 或 None——提供时对目标引用做存在性初筛
    （market schema 表 + 行业码表），None 时仅做格式清洗。

    Returns: ai_suggestions dict；LLM 不可用/解析失败返回 {}。
    """
    llm = get_llm()
    if llm is None:
        return {}
    text = f"标题：{draft.get('title', '')}\n内容：{draft.get('content', '')[:500]}"
    try:
        result = llm.invoke([
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ])
        suggestion = _parse_json_output(result.content)
        if suggestion is None:
            logger.warning(f"AI 预填解析失败（draft={draft.get('draft_id')}），输出非 JSON")
            return {}
        return _sanitize(suggestion, existence)
    except Exception as e:
        logger.warning(f"AI 预填失败（draft={draft.get('draft_id')}）: {e}")
        return {}


def _open_existence():
    """为存在性初筛建 PG 连接；不可用返回 (None, None)（预填不阻塞）。

    Returns: (RouteExistence|None, conn|None)——conn 由调用方关闭。
    """
    try:
        from AI.eventStudy.db.connection import get_connection
        conn = get_connection()
    except Exception as e:
        logger.warning(f"引用存在性初筛不可用（PG 连接失败）：{e}")
        return None, None
    return RouteExistence(conn), conn


def prelabel_events(drafts: list[dict]) -> int:
    """对尚无 ai_suggestions 的草稿批量预填，写回 Redis。

    存在性初筛数据源每批只建一次连接（逐引用主键命中查询）。

    Returns: 成功生成建议的条数。
    """
    if not drafts or not is_redis_available():
        return 0
    r = get_redis_client()
    done = 0
    existence, conn = _open_existence()
    try:
        for draft in drafts:
            draft_id = draft.get("draft_id")
            if draft_id is None or draft.get("ai_suggestions"):
                continue  # 已有建议，幂等跳过
            suggestion = prelabel_one(draft, existence=existence)
            if not suggestion:
                continue
            draft["ai_suggestions"] = suggestion
            r.set(KEY_PENDING_EVENT.format(draft_id=draft_id),
                  json.dumps(draft, ensure_ascii=False, default=str))
            done += 1
    finally:
        if conn is not None:
            conn.close()
    logger.info(f"[AI预填] {done}/{len(drafts)} 条草稿生成建议")
    return done
