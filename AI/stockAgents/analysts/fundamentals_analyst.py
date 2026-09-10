"""
LiveProfit 基本面分析师 (简化版)
分析公司财务数据：ROE、ROA、毛利率、EPS、营收、利润、资产负债等。
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from AI.dataflows import interface as dataflow
from AI.stockAgents.utils.instrument_utils import build_instrument_context
from AI.templates import load_output_format
from AI.utils.prompts import DEFAULT_PROMPTS, system_message

logger = logging.getLogger(__name__)


def create_fundamentals_analyst(llm, toolkit):

    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]

        logger.info(f"[基本面分析师] 开始分析 {ticker} @ {current_date}")

        from AI.utils.stock_utils import StockUtils
        market_info = StockUtils.get_market_info(ticker)
        instrument_context = build_instrument_context(ticker)

        # 获取公司名称
        company_name = _get_company_name(ticker)

        tool_call_count = state.get("fundamentals_tool_call_count", 0)

        # 直接调用 dataflows 函数获取财务数据
        fundamentals_data = dataflow.get_china_fundamentals(ticker, current_date)

        date_line = f"分析日期：{current_date}\n"
        output_format = load_output_format("stock", "fundamentals_analyst")

        prompt = ChatPromptTemplate.from_messages([
                system_message(
                    state.get("_current_node_id"),
                    lambda: DEFAULT_PROMPTS["stock:Fundamentals Analyst"]
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
            currency_name=market_info["currency_name"],
            currency_symbol=market_info["currency_symbol"],
            instrument_context=instrument_context,
            fundamentals_data=fundamentals_data,
        )

        result = llm.invoke(prompt.format_messages(messages=state["messages"]))
        report = result.content

        logger.info(f"[基本面分析师] 报告完成，长度: {len(report)}")
        return {
            "messages": [result],
            "fundamentals_report": report,
            "fundamentals_tool_call_count": tool_call_count + 1,
        }

    return fundamentals_analyst_node


def _get_company_name(ticker: str) -> str:
    try:
        from AI.dataflows.interface import get_china_stock_info
        info = get_china_stock_info(ticker)
        if "股票名称:" in info:
            return info.split("股票名称:")[1].split("\n")[0].strip()
    except Exception:
        pass
    return f"股票{ticker}"
