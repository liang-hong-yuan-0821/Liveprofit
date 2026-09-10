"""
LiveProfit 社交媒体/情绪分析师 (简化版)
分析市场情绪和投资者情绪。Tushare 无社交情绪数据，该 Agent 基于已有报告进行综合评估。

移除：GoogleToolCallHandler、港股/美股代码路径、Reddit 情绪工具
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from AI.stockAgents.utils.instrument_utils import build_instrument_context
from AI.templates import load_output_format
from AI.utils.prompts import DEFAULT_PROMPTS, system_message

logger = logging.getLogger(__name__)


def create_social_media_analyst(llm, toolkit):

    def social_media_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]

        logger.info(f"[情绪分析师] 开始分析 {ticker} @ {current_date}")

        from AI.utils.stock_utils import StockUtils
        market_info = StockUtils.get_market_info(ticker)
        instrument_context = build_instrument_context(ticker)

        company_name = _get_company_name(ticker)

        date_line = f"分析日期：{current_date}\n"
        output_format = load_output_format("stock", "social_media_analyst")

        prompt = ChatPromptTemplate.from_messages([
                system_message(
                    state.get("_current_node_id"),
                    lambda: DEFAULT_PROMPTS["stock:Social Analyst"]
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
        )

        result = llm.invoke(prompt.format(messages=state["messages"]))
        report = result.content

        logger.info(f"[情绪分析师] 报告生成完成，长度: {len(report)}")

        return {
            "messages": [result],
            "sentiment_report": report,
            "sentiment_tool_call_count": state.get("sentiment_tool_call_count", 0),
        }

    return social_media_analyst_node


def _get_company_name(ticker: str) -> str:
    try:
        from AI.dataflows.interface import get_china_stock_info
        info = get_china_stock_info(ticker)
        if "股票名称:" in info:
            return info.split("股票名称:")[1].split("\n")[0].strip()
    except Exception:
        pass
    return f"股票{ticker}"
