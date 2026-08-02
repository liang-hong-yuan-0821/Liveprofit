"""
YoHo 状态传播器 (简化版)
处理 LangGraph 初始状态的创建和图调用参数。
从 TradingAgents-CN 复制，仅修改 import 路径。
"""

import logging
from typing import Dict, Any

from AI.stockAgents.utils.agent_states import InvestDebateState, RiskDebateState
from AI.utils.call_trace import trace_call

logger = logging.getLogger(__name__)


class Propagator:
    """处理图的状态初始化和传播"""

    def __init__(self, max_recur_limit=100):
        self.max_recur_limit = max_recur_limit

    @trace_call(show_params=["trade_date"])
    def create_initial_state(self, trade_date: str) -> Dict[str, Any]:
        """创建编排图的初始状态（自顶向下：市场 → 板块 → 个股）"""
        from langchain_core.messages import HumanMessage

        return {
            "messages": [HumanMessage(content=f"开始交易分析，交易日期为 {trade_date}。")],
            "company_of_interest": "",
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
            # 市场层报告（宏观）
            "international_news_report": "",
            "us_news_report": "",
            "us_tech_report": "",
            "kr_news_report": "",
            "kr_tech_report": "",
            "cn_news_report": "",
            "cn_tech_report": "",
            # 板块层报告
            "sector_news_report": "",
            "sector_tech_report": "",
            # 个股层报告
            "stock_tech_report": "",
            "fundamentals_report": "",
            "sentiment_report": "",
            "news_report": "",
        }

    @trace_call(show_params=["use_progress_callback"], show_result=True)
    def get_graph_args(self, use_progress_callback: bool = False) -> Dict[str, Any]:
        """获取图调用参数"""
        stream_mode = "updates" if use_progress_callback else "values"
        return {
            "stream_mode": stream_mode,
            "config": {"recursion_limit": self.max_recur_limit},
        }
