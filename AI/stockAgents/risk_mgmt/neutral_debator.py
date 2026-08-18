"""
LiveProfit 中性风险分析师 (简化版)
提供平衡视角，权衡风险和收益，倡导适度策略。
"""

import logging

logger = logging.getLogger(__name__)


def create_neutral_debator(llm):

    def neutral_node(state) -> dict:
        risk_debate_state = state["risk_debate_state"]
        history = risk_debate_state.get("history", "")
        neutral_history = risk_debate_state.get("neutral_history", "")

        current_risky_response = risk_debate_state.get("current_risky_response", "")
        current_safe_response = risk_debate_state.get("current_safe_response", "")

        market_report = state.get("stock_tech_report", "")
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]
        trader_decision = state["trader_investment_plan"]

        from AI.stockAgents.utils.agent_utils import build_cross_layer_context
        cross_ctx = build_cross_layer_context(state)

        prompt = f"""作为中性风险分析师，你的角色是提供平衡的视角，权衡交易员决策的潜在收益和风险。

大盘与板块环境（来自市场层+板块层分析）：
{cross_ctx}

交易员决策：
{trader_decision}

你的任务是挑战激进和安全分析师，指出每种观点可能过于乐观或过于谨慎的地方。

参考数据：
市场研究报告：{market_report}
社交媒体情绪报告：{sentiment_report}
最新新闻：{news_report}
公司基本面报告：{fundamentals_report}

当前对话历史：{history}
激进分析师最后论点：{current_risky_response}
安全分析师最后论点：{current_safe_response}

如果其他观点没有回应，请不要虚构，只需提出你的观点。
请用中文以对话方式输出，批判性地分析双方，倡导更平衡的方法。"""

        response = llm.invoke(prompt)
        argument = f"Neutral Analyst: {response.content}"
        new_count = risk_debate_state["count"] + 1

        logger.info(f"[中性风险] 发言完成，计数: {risk_debate_state['count']} -> {new_count}")

        new_state = {
            "history": history + "\n" + argument,
            "risky_history": risk_debate_state.get("risky_history", ""),
            "safe_history": risk_debate_state.get("safe_history", ""),
            "neutral_history": neutral_history + "\n" + argument,
            "latest_speaker": "Neutral",
            "current_risky_response": risk_debate_state.get("current_risky_response", ""),
            "current_safe_response": risk_debate_state.get("current_safe_response", ""),
            "current_neutral_response": argument,
            "count": new_count,
        }

        return {"risk_debate_state": new_state}

    return neutral_node
