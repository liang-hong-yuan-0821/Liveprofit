"""
YoHo 看涨研究员 (简化版)
构建看涨投资论证，强调增长潜力、竞争优势和积极指标。

移除：港股/美股代码路径、data_source_manager 回退
"""

import logging

logger = logging.getLogger(__name__)


def create_bull_researcher(llm, memory):

    def bull_node(state) -> dict:
        ticker = state["company_of_interest"]
        investment_debate_state = state["investment_debate_state"]
        history = investment_debate_state.get("history", "")
        bull_history = investment_debate_state.get("bull_history", "")
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

        prompt = f"""你是一位看涨分析师，负责为股票 {company_name}（{ticker}）建立强有力的看涨论证。

当前分析的是中国A股，所有价格和估值请使用 {currency}（{currency_symbol}）作为单位。
在你的分析中，请始终使用公司名称"{company_name}"。

请用中文回答，重点关注：
- 增长潜力：市场机会、收入预测、可扩展性
- 竞争优势：独特产品、强势品牌、市场地位
- 积极指标：财务健康、行业趋势、正面消息
- 反驳看跌观点：用数据和推理回应看跌担忧

可用资源：
市场研究报告：{market_report}
社交媒体情绪报告：{sentiment_report}
最新新闻：{news_report}
公司基本面报告：{fundamentals_report}
辩论历史：{history}
最后看跌论点：{current_response}
历史反思：{past_memory_str}

请使用中文，以对话风格呈现你的看涨论点。"""

        response = llm.invoke(prompt)
        argument = f"Bull Analyst: {response.content}"
        new_count = investment_debate_state["count"] + 1

        logger.info(f"[看涨研究员] 发言完成，计数: {investment_debate_state['count']} -> {new_count}")

        new_state = {
            "history": history + "\n" + argument,
            "bull_history": bull_history + "\n" + argument,
            "bear_history": investment_debate_state.get("bear_history", ""),
            "current_response": argument,
            "count": new_count,
        }

        return {"investment_debate_state": new_state}

    return bull_node


def _get_company_name(ticker: str) -> str:
    try:
        from AI.dataflows.interface import get_china_stock_info
        info = get_china_stock_info(ticker)
        if "股票名称:" in info:
            return info.split("股票名称:")[1].split("\n")[0].strip()
    except Exception:
        pass
    return f"股票{ticker}"
