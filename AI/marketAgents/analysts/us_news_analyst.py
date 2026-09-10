"""
市场层 — Layer 1: 美国新闻分析师
分析美国本土事件对美股的影响：美联储决议、经济数据、财报季、VIX。
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from AI.dataflows import interface as dataflow
from AI.templates import load_output_format
from AI.utils.prompts import DEFAULT_PROMPTS, system_message

logger = logging.getLogger(__name__)


def create_us_news_analyst(llm, toolkit):

    def node(state):
        current_date = state["trade_date"]
        requested_date = state.get("requested_trade_date", current_date)
        date_correction = state.get("date_correction", "")
        logger.info(f"[美国新闻分析] 开始分析 @ {current_date}")

        count = state.get("us_news_tool_call_count", 0)

        # 构建日期说明
        if date_correction:
            date_line = (
                f"分析日期：{current_date}\n"
                f"⚠️ 原始请求 {requested_date}，校正为 {current_date}（{date_correction}）。"
                f"请在报告中如实标注实际数据日期。\n"
            )
        else:
            date_line = f"分析日期：{current_date}\n"

        # 直接调用 dataflows 函数获取数据（使用纯日期）
        macro_news = dataflow.get_us_macro_news(current_date)
        econ_calendar = dataflow.get_us_economic_calendar(current_date)
        vix_data = dataflow.get_vix_index()

        output_format = load_output_format("market", "news_common")

        prompt = ChatPromptTemplate.from_messages([
                system_message(
                    state.get("_current_node_id"),
                    lambda: DEFAULT_PROMPTS["market:US News Analyst"]
                    .replace("{date_line}", date_line)
                    .replace("{output_format}", output_format),
                ),
            MessagesPlaceholder(variable_name="messages"),
        ])

        prompt = prompt.partial(
            macro_news=macro_news,
            econ_calendar=econ_calendar,
            vix_data=vix_data,
        )
        result = llm.invoke(prompt.format_messages(messages=state["messages"]))
        report = result.content

        logger.info(f"[美国新闻分析] 报告完成，长度: {len(report)}")
        return {
            "messages": [result],
            "us_news_report": report,
            "us_news_tool_call_count": count + 1,
        }

    return node
