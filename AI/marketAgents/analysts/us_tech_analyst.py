"""
市场层 — Layer 1: 美国技术分析师
分析标普500 / 纳指 / 道指技术面，判断美股趋势方向。
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from AI.dataflows import interface as dataflow

logger = logging.getLogger(__name__)


def create_us_tech_analyst(llm, toolkit):

    def node(state):
        current_date = state["trade_date"]
        requested_date = state.get("requested_trade_date", current_date)
        date_correction = state.get("date_correction", "")
        logger.info(f"[美国技术分析] 开始分析 @ {current_date}")

        count = state.get("us_tech_tool_call_count", 0)

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
        index_data = dataflow.get_us_index_data(days=20)
        sector_rotation = dataflow.get_us_sector_rotation(days=20)

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是一位专注美股的技术分析师。\n\n"
                + date_line + "\n"
                "## 已获取的数据\n\n"
                "### 美股三大指数数据\n{index_data}\n\n"
                "### 美股板块轮动数据\n{sector_rotation}\n\n"
                "分析维度：\n"
                "- 标普500 / 纳斯达克 / 道琼斯：趋势方向、均线系统(MA5/10/20/60)、量能变化、RSI/MACD\n"
                "- 板块结构：科技 vs 价值、大盘 vs 小盘轮动\n"
                "- 跨市场定位：美股在全球风险资产中的相对强弱\n\n"
                "数据不可用时标注'数据暂不可用，以下分析基于公开信息'\n\n"
                "输出格式：\n"
                "# 美国市场技术分析报告\n\n"
                "## 一、标普500\n"
                "（趋势、均线、量能、RSI/MACD）\n"
                "## 二、纳斯达克\n"
                "## 三、道琼斯\n"
                "## 四、板块与风格\n"
                "## 五、综合研判\n"
                "请使用中文。"
            ),
            MessagesPlaceholder(variable_name="messages"),
        ])

        prompt = prompt.partial(
            index_data=index_data,
            sector_rotation=sector_rotation,
        )
        result = llm.invoke(prompt.format_messages(messages=state["messages"]))
        report = result.content

        logger.info(f"[美国技术分析] 报告完成，长度: {len(report)}")
        return {
            "messages": [result],
            "us_tech_report": report,
            "us_tech_tool_call_count": count + 1,
        }

    return node
