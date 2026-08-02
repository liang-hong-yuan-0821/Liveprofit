"""
板块层 — 板块新闻分析师
扫描全市场行业板块和概念板块，从"信息面 + 资金面"做横向对比：
行业涨跌排名、资金流向、概念热度、板块轮动判断。
"""
import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import ToolMessage, HumanMessage

logger = logging.getLogger(__name__)


def create_sector_news_analyst(llm, toolkit):

    def node(state):
        current_date = state["trade_date"]
        logger.info(f"[板块新闻分析] 开始分析 @ {current_date}")

        # 组装市场层上下文摘要
        sector_ctx = _build_sector_market_context(state)

        tools = [
            toolkit.get_industry_sector_performance,
            toolkit.get_sector_fund_flow,
            toolkit.get_concept_board_heat,
            toolkit.get_industry_policy_news,
        ]
        count = state.get("sector_news_tool_call_count", 0)

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是一位专注 A 股全市场板块横向对比的分析师，"
                "从'信息面 + 资金面'两个维度扫描所有行业的强弱状态。\n\n"
                "分析日期：{current_date}\n"
                "可用工具：{tool_names}\n\n"
                "背景上下文（来自市场层宏观分析）：\n"
                "{sector_market_context}\n\n"
                "工作流程：\n"
                "1. 调用 get_industry_sector_performance 获取全行业（申万一级+二级）涨跌排名\n"
                "2. 调用 get_sector_fund_flow 获取行业资金流向排名\n"
                "3. 调用 get_concept_board_heat 获取热门概念板块热度\n"
                "4. 调用 get_industry_policy_news 获取近期产业政策/行业新闻\n"
                "5. 综合以上数据，给出板块轮动主线判断\n\n"
                "分析要点：\n"
                "- 行业涨跌排名：哪些行业在领涨/领跌？持续多长时间了？\n"
                "- 资金流向：主力资金进攻哪些行业？撤出哪些行业？\n"
                "- 概念热度：热门概念的持续性如何？是否有板块内扩散效应？\n"
                "- 板块轮动：当前是持续主线（什么主线？）还是快速轮动（无主线）？\n"
                "- 风格验证：结合市场层的大盘风格判断，验证板块层面的风格一致性\n"
                "- 数据不可用时如实标注，不编造\n\n"
                "输出格式：\n"
                "# 板块新闻分析报告\n\n"
                "## 一、行业涨跌排名\n"
                "（申万一级行业近N日涨跌幅排序，标注领涨行业TOP5和领跌行业BOTTOM5）\n\n"
                "## 二、资金流向\n"
                "（行业主力资金净流入/流出排名，标注资金进攻方向）\n\n"
                "## 三、概念板块热度\n"
                "（热门概念板块涨幅/成交额变化/持续性判断）\n\n"
                "## 四、板块轮动主线判断\n"
                "（有主线/快速轮动/无方向，给出置信度和逻辑链）\n\n"
                "## 五、风格一致性验证\n"
                "（结合大盘风格判断，验证板块层面的风格是否一致）\n\n"
                "请使用中文。"
            ),
            MessagesPlaceholder(variable_name="messages"),
        ])

        tool_names = [getattr(t, 'name', getattr(t, '__name__', str(t))) for t in tools]
        prompt = prompt.partial(
            tool_names=", ".join(tool_names),
            current_date=current_date,
            sector_market_context=sector_ctx,
        )
        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke({"messages": state["messages"]})

        if len(result.tool_calls) == 0:
            report = result.content
            logger.info(f"[板块新闻分析] 报告完成，长度: {len(report)}")
            return {
                "messages": [result],
                "sector_news_report": report,
                "sector_news_tool_call_count": count + 1,
            }

        logger.info(f"[板块新闻分析] 执行 {len(result.tool_calls)} 个工具调用")
        try:
            tool_messages = _exec_tools(tools, result.tool_calls)
            analysis_prompt = (
                "请基于以上数据生成板块新闻分析报告（行业排名+资金流向+轮动判断）。\n\n"
                "# 板块新闻分析报告\n\n"
                "## 一、行业涨跌排名\n"
                "（申万一级行业近N日涨跌幅排序，标注领涨行业TOP5和领跌行业BOTTOM5）\n\n"
                "## 二、资金流向\n"
                "（行业主力资金净流入/流出排名，标注资金进攻方向）\n\n"
                "## 三、概念板块热度\n"
                "（热门概念板块涨幅/成交额变化/持续性判断）\n\n"
                "## 四、板块轮动主线判断\n"
                "（有主线/快速轮动/无方向，给出置信度和逻辑链）\n\n"
                "## 五、风格一致性验证\n"
                "（结合大盘风格判断，验证板块层面的风格是否一致）\n\n"
                "请使用中文。数据不可用时如实标注。"
            )
            messages = state["messages"] + [result] + tool_messages + [HumanMessage(content=analysis_prompt)]
            final_result = llm.invoke(messages)
            report = final_result.content
            logger.info(f"[板块新闻分析] 报告完成，长度: {len(report)}")
            return {
                "messages": [result] + tool_messages + [final_result],
                "sector_news_report": report,
                "sector_news_tool_call_count": count + 1,
            }
        except Exception as e:
            logger.error(f"[板块新闻分析] 失败: {e}")
            return {
                "messages": [result],
                "sector_news_report": f"分析生成失败: {e}",
                "sector_news_tool_call_count": count + 1,
            }

    return node


def _build_sector_market_context(state) -> str:
    """组装市场层中与板块分析相关的上下文摘要"""
    parts = []
    cn_tech = state.get("cn_tech_report", "")
    cn_news = state.get("cn_news_report", "")
    intl_news = state.get("international_news_report", "")

    if cn_tech and len(cn_tech) > 20:
        # 截取前 800 字作为摘要
        parts.append(f"## 大盘环境\n{cn_tech[:800]}")
    if cn_news and len(cn_news) > 20:
        parts.append(f"## 资金日历\n{cn_news[:500]}")
    if intl_news and len(intl_news) > 20:
        parts.append(f"## 国际宏观\n{intl_news[:500]}")

    return "\n\n".join(parts) if parts else "（市场层数据暂不可用）"


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
