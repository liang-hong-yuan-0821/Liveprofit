"""
YoHo 看跌研究员 (简化版)
构建看跌投资论证，强调风险、挑战和负面指标。

移除：港股/美股代码路径、data_source_manager 回退
"""

import logging

logger = logging.getLogger(__name__)


def create_bear_researcher(llm, memory):

    def bear_node(state) -> dict:
        ticker = state["company_of_interest"]
        investment_debate_state = state["investment_debate_state"]
        history = investment_debate_state.get("history", "")
        bear_history = investment_debate_state.get("bear_history", "")
        current_response = investment_debate_state.get("current_response", "")

        market_report = state["market_report"]
        sentiment_report = state["sentiment_report"]
        news_report = state["news_report"]
        fundamentals_report = state["fundamentals_report"]

        from AI.utils.stock_utils import StockUtils
        market_info = StockUtils.get_market_info(ticker)
        company_name = _get_company_name(ticker)

        currency = market_info["currency_name"]
        currency_symbol = market_info["currency_symbol"]

        curr_situation = (
            f"{market_report}\n\n{sentiment_report}\n\n"
            f"{news_report}\n\n{fundamentals_report}"
        )

        past_memory_str = ""
        if memory is not None:
            memories = memory.get_memories(curr_situation, n_matches=2)
            for rec in memories:
                if isinstance(rec, str):
                    past_memory_str += rec + "\n\n"

        prompt = f"""你是一位看跌分析师，负责论证不投资股票 {company_name}（{ticker}）的理由。

当前分析的是中国A股，所有价格和估值请使用 {currency}（{currency_symbol}）作为单位。
在你的分析中，请始终使用公司名称"{company_name}"。

请用中文回答，重点关注：
- 风险和挑战：市场饱和、财务不稳定、宏观经济威胁
- 竞争劣势：市场地位较弱、创新下降、竞争对手威胁
- 负面指标：财务数据恶化、市场趋势不利、负面消息
- 反驳看涨观点：用数据和推理批判性分析看涨论点

可用资源：
市场研究报告：{market_report}
社交媒体情绪报告：{sentiment_report}
最新新闻：{news_report}
公司基本面报告：{fundamentals_report}
辩论历史：{history}
最后看涨论点：{current_response}
历史反思：{past_memory_str}

请使用中文，以对话风格呈现你的看跌论点。"""

        response = llm.invoke(prompt)
        argument = f"Bear Analyst: {response.content}"
        new_count = investment_debate_state["count"] + 1

        logger.info(f"[看跌研究员] 发言完成，计数: {investment_debate_state['count']} -> {new_count}")

        new_state = {
            "history": history + "\n" + argument,
            "bear_history": bear_history + "\n" + argument,
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": argument,
            "count": new_count,
        }

        return {"investment_debate_state": new_state}

    return bear_node


def _get_company_name(ticker: str) -> str:
    try:
        from AI.dataflows.interface import get_china_stock_info
        info = get_china_stock_info(ticker)
        if "股票名称:" in info:
            return info.split("股票名称:")[1].split("\n")[0].strip()
    except Exception:
        pass
    return f"股票{ticker}"
