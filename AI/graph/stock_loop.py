"""
个股层循环编排（一期：propagate 层顺序循环）

对候选池逐票组装"全局态 + 逐票态重置"的输入 state，
复用同一编译子图对象 invoke，结果收进 stock_results。

逐票态重置防污染：上一只票的报告/消息/辩论历史不得泄漏到下一只。
"""

import logging

from langchain_core.messages import HumanMessage

from AI.stockAgents.utils.agent_states import InvestDebateState, RiskDebateState

logger = logging.getLogger(__name__)

# 逐票态字段：每只票独立一份，循环前必须重置（见方案 2.2）
PER_STOCK_FIELDS = [
    "company_of_interest",
    "stock_tech_report",
    "sentiment_report",
    "news_report",
    "fundamentals_report",
    "investment_plan",
    "trader_investment_plan",
    "risk_debate_state",
    "investment_debate_state",
    "final_trade_decision",
    # 个股层工具调用计数（conditional_logic 路由依赖，防御性重置）
    "stock_tech_tool_call_count",
    "news_tool_call_count",
    "sentiment_tool_call_count",
    "fundamentals_tool_call_count",
]


def _fresh_debate_states() -> dict:
    """构造与 Propagator.create_initial_state 一致的初始辩论状态"""
    return {
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
    }


def build_sub_state(global_state: dict, stock: dict) -> dict:
    """组装单只票的输入 state：全局态 + 逐票态重置。

    - 全局态（市场层/板块层字段、risk_gate、sector_shortlist_structured、
      candidate_stock_pool、trade_date 等）原样保留
    - 逐票态字段全部移除后重建（含 messages 初始消息，防止上一只票污染）
    """
    sub = {k: v for k, v in global_state.items()}
    for field in PER_STOCK_FIELDS:
        sub.pop(field, None)
    sub.update(_fresh_debate_states())
    sub["company_of_interest"] = stock.get("code", "")
    sub["messages"] = [
        HumanMessage(
            content=f"开始分析股票 {stock.get('name', '')}（{stock.get('code', '')}）。"
        )
    ]
    return sub


def run_stock_loop(stock_subgraph, global_state: dict, pool: list,
                   process_signal_fn) -> dict:
    """逐票顺序循环 invoke 个股层子图，产出 stock_results。

    单票失败 → 记入 {"error": ...} 并跳过，不阻断其他票。
    """
    stock_results = {}
    for i, stock in enumerate(pool, 1):
        code = stock.get("code", "")
        name = stock.get("name", "")
        if not code:
            logger.warning(f"[个股层循环] 候选池第 {i} 条缺少 code，跳过: {stock}")
            continue
        logger.info(f"[个股层循环] {i}/{len(pool)} 开始分析: {name} ({code})")
        try:
            sub_state = build_sub_state(global_state, stock)
            final = stock_subgraph.invoke(sub_state)
            decision_text = final.get("final_trade_decision", "")
            try:
                decision_json = process_signal_fn(decision_text)
            except Exception as e:
                logger.warning(f"[个股层循环] {code} 决策抽取失败: {e}")
                decision_json = {"error": f"决策抽取失败: {e}"}
            stock_results[code] = {
                "name": name,
                "sector": stock.get("sector", ""),
                "final_trade_decision": decision_text,
                "decision_json": decision_json,
                "last_close": stock.get("last_close", 0.0),
                "pct_change": stock.get("pct_change", 0.0),
                "vs_avg": stock.get("vs_avg", 0.0),
            }
            logger.info(f"[个股层循环] {code} 完成: action={decision_json.get('action')}")
        except Exception as e:
            logger.error(f"[个股层循环] {code} 分析失败，跳过: {e}")
            stock_results[code] = {
                "name": name,
                "sector": stock.get("sector", ""),
                "error": str(e),
            }
    return stock_results
