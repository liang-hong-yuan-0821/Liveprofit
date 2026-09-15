"""
市场层事件研究预取（T4；方案第三章「分层消费与隔离」、第五章「事件研究预取」）

LLM 调用**前**固定候选、资产与窗口：
- 候选来源：市场路由正式事件（`event_scope='market'` 已审核）+ 全球新闻 +
  央行日历 + 宏观发布（文本抽取）
- 去重（ID/来源+标题+时间）、时点校验（`event_time <= trade_date`，日期精度
  不足保守排除同日）、固定窗口（首期 `000300.SH` + `post_event_5d`）、最多 5 条
- 逐条 `predict_impact(..., as_of=event_time, event_scope="market")`；单条失败
  降级为 `api_unavailable`/`no_sample`/`partial`，**不得由 LLM 补写统计**
- 结果仅作 Prompt 变量（`event_study_prefetch` 数据块）与 `international_events`
  组装输入，不进 State

依赖可注入：`conn` / `predict_fn` / `route_lister` 默认取真实 PG 连接与
事件研究预测器/路由查询（进程内直连，`AI/eventStudy/**`），单测用 fake 替代。
"""

import logging
import re

from AI.utils.event_prefetch_core import (
    DEFAULT_ASSET_TICKERS, DEFAULT_WINDOW_TYPE, MAX_PREFETCH_CANDIDATES, SCOPE_MARKET,
    close_conn, extract_text_candidates, fetch_route_candidates, normalize_title,
    open_conn, run_prefetch,
)

logger = logging.getLogger(__name__)

# 候选来源标签（Prompt 数据块与结构化事件的「来源」字段）
SOURCE_ROUTE = "事件库"
SOURCE_NEWS = "全球新闻"
SOURCE_CALENDAR = "央行日历"
SOURCE_MACRO = "宏观指标"

# 「实际/预期/前值」预期差：同一行出现关键词即可（无结构化来源时不产出，
# 不新增独立 Provider 接口）
_RELEASE_ITEM = re.compile(r"(实际|预期|前值)\s*[:：]?\s*([+\-]?\d+(?:\.\d+)?)")
_RELEASE_SPLIT = re.compile(r"(?:实际|预期|前值)")


def market_route():
    """市场路由（作用域 market，市场事件目标固定空数组）。"""
    try:
        from AI.eventStudy.review.review_dao import EventRoute
        return EventRoute(scope=SCOPE_MARKET, scope_refs=())
    except Exception as e:  # 事件研究模块不可用 → 路由查询一并降级（候选仅剩文本抽取）
        logger.warning(f"事件研究路由构造失败: {e}")
        return None


