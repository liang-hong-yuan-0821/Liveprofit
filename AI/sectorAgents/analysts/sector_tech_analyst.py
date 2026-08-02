"""
板块层 — 板块技术分析师
逐行业做技术面体检，分析对象是行业板块指数的日K线。
覆盖 A 股所有申万一级行业（约30个），对每条行业K线做均线/趋势/量价分析，
输出全行业技术状态矩阵。对科技行业做AI产业链专题深挖。
直接边模式（无工具循环），在节点内完成工具调用+分析。
"""
import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import ToolMessage, HumanMessage

logger = logging.getLogger(__name__)


def create_sector_tech_analyst(llm, toolkit):

    def node(state):
        current_date = state["trade_date"]
        logger.info(f"[板块技术分析] 开始分析 @ {current_date}")

        # 组装市场层上下文摘要 + 板块新闻分析结论
        sector_ctx = _build_sector_market_context(state)
        news_report = state.get("sector_news_report", "")
        if news_report and len(news_report) > 20:
            sector_ctx += f"\n\n## 板块新闻分析结论（供技术面交叉验证）\n{news_report[:1000]}"

        tools = [
            toolkit.get_industry_sector_performance,
            toolkit.get_sector_technical_screening,
            toolkit.get_sector_relative_strength,
            toolkit.get_ai_industry_chain,
            toolkit.get_tech_correlation_analysis,
        ]
        count = state.get("sector_tech_tool_call_count", 0)

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是一位资深 A 股板块技术分析师，负责逐行业做技术面体检。\n"
                "你的分析对象是行业板块指数的日K线（OHLCV），不是个股K线。\n\n"
                "分析日期：{current_date}\n"
                "可用工具：{tool_names}\n\n"
                "背景上下文（来自市场层宏观分析）：\n"
                "{sector_market_context}\n\n"
                "工作流程：\n"
                "1. 调用 get_industry_sector_performance(days=20) 获取全行业近期涨跌数据\n"
                "2. 调用 get_sector_technical_screening(days=60) 获取全行业技术状态矩阵\n"
                "   （每行=一个行业，含均线排列、RSI、MACD、量比等指标的多空状态）\n"
                "3. 调用 get_sector_relative_strength(days=20) 获取各行业 alpha 排名\n"
                "4. 对科技相关行业，调用 get_ai_industry_chain 和 get_tech_correlation_analysis\n"
                "   做产业链级别的专题深挖（不要重复调用）\n\n"
                "分析维度（按优先级排列）：\n\n"
                "A. 全行业技术状态总览\n"
                "  - 技术面强势的行业有哪些？（均线多头排列 + RSI > 50 + MACD 金叉）\n"
                "  - 技术面弱势的行业有哪些？（均线空头排列 + RSI < 50 + MACD 死叉）\n"
                "  - 哪些行业出现异动信号？（放量突破/高位背离/底部放量企稳）\n\n"
                "B. 重点行业深度分析\n"
                "  - 领涨行业：趋势是否健康？量价配合如何？是否有背离风险？\n"
                "  - 领跌行业：是否出现底部企稳信号？还是下跌中继？\n"
                "  - 行业 alpha 排名：哪些行业真正跑赢大盘（持续正 alpha）？\n\n"
                "C. 板块轮动技术验证\n"
                "  - 对新闻分析识别的'主线板块'，用技术面做确认\n"
                "  - 趋势不背离 + 量价配合 = 主线健康\n"
                "  - 高位放量滞涨/顶背离 = 主线可能见顶\n\n"
                "D. 风格因子技术验证\n"
                "  - 结合市场层的风格判断，验证板块层面的技术信号是否一致\n"
                "  - 如果市场层判断'大盘价值占优'但小盘成长板块也在放量走强 → 可能是风格切换前兆\n\n"
                "E. AI/科技产业链专题（科技行业深挖）\n"
                "  - AI产业链内部轮动：当前热点在哪个环节（存储芯片/半导体/光模块/AI服务器/算力/AI应用）？\n"
                "  - 美股科技→韩股科技→A股科技板块的传导有效性\n"
                "  - 产业链是否存在传导断裂或局部过热信号？\n\n"
                "注意事项：\n"
                "- 数据不可用时标注'数据暂不可用（需 AKShare 数据源）'\n"
                "- 技术指标只是辅助工具，不构成投资建议\n"
                "- 行业数量多（约30个），请做归纳总结而非逐行业罗列\n\n"
                "输出格式：\n"
                "# 板块技术分析报告\n\n"
                "## 一、全行业技术状态总览\n"
                "（技术面强势行业 / 技术面弱势行业 / 异动信号行业，用表格呈现）\n\n"
                "## 二、重点行业深度分析\n"
                "（领涨行业技术健康度 + 领跌行业底部信号 + alpha排名验证）\n\n"
                "## 三、板块轮动技术验证\n"
                "（主线板块技术面确认 + 风险信号扫描）\n\n"
                "## 四、风格因子技术验证\n"
                "（大小盘/成长价值的技术信号是否与市场层判断一致）\n\n"
                "## 五、AI/科技产业链专题\n"
                "（产业链轮动位置 + 全球科技传导有效性 + 风险提示）\n\n"
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
            logger.info(f"[板块技术分析] 报告完成，长度: {len(report)}")
            return {
                "messages": [result],
                "sector_tech_report": report,
                "sector_tech_tool_call_count": count + 1,
            }

        logger.info(f"[板块技术分析] 执行 {len(result.tool_calls)} 个工具调用")
        try:
            tool_messages = _exec_tools(tools, result.tool_calls)
            analysis_prompt = (
                "请基于以上数据生成板块技术分析报告（全行业技术扫描+AI专题+风格验证）。\n\n"
                "# 板块技术分析报告\n\n"
                "## 一、全行业技术状态总览\n"
                "（技术面强势行业 / 技术面弱势行业 / 异动信号行业，用表格呈现）\n\n"
                "## 二、重点行业深度分析\n"
                "（领涨行业技术健康度 + 领跌行业底部信号 + alpha排名验证）\n\n"
                "## 三、板块轮动技术验证\n"
                "（主线板块技术面确认 + 风险信号扫描）\n\n"
                "## 四、风格因子技术验证\n"
                "（大小盘/成长价值的技术信号是否与市场层判断一致）\n\n"
                "## 五、AI/科技产业链专题\n"
                "（产业链轮动位置 + 全球科技传导有效性 + 风险提示）\n\n"
                "请使用中文。数据不可用时如实标注。"
            )
            messages = state["messages"] + [result] + tool_messages + [HumanMessage(content=analysis_prompt)]
            final_result = llm.invoke(messages)
            report = final_result.content
            logger.info(f"[板块技术分析] 报告完成，长度: {len(report)}")
            return {
                "messages": [result] + tool_messages + [final_result],
                "sector_tech_report": report,
                "sector_tech_tool_call_count": count + 1,
            }
        except Exception as e:
            logger.error(f"[板块技术分析] 失败: {e}")
            return {
                "messages": [result],
                "sector_tech_report": f"分析生成失败: {e}",
                "sector_tech_tool_call_count": count + 1,
            }

    return node


def _build_sector_market_context(state) -> str:
    """组装市场层中与板块分析相关的上下文摘要"""
    parts = []
    cn_tech = state.get("cn_tech_report", "")
    cn_news = state.get("cn_news_report", "")
    intl_news = state.get("international_news_report", "")

    if cn_tech and len(cn_tech) > 20:
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
