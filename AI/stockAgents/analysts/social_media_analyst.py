"""
YoHo 社交媒体/情绪分析师 (简化版)
分析市场情绪和投资者情绪。Tushare 无社交情绪数据，该 Agent 基于已有报告进行综合评估。

移除：GoogleToolCallHandler、港股/美股代码路径、Reddit 情绪工具
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from AI.stockAgents.utils.instrument_utils import build_instrument_context
from AI.templates import load_output_format

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

        output_format = load_output_format("stock", "social_media_analyst")

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是一位专业的市场情绪分析师。\n\n"
                "分析对象：{company_name}（{ticker}），{market_name}\n"
                "分析日期：{current_date}\n"
                "{instrument_context}\n\n"
                "注意：当前系统仅支持 Tushare 数据源，暂无专门的社交媒体情绪数据接口。\n"
                "请基于以下可用信息进行综合情绪评估：\n"
                "1. 如果当前状态中有市场报告、新闻报告、基本面报告，请从中提取市场情绪信号\n"
                "2. 分析成交量变化、涨跌幅、换手率等指标反映的市场情绪\n"
                "3. 从新闻标题和内容中判断舆论倾向\n\n"
                "输出格式：\n"
                + output_format
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
