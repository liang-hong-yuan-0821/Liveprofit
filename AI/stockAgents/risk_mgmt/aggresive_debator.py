"""
LiveProfit 激进风险分析师 (简化版)
倡导高回报、高风险的投资机会，挑战保守观点。
"""

import logging

logger = logging.getLogger(__name__)


def create_risky_debator(llm):

    def risky_node(state) -> dict:
        risk_debate_state = state["risk_debate_state"]
        history = risk_debate_state.get("history", "")
        risky_history = risk_debate_state.get("risky_history", "")

        current_safe_response = risk_debate_state.get("current_safe_response", "")
        current_neutral_response = risk_debate_state.get("current_neutral_response", "")

        market_report = state.get("stock_tech_report", "")
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]
        trader_decision = state["trader_investment_plan"]

        from AI.stockAgents.utils.agent_utils import build_cross_layer_context
        cross_ctx = build_cross_layer_context(state)

        prompt = f"""作为激进风险分析师，你的职责是积极倡导高回报、高风险的投资机会，强调大胆策略和竞争优势。

在评估交易员的决策时，请重点关注潜在的上涨空间、增长潜力和创新收益。

大盘与板块环境（来自市场层+板块层分析）：
{cross_ctx}

交易员决策：
{trader_decision}

你的任务是通过质疑和批评保守和中性立场来为交易员的决策创建令人信服的案例。

参考数据：
市场研究报告：{market_report}
社交媒体情绪报告：{sentiment_report}
最新新闻：{news_report}
公司基本面报告：{fundamentals_report}

当前对话历史：{history}
保守分析师最后论点：{current_safe_response}
中性分析师最后论点：{current_neutral_response}

如果其他观点没有回应，请不要虚构，只需提出你的观点。
请用中文以对话方式输出，专注于辩论和说服，而不仅仅是呈现数据。"""

        response = llm.invoke(prompt)
        argument = f"Risky Analyst: {response.content}"
        new_count = risk_debate_state["count"] + 1

        logger.info(f"[激进风险] 发言完成，计数: {risk_debate_state['count']} -> {new_count}")

        new_state = {
            "history": history + "\n" + argument,
            "risky_history": risky_history + "\n" + argument,
            "safe_history": risk_debate_state.get("safe_history", ""),
            "neutral_history": risk_debate_state.get("neutral_history", ""),
            "latest_speaker": "Risky",
            "current_risky_response": argument,
            "current_safe_response": risk_debate_state.get("current_safe_response", ""),
            "current_neutral_response": risk_debate_state.get("current_neutral_response", ""),
            "count": new_count,
        }

        return {"risk_debate_state": new_state}

    return risky_node
