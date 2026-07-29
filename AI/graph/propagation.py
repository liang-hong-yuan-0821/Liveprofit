"""
YoHo 状态传播器 (简化版)
处理 LangGraph 初始状态的创建和图调用参数。
从 TradingAgents-CN 复制，仅修改 import 路径。
"""

import logging
from typing import Dict, Any

from AI.agents.utils.agent_states import InvestDebateState, RiskDebateState

logger = logging.getLogger(__name__)


class Propagator:
    """处理图的状态初始化和传播"""

    def __init__(self, max_recur_limit=100):
        self.max_recur_limit = max_recur_limit

    def create_initial_state(self, company_name: str, trade_date: str) -> Dict[str, Any]:
        """创建 Agent 图的初始状态"""
        from langchain_core.messages import HumanMessage

        analysis_request = (
            f"请对股票 {company_name} 进行全面分析，交易日期为 {trade_date}。"
        )

        return {
            "messages": [HumanMessage(content=analysis_request)],
            "company_of_interest": company_name,
            "trade_date": str(trade_date),
            "investment_debate_state": InvestDebateState(
                {"history": "", "current_response": "", "count": 0}
            ),
            "risk_debate_state": RiskDebateState(
                {
                    "history": "",
                    "current_risky_response": "",
                    "current_safe_response": "",
                    "current_neutral_response": "",
                    "count": 0,
                }
            ),
            "market_report": "",
            "fundamentals_report": "",
            "sentiment_report": "",
            "news_report": "",
            "tech_market_report": "",
        }

    def get_graph_args(self, use_progress_callback: bool = False) -> Dict[str, Any]:
        """获取图调用参数"""
        stream_mode = "updates" if use_progress_callback else "values"
        return {
            "stream_mode": stream_mode,
            "config": {"recursion_limit": self.max_recur_limit},
        }
