"""
市场层 — Layer 1: 韩国技术分析师
分析 KOSPI / KOSDAQ 技术面。
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from AI.dataflows import interface as dataflow
from AI.templates import load_output_format

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
            (
                "system",
                "你是一位专注韩国市场的技术分析师。\n\n"
                + date_line + "\n"
                "## 已获取的数据\n\n"
                "### 全球科技指数（含韩国部分）\n{tech_indices}\n\n"
                "### 外资流向\n{foreign_flow}\n\n"
                "分析维度：\n"
                "- KOSPI / KOSDAQ：趋势方向、均线系统(MA5/10/20/60)、量能、RSI/MACD\n"
                "- 外资流向（韩国市场外资占比高，是重要信号）\n"
                "- 与美股科技的相关性（韩股 = 美股科技beta）\n\n"
                "数据不可用时标注'数据暂不可用'\n\n"
                "输出格式：\n"
                "# 韩国市场技术分析报告\n\n"
                + output_format
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
