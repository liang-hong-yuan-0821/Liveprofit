"""
LiveProfit 市场分析师 (简化版)
分析股票技术面：价格走势、移动均线、MACD、RSI、布林带等。
"""

import logging
from datetime import datetime, timedelta
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from AI.dataflows import interface as dataflow
from AI.stockAgents.utils.instrument_utils import build_instrument_context
from AI.templates import load_output_format

logger = logging.getLogger(__name__)


def create_market_analyst(llm, toolkit):

    def market_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]

        logger.info(f"[市场分析师] 开始分析 {ticker} @ {current_date}")

        # 获取市场信息
        from AI.utils.stock_utils import StockUtils
        market_info = StockUtils.get_market_info(ticker)

        # 获取公司名称
        company_name = _get_company_name(ticker)
        instrument_context = build_instrument_context(ticker)

        tool_call_count = state.get("stock_tech_tool_call_count", 0)

        # 计算回看窗口：从 trade_date 往前 365 天
        try:
            end_date = current_date
            start_date = (datetime.strptime(current_date, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            start_date = "2020-01-01"
            end_date = current_date

        # 直接调用 dataflows 函数获取行情数据
        market_data = dataflow.get_china_stock_data(ticker, start_date, end_date)

        output_format = load_output_format("stock", "market_analyst")

        # 构建提示词
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是一位专业的股票技术分析师。\n\n"
                "分析对象：{company_name}（{ticker}），{market_name}，货币：{currency_name}（{currency_symbol}）\n"
                "分析日期：{current_date}\n"
                "{instrument_context}\n\n"
                "## 已获取的数据\n\n"
                "### 股票行情数据（{start_date} ~ {end_date}）\n{market_data}\n\n"
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
            currency_name=market_info["currency_name"],
            currency_symbol=market_info["currency_symbol"],
            instrument_context=instrument_context,
            start_date=start_date,
            end_date=end_date,
            market_data=market_data,
        )

        result = llm.invoke(prompt.format_messages(messages=state["messages"]))
        report = result.content
        logger.info(f"[市场分析师] 报告生成完成，长度: {len(report)}")

        return {
            "messages": [result],
            "stock_tech_report": report,
            "stock_tech_tool_call_count": tool_call_count + 1,
        }

    return market_analyst_node


def _get_company_name(ticker: str) -> str:
    """获取 A 股公司名称"""
    try:
        from AI.dataflows.interface import get_china_stock_info
        info = get_china_stock_info(ticker)
        if "股票名称:" in info:
            return info.split("股票名称:")[1].split("\n")[0].strip()
    except Exception:
        pass
    return f"股票{ticker}"
