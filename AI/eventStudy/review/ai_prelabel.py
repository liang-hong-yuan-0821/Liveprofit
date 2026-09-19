"""
AI 预填写模块（审核辅助，2026-08-18）

用 LLM（项目 quick 模型）对爬虫事件草稿做结构化预分类：
event_type / event_subtype / event_condition / importance /
expected_value / actual_value / previous_value（仅原文明确提到数字时提取）/
event_scope / affected_scope_refs（三级路由作用域与目标引用，2026-09-11 扩产）。

- 预填结果写回 Redis 草稿的 ai_suggestions 字段，审核界面作为表单
  默认值展示（标注"AI 预填，请确认"），人工可改
- LLM 不可用 / 输出解析失败 → 无建议，人工照旧填，不阻塞
- 回填谓词 needs_prelabel（2026-09-16）：只预填无建议或缺 event_scope 的
  旧建议；force=True 覆写模式是否传入哪些草稿由调用方决定（平台侧
  draft_ids 分片驱动 / 调用方分片循环）
- LLM 不凭记忆补代码（2026-09-16）：代码仅在原文明确出现时输出；原文
  只出现名称时输出中文实体名称，经 NameResolver 查字典表解析为规范引用
  （名称→代码唯一映射源），未解析名称落 unresolved_entities 供人工处理；
  美股公司事件映射 A 股概念板块（如「英伟达概念」）
- 作用域/引用经同一归一化（review_dao）+ 存在性初筛（market schema 表
  stock_info / sector(dc) + 行业表 industry）：格式非法或代码不存在的
  引用不写回建议；存在性数据源不可用时仅做格式清洗（fail-open），服务端
  approve 的严格校验为最终防线
"""

import json
import logging
import re

