"""
YoHo 科技市场分析师 (新增)
分析全球科技指数（纳斯达克、韩国科斯达克、A股科创/创业板）
以及 AI 产业链细分板块（存储、半导体、光模块、算力等），
基于前10天数据的相关性推测明日走势。

使用的数据源：AKShare（全球指数 + A 股概念板块）。
"""

import logging
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.messages import ToolMessage, HumanMessage

logger = logging.getLogger(__name__)


def create_tech_market_analyst(llm, toolkit):

    def tech_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]

        logger.info(f"[科技分析师] 开始分析全球科技指数 @ {current_date}")

        tools = [
            toolkit.get_global_tech_indices,
            toolkit.get_ai_industry_chain,
            toolkit.get_tech_correlation_analysis,
        ]

        instrument_context = (
            f"分析标的: {ticker}，分析日期: {current_date}。"
            "请综合全球科技指数和AI产业链数据，推测明日科技板块走势。"
        )

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "你是一位专业的全球科技市场分析师，专注于分析美股（纳斯达克）、"
                "韩国（科斯达克/KOSPI）和中国A股（科创50/创业板）的科技指数联动关系。\n\n"
                "你的职责：\n"
                "1. 获取全球主要科技指数近10个交易日的数据\n"
                "2. 获取AI产业链各细分板块（存储芯片、半导体、光模块、AI服务器、"
                "先进封装、算力、AI应用、机器人、智能汽车）的表现\n"
                "3. 分析指数间的相关性（正相关/负相关/领先滞后关系）\n"
                "4. 结合相关性和动量，推测明日A股科技板块走势\n\n"
                "分析日期：{current_date}\n"
                "{instrument_context}\n\n"
                "可用工具：{tool_names}\n\n"
                "工作流程：\n"
                "1. 首次调用 get_global_tech_indices 获取全球科技指数\n"
                "2. 调用 get_ai_industry_chain 获取 AI 产业链细分板块\n"
                "3. 调用 get_tech_correlation_analysis 获取相关性分析和走势预测\n"
                "4. 综合以上数据生成分析报告\n\n"
                "输出格式：\n"
                "## 一、全球科技指数概览\n"
                "（纳斯达克、韩国、A股主要科技指数昨日表现）\n\n"
                "## 二、AI产业链分析\n"
                "（存储/半导体/光模块/算力/AI应用等板块表现）\n\n"
                "## 三、指数相关性分析\n"
                "（美股→韩股→A股的传导链条分析）\n\n"
                "## 四、明日走势预测\n"
                "（基于相关性和动量的综合判断）\n\n"
                "请使用中文，基于真实数据分析。"
            ),
            MessagesPlaceholder(variable_name="messages"),
        ])

        tool_names = []
        for t in tools:
            tn = getattr(t, 'name', getattr(t, '__name__', str(t)))
            tool_names.append(tn)

        prompt = prompt.partial(
            tool_names=", ".join(tool_names),
            current_date=current_date,
            instrument_context=instrument_context,
        )

        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke({"messages": state["messages"]})

        # 如果无工具调用，直接返回
        if len(result.tool_calls) == 0:
            report = result.content
            logger.info(f"[科技分析师] 直接生成报告，长度: {len(report)}")
            return {
                "messages": [result],
                "tech_market_report": report,
            }

        # 执行工具调用
        logger.info(f"[科技分析师] 检测到 {len(result.tool_calls)} 个工具调用")
        try:
            tool_messages = []
            for tc in result.tool_calls:
                tool_name = tc.get("name")
                tool_args = tc.get("args", {})
                tool_id = tc.get("id")
                tool_result = None
                for t in tools:
                    tn = getattr(t, 'name', getattr(t, '__name__', ''))
                    if tn == tool_name:
                        try:
                            tool_result = t.invoke(tool_args)
                        except Exception as te:
                            tool_result = f"工具执行失败: {te}"
                        break
                if tool_result is None:
                    tool_result = f"未找到工具: {tool_name}"
                tool_messages.append(
                    ToolMessage(content=str(tool_result), tool_call_id=tool_id)
                )

            # 生成综合分析报告
            analysis_prompt = """请基于以下全球科技指数、AI产业链、相关性分析和走势预测数据，
生成一份专业的科技市场分析报告。

请按照以下格式输出：

# 全球科技市场分析报告

## 一、全球科技指数概览
- 美股纳斯达克/费城半导体的表现和趋势
- 韩国科斯达克/KOSPI的表现和趋势
- A股科创50/创业板指的表现和趋势
- 各指数间的联动关系

## 二、AI产业链细分分析
- 上游（存储芯片、半导体设备）表现
- 中游（光模块、AI服务器、先进封装）表现
- 下游（AI应用、机器人、智能汽车）表现
- 产业链各环节的轮动特征

## 三、跨市场相关性分析
- 美股科技→韩国科技→A股科技的传导链条
- 领先/滞后关系（美股领先A股多久）
- 相关性最强和最弱的指数对

## 四、明日走势预测
- 综合相关性和动量，推测明日A股科技板块方向
- 最值得关注的细分板块
- 风险提示

⚠️ 此预测基于统计相关性，不构成投资建议。请使用中文。"""

            messages = state["messages"] + [result] + tool_messages + [HumanMessage(content=analysis_prompt)]
            final_result = llm.invoke(messages)
            report = final_result.content

            logger.info(f"[科技分析师] 完整报告生成完成，长度: {len(report)}")

            return {
                "messages": [result] + tool_messages + [final_result],
                "tech_market_report": report,
            }
        except Exception as e:
            logger.error(f"[科技分析师] 工具执行失败: {e}")
            return {
                "messages": [result],
                "tech_market_report": f"分析生成失败: {e}",
            }

    return tech_analyst_node
