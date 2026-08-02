"""
市场层 — Layer 1: 韩国新闻分析师
分析韩国央行政策、出口数据、权重股动态对 KOSPI/KOSDAQ 的影响。
一期工具占位，Agent 靠 LLM 内部知识 + 国际新闻上下文推断。
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import ToolMessage, HumanMessage

logger = logging.getLogger(__name__)


def create_kr_news_analyst(llm, toolkit):

    def node(state):
        current_date = state["trade_date"]
        logger.info(f"[韩国新闻分析] 开始分析 @ {current_date}")

        tools = [toolkit.get_kr_macro_news, toolkit.get_kr_export_data]
        count = state.get("kr_news_tool_call_count", 0)

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是一位专注韩国市场的宏观分析师。\n\n"
                "分析日期：{current_date}\n"
                "可用工具：{tool_names}\n\n"
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

        tool_names = [getattr(t, 'name', getattr(t, '__name__', str(t))) for t in tools]
        prompt = prompt.partial(tool_names=", ".join(tool_names), current_date=current_date)
        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke({"messages": state["messages"]})

        if len(result.tool_calls) == 0:
            return {
                "messages": [result],
                "kr_news_report": result.content,
                "kr_news_tool_call_count": count + 1,
            }

        try:
            tool_messages = _exec_tools(tools, result.tool_calls)
            analysis_prompt = (
                "请基于以上数据生成韩国市场新闻分析报告。\n"
                "# 韩国市场新闻分析报告\n"
                "## 一、政策与央行动态\n## 二、出口与产业\n"
                "## 三、权重股动态\n## 四、对KOSPI/KOSDAQ的影响判断\n"
                "请使用中文。数据不可用时如实标注。"
            )
            messages = state["messages"] + [result] + tool_messages + [HumanMessage(content=analysis_prompt)]
            final = llm.invoke(messages)
            return {
                "messages": [result] + tool_messages + [final],
                "kr_news_report": final.content,
                "kr_news_tool_call_count": count + 1,
            }
        except Exception as e:
            logger.error(f"[韩国新闻分析] 失败: {e}")
            return {
                "messages": [result],
                "kr_news_report": f"分析生成失败: {e}",
                "kr_news_tool_call_count": count + 1,
            }

    return node


def _exec_tools(tools, tool_calls):
    msgs = []
    for tc in tool_calls:
        tname, targs, tid = tc.get("name"), tc.get("args", {}), tc.get("id")
        for t in tools:
            if getattr(t, 'name', getattr(t, '__name__', '')) == tname:
                try:
                    res = t.invoke(targs)
                except Exception as e:
                    res = f"工具执行失败: {e}"
                msgs.append(ToolMessage(content=str(res), tool_call_id=tid))
                break
        else:
            msgs.append(ToolMessage(content=f"未找到工具: {tname}", tool_call_id=tid))
    return msgs
