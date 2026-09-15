"""
个股层事件研究预取（T4；方案第三章「分层消费与隔离」「板块/个股事件生产与消费」）

与市场层 `international_event_prefetch` 同构，差异只在作用域与命中目标：
- 作用域 `stock`，命中目标 = `company_of_interest`（统一市场后缀股票代码）
- **无命中目标时返回空列表**（不跨层借用沪深 300 CAR）
- 候选来源：个股路由正式事件（`event_scope='stock'` 且命中该股）+ 既有个股
  新闻文本（只作事实候选来源；历史统计只来自预测器）
- 不新增任何打名单或全市场扫描接口

依赖可注入：`conn` / `predict_fn` / `route_lister` / `resolve_refs_fn` 默认取
真实 PG 连接与事件研究预测器/路由查询；单测用 fake 替代。
"""

import logging

from AI.utils.event_prefetch_core import (
    DEFAULT_ASSET_TICKERS, DEFAULT_WINDOW_TYPE, MAX_PREFETCH_CANDIDATES, SCOPE_STOCK,
    close_conn, extract_text_candidates, fetch_route_candidates, normalize_refs,
    normalize_route_refs, open_conn, run_prefetch,
)

logger = logging.getLogger(__name__)

SOURCE_ROUTE = "事件库"
SOURCE_STOCK_NEWS = "个股新闻"


def stock_route(scope_refs=()):
    """个股路由（作用域 stock + 命中目标引用）。"""
    try:
        from AI.eventStudy.review.review_dao import EventRoute
        return EventRoute(scope=SCOPE_STOCK, scope_refs=tuple(scope_refs))
    except Exception as e:
        logger.warning(f"事件研究路由构造失败: {e}")
        return None


def resolve_stock_refs(ticker, conn=None) -> tuple[list[str], list[str]]:
    """`company_of_interest` → stock 路由引用。返回 (refs, 未解析项)。

    统一市场后缀代码（600519.SH / 000001.SZ）经同一归一化直通；无后缀的 6 位
    代码经 `StockUtils.normalize_code` 补后缀（沪深判据同上证/深证代码段），
    仍无法识别时返回未解析（不猜测市场，也不打全市场名单）。
    """
    text = str(ticker or "").strip()
    if not text:
        return [], []
    refs = normalize_refs([text])
    if not refs:
        try:
            from AI.utils.stock_utils import StockUtils
            normalized = StockUtils.normalize_code(text)
        except Exception as e:
            logger.warning(f"股票代码归一失败: {e}")
            normalized = text
        refs = normalize_refs([normalized])
    # 仅保留个股引用（越层引用不进 stock 路由）
    refs = [ref for ref in refs if ref.startswith("stock:")]
    return refs, ([] if refs else [text])


def prefetch_stock_event_study(
    ticker,
    news_text: str = "",
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
    """个股层事件研究预取（与市场层同构，作用域 stock + `company_of_interest`）。

    Args:
        ticker: `company_of_interest`（个股代码）
        news_text: 个股新闻文本（仅作候选事实来源；历史统计只来自预测器）
        trade_date: 分析交易日（时点上界）
        asset_tickers / window_type: 固定资产与窗口（首期沪深 300 + post_event_5d）
        resolve_refs_fn: 引用解析注入点（默认 `resolve_stock_refs`）

    Returns:
        run_prefetch 结果 + `unresolved_refs`（未解析目标）+ `route_error`
    """
    own_conn = False
    if conn is None and predict_fn is None:
        conn = open_conn()
        own_conn = conn is not None
    try:
        resolver = resolve_refs_fn or resolve_stock_refs
        try:
            refs, unresolved = resolver(ticker, conn)
        except Exception as e:  # 解析失败不阻塞：按无命中目标降级
            logger.warning(f"个股目标解析失败: {e}")
            refs, unresolved = [], [str(ticker)]
        # 归一 + **统一截断**（评审 M10）：候选侧 `EventRoute` 与预测侧
        # `run_prefetch(scope_refs=...)` 共用同一 refs（个股层通常单目标，
        # 与板块层同口径以防越层引用混入）
        refs, refs_meta = normalize_route_refs(refs)
        if not refs:
            result = run_prefetch(
                [], event_scope=SCOPE_STOCK, scope_refs=(), trade_date=trade_date,
                asset_tickers=asset_tickers, window_type=window_type,
                conn=conn, predict_fn=predict_fn, max_candidates=max_candidates,
                source_label="个股事件预取",
            )
            result["filter_stats"] = dict(result.get("filter_stats") or {},
                                          route_refs=refs_meta)
            result["unresolved_refs"] = unresolved
            result["route_error"] = None
            return result

        route = stock_route(refs)
        if route is None:
            route_candidates, route_error = [], "事件研究路由不可用"
        else:
            route_candidates, route_error = fetch_route_candidates(
                conn, route, trade_date, route_lister=route_lister,
                source_label=SOURCE_ROUTE,
            )
        text_candidates = extract_text_candidates(news_text, trade_date, SOURCE_STOCK_NEWS)
        result = run_prefetch(
            route_candidates + text_candidates,
            event_scope=SCOPE_STOCK,
            scope_refs=refs,
            trade_date=trade_date,
            asset_tickers=asset_tickers,
            window_type=window_type,
            conn=conn,
            predict_fn=predict_fn,
            max_candidates=max_candidates,
            source_label="个股事件预取",
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
