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
        """创建编排图的初始状态（自顶向下：市场 → 板块 → 个股）

        在入口处对 trade_date 做交易日校正：
        - 非交易日 / 盘中交易日 → 回退到上一个可用交易日
        - 原始日期保留在 requested_trade_date 中，供 LLM 透明度标注
        """
        from langchain_core.messages import HumanMessage
        from AI.dataflows.utils.trading_calendar import get_available_trade_date

        raw_date = str(trade_date) if trade_date else ""
        effective_date = raw_date
        date_correction = ""

        if raw_date:
            try:
                effective_date = get_available_trade_date(raw_date)
                if effective_date != raw_date:
                    date_correction = f"{raw_date} → {effective_date}"
                    logger.info(f"日期校正: {date_correction}")
            except Exception as e:
                logger.warning(f"日期校正失败（使用原始日期）: {e}")
                effective_date = raw_date

        # 构建 HumanMessage：如果日期被校正，在消息中说明
        if date_correction:
            msg_content = (
                f"开始交易分析。原始请求日期为 {raw_date}，"
                f"校正后的实际数据日期为 {effective_date}（{date_correction}）。"
            )
        else:
            msg_content = f"开始交易分析，交易日期为 {effective_date}。"

        return {
            "messages": [HumanMessage(content=msg_content)],
            "company_of_interest": "",
            "trade_date": effective_date,
            "requested_trade_date": raw_date,
            "date_correction": date_correction,
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
            "international_event_report": "",
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
            # 市场层结构化结论字段
            "market_regime": "",
            "market_event_calendar": "",
            "risk_gate": "normal",
            # 板块层结构化结论字段
            "sector_shortlist": "",
            "sector_tech_confirm": "",
            "sector_shortlist_structured": [],
            # 选股层
            "candidate_stock_pool": [],
            # 个股层循环
            "stock_results": {},
            # 仓位管理层
            "final_position_plan": {},
            # 板块层 — 轮动预测
            "rotation_prediction_report": "",
            "rotation_top_picks": "",
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
