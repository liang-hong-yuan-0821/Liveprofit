"""
市场层 — Layer 1: 美国技术分析师
分析标普500 / 纳指 / 道指技术面，判断美股趋势方向。
直接边模式（无工具循环），在节点内完成工具调用+分析。
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import ToolMessage, HumanMessage

logger = logging.getLogger(__name__)


def create_us_tech_analyst(llm, toolkit):

    def node(state):
        current_date = state["trade_date"]
        logger.info(f"[美国技术分析] 开始分析 @ {current_date}")

        tools = [toolkit.get_us_index_data]
        # get_us_sector_rotation 是占位工具，可选绑定
        try:
            tools.append(toolkit.get_us_sector_rotation)
        except AttributeError:
            pass

        count = state.get("us_tech_tool_call_count", 0)

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是一位专注美股的技术分析师。\n\n"
                "分析日期：{current_date}\n"
                "可用工具：{tool_names}\n\n"
                "分析维度：\n"
                "- 标普500 / 纳斯达克 / 道琼斯：趋势方向、均线系统(MA5/10/20/60)、量能变化、RSI/MACD\n"
                "- 板块结构：科技 vs 价值、大盘 vs 小盘轮动\n"
                "- 跨市场定位：美股在全球风险资产中的相对强弱\n\n"
                "工作流程：\n"
                "1. 调用 get_us_index_data 获取美股三大指数数据\n"
                "2. 直接生成技术分析报告（不要重复调用工具）\n"
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

        tool_names = [getattr(t, 'name', getattr(t, '__name__', str(t))) for t in tools]
        prompt = prompt.partial(tool_names=", ".join(tool_names), current_date=current_date)
        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke({"messages": state["messages"]})

        if len(result.tool_calls) == 0:
            report = result.content
            logger.info(f"[美国技术分析] 报告完成，长度: {len(report)}")
            return {
                "messages": [result],
                "us_tech_report": report,
                "us_tech_tool_call_count": count + 1,
            }

        try:
            tool_messages = []
            for tc in result.tool_calls:
                tname, targs, tid = tc.get("name"), tc.get("args", {}), tc.get("id")
                for t in tools:
                    if getattr(t, 'name', getattr(t, '__name__', '')) == tname:
                        try:
                            tres = t.invoke(targs)
                        except Exception as e:
                            tres = f"工具执行失败: {e}"
                        tool_messages.append(ToolMessage(content=str(tres), tool_call_id=tid))
                        break

            analysis_prompt = (
                "请基于以上数据生成美国市场技术分析报告。\n"
                "# 美国市场技术分析报告\n"
                "## 一、标普500\n## 二、纳斯达克\n## 三、道琼斯\n"
                "## 四、板块与风格\n## 五、综合研判\n"
                "请使用中文。数据不可用时如实标注。"
            )
            messages = state["messages"] + [result] + tool_messages + [HumanMessage(content=analysis_prompt)]
            final = llm.invoke(messages)
            return {
                "messages": [result] + tool_messages + [final],
                "us_tech_report": final.content,
                "us_tech_tool_call_count": count + 1,
            }
        except Exception as e:
            logger.error(f"[美国技术分析] 失败: {e}")
            return {
                "messages": [result],
                "us_tech_report": f"分析生成失败: {e}",
                "us_tech_tool_call_count": count + 1,
            }

    return node
