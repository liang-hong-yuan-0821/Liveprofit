"""
LiveProfit 保守风险分析师 (简化版)
优先保护资产、最小化波动性、确保稳定增长。
"""

import logging

from AI.utils.prompts import get_system_prompt

logger = logging.getLogger(__name__)


def create_safe_debator(llm):

    def safe_node(state) -> dict:
        risk_debate_state = state["risk_debate_state"]
        history = risk_debate_state.get("history", "")
        safe_history = risk_debate_state.get("safe_history", "")

        current_risky_response = risk_debate_state.get("current_risky_response", "")
        current_neutral_response = risk_debate_state.get("current_neutral_response", "")

        market_report = state.get("stock_tech_report", "")
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]
        trader_decision = state["trader_investment_plan"]

        from AI.stockAgents.utils.agent_utils import build_cross_layer_context
        cross_ctx = build_cross_layer_context(state)

        prompt = get_system_prompt(
            state.get("_current_node_id"),
            lambda: f"""作为安全/保守风险分析师，你的主要目标是保护资产、最小化波动性，并确保稳定、可靠的增长。

在评估交易员的决策时，请批判性地审查高风险要素，指出决策可能使公司面临不当风险的地方。

大盘与板块环境（来自市场层+板块层分析）：
{cross_ctx}

交易员决策：
{trader_decision}

你的任务是积极反驳激进和中性分析师的论点，突出潜在威胁和可持续性问题。

参考数据：
市场研究报告：{market_report}
社交媒体情绪报告：{sentiment_report}
最新新闻：{news_report}
公司基本面报告：{fundamentals_report}

当前对话历史：{history}
激进分析师最后论点：{current_risky_response}
中性分析师最后论点：{current_neutral_response}

如果其他观点没有回应，请不要虚构，只需提出你的观点。
请用中文以对话方式输出，专注于辩论和批评，展示保守立场的优势。"""
        )

        response = llm.invoke(prompt)
        argument = f"Safe Analyst: {response.content}"
        new_count = risk_debate_state["count"] + 1

        logger.info(f"[保守风险] 发言完成，计数: {risk_debate_state['count']} -> {new_count}")

        new_state = {
            "history": history + "\n" + argument,
            "risky_history": risk_debate_state.get("risky_history", ""),
            "safe_history": safe_history + "\n" + argument,
            "neutral_history": risk_debate_state.get("neutral_history", ""),
            "latest_speaker": "Safe",
            "current_risky_response": risk_debate_state.get("current_risky_response", ""),
            "current_safe_response": argument,
            "current_neutral_response": risk_debate_state.get("current_neutral_response", ""),
            "count": new_count,
        }

        return {"risk_debate_state": new_state}

    return safe_node
