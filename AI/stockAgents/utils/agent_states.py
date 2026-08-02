"""
YoHo Agent 状态定义
定义 LangGraph 中使用的所有状态 TypedDict。

从 TradingAgents-CN 复制，仅修改 import 路径。
"""

from typing import Annotated
from typing_extensions import TypedDict
from langgraph.graph import MessagesState


# 投资研究团队状态
class InvestDebateState(TypedDict):
    bull_history: Annotated[str, "看涨研究员对话历史"]
    bear_history: Annotated[str, "看跌研究员对话历史"]
    history: Annotated[str, "完整对话历史"]
    current_response: Annotated[str, "最新发言"]
    judge_decision: Annotated[str, "研究经理最终裁决"]
    count: Annotated[int, "当前辩论轮数"]


# 风险管理团队状态
class RiskDebateState(TypedDict):
    risky_history: Annotated[str, "激进分析师对话历史"]
    safe_history: Annotated[str, "保守分析师对话历史"]
    neutral_history: Annotated[str, "中性分析师对话历史"]
    history: Annotated[str, "完整对话历史"]
    latest_speaker: Annotated[str, "最后发言的分析师"]
    current_risky_response: Annotated[str, "激进分析师最新发言"]
    current_safe_response: Annotated[str, "保守分析师最新发言"]
    current_neutral_response: Annotated[str, "中性分析师最新发言"]
    judge_decision: Annotated[str, "风险经理裁决"]
    count: Annotated[int, "当前讨论轮数"]


# 主状态
class AgentState(MessagesState):
    company_of_interest: Annotated[str, "待分析的股票"]
    trade_date: Annotated[str, "分析日期"]

    sender: Annotated[str, "发送消息的 Agent"]

    # 市场层（宏观）报告 — Layer 0 + Layer 1
    international_news_report: Annotated[str, "国际新闻分析师报告"]  # Layer 0
    us_news_report: Annotated[str, "美国新闻分析师报告"]
    us_tech_report: Annotated[str, "美国技术分析师报告"]
    kr_news_report: Annotated[str, "韩国新闻分析师报告"]
    kr_tech_report: Annotated[str, "韩国技术分析师报告"]
    cn_news_report: Annotated[str, "中国新闻分析师报告"]
    cn_tech_report: Annotated[str, "中国技术分析师报告"]

    # 个股层分析师报告
    stock_tech_report: Annotated[str, "个股技术面分析师报告"]  # 原名 market_report
    sentiment_report: Annotated[str, "社交媒体分析师报告"]
    news_report: Annotated[str, "新闻分析师报告"]
    fundamentals_report: Annotated[str, "基本面分析师报告"]

    # 板块层报告
    sector_news_report: Annotated[str, "板块新闻分析师报告（行业排名+资金流向+轮动判断）"]
    sector_tech_report: Annotated[str, "板块技术分析师报告（全行业技术扫描+AI专题+风格验证）"]

    # 市场层工具调用计数器
    international_news_tool_call_count: Annotated[int, "国际新闻分析师工具调用计数"]
    us_news_tool_call_count: Annotated[int, "美国新闻分析师工具调用计数"]
    us_tech_tool_call_count: Annotated[int, "美国技术分析师工具调用计数"]
    kr_news_tool_call_count: Annotated[int, "韩国新闻分析师工具调用计数"]
    kr_tech_tool_call_count: Annotated[int, "韩国技术分析师工具调用计数"]
    cn_news_tool_call_count: Annotated[int, "中国新闻分析师工具调用计数"]
    cn_tech_tool_call_count: Annotated[int, "中国技术分析师工具调用计数"]

    # 板块层工具调用计数器
    sector_news_tool_call_count: Annotated[int, "板块新闻分析师工具调用计数"]
    sector_tech_tool_call_count: Annotated[int, "板块技术分析师工具调用计数"]

    # 个股层工具调用计数器
    stock_tech_tool_call_count: Annotated[int, "个股技术分析师工具调用计数"]
    news_tool_call_count: Annotated[int, "新闻分析师工具调用计数"]
    sentiment_tool_call_count: Annotated[int, "社交媒体分析师工具调用计数"]
    fundamentals_tool_call_count: Annotated[int, "基本面分析师工具调用计数"]

    # 投资辩论
    investment_debate_state: Annotated[InvestDebateState, "投资辩论状态"]
    investment_plan: Annotated[str, "研究经理生成的投资计划"]

    trader_investment_plan: Annotated[str, "交易员生成的交易计划"]

    # 风险管理
    risk_debate_state: Annotated[RiskDebateState, "风险讨论状态"]
    final_trade_decision: Annotated[str, "最终交易决策"]
