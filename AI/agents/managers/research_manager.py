"""
YoHo 研究经理 (简化版)
作为投资委员会主席，评估 Bull/Bear 辩论并生成综合投资计划。

移除：token 统计、data_source_manager 回退
"""

import logging

from AI.agents.utils.instrument_utils import build_instrument_context

logger = logging.getLogger(__name__)


def create_research_manager(llm, memory):

    def research_manager_node(state) -> dict:
        ticker = state["company_of_interest"]
        instrument_context = build_instrument_context(ticker)
        history = state["investment_debate_state"].get("history", "")

        market_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]

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

        prompt = f"""作为投资组合经理和辩论主持人，你的职责是批判性地评估这轮辩论并做出明确决策：买入、卖出或持有。

简洁地总结双方的关键观点，重点关注最有说服力的证据。

此外，为交易员制定详细的投资计划：
- 明确建议：买入/持有/卖出
- 理由：解释为什么这些论点导致你的结论
- 战略行动：实施建议的具体步骤
- 目标价格分析：提供具体的目标价格区间和价格目标
  考虑：基本面估值、新闻影响、情绪驱动、技术支撑/阻力位
  必须提供具体的目标价格——不要回复"无法确定"

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
