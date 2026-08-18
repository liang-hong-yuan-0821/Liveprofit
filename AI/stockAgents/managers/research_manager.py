"""
LiveProfit 研究经理 (简化版)
作为投资委员会主席，评估 Bull/Bear 辩论并生成综合投资计划。

移除：token 统计、data_source_manager 回退
"""

import logging

from AI.stockAgents.utils.instrument_utils import build_instrument_context

logger = logging.getLogger(__name__)


def create_research_manager(llm, memory):

    def research_manager_node(state) -> dict:
        ticker = state["company_of_interest"]
        instrument_context = build_instrument_context(ticker)
        history = state["investment_debate_state"].get("history", "")

        market_report = state.get("stock_tech_report", "")
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]

        from AI.stockAgents.utils.agent_utils import build_cross_layer_context
        cross_ctx = build_cross_layer_context(state)

        curr_situation = (
            f"{market_report}\n\n{sentiment_report}\n\n"
            f"{news_report}\n\n{fundamentals_report}"
        )

        past_memory_str = ""
        if memory is not None:
            memories = memory.get_memories(curr_situation, n_matches=2)
            for rec in memories:
                if isinstance(rec, str):
                    past_memory_str += rec + "\n\n"

        prompt = f"""作为投资组合经理和辩论主持人，你的职责是批判性地评估这轮辩论并做出明确的三级别投资决策。

大盘与板块环境（来自市场层+板块层分析）：
{cross_ctx}

板块归属校验要求：
- 判断该个股所属行业是否在候选板块短名单中（"顺势/逆势/中性"）
- 顺势股可提高仓位置信度，逆势股需更强的基本面证据才能看多

简洁地总结双方的关键观点，重点关注最有说服力的证据。

此外，为交易员制定三级别投资计划：

一、短线计划（1-5 交易日）：
- 短线建议：适合操作/观望/回避
- 短线目标价位区间
- 止损位
- 关键催化剂（财报/事件/技术突破）

二、波段计划（1-4 周）：
- 波段建议：买入/持有/卖出（主判断）
- 波段目标价位区间（必须提供具体价格）
- 止损位
- 核心逻辑（基本面+技术面+板块面）

三、长线计划（3 月+）：
- 长线建议：配置/减仓/观望
- 长线估值判断（低估/合理/高估）
- 长线风险提示

级别嵌套约束：
- 波段是主判断，短线在波段方向上操作
- 长线决定仓位基调，波段决定进出时机

历史反思（请吸取过去错误教训）：
{past_memory_str}

标的约束：
{instrument_context}

综合分析报告：
市场研究：{market_report}
情绪分析：{sentiment_report}
新闻分析：{news_report}
基本面分析：{fundamentals_report}

辩论历史：
{history}

请用中文撰写所有分析内容。"""

        response = llm.invoke(prompt)
        logger.info(f"[研究经理] 投资计划生成完成，长度: {len(response.content)}")

        investment_debate_state = state["investment_debate_state"]
        new_state = {
            "judge_decision": response.content,
            "history": investment_debate_state.get("history", ""),
            "bear_history": investment_debate_state.get("bear_history", ""),
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": response.content,
            "count": investment_debate_state["count"],
        }

        return {
            "investment_debate_state": new_state,
            "investment_plan": response.content,
        }

    return research_manager_node
