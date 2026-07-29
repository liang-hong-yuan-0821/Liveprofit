"""
YoHo 新闻分析师 (简化版)
分析股票相关新闻和公告对股价的影响。

移除：unified_news_tool、GoogleToolCallHandler、港股/美股代码路径、tool_logging
"""

import logging
from datetime import datetime, timedelta
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import ToolMessage, HumanMessage

from AI.agents.utils.instrument_utils import build_instrument_context

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
        tools = [toolkit.get_stock_news_unified]
        tool_call_count = state.get("news_tool_call_count", 0)

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是一位专业的财经新闻分析师。\n\n"
                "分析对象：{company_name}（{ticker}），{market_name}\n"
                "分析日期：{current_date}\n"
                "{instrument_context}\n\n"
                "可用工具：{tool_names}\n"
                "工作流程：\n"
                "1. 如果消息历史中没有工具结果，立即调用工具获取新闻数据\n"
                "2. 收到工具数据后，立即生成完整新闻分析报告\n"
                "3. 不要重复调用工具\n"
                "注意：如果工具返回提示新闻不可用，请在报告中如实说明。\n\n"
                "输出格式：\n"
                "## 一、近期重要新闻\n"
                "## 二、公告分析\n"
                "## 三、新闻对股价影响评估\n"
                "## 四、综合观点\n"
                "请使用中文。"
            ),
            MessagesPlaceholder(variable_name="messages"),
        ])

        tool_names = []
        for t in tools:
            tn = getattr(t, 'name', getattr(t, '__name__', str(t)))
            tool_names.append(tn)

        prompt = prompt.partial(
            tool_names=", ".join(tool_names),
            current_date=current_date,
            ticker=ticker,
            company_name=company_name,
            market_name=market_info["market_name"],
            instrument_context=instrument_context,
        )

        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke({"messages": state["messages"]})

        if len(result.tool_calls) == 0:
            report = result.content
            return {
                "messages": [result],
                "news_report": report,
                "news_tool_call_count": tool_call_count + 1,
            }

        try:
            tool_messages = []
            for tc in result.tool_calls:
                tool_name = tc.get("name")
                tool_args = tc.get("args", {})
                tool_id = tc.get("id")
                tool_result = None
                for t in tools:
                    tn = getattr(t, 'name', getattr(t, '__name__', ''))
                    if tn == tool_name:
                        try:
                            tool_result = t.invoke(tool_args)
                        except Exception as te:
                            tool_result = f"工具执行失败: {te}"
                        break
                if tool_result is None:
                    tool_result = f"未找到工具: {tool_name}"
                tool_messages.append(ToolMessage(content=str(tool_result), tool_call_id=tool_id))

            analysis_prompt = f"""请基于以下新闻数据，生成 {company_name}（{ticker}）的新闻分析报告。

格式要求：
# {company_name}（{ticker}）新闻分析报告

## 一、近期重要新闻
## 二、公告分析
## 三、新闻对股价影响评估
## 四、综合观点

请使用中文。如果新闻数据不可用，请如实说明并在其他方面提供分析。"""

            messages = state["messages"] + [result] + tool_messages + [HumanMessage(content=analysis_prompt)]
            final_result = llm.invoke(messages)
            report = final_result.content

            return {
                "messages": [result] + tool_messages + [final_result],
                "news_report": report,
                "news_tool_call_count": tool_call_count + 1,
            }
        except Exception as e:
            logger.error(f"[新闻分析师] 工具执行失败: {e}")
            return {
                "messages": [result],
                "news_report": f"分析生成失败: {e}",
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
