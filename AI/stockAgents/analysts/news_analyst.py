"""
LiveProfit 新闻分析师 (简化版)
分析股票相关新闻和公告对股价的影响。
"""

import logging
from datetime import datetime, timedelta
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from AI.dataflows import interface as dataflow
from AI.stockAgents.utils.instrument_utils import build_instrument_context
from AI.templates import load_output_format
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

        prompt = prompt.partial(
            current_date=current_date,
            ticker=ticker,
            company_name=company_name,
            market_name=market_info["market_name"],
            instrument_context=instrument_context,
            news_data=news_data,
        )

        result = llm.invoke(prompt.format_messages(messages=state["messages"]))
        report = result.content

        logger.info(f"[新闻分析师] 报告完成，长度: {len(report)}")
        return {
            "messages": [result],
            "news_report": report,
            "news_tool_call_count": tool_call_count + 1,
        }

    return news_analyst_node


def _get_company_name(ticker: str) -> str:
    try:
        from AI.dataflows.interface import get_china_stock_info
        info = get_china_stock_info(ticker)
        if "股票名称:" in info:
            return info.split("股票名称:")[1].split("\n")[0].strip()
    except Exception:
        pass
    return f"股票{ticker}"