from AI.eventStudy.collectors.config import (
    KEY_PENDING_EVENT, PENDING_DRAFT_TTL, get_redis_client, is_redis_available,
)
from AI.eventStudy.review.name_resolver import NameResolver
from AI.eventStudy.review.review_dao import (
    MAX_SCOPE_REFS, SCOPE_SECTOR, SCOPE_STOCK, RouteExistence,
    _raw_ref_items, filter_existing_refs, normalize_scope,
    normalize_scope_ref, normalize_scope_refs,
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
    "仅全市场性影响（宏观数据、央行政策、地缘冲突、交易制度、大盘行情）选 market；"
    "影响特定行业或概念板块（行业政策、题材催化、行业供需/涨价）选 sector；"
    "点名特定上市公司（公司公告、并购、业绩、订单）选 stock；"
    "美股等海外公司事件 → 选 sector 并输出 A 股对应概念板块名称（如「英伟达概念」），"
    "找不到对应概念时才保持 market\n"
    "8. affected_scope_refs：目标引用数组——event_scope=market 时固定为 []；"
    "sector/stock 允许三种形态：规范代码（SW:801080 / CONCEPT:BK1753.DC / "
    "stock:600519.SH）、裸代码（801080 / BK1753.DC / 600519.SH）、"
    "中文实体名称（行业名如「电子」、概念名如「光刻胶」「英伟达概念」、"
    "公司全名如「贵州茅台」）；代码仅在原文明确出现时输出，原文只出现"
    "名称时只输出准确的中文名称，不要凭记忆补代码（名称→代码由系统"
    "查表完成，禁止编造名称与代码）；确实无法确定具体影响对象时"
    "event_scope=market、affected_scope_refs=[]\n"
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


_MAX_NAME_LEN = 64  # 候选名称单条长度上限（超长视为垃圾丢弃，不进 unresolved）


def _sanitize(suggestion: dict, existence=None, resolver=None) -> dict:
    """清洗 LLM 输出：类型校验 + 值域收敛，非法字段置 None。

    路由字段（2026-09-11 三级路由 + 2026-09-16 名称解析）：
    - event_scope 收敛三值；非法/缺失 → None（表单回退 market）
    - affected_scope_refs 分流：normalize_scope_ref 成功 = 代码引用；
      失败且非空白 = 候选名称（单条 >64 丢弃、条数上限 MAX_SCOPE_REFS），
      resolver 可用时名称经字典表解析并入（代码引用在前、解析引用在后），
      合并去重后截断至 MAX_SCOPE_REFS；existence 可用时再做存在性初筛
      剔除幻觉引用（数据源不可用则保留全部格式合法引用，服务端 approve
      的严格校验为最终防线）
    - 未解析名称落 unresolved_entities（仅展示，去重保序，为空不写键）
    - scope 非 sector/stock 时目标强制为空（market 固定 []，名称不解析不
      记录——market 语义无目标）
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
    code_refs: list[str] = []
    names: list[str] = []
    for item in _raw_ref_items(suggestion.get("affected_scope_refs")):
        ref = normalize_scope_ref(item)
        if ref is not None:
            if ref not in code_refs:
                code_refs.append(ref)
            continue
        text = str(item).strip()
        if text and len(text) <= _MAX_NAME_LEN and len(names) < MAX_SCOPE_REFS \
                and text not in names:
            names.append(text)

    unresolved: list[str] = []
    if scope in (SCOPE_SECTOR, SCOPE_STOCK):
        refs = list(code_refs)
        if resolver is not None and names:
            resolved, unresolved = resolver.resolve_names(names, scope)
            for ref in resolved:
                if ref not in refs:
                    refs.append(ref)
        else:
            unresolved = list(names)
        refs = refs[:MAX_SCOPE_REFS]
        refs = filter_existing_refs(refs, existence)
    else:
        refs = []
    clean["event_scope"] = scope
    clean["affected_scope_refs"] = refs
    if unresolved:
        clean["unresolved_entities"] = unresolved
    return clean


def prelabel_one(draft: dict, existence=None, resolver=None) -> dict:
    """对单条事件草稿生成 AI 预填建议（不写 Redis）。

    existence：RouteExistence 或 None——提供时对目标引用做存在性初筛
    （market schema 表 + 行业码表），None 时仅做格式清洗。
    resolver：NameResolver 或 None——提供时把 LLM 输出的中文实体名称
    解析为规范引用；None 时名称全部落 unresolved_entities（fail-open）。

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
        return _sanitize(suggestion, existence, resolver)
    except Exception as e:
        logger.warning(f"AI 预填失败（draft={draft.get('draft_id')}）: {e}")
        return {}


def _open_existence():
    """为存在性初筛与名称解析建 PG 连接；不可用返回 (None, None, None)（预填不阻塞）。

    Returns: (RouteExistence|None, NameResolver|None, conn|None)——conn 由
    调用方关闭；两个数据源共享同一 conn。
    """
    try:
        from AI.eventStudy.db.connection import get_connection
        conn = get_connection()
    except Exception as e:
        logger.warning(f"引用存在性初筛不可用（PG 连接失败）：{e}")
        return None, None, None
    return RouteExistence(conn), NameResolver(conn), conn


def needs_prelabel(draft: dict) -> bool:
    """非 force 预填判定：无 ai_suggestions，或建议缺 event_scope
    （2026-09-11 扩产前旧建议回填）。"""
    suggestions = draft.get("ai_suggestions")
    return not suggestions or "event_scope" not in suggestions


def prelabel_events(drafts: list[dict], force: bool = False) -> int:
    """对需要预填的草稿批量预填，写回 Redis。

    非 force：只预填命中 needs_prelabel 的草稿（无建议或缺 event_scope 的
    旧建议回填）；force=True：对传入列表全部重生成建议（覆写）——是否
    覆写哪些草稿由调用方决定（平台侧 draft_ids 分片驱动 / 调用方分片
    循环传入目标草稿）。
    写回带 ex=PENDING_DRAFT_TTL（2026-09-16 顺手修 TTL 抹除：原实现
    r.set 不带 ex，预填后草稿过期被抹）。
    存在性初筛与名称解析数据源每批只建一次连接（逐引用主键命中查询）。

    Returns: 成功生成建议的条数。
    """
    if not drafts or not is_redis_available():
        return 0
    r = get_redis_client()
    done = 0
    existence, resolver, conn = _open_existence()
    try:
        for draft in drafts:
            draft_id = draft.get("draft_id")
            if draft_id is None:
                continue
            if not force and not needs_prelabel(draft):
                continue  # 已有完整建议，幂等跳过（缺 scope 旧建议仍会回填）
            suggestion = prelabel_one(draft, existence=existence, resolver=resolver)
            if not suggestion:
                continue
            draft["ai_suggestions"] = suggestion
            r.set(KEY_PENDING_EVENT.format(draft_id=draft_id),
                  json.dumps(draft, ensure_ascii=False, default=str),
                  ex=PENDING_DRAFT_TTL)
            done += 1
    finally:
        if conn is not None:
            conn.close()
    logger.info(f"[AI预填] {done}/{len(drafts)} 条草稿生成建议")
    return done
