"""
市场层 — Layer 0b: 国际新闻影响分析师
基于上游事件提取结果（含历史案例），分析事件的传导链条和系统性风险。
由原 international_news_analyst 拆分而来，专注影响分析。
不依赖 ticker，仅使用 trade_date。
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

logger = logging.getLogger(__name__)


def create_international_news_analyst(llm, toolkit):

    def node(state):
        current_date = state["trade_date"]
        requested_date = state.get("requested_trade_date", current_date)
        date_correction = state.get("date_correction", "")
        logger.info(f"[国际新闻影响分析] 开始分析 @ {current_date}")

        count = state.get("international_news_tool_call_count", 0)

        # 构建日期说明
        if date_correction:
            date_line = (
                f"分析日期：{current_date}\n"
                f"⚠️ 原始请求 {requested_date}，校正为 {current_date}。"
                f"请在报告中如实标注实际数据日期。\n"
            )
        else:
            date_line = f"分析日期：{current_date}\n"

        # 读取上游事件提取结果（含事件列表 + 历史案例）
        event_report = state.get("international_event_report", "（事件提取数据暂不可用）")

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是一位资深国际宏观策略分析师，专注于分析宏观事件的市场影响。\n\n"
                + date_line + "\n"
                "## 上游事件提取结果（含历史案例）\n"
                "{event_report}\n\n"
                "任务：基于上述事件提取结果，分析事件对全球金融市场的传导链条和系统性风险。\n\n"
                "分析要求：\n"
                "- 完整传导链条：事件 → 中间变量（利率/汇率/大宗商品）→ 受影响行业方向\n"
                "- 必须考虑存量资金下的'逻辑利好 vs 资金利空'背离（资金虹吸/跷跷板效应）\n"
                "- 历史案例类比需给出相似度 + 当时市场反应 + 对当下的参考意义\n"
                "- 明确标注系统性风险等级（低/中/高）和流动性危机信号\n"
                "- 数据不可用时如实标注，不编造\n"
                "- ⚠️ 宏观事件解读属于定性分析，应明确标注'基于公开信息的方向性判断，不构成投资建议'\n\n"
                "输出格式（结论前置）：\n"
                "# 国际金融市场新闻分析报告\n\n"
                "## 〇、全球宏观速览\n"
                "（3-5 句话概括当前全球宏观核心矛盾 + 风险偏好方向）\n"
                "> 全球风险偏好: <进攻/中性/避险>\n\n"
                "## 一、传导链条分析\n"
                "（事件 → 中间变量 → 行业方向；含资金虹吸/跷跷板提示）\n\n"
                "## 二、系统性风险评估\n"
                "（流动性信号 / 风险等级 / 是否构成系统性风险）\n\n"
                "## 三、市场状态标签与仓位基调\n"
                "请使用中文。"
            ),
            MessagesPlaceholder(variable_name="messages"),
        ])

        prompt = prompt.partial(
            event_report=event_report,
        )
        result = llm.invoke(prompt.format_messages(messages=state["messages"]))
        report = result.content

        logger.info(f"[国际新闻影响分析] 报告完成，长度: {len(report)}")
        return {
            "messages": [result],
            "international_news_report": report,
            "international_news_tool_call_count": count + 1,
        }

    return node
