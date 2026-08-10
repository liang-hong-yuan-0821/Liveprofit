"""
市场层 — Layer 0a: 国际事件提取分析师
从宏观数据中识别重大事件，检索历史案例。
拆自原 international_news_analyst，专注事件识别 + 历史案例检索。
不依赖 ticker，仅使用 trade_date。
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import HumanMessage
from AI.dataflows import interface as dataflow

logger = logging.getLogger(__name__)


def create_international_event_extraction(llm, toolkit):

    def node(state):
        current_date = state["trade_date"]
        requested_date = state.get("requested_trade_date", current_date)
        date_correction = state.get("date_correction", "")
        logger.info(f"[国际事件提取] 开始分析 @ {current_date}")

        count = state.get("international_event_tool_call_count", 0)

        # 构建日期说明
        if date_correction:
            date_line = (
                f"分析日期：{current_date}\n"
                f"⚠️ 原始请求 {requested_date}，校正为 {current_date}（{date_correction}）。"
                f"请在报告中如实标注实际数据日期。\n"
            )
        else:
            date_line = f"分析日期：{current_date}\n"

        # 1. 直接调用 dataflows 函数获取宏观数据（使用纯日期）
        macro_news = dataflow.get_global_macro_news(current_date)
        central_bank = dataflow.get_central_bank_calendar(current_date)
        macro_indicators = dataflow.get_macro_indicators(current_date)
        commodity_fx = dataflow.get_commodity_fx_overview(days=10)

        # 2. 第一次 LLM：识别重大事件
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是一位资深国际宏观策略分析师，专注于从宏观数据中识别重大事件。\n\n"
                + date_line + "\n"
                "## 已获取的数据\n\n"
                "### 全球宏观新闻\n{macro_news}\n\n"
                "### 央行利率决议日历\n{central_bank}\n\n"
                "### 宏观经济指标\n{macro_indicators}\n\n"
                "### 大宗商品与汇率概览\n{commodity_fx}\n\n"
                "任务：\n"
                "1. 识别当前最重要的宏观事件（3-5个）\n"
                "2. 对每个事件，给出：事件名称、事件类型（央行政策/地缘政治/经济数据/商品价格/其他）、"
                "一句话事件描述、利好/利空/中性判断、影响方向\n"
                "3. 在报告末尾，输出一个【事件描述摘要】块，包含所有事件的简洁关键词描述"
                "（用于检索相似历史案例，每行一个事件）\n\n"
                "输出格式：\n"
                "# 国际事件提取报告\n\n"
                "## 一、已识别的重大事件\n"
                "| 事件 | 类型 | 判断 | 影响方向 | 置信度 |\n\n"
                "## 二、事件详细描述\n"
                "（每个事件展开说明，包括事件背景和初步影响评估）\n\n"
                "【事件描述摘要】\n"
                "（所有事件的关键词描述，每行一个事件，用于历史案例检索）\n"
                "请使用中文。数据不可用时如实标注，不编造。"
            ),
            MessagesPlaceholder(variable_name="messages"),
        ])

        prompt = prompt.partial(
            macro_news=macro_news,
            central_bank=central_bank,
            macro_indicators=macro_indicators,
            commodity_fx=commodity_fx,
        )
        result = llm.invoke(prompt.format_messages(messages=state["messages"]))
        event_identification = result.content

        # 3. 提取事件描述摘要，检索历史案例
        events_desc = _extract_events_desc(event_identification)
        history_results = dataflow.get_event_calendar_history(events_desc)

        # 4. 第二次 LLM：整合历史案例，输出最终事件提取报告
        final_prompt = (
            "请基于以下事件识别结果和历史案例检索结果，生成完整的国际事件提取报告。\n\n"
            f"## 事件识别结果\n{event_identification}\n\n"
            f"## 历史案例检索结果\n{history_results}\n\n"
            "输出格式：\n"
            "# 国际事件提取报告\n\n"
            "## 〇、事件速览\n"
            "（3-5 句话概括当前全球宏观核心矛盾 + 主要事件）\n\n"
            "## 一、已识别的重大事件\n"
            "| 事件 | 类型 | 判断 | 影响方向 | 置信度 |\n\n"
            "## 二、历史案例类比\n"
            "| 当前事件 | 最相似历史案例 | 相似度 | 当时市场反应 | 参考意义 |\n\n"
            "## 三、事件影响方向初判\n"
            "（每个事件的利好/利空/中性判断 + 传导链条方向）\n\n"
            "请使用中文。数据不可用时如实标注。"
        )
        messages = state["messages"] + [result] + [HumanMessage(content=final_prompt)]
        final_result = llm.invoke(messages)
        final_report = final_result.content

        logger.info(f"[国际事件提取] 报告完成，长度: {len(final_report)}")
        return {
            "messages": [result] + [final_result],
            "international_event_report": final_report,
            "international_event_tool_call_count": count + 1,
        }

    return node


def _extract_events_desc(report: str) -> str:
    """从事件识别报告中提取事件描述摘要用于历史案例检索"""
    import re
    # 尝试提取【事件描述摘要】块
    match = re.search(r'【事件描述摘要】\s*\n(.*?)(?=\n#|\Z)', report, re.DOTALL)
    if match:
        return match.group(1).strip()[:1000]
    # 兜底：返回报告后 1000 字作为事件描述
    return report[-1000:]
