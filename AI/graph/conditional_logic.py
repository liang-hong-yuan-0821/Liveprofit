"""
YoHo 条件逻辑 (简化版)
控制 LangGraph 中的条件边路由。
从 TradingAgents-CN 复制，仅修改 import 路径。
"""

import logging

from AI.agents.utils.agent_states import AgentState

logger = logging.getLogger(__name__)


class ConditionalLogic:
    """处理图的流程控制"""

    def __init__(self, max_debate_rounds=1, max_risk_discuss_rounds=1):
        self.max_debate_rounds = max_debate_rounds
        self.max_risk_discuss_rounds = max_risk_discuss_rounds

    # ==================== 分析师条件 ====================

    def _check_analyst_continue(self, state: AgentState, report_key: str,
                                 tool_count_key: str, clear_node: str,
                                 tools_node: str, max_tool_calls: int = 3) -> str:
        """通用分析师条件判断"""
        messages = state["messages"]
        last_message = messages[-1]
        tool_call_count = state.get(tool_count_key, 0)
        report = state.get(report_key, "")

        if tool_call_count >= max_tool_calls:
            logger.warning(f"[条件] {report_key}: 达到最大工具调用次数，强制结束")
            return clear_node

        if report and len(report) > 100:
            return clear_node

        if hasattr(last_message, 'tool_calls') and last_message.tool_calls:
            return tools_node

        return clear_node

    def should_continue_market(self, state: AgentState) -> str:
        return self._check_analyst_continue(
            state, "market_report", "market_tool_call_count",
            "Msg Clear Market", "tools_market"
        )

    def should_continue_social(self, state: AgentState) -> str:
        return self._check_analyst_continue(
            state, "sentiment_report", "sentiment_tool_call_count",
            "Msg Clear Social", "tools_social"
        )

    def should_continue_news(self, state: AgentState) -> str:
        return self._check_analyst_continue(
            state, "news_report", "news_tool_call_count",
            "Msg Clear News", "tools_news"
        )

    def should_continue_fundamentals(self, state: AgentState) -> str:
        return self._check_analyst_continue(
            state, "fundamentals_report", "fundamentals_tool_call_count",
            "Msg Clear Fundamentals", "tools_fundamentals", max_tool_calls=1
        )

    # ==================== 辩论条件 ====================

    def should_continue_debate(self, state: AgentState) -> str:
        """决定投资辩论是否继续"""
        current_count = state["investment_debate_state"]["count"]
        max_count = 2 * self.max_debate_rounds
        current_speaker = state["investment_debate_state"]["current_response"]

        logger.info(f"[辩论控制] 发言次数: {current_count}/{max_count}")

        if current_count >= max_count:
            logger.info("[辩论控制] 达到最大次数 -> Research Manager")
            return "Research Manager"

        next_speaker = (
            "Bear Researcher" if current_speaker.startswith("Bull")
            else "Bull Researcher"
        )
        return next_speaker

    # ==================== 风险分析条件 ====================

    def should_continue_risk_analysis(self, state: AgentState) -> str:
        """决定风险讨论是否继续"""
        current_count = state["risk_debate_state"]["count"]
        max_count = 3 * self.max_risk_discuss_rounds
        latest_speaker = state["risk_debate_state"]["latest_speaker"]

        logger.info(f"[风险控制] 发言次数: {current_count}/{max_count}")

        if current_count >= max_count:
            logger.info("[风险控制] 达到最大次数 -> Risk Judge")
            return "Risk Judge"

        if latest_speaker.startswith("Risky"):
            return "Safe Analyst"
        elif latest_speaker.startswith("Safe"):
            return "Neutral Analyst"
        return "Risky Analyst"
