"""
LiveProfit 新闻分析师 (简化版)
分析股票相关新闻和公告对股价的影响。

T4 改造（方案第三章）：消费个股事件预取（作用域 stock + 命中
`company_of_interest`），Prompt 变量注入 `stock_event_prefetch` 历史统计块；
节点返回值新增 `stock_events`。系统提示词文本规则属 T6（`AI/utils/prompts.py`）。
"""

import logging
from datetime import datetime, timedelta
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from AI.dataflows import interface as dataflow
from AI.stockAgents.analysts.stock_event_prefetch import prefetch_stock_event_study
from AI.stockAgents.utils.instrument_utils import build_instrument_context
from AI.templates import load_output_format
from AI.utils.event_prefetch_core import (
    SCOPE_STOCK, candidates_to_events, degraded_result, render_prefetch_block,
)
from AI.utils.prompts import DEFAULT_PROMPTS, system_message

logger = logging.getLogger(__name__)


def create_news_analyst(llm, toolkit):

    def news_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]

        logger.info(f"[新闻分析师] 开始分析 {ticker} @ {current_date}")

        from AI.utils.stock_utils import StockUtils
        market_info = StockUtils.get_market_info(ticker)
        instrument_context = build_instrument_context(ticker)

        company_name = _get_company_name(ticker)
        tool_call_count = state.get("news_tool_call_count", 0)

        # 直接调用 dataflows 函数获取新闻数据（回看30天）
        try:
            start_date = (datetime.strptime(current_date, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            start_date = "2020-01-01"
        news_data = dataflow.get_china_news(ticker, start_date, current_date)

        # 个股事件预取：命中 company_of_interest（无命中目标时返回空列表，
        # 不跨层借用沪深 300 CAR；不新增打名单接口）
        try:
            prefetch = prefetch_stock_event_study(
                ticker=ticker, news_text=news_data, trade_date=current_date,
            )
            # 渲染 + 结构化事件组装纳入 try（评审 M17 残留）：任一异常不得中断节点
            prefetch_block = render_prefetch_block(
                prefetch, heading="个股事件研究历史统计（预取）")
            stock_events = candidates_to_events(
                prefetch.get("candidates") or [],
                event_scope=SCOPE_STOCK, trade_date=current_date,
                scope_refs=prefetch.get("scope_refs") or (),
            )
        except Exception as e:  # 预取/渲染异常兜底：仅缺历史统计，不影响新闻分析
            logger.warning(f"[新闻分析师] 事件研究预取异常（跳过历史统计）: {e}")
            prefetch = degraded_result(
                event_scope=SCOPE_STOCK, trade_date=current_date,
                reason=f"预取异常: {e}",
            )
            prefetch_block = ""
            stock_events = []
        logger.info(
            f"[新闻分析师] 预取完成：状态={prefetch.get('status')} "
            f"候选={prefetch.get('candidate_count')}"
        )

        date_line = f"分析日期：{current_date}\n"
        output_format = load_output_format("stock", "news_analyst")

        prompt = ChatPromptTemplate.from_messages([
                system_message(
                    state.get("_current_node_id"),
                    lambda: DEFAULT_PROMPTS["stock:News Analyst"]
                    .replace("{date_line}", date_line)
                    .replace("{output_format}", output_format),
                ),
            MessagesPlaceholder(variable_name="messages"),
        ])

        prompt = prompt.partial(**build_stock_prompt_variables(
            current_date=current_date,
            ticker=ticker,
            company_name=company_name,
            market_name=market_info["market_name"],
            instrument_context=instrument_context,
            news_data=news_data,
            event_prefetch_block=prefetch_block,
        ))

        result = llm.invoke(prompt.format_messages(messages=state["messages"]))
        report = result.content

        logger.info(f"[新闻分析师] 报告完成，长度: {len(report)}")
        return {
            "messages": [result],
            "news_report": report,
            "stock_events": stock_events,
            "news_tool_call_count": tool_call_count + 1,
        }

    return news_analyst_node


def build_stock_prompt_variables(*, current_date, ticker, company_name, market_name,
                                 instrument_context, news_data, event_prefetch_block) -> dict:
    """个股新闻 Prompt 变量（含事件研究历史统计块）。

    `news_data` 为当期新闻事实流（原文）；`stock_event_prefetch` 为作用域路由的
    历史统计块（只含预取统计，不引用新闻原文冒充 CAR 证据）。
    """
    return {
        "current_date": current_date,
        "ticker": ticker,
        "company_name": company_name,
        "market_name": market_name,
        "instrument_context": instrument_context,
        "news_data": news_data,
        "stock_event_prefetch": event_prefetch_block,
    }


def _get_company_name(ticker: str) -> str:
    try:
        from AI.dataflows.interface import get_china_stock_info
        info = get_china_stock_info(ticker)
        if "股票名称:" in info:
            return info.split("股票名称:")[1].split("\n")[0].strip()
    except Exception:
        pass
    return f"股票{ticker}"
