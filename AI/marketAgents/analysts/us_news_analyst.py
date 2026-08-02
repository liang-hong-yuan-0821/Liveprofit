"""
市场层 — Layer 1: 美国新闻分析师
分析美国本土事件对美股的影响：美联储决议、经济数据、财报季、VIX。
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import ToolMessage, HumanMessage

logger = logging.getLogger(__name__)


def create_us_news_analyst(llm, toolkit):

    def node(state):
        current_date = state["trade_date"]
        logger.info(f"[美国新闻分析] 开始分析 @ {current_date}")

        tools = [
            toolkit.get_us_macro_news,
            toolkit.get_us_economic_calendar,
            toolkit.get_vix_index,
        ]
        count = state.get("us_news_tool_call_count", 0)

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是一位专注美国市场的宏观分析师。\n\n"
                "分析日期：{current_date}\n"
                "可用工具：{tool_names}\n\n"
                "工作流程：\n"
                "1. 调用 get_us_macro_news 获取美国财经要闻\n"
                "2. 调用 get_us_economic_calendar 获取近期经济数据发布日历\n"
                "3. 调用 get_vix_index 获取恐慌指数水位\n"
                "4. 综合生成美国市场新闻分析报告\n\n"
                "分析要求：\n"
                "- 关注美联储政策预期、非农/CPI等关键数据\n"
                "- 关注财报季整体表现（标普500盈利增速）\n"
                "- 关注科技监管/反垄断动态\n"
                "- VIX水位判断市场恐慌程度\n"
                "- 数据不可用时标注'数据暂不可用，以下分析基于公开信息'\n\n"
                "输出格式：\n"
                "# 美国市场新闻分析报告\n\n"
                "## 一、政策与宏观事件\n"
                "## 二、经济数据解读\n"
                "## 三、VIX与市场情绪\n"
                "## 四、对美股及全球市场的影响判断\n"
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
            return {
                "messages": [result],
                "us_news_report": report,
                "us_news_tool_call_count": count + 1,
            }

        logger.info(f"[美国新闻分析] 执行 {len(result.tool_calls)} 个工具调用")
        try:
            tool_messages = _exec_tools(tools, result.tool_calls)
            analysis_prompt = (
                "请基于以上数据生成美国市场新闻分析报告。\n"
                "# 美国市场新闻分析报告\n"
                "## 一、政策与宏观事件\n## 二、经济数据解读\n"
                "## 三、VIX与市场情绪\n## 四、对美股及全球市场的影响判断\n"
                "请使用中文。数据不可用时如实标注。"
            )
            messages = state["messages"] + [result] + tool_messages + [HumanMessage(content=analysis_prompt)]
            final = llm.invoke(messages)
            return {
                "messages": [result] + tool_messages + [final],
                "us_news_report": final.content,
                "us_news_tool_call_count": count + 1,
            }
        except Exception as e:
            logger.error(f"[美国新闻分析] 失败: {e}")
            return {
                "messages": [result],
                "us_news_report": f"分析生成失败: {e}",
                "us_news_tool_call_count": count + 1,
            }

    return node


def _exec_tools(tools, tool_calls):
    msgs = []
    for tc in tool_calls:
        tool_name = tc.get("name")
        tool_args = tc.get("args", {})
        tool_id = tc.get("id")
        for t in tools:
            tn = getattr(t, 'name', getattr(t, '__name__', ''))
            if tn == tool_name:
                try:
                    res = t.invoke(tool_args)
                except Exception as e:
                    res = f"工具执行失败: {e}"
                msgs.append(ToolMessage(content=str(res), tool_call_id=tool_id))
                break
        else:
            msgs.append(ToolMessage(content=f"未找到工具: {tool_name}", tool_call_id=tool_id))
    return msgs
