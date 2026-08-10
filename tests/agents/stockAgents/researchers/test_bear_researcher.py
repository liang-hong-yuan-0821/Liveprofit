"""
集成测试 AI.stockAgents.researchers.bear_researcher — 看跌研究员。
使用真实 LLM + 真实 Memory。
"""

import pytest
from AI.stockAgents.researchers.bear_researcher import create_bear_researcher


@pytest.fixture
def state():
    return {
        "company_of_interest": "000001.SZ",
        "investment_debate_state": {
            "history": "Bull: 强烈看涨，基于低估值和稳定增长\n",
            "bull_history": "Bull: 强烈看涨\n",
            "bear_history": "",
            "current_response": "Bull: 强烈看涨，基于低估值和稳定增长\n",
            "count": 1,
        },
        "stock_tech_report": "技术面：短期涨幅已大，RSI 接近超买区域，存在回调压力。",
        "sentiment_report": "市场情绪：散户热情高涨，但机构资金出现分歧。",
        "news_report": "新闻面：监管政策不确定性增加，可能影响银行业盈利预期。",
        "fundamentals_report": "基本面：虽然估值低，但不良贷款率上升，净息差持续收窄。",
    }


def test_factory_returns_callable(real_llm, real_memory):
    node = create_bear_researcher(real_llm, real_memory)
    assert callable(node)


def test_factory_accepts_none_memory(real_llm):
    node = create_bear_researcher(real_llm, None)
    assert callable(node)


def test_builds_bear_argument(real_llm, real_memory, state):
    """真实运行：构建看跌论证，关注估值和风险因素"""
    node = create_bear_researcher(real_llm, real_memory)
    result = node(state)

    debate = result["investment_debate_state"]
    assert "Bear Analyst" in debate["current_response"]
    assert debate["count"] == 2
    assert len(debate["current_response"]) > 50
