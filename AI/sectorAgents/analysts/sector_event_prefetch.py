"""
板块层事件研究预取（T4；方案第三章「分层消费与隔离」「板块/个股事件生产与消费」）

与市场层 `international_event_prefetch` 同构，差异只在作用域与命中目标：
- 作用域 `sector`，命中目标 = 板块扫描范围（申万行业 / 东财 dc 概念引用）
- **无命中目标时返回空列表**（不跨层借用沪深 300 CAR）
- 候选来源：板块路由正式事件（`event_scope='sector'` 且命中扫描范围）+ 既有
  政策新闻流文本（`get_industry_policy_news`，只作事实候选来源）
- 政策新闻流与历史统计**分块注入**：本模块只产出历史统计块；政策新闻流原文
  由节点另行注入，历史统计块不得引用政策新闻流文本冒充 CAR 证据

依赖可注入：`conn` / `predict_fn` / `route_lister` / `resolve_refs_fn` 默认取
真实 PG 连接与事件研究预测器/路由查询；单测用 fake 替代。
"""

import logging
import re

from AI.utils.event_prefetch_core import (
    DEFAULT_ASSET_TICKERS, DEFAULT_WINDOW_TYPE, MAX_PREFETCH_CANDIDATES, SCOPE_SECTOR,
    close_conn, extract_text_candidates, fetch_route_candidates, normalize_refs,
    normalize_route_refs, open_conn, run_prefetch, safe_rollback,
)

logger = logging.getLogger(__name__)

SOURCE_ROUTE = "事件库"
SOURCE_POLICY_NEWS = "政策新闻"

_LEADING_MARKER = re.compile(r"^\s*(?:[-*•·]|\d+[.、)])\s*")
_METRIC_CELL = re.compile(r"[+\-]?[\d.,%％倍万亿\s]*")
# 表头词（表格首行/名称列）：不作为板块名参与解析
_HEADER_WORDS = {
    "排名", "序号", "行业", "行业名称", "板块", "板块名称", "概念", "概念板块",
    "名称", "指标", "代码", "涨跌幅(%)", "涨跌幅",
}


def sector_route(scope_refs=()):
    """板块路由（作用域 sector + 命中目标引用）。"""
    try:
        from AI.eventStudy.review.review_dao import EventRoute
        return EventRoute(scope=SCOPE_SECTOR, scope_refs=tuple(scope_refs))
    except Exception as e:
        logger.warning(f"事件研究路由构造失败: {e}")
        return None


def resolve_sector_refs(items, conn=None) -> tuple[list[str], list[str]]:
    """板块扫描范围 → 路由引用（保序去重）。返回 (refs, 未解析项)。

    - 已是规范引用或裸代码（801080 / BK1753.DC）经同一归一化直通
    - 名称解析（既有扫描数据只给名称不给代码）：
      行业名 → market.industry（SW2021）→ `SW:<code>`；
      概念名 → market.sector（source 锁定东财 dc）→ `CONCEPT:<code>`
    - 解析不到的名称丢弃（不猜测引用），由调用方记录覆盖率
    """
    direct, names = [], []
    for item in items or []:
        normalized = normalize_refs([item])
        # 仅保留板块引用（越层的个股引用不进 sector 路由）
        normalized = [ref for ref in normalized
                      if ref.startswith(("SW:", "CONCEPT:"))]
        if normalized:
            direct.append(normalized[0])
        else:
            text = str(item or "").strip()
            if text:
                names.append(text)

    resolved = {}
    if names and conn is not None:
        resolved.update(_lookup_industry_codes(names, conn))
        resolved.update(_lookup_concept_codes(names, conn))

    refs, missing = list(direct), []
    for name in names:
        ref = resolved.get(name)
        if ref:
            refs.append(ref)
        else:
            missing.append(name)

    return list(dict.fromkeys(refs)), list(dict.fromkeys(missing))


def _lookup_industry_codes(names, conn) -> dict:
    try:
        rows = conn.execute(
            "SELECT name, industry_code FROM market.industry WHERE source = 'SW2021' "
            "AND name = ANY(%s)", (list(names),),
        ).fetchall()
    except Exception as e:
        safe_rollback(conn)  # 失败语句中毒事务，先回滚（评审 M4 残留）
        logger.warning(f"行业码表解析失败（按未命中处理）: {e}")
        return {}
    return {row[0]: f"SW:{row[1]}" for row in rows or []}


def _lookup_concept_codes(names, conn) -> dict:
    try:
        rows = conn.execute(
            "SELECT name, sector_code FROM market.sector WHERE source = 'dc' AND name = ANY(%s)",
            (list(names),),
        ).fetchall()
    except Exception as e:
        safe_rollback(conn)  # 失败语句中毒事务，先回滚（评审 M4 残留）
        logger.warning(f"概念码表解析失败（按未命中处理）: {e}")
        return {}
    return {row[0]: f"CONCEPT:{row[1]}" for row in rows or []}


