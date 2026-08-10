"""
市场层 — Layer 1: 韩国新闻分析师
分析韩国央行政策、出口数据、权重股动态对 KOSPI/KOSDAQ 的影响。
一期工具占位，Agent 靠 LLM 内部知识 + 国际新闻上下文推断。
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from AI.dataflows import interface as dataflow

logger = logging.getLogger(__name__)


def create_kr_news_analyst(llm, toolkit):

    def node(state):
        current_date = state["trade_date"]
        requested_date = state.get("requested_trade_date", current_date)
        date_correction = state.get("date_correction", "")
        logger.info(f"[韩国新闻分析] 开始分析 @ {current_date}")

        count = state.get("kr_news_tool_call_count", 0)

        # 构建日期说明
        if date_correction:
            date_line = (
                f"分析日期：{current_date}\n"
                f"⚠️ 原始请求 {requested_date}，校正为 {current_date}（{date_correction}）。"
                f"请在报告中如实标注实际数据日期。\n"
            )
        else:
            date_line = f"分析日期：{current_date}\n"

        # 直接调用 dataflows 函数获取数据（使用纯日期，一期为占位实现）
        macro_news = dataflow.get_kr_macro_news(current_date)
        export_data = dataflow.get_kr_export_data(current_date)

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是一位专注韩国市场的宏观分析师。\n\n"
                + date_line + "\n"
                "## 已获取的数据\n\n"
                "### 韩国财经要闻\n{macro_news}\n\n"
                "### 韩国出口数据\n{export_data}\n\n"
                "注意事项：\n"
                "- 一期韩国数据源有限，工具可能返回'数据不可用'——此时请基于你的训练知识做方向性判断\n"
                "- 关注韩国央行利率决议、半导体/汽车出口数据、三星/SK海力士等权重股动态\n"
                "- 关注韩元汇率波动对出口企业的影响\n"
                "- 数据不可用时如实标注\n\n"
                "输出格式：\n"
                "# 韩国市场新闻分析报告\n\n"
                "## 一、政策与央行动态\n"
                "## 二、出口与产业\n"
                "## 三、权重股动态\n"
                "## 四、对KOSPI/KOSDAQ的影响判断\n"
                "请使用中文。"
            ),
            MessagesPlaceholder(variable_name="messages"),
        ])

        prompt = prompt.partial(
            macro_news=macro_news,
            export_data=export_data,
        )
        result = llm.invoke(prompt.format_messages(messages=state["messages"]))
        report = result.content

        logger.info(f"[韩国新闻分析] 报告完成，长度: {len(report)}")
        return {
            "messages": [result],
            "kr_news_report": report,
            "kr_news_tool_call_count": count + 1,
        }

    return node
