"""
LiveProfit Agent 状态定义
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
    trade_date: Annotated[str, "分析日期（校正后的有效数据日期）"]
    requested_trade_date: Annotated[str, "原始请求日期（校正前，用于透明度标注）"]
    date_correction: Annotated[str, "日期校正说明（如 '2026-08-08(周六,非交易日)→2026-08-07'），空字符串表示无需校正"]

    sender: Annotated[str, "发送消息的 Agent"]

    # 市场层（宏观）报告 — Layer 0 + Layer 1
    international_event_report: Annotated[str, "国际事件提取分析师报告"]  # Layer 0a — 事件识别+历史案例
    international_news_report: Annotated[str, "国际新闻影响分析师报告"]  # Layer 0b — 影响分析
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

    # 市场层结构化结论字段（下游消费用，不截断）
    market_regime: Annotated[str, "三级别市场环境判定（短线/波段/长线）— CN Tech 产出"]
    market_event_calendar: Annotated[str, "三级别事件日历（短线/波段/长线）— CN News 产出"]
    risk_gate: Annotated[str, "市场层熔断开关（normal/caution/block）— CN Tech 规则派生"]

    # 板块层结构化结论字段（下游消费用，不截断）
    sector_shortlist: Annotated[str, "三级别候选板块短名单（短线候选/波段主线/长线配置）— Sector News 产出"]
    sector_tech_confirm: Annotated[str, "候选板块技术确认结论（确认/存疑/否认）— Sector Tech 产出"]
    sector_shortlist_structured: Annotated[list, "候选板块名清单（东财概念体系，经全名单过滤）— 选股层机器消费"]

    # 选股层（纯代码节点产出）
    candidate_stock_pool: Annotated[list, "候选个股池（东财概念板块内跑赢均值的个股）— Screening 节点产出"]

    # 个股层循环（全市场模式，propagate 层顺序循环产出）
    stock_results: Annotated[dict, "逐票分析结果: code → {final_trade_decision, decision_json, ...}"]

    # 仓位管理层（纯代码节点产出）
    final_position_plan: Annotated[dict, "最终交易计划（逐票金额/股数 + 组合汇总）— Position Manager 产出"]

    # 市场层工具调用计数器
    international_event_tool_call_count: Annotated[int, "国际事件提取分析师工具调用计数"]
    international_news_tool_call_count: Annotated[int, "国际新闻影响分析师工具调用计数"]
    us_news_tool_call_count: Annotated[int, "美国新闻分析师工具调用计数"]
    us_tech_tool_call_count: Annotated[int, "美国技术分析师工具调用计数"]
    kr_news_tool_call_count: Annotated[int, "韩国新闻分析师工具调用计数"]
    kr_tech_tool_call_count: Annotated[int, "韩国技术分析师工具调用计数"]
    cn_news_tool_call_count: Annotated[int, "中国新闻分析师工具调用计数"]
    cn_tech_tool_call_count: Annotated[int, "中国技术分析师工具调用计数"]

    # 板块层工具调用计数器
    sector_news_tool_call_count: Annotated[int, "板块新闻分析师工具调用计数"]
    sector_tech_tool_call_count: Annotated[int, "板块技术分析师工具调用计数"]

    # 板块层 — 轮动预测
    rotation_prediction_report: Annotated[str, "板块轮动预测报告（主线+新热点+退潮+明日预测）"]
    rotation_top_picks: Annotated[str, "轮动预测速览结构化文本（供下游展示）"]
    rotation_tool_call_count: Annotated[int, "板块轮动预测工具调用计数"]

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

    # 平台保留 key（单Agent重跑方案 3.2；须在 schema 内否则 LangGraph 按
    # channels 白名单丢弃、快进 guard 失效；checkpoint 序列化会丢弃 `_` 前缀键）
    _rerun_from: Annotated[str, "重跑起点节点 id（快进 guard 用；空串=普通执行）"]
    _current_node_id: Annotated[str, "当前执行节点 id（提示词覆盖解析用）"]
    # selected_layers 亦须在 schema 内：checkpoint 的 screening 模式 stock 层
    # 跳过落盘判定读它，白名单外输入键会被 langgraph 静默丢弃（2026-09-10
    # Code Review 实测：未声明时 state.get("selected_layers") 恒空）
    selected_layers: Annotated[list, "选中的分析层（平台元数据；checkpoint 落盘）"]