def prefetch_event_study_evidence(
    raw_news: str,
    central_bank_calendar: str,
    macro_indicators: str,
    trade_date: str,
    asset_tickers: tuple[str, ...] = DEFAULT_ASSET_TICKERS,
    window_type: str = DEFAULT_WINDOW_TYPE,
    *,
    conn=None,
    predict_fn=None,
    route_lister=None,
    max_candidates: int = MAX_PREFETCH_CANDIDATES,
) -> dict:
    """市场层事件研究预取（方案第五章签名 + 依赖注入）。

    Args:
        raw_news / central_bank_calendar / macro_indicators: 上游 dataflow 文本
            （`get_global_macro_news` / `get_central_bank_calendar` / `get_macro_indicators`）
        trade_date: 分析交易日（'YYYY-MM-DD'）——时点上界
        asset_tickers: 固定资产（首期沪深 300）
        window_type: 固定窗口（首期 post_event_5d）
        conn / predict_fn / route_lister: 注入点（默认真实 PG 连接 + 事件研究预测器/路由查询）

    Returns:
        run_prefetch 结果 + `macro_releases`（预期差，市场确认证据来源）+
        `route_error`（路由查询降级原因）+ `candidate_sources`（候选来源计数）
    """
    own_conn = False
    if conn is None and predict_fn is None:
        conn = open_conn()
        own_conn = conn is not None
    try:
        route = market_route()
        if route is None:
            route_candidates, route_error = [], "事件研究路由不可用"
        else:
            route_candidates, route_error = fetch_route_candidates(
                conn, route, trade_date, route_lister=route_lister,
                source_label=SOURCE_ROUTE,
            )
        text_candidates = (
            extract_text_candidates(raw_news, trade_date, SOURCE_NEWS)
            + extract_text_candidates(central_bank_calendar, trade_date, SOURCE_CALENDAR)
            + extract_text_candidates(macro_indicators, trade_date, SOURCE_MACRO)
        )
        result = run_prefetch(
            route_candidates + text_candidates,
            event_scope=SCOPE_MARKET,
            scope_refs=(),
            trade_date=trade_date,
            asset_tickers=asset_tickers,
            window_type=window_type,
            conn=conn,
            predict_fn=predict_fn,
            max_candidates=max_candidates,
            source_label="市场事件预取",
        )
    finally:
        if own_conn:
            close_conn(conn)
    result["macro_releases"] = extract_macro_releases(macro_indicators)
    result["route_error"] = route_error
    result["candidate_sources"] = {
        "route_events": len(route_candidates),
        "text": len(text_candidates),
    }
    return result


# ==================== 宏观发布预期差（市场确认证据） ====================

def extract_macro_releases(text) -> list[dict]:
    """从宏观文本提取「实际/预期/前值」（同行为一组）。无结构化来源时返回 []。

    返回 [{"indicator", "actual", "expected", "previous", "surprise"}]；
    `surprise` 仅在同时有实际与预期时判定（超预期/不及预期/符合预期），否则 None。
    """
    releases = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "实际" not in line or ("预期" not in line and "前值" not in line):
            continue
        values = {key: value for key, value in _RELEASE_ITEM.findall(line)}
        indicator = _RELEASE_SPLIT.split(line, maxsplit=1)[0]
        indicator = re.sub(r"^\s*(?:[-*•·]|\d+[.、)])\s*", "", indicator)
        indicator = indicator.strip(" |:：-—·")[:40]
        if not values.get("实际"):
            continue
        releases.append({
            "indicator": indicator,
            "actual": values.get("实际"),
            "expected": values.get("预期"),
            "previous": values.get("前值"),
            "surprise": _surprise_direction(values.get("实际"), values.get("预期")),
        })
    return releases


def _surprise_direction(actual, expected):
    try:
        actual_value, expected_value = float(actual), float(expected)
    except (TypeError, ValueError):
        return None
    if actual_value > expected_value:
        return "超预期"
    if actual_value < expected_value:
        return "不及预期"
    return "符合预期"


def find_macro_release(title, releases):
    """事件标题 → 命中的宏观发布（标题含指标名或指标名含标题，取最长命中）。"""
    key = normalize_title(title)
    if not key:
        return None
    hits = []
    for release in releases or []:
        indicator = normalize_title(release.get("indicator"))
        if not indicator:
            continue
        if indicator in key or key in indicator:
            hits.append((len(indicator), release))
    return max(hits, key=lambda item: item[0])[1] if hits else None


def build_market_confirmation(title, releases):
    """事件 → 市场确认证据（预期差）。无命中或无预期差方向时返回 None。

    「没有价格/预期差不得宣称市场已定价」（方案第五章规则）——价格类证据
    （商品/汇率/VIX）由特征层 `global_risk_features` 提供，本函数只负责预期差。
    """
    release = find_macro_release(title, releases)
    if not release or not release.get("surprise"):
        return None
    return {
        "basis": "macro_release_surprise",
        "indicator": release.get("indicator"),
        "actual": release.get("actual"),
        "expected": release.get("expected"),
        "previous": release.get("previous"),
        "surprise": release.get("surprise"),
    }