def scan_names_from_text(*texts, max_items: int = 60) -> list[str]:
    """从既有扫描数据文本（行业涨跌排名 / 概念热度，节点内已获取）抽板块名。

    只取表格「名称」列与「序号. 名称: 涨跌幅」形态——复用节点已获取的扫描
    范围，**不新增任何打名单或全市场扫描接口**；解析不到名称时返回空列表。
    """
    names = []
    for text in texts:
        for raw_line in str(text or "").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            name = None
            if line.startswith("|"):
                cells = [c.strip() for c in line.strip("|").split("|")]
                picked = [c for c in cells if c and not _is_metric(c)]
                if len(cells) >= 3 and picked:
                    name = picked[0]
            else:
                head = _LEADING_MARKER.sub("", line.split(":", 1)[0].split("：", 1)[0]).strip()
                if head and not _is_metric(head):
                    name = head
            if name and name not in _HEADER_WORDS and not name.isdigit():
                names.append(name)
            if len(names) >= max_items:
                return list(dict.fromkeys(names))
    return list(dict.fromkeys(names))


def _is_metric(cell: str) -> bool:
    return bool(_METRIC_CELL.fullmatch(cell)) or cell in {"-", "—"}


def prefetch_sector_event_study(
    scan_refs=(),
    raw_news: str = "",
    trade_date: str = "",
    asset_tickers: tuple[str, ...] = DEFAULT_ASSET_TICKERS,
    window_type: str = DEFAULT_WINDOW_TYPE,
    *,
    conn=None,
    predict_fn=None,
    route_lister=None,
    resolve_refs_fn=None,
    max_candidates: int = MAX_PREFETCH_CANDIDATES,
) -> dict:
    """板块层事件研究预取（与市场层同构，作用域 sector + 命中扫描范围）。

    Args:
        scan_refs: 板块扫描范围（路由引用或名称；名称经 `resolve_sector_refs` 解析）
        raw_news: 政策新闻流文本（仅作候选事实来源；历史统计只来自预测器）
        trade_date: 分析交易日（时点上界）
        asset_tickers / window_type: 固定资产与窗口（首期沪深 300 + post_event_5d）
        resolve_refs_fn: 引用解析注入点（默认 `resolve_sector_refs`）

    Returns:
        run_prefetch 结果 + `unresolved_refs`（未解析扫描范围）+ `route_error`
    """
    own_conn = False
    if conn is None and predict_fn is None:
        conn = open_conn()
        own_conn = conn is not None
    try:
        resolver = resolve_refs_fn or resolve_sector_refs
        try:
            refs, unresolved = resolver(scan_refs, conn)
        except Exception as e:  # 解析失败不阻塞：按无命中目标降级
            safe_rollback(conn)  # 解析中的失败语句中毒事务，先回滚（评审 M4 残留）
            logger.warning(f"板块扫描范围解析失败: {e}")
            refs, unresolved = [], [str(item) for item in (scan_refs or [])]

        # 归一 + **统一截断**（评审 M10）：候选侧 `EventRoute` 与预测侧
        # `run_prefetch(scope_refs=...)` 共用同一 refs，否则候选按未截断目标
        # 扫描、预测按截断后目标取统计，两侧目标集合不一致会污染历史统计
        refs, refs_meta = normalize_route_refs(refs)
        if not refs:
            result = run_prefetch(
                [], event_scope=SCOPE_SECTOR, scope_refs=(), trade_date=trade_date,
                asset_tickers=asset_tickers, window_type=window_type,
                conn=conn, predict_fn=predict_fn, max_candidates=max_candidates,
                source_label="板块事件预取",
            )
            result["filter_stats"] = dict(result.get("filter_stats") or {},
                                          route_refs=refs_meta)
            result["unresolved_refs"] = unresolved
            result["route_error"] = None
            return result

        route = sector_route(refs)
        if route is None:
            route_candidates, route_error = [], "事件研究路由不可用"
        else:
            route_candidates, route_error = fetch_route_candidates(
                conn, route, trade_date, route_lister=route_lister,
                source_label=SOURCE_ROUTE,
            )
        text_candidates = extract_text_candidates(raw_news, trade_date, SOURCE_POLICY_NEWS)
        result = run_prefetch(
            route_candidates + text_candidates,
            event_scope=SCOPE_SECTOR,
            scope_refs=refs,
            trade_date=trade_date,
            asset_tickers=asset_tickers,
            window_type=window_type,
            conn=conn,
            predict_fn=predict_fn,
            max_candidates=max_candidates,
            source_label="板块事件预取",
        )
    finally:
        if own_conn:
            close_conn(conn)
    # 截断/丢弃事实进入覆盖统计（评审 M10）
    result["filter_stats"] = dict(result.get("filter_stats") or {},
                                  route_refs=refs_meta)
    result["unresolved_refs"] = unresolved
    result["route_error"] = route_error
    return result
