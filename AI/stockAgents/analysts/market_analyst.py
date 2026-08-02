"""
YoHo 市场分析师 (简化版)
分析股票技术面：价格走势、移动均线、MACD、RSI、布林带等。

移除：GoogleToolCallHandler、港股/美股代码路径、tool_logging 装饰器
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import ToolMessage, HumanMessage

from AI.stockAgents.utils.instrument_utils import build_instrument_context

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

        # 工具绑定
        tools = [toolkit.get_stock_market_data_unified]
        tool_call_count = state.get("stock_tech_tool_call_count", 0)

        # 构建提示词
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是一位专业的股票技术分析师。\n\n"
                "分析对象：{company_name}（{ticker}），{market_name}，货币：{currency_name}（{currency_symbol}）\n"
                "分析日期：{current_date}\n"
                "{instrument_context}\n\n"
                "可用工具：{tool_names}\n"
                "工作流程：\n"
                "1. 如果消息历史中没有工具结果，立即调用工具获取行情数据\n"
                "2. 收到工具数据后，立即生成完整技术分析报告\n"
                "3. 不要重复调用工具\n\n"
                "输出格式：\n"
                "## 一、股票基本信息\n"
                "## 二、技术指标分析（均线、MACD、RSI、布林带）\n"
                "## 三、价格趋势分析\n"
                "## 四、投资建议\n"
                "请使用中文，基于真实数据进行分析。"
            ),
            MessagesPlaceholder(variable_name="messages"),
        ])

        tool_names = []
        for t in tools:
            if hasattr(t, 'name'):
                tool_names.append(t.name)
            elif hasattr(t, '__name__'):
                tool_names.append(t.__name__)
            else:
                tool_names.append(str(t))

        prompt = prompt.partial(
            tool_names=", ".join(tool_names),
            current_date=current_date,
            ticker=ticker,
            company_name=company_name,
            market_name=market_info["market_name"],
            currency_name=market_info["currency_name"],
            currency_symbol=market_info["currency_symbol"],
            instrument_context=instrument_context,
        )

        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke({"messages": state["messages"]})

        # 处理结果
        if len(result.tool_calls) == 0:
            report = result.content
            logger.info(f"[市场分析师] 直接生成报告，长度: {len(report)}")
            return {
                "messages": [result],
                "stock_tech_report": report,
                "stock_tech_tool_call_count": tool_call_count + 1,
            }

        # 有工具调用 — 执行工具并生成分析报告
        logger.info(f"[市场分析师] 检测到工具调用，执行工具...")
        try:
            tool_messages = []
            for tc in result.tool_calls:
                tool_name = tc.get("name")
                tool_args = tc.get("args", {})
                tool_id = tc.get("id")

                tool_result = None
                for t in tools:
                    t_name = getattr(t, 'name', getattr(t, '__name__', ''))
                    if t_name == tool_name:
                        try:
                            tool_result = t.invoke(tool_args)
                        except Exception as te:
                            tool_result = f"工具执行失败: {te}"
                        break

                if tool_result is None:
                    tool_result = f"未找到工具: {tool_name}"

                tool_messages.append(ToolMessage(content=str(tool_result), tool_call_id=tool_id))

            # 生成分析报告
            analysis_prompt = f"""请基于以下工具获取的数据，生成 {company_name}（{ticker}）的完整技术分析报告。

分析对象：
- 公司：{company_name}
- 代码：{ticker}
- 市场：{market_info['market_name']}
- 货币：{market_info['currency_name']}（{market_info['currency_symbol']}）

输出要求：
# {company_name}（{ticker}）技术分析报告

## 一、股票基本信息
- 当前价格、涨跌幅、成交量

## 二、技术指标分析
- 均线系统（MA5/10/20/60）
- MACD（DIF/DEA/柱）
- RSI（超买/超卖）
- 布林带

## 三、价格趋势分析
- 短期/中期趋势、支撑/压力位

## 四、投资建议
- 评级（买入/持有/卖出）、目标价、风险提示

请使用中文，基于真实数据分析。"""

            messages = state["messages"] + [result] + tool_messages + [HumanMessage(content=analysis_prompt)]
            final_result = llm.invoke(messages)
            report = final_result.content
            logger.info(f"[市场分析师] 报告生成完成，长度: {len(report)}")

            return {
                "messages": [result] + tool_messages + [final_result],
                "stock_tech_report": report,
                "stock_tech_tool_call_count": tool_call_count + 1,
            }
        except Exception as e:
            logger.error(f"[市场分析师] 工具执行失败: {e}")
            return {
                "messages": [result],
                "stock_tech_report": f"分析生成失败: {e}",
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
