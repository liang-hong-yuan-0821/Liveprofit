"""
市场层 — Layer 1: 韩国技术分析师
分析 KOSPI / KOSDAQ 技术面。
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from AI.dataflows import interface as dataflow
from AI.templates import load_output_format
from AI.utils.prompts import DEFAULT_PROMPTS, system_message

logger = logging.getLogger(__name__)


def create_kr_tech_analyst(llm, toolkit):

    def node(state):
        current_date = state["trade_date"]
        requested_date = state.get("requested_trade_date", current_date)
        date_correction = state.get("date_correction", "")
        logger.info(f"[韩国技术分析] 开始分析 @ {current_date}")

        count = state.get("kr_tech_tool_call_count", 0)

        # 构建日期说明
        if date_correction:
            date_line = (
                f"分析日期：{current_date}\n"
                f"⚠️ 原始请求 {requested_date}，校正为 {current_date}。"
                f"请在报告中如实标注实际数据日期。\n"
            )
        else:
            date_line = f"分析日期：{current_date}\n"

        # 直接调用 dataflows 函数获取数据
        tech_indices = dataflow.get_all_tech_indices(days=10)
        foreign_flow = dataflow.get_kr_foreign_flow(days=10)

        output_format = load_output_format("market", "tech_common")

        prompt = ChatPromptTemplate.from_messages([
                system_message(
                    state.get("_current_node_id"),
                    lambda: DEFAULT_PROMPTS["market:KR Tech Analyst"]
                    .replace("{date_line}", date_line)
                    .replace("{output_format}", output_format),
                ),
            MessagesPlaceholder(variable_name="messages"),
        ])

        prompt = prompt.partial(
            tech_indices=tech_indices,
            foreign_flow=foreign_flow,
        )
        result = llm.invoke(prompt.format_messages(messages=state["messages"]))
        report = result.content

        logger.info(f"[韩国技术分析] 报告完成，长度: {len(report)}")
        return {
            "messages": [result],
            "kr_tech_report": report,
            "kr_tech_tool_call_count": count + 1,
        }

    return node
