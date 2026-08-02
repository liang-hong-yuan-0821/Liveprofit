"""
市场层 — Layer 1: 韩国技术分析师
分析 KOSPI / KOSDAQ 技术面，复用现有 get_global_tech_indices。
直接边模式（无工具循环），在节点内完成工具调用+分析。
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import ToolMessage, HumanMessage

logger = logging.getLogger(__name__)


def create_kr_tech_analyst(llm, toolkit):

    def node(state):
        current_date = state["trade_date"]
        logger.info(f"[韩国技术分析] 开始分析 @ {current_date}")

        tools = [toolkit.get_global_tech_indices, toolkit.get_kr_foreign_flow]
        count = state.get("kr_tech_tool_call_count", 0)

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是一位专注韩国市场的技术分析师。\n\n"
                "分析日期：{current_date}\n"
                "可用工具：{tool_names}\n\n"
                "分析维度：\n"
                "- KOSPI / KOSDAQ：趋势方向、均线系统(MA5/10/20/60)、量能、RSI/MACD\n"
                "- 外资流向（韩国市场外资占比高，是重要信号）\n"
                "- 与美股科技的相关性（韩股 = 美股科技beta）\n\n"
                "工作流程：\n"
                "1. 调用 get_global_tech_indices 获取近期数据（关注韩国部分）\n"
                "2. 调用 get_kr_foreign_flow 获取外资流向（一期返回'数据不可用'则跳过）\n"
                "3. 直接生成技术分析报告（不要重复调用工具）\n"
                "数据不可用时标注'数据暂不可用'\n\n"
                "输出格式：\n"
                "# 韩国市场技术分析报告\n\n"
                "## 一、KOSPI\n"
                "（趋势、均线、量能、RSI/MACD）\n"
                "## 二、KOSDAQ\n"
                "## 三、外资动向\n"
                "## 四、与美股科技相关性\n"
                "## 五、综合研判\n"
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
                "kr_tech_report": result.content,
                "kr_tech_tool_call_count": count + 1,
            }

        try:
            tool_messages = _exec_tools(tools, result.tool_calls)
            analysis_prompt = (
                "请基于以上数据生成韩国市场技术分析报告。\n"
                "# 韩国市场技术分析报告\n"
                "## 一、KOSPI\n## 二、KOSDAQ\n## 三、外资动向\n"
                "## 四、与美股科技相关性\n## 五、综合研判\n"
                "请使用中文。数据不可用时如实标注。"
            )
            messages = state["messages"] + [result] + tool_messages + [HumanMessage(content=analysis_prompt)]
            final = llm.invoke(messages)
            return {
                "messages": [result] + tool_messages + [final],
                "kr_tech_report": final.content,
                "kr_tech_tool_call_count": count + 1,
            }
        except Exception as e:
            logger.error(f"[韩国技术分析] 失败: {e}")
            return {
                "messages": [result],
                "kr_tech_report": f"分析生成失败: {e}",
                "kr_tech_tool_call_count": count + 1,
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
