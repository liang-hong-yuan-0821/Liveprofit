"""
LiveProfit 风险经理 (简化版)
作为风险管理委员会主席，评估三位风险分析师的辩论并做出最终决策。

移除：重试逻辑、token 统计
"""

import logging

from AI.stockAgents.utils.instrument_utils import build_instrument_context

logger = logging.getLogger(__name__)


def create_risk_manager(llm, memory):

    def risk_manager_node(state) -> dict:
        company_name = state["company_of_interest"]
        instrument_context = build_instrument_context(company_name)
        history = state["risk_debate_state"]["history"]
        risk_debate_state = state["risk_debate_state"]

        trader_plan = state["investment_plan"]

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

        prompt = f"""作为风险管理委员会主席，评估三位风险分析师（激进、中性、保守）之间的辩论，做出最终明确的三级别交易决策：买入、卖出或持有。

大盘与板块环境（来自市场层+板块层分析）：
{cross_ctx}

板块归属校验要求：
- 确认该个股所属行业是否在候选板块短名单中（"顺势/逆势/中性"）
- 板块归属结论应作为风险评估的参考维度之一

决策指导原则：
1. 总结关键论点：提取每位分析师的最强观点
2. 提供理由：用辩论中的直接引用支持你的建议
3. 完善交易员计划：从交易员的原始计划开始，根据分析师见解调整
4. 从过去的错误中学习（{past_memory_str}）
5. 三级别风险判断：
   - 短线风险：情绪/事件/量价异常
   - 波段风险：趋势/资金/板块轮动位置
   - 长线风险：估值/政策/景气方向

标的约束：
{instrument_context}

分析师辩论历史：
{history}

交易员原始计划：
{trader_plan}

请用中文撰写最终决策，包含明确建议（波段为主）、止损止盈位、三级别风险提示。"""

        response = llm.invoke(prompt)
        response_content = response.content

        logger.info(f"[风险经理] 最终决策生成完成，长度: {len(response_content)}")

        new_state = {
            "judge_decision": response_content,
            "history": risk_debate_state["history"],
            "risky_history": risk_debate_state["risky_history"],
            "safe_history": risk_debate_state["safe_history"],
            "neutral_history": risk_debate_state["neutral_history"],
            "latest_speaker": "Judge",
            "current_risky_response": risk_debate_state["current_risky_response"],
            "current_safe_response": risk_debate_state["current_safe_response"],
            "current_neutral_response": risk_debate_state["current_neutral_response"],
            "count": risk_debate_state["count"],
        }

        return {
            "risk_debate_state": new_state,
            "final_trade_decision": response_content,
        }

    return risk_manager_node
