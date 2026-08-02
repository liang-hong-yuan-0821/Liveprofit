"""
市场层 — Layer 1: 中国新闻分析师
聚焦 A 股市场微观结构和资金日历事件：
IPO 抽血、限售解禁抛压、期指交割日效应、两融余额、季节效应。
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import ToolMessage, HumanMessage

logger = logging.getLogger(__name__)


def create_cn_news_analyst(llm, toolkit):

    def node(state):
        current_date = state["trade_date"]
        logger.info(f"[中国新闻分析] 开始分析 @ {current_date}")

        tools = [
            toolkit.get_ipo_calendar,
            toolkit.get_share_unlock_calendar,
            toolkit.get_futures_expiry_calendar,
            toolkit.get_margin_trading_balance,
        ]
        count = state.get("cn_news_tool_call_count", 0)

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是一位专注 A 股市场微观结构的分析师，聚焦资金日历事件对短期资金面的影响。\n\n"
                "分析日期：{current_date}\n"
                "可用工具：{tool_names}\n\n"
                "工作流程：\n"
                "1. 调用 get_ipo_calendar 获取近期新股申购/上市日历\n"
                "2. 调用 get_share_unlock_calendar 获取限售股解禁日历\n"
                "3. 调用 get_futures_expiry_calendar 获取期指/期权交割日\n"
                "4. 调用 get_margin_trading_balance 获取两融余额变化\n"
                "5. 综合评估短期资金压力\n\n"
                "分析要点：\n"
                "- 大盘 IPO/新股上市 → 打新资金抽血/虹吸效应，标注大市值新股\n"
                "- 限售股解禁 → 潜在抛压来源，标注解禁市值规模\n"
                "- 期货/期权交割日 → 到期日效应，警惕尾盘异常波动\n"
                "- 两融余额变化 → 杠杆资金松紧信号\n"
                "- 季节效应 → 季末排名调仓冲击、长假前避险效应\n"
                "- 数据不可用时如实标注，不编造\n\n"
                "输出格式：\n"
                "# 中国市场新闻分析报告\n\n"
                "## 一、资金日历事件\n"
                "（近期 IPO 申购/上市、限售解禁、交割日临近提示）\n\n"
                "## 二、杠杆资金状况\n"
                "（两融余额变化趋势、杠杆率水位）\n\n"
                "## 三、季节性/事件性效应\n"
                "（季末调仓、长假效应）\n\n"
                "## 四、短期资金面结论\n"
                "（资金压力判断 + 波动率放大提示）\n"
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
            logger.info(f"[中国新闻分析] 报告完成，长度: {len(report)}")
            return {
                "messages": [result],
                "cn_news_report": report,
                "cn_news_tool_call_count": count + 1,
            }

        logger.info(f"[中国新闻分析] 执行 {len(result.tool_calls)} 个工具调用")
        try:
            tool_messages = _exec_tools(tools, result.tool_calls)
            analysis_prompt = (
                "请基于以上数据生成中国市场新闻分析报告（资金日历与微观结构）。\n\n"
                "# 中国市场新闻分析报告\n"
                "## 一、资金日历事件\n"
                "## 二、杠杆资金状况\n"
                "## 三、季节性/事件性效应\n"
                "## 四、短期资金面结论\n\n"
                "请使用中文。数据不可用时如实标注。"
            )
            messages = state["messages"] + [result] + tool_messages + [HumanMessage(content=analysis_prompt)]
            final_result = llm.invoke(messages)
            report = final_result.content
            logger.info(f"[中国新闻分析] 报告完成，长度: {len(report)}")
            return {
                "messages": [result] + tool_messages + [final_result],
                "cn_news_report": report,
                "cn_news_tool_call_count": count + 1,
            }
        except Exception as e:
            logger.error(f"[中国新闻分析] 失败: {e}")
            return {
                "messages": [result],
                "cn_news_report": f"分析生成失败: {e}",
                "cn_news_tool_call_count": count + 1,
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
