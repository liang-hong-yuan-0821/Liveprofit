"""
YoHo 交易员 (简化版)
基于研究经理的投资计划，做出具体的交易决策。

移除：港股/美股代码路径
"""

import functools
import logging

from AI.stockAgents.utils.instrument_utils import build_instrument_context

logger = logging.getLogger(__name__)


def create_trader(llm, memory):

    def trader_node(state, name):
        ticker = state["company_of_interest"]
        instrument_context = build_instrument_context(ticker)
        investment_plan = state["investment_plan"]

        market_report = state.get("stock_tech_report", "")
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]

        from AI.stockAgents.utils.agent_utils import build_cross_layer_context
        cross_ctx = build_cross_layer_context(state)

        from AI.utils.stock_utils import StockUtils
        market_info = StockUtils.get_market_info(ticker)
        currency = market_info["currency_name"]
        currency_symbol = market_info["currency_symbol"]

        curr_situation = (
            f"{market_report}\n\n{sentiment_report}\n\n"
            f"{news_report}\n\n{fundamentals_report}"
        )

        past_memory_str = "暂无历史记忆数据可参考。"
        if memory is not None:
            memories = memory.get_memories(curr_situation, n_matches=2)
            if memories:
                past_memory_str = "\n\n".join(
                    rec if isinstance(rec, str) else str(rec)
                    for rec in memories
                )

        context = {
            "role": "user",
            "content": (
                f"基于分析师团队的综合分析，以下是针对 {ticker} 的投资计划。"
                f"将此计划作为评估下一次交易决策的基础。\n\n"
                f"投资计划：{investment_plan}\n\n"
                f"利用这些见解做出明智的战略决策。"
            ),
        }

        messages = [
            {
                "role": "system",
                "content": f"""你是一位专业的交易员，负责分析市场数据并做出三级别交易决策。

当前分析的股票：{ticker}，使用货币：{currency}（{currency_symbol}）
{instrument_context}

大盘与板块环境（来自市场层+板块层分析）：
{cross_ctx}

板块归属校验要求：
- 确认 {ticker} 所属行业是否在候选板块短名单中
- 顺势股 → 止损可稍宽（波段级别）；逆势股 → 止损收紧

严格要求：
- 提供三级别建议（短线/波段/长线），波段为主判断
- 必须提供具体的目标价位和止损位：
  * 短线目标价 + 止损位（建议 -3%~-5%）
  * 波段目标价 + 止损位（建议 -8%~-12%）
  * 长线目标价区间
- 提供置信度（0-1之间）和风险评分（0-1之间）
- 所有价格使用 {currency_symbol}

目标价位计算指导：
- 基于基本面分析中的估值数据（P/E、P/B等）
- 参考技术分析的支撑位和阻力位
- 考虑行业平均估值水平
- 结合市场情绪和板块主线判断

请用中文撰写分析内容，并以'最终交易建议: **买入/持有/卖出**'结束你的回应。

历史交易反思和经验教训: {past_memory_str}""",
            },
            context,
        ]

        result = llm.invoke(messages)

        logger.info(f"[交易员] 决策生成完成，长度: {len(result.content)}")

        return {
            "messages": [result],
            "trader_investment_plan": result.content,
            "sender": name,
        }

    return functools.partial(trader_node, name="Trader")
