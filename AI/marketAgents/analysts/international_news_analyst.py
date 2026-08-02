"""
市场层 — Layer 0: 国际新闻分析师
合并宏观事件驱动层 + 历史案例分析。
扫描跨国宏观事件，逐事件检索历史案例，输出传导链条 + 风险评估。
不依赖 ticker，仅使用 trade_date。
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import ToolMessage, HumanMessage

logger = logging.getLogger(__name__)


def create_international_news_analyst(llm, toolkit):

    def node(state):
        current_date = state["trade_date"]
        logger.info(f"[国际新闻分析] 开始分析 @ {current_date}")

        tools = [
            toolkit.get_global_macro_news,
            toolkit.get_central_bank_calendar,
            toolkit.get_macro_indicators,
            toolkit.get_commodity_fx_overview,
            toolkit.get_event_calendar_history,
        ]
        count = state.get("international_news_tool_call_count", 0)

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是一位资深国际宏观策略分析师，专注于跨国宏观事件扫描和历史案例类比。\n\n"
                "分析日期：{current_date}\n\n"
                "可用工具：{tool_names}\n\n"
                "工作流程：\n"
                "1. 调用 get_global_macro_news 获取近期全球宏观要闻\n"
                "2. 调用 get_central_bank_calendar 获取主要央行利率决议日历\n"
                "3. 调用 get_macro_indicators 获取关键宏观经济指标\n"
                "4. 调用 get_commodity_fx_overview 获取大宗商品和汇率概览\n"
                "5. 对每个识别出的重大事件，调用 get_event_calendar_history 检索历史案例\n"
                "6. 综合生成分析报告\n\n"
                "分析要求：\n"
                "- 逐事件给出'利好/利空/中性'判断 + 影响方向\n"
                "- 完整传导链条：事件 → 中间变量（利率/汇率/大宗商品）→ 受影响行业方向\n"
                "- 必须考虑存量资金下的'逻辑利好 vs 资金利空'背离（资金虹吸/跷跷板效应）\n"
                "- 历史案例类比需给出相似度 + 当时市场反应 + 对当下的参考意义\n"
                "- 明确标注系统性风险等级（低/中/高）和流动性危机信号\n"
                "- 数据不可用时如实标注，不编造\n"
                "- ⚠️ 宏观事件解读属于定性分析，应明确标注'基于公开信息的方向性判断，不构成投资建议'\n\n"
                "输出格式：\n"
                "# 国际金融市场新闻分析报告\n\n"
                "## 一、当前重大宏观事件扫描\n"
                "| 事件 | 判断 | 受影响行业方向 | 置信度 |\n\n"
                "## 二、历史案例类比\n"
                "| 当前事件 | 最相似历史案例 | 相似度 | 当时市场反应 | 参考意义 |\n\n"
                "## 三、传导链条分析\n"
                "（事件 → 中间变量 → 行业方向；含资金虹吸/跷跷板提示）\n\n"
                "## 四、系统性风险评估\n"
                "（流动性信号 / 风险等级 / 是否构成系统性风险）\n\n"
                "## 五、市场状态标签与仓位基调\n"
                "请使用中文。"
            ),
            MessagesPlaceholder(variable_name="messages"),
        ])

        tool_names = [getattr(t, 'name', getattr(t, '__name__', str(t))) for t in tools]
        prompt = prompt.partial(tool_names=", ".join(tool_names), current_date=current_date)
        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke({"messages": state["messages"]})

        if len(result.tool_calls) == 0:
            report = result.content
            logger.info(f"[国际新闻分析] 报告完成，长度: {len(report)}")
            return {
                "messages": [result],
                "international_news_report": report,
                "international_news_tool_call_count": count + 1,
            }

        logger.info(f"[国际新闻分析] 执行 {len(result.tool_calls)} 个工具调用")
        try:
            tool_messages = []
            for tc in result.tool_calls:
                tool_name = tc.get("name")
                tool_args = tc.get("args", {})
                tool_id = tc.get("id")
                tool_result = _exec_tool(tools, tool_name, tool_args)
                tool_messages.append(ToolMessage(content=str(tool_result), tool_call_id=tool_id))

            analysis_prompt = (
                "请基于以上工具获取的宏观数据，生成国际金融市场新闻分析报告。\n\n"
                "格式要求：\n"
                "# 国际金融市场新闻分析报告\n"
                "## 一、当前重大宏观事件扫描\n"
                "## 二、历史案例类比\n"
                "## 三、传导链条分析\n"
                "## 四、系统性风险评估\n"
                "## 五、市场状态标签与仓位基调\n\n"
                "请使用中文，基于真实数据分析。数据不可用时如实标注。"
            )
            messages = state["messages"] + [result] + tool_messages + [HumanMessage(content=analysis_prompt)]
            final_result = llm.invoke(messages)
            report = final_result.content
            logger.info(f"[国际新闻分析] 报告完成，长度: {len(report)}")
            return {
                "messages": [result] + tool_messages + [final_result],
                "international_news_report": report,
                "international_news_tool_call_count": count + 1,
            }
        except Exception as e:
            logger.error(f"[国际新闻分析] 失败: {e}")
            return {
                "messages": [result],
                "international_news_report": f"分析生成失败: {e}",
                "international_news_tool_call_count": count + 1,
            }

    return node


def _exec_tool(tools, tool_name, tool_args):
    for t in tools:
        tn = getattr(t, 'name', getattr(t, '__name__', ''))
        if tn == tool_name:
            try:
                return t.invoke(tool_args)
            except Exception as e:
                return f"工具执行失败: {e}"
    return f"未找到工具: {tool_name}"
