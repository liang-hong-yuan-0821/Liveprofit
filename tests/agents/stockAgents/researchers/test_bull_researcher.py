"""
集成测试 AI.stockAgents.researchers.bull_researcher — 看涨研究员。
使用真实 LLM + 真实 Memory。
注意：该 Agent 依赖上游 analyst 报告，测试时使用占位报告。
"""

import pytest
from AI.stockAgents.researchers.bull_researcher import create_bull_researcher


@pytest.fixture
def state():
    return {
        "company_of_interest": "000001.SZ",
        "investment_debate_state": {
            "history": "",
            "bull_history": "",
            "bear_history": "",
            "current_response": "",
            "count": 0,
        },
        "stock_tech_report": "技术面：股价在 20 日均线上方运行，MACD 金叉，成交量温和放大。",
        "sentiment_report": "市场情绪：整体偏乐观，资金持续流入银行板块。",
        "news_report": "新闻面：平安银行近期发布年报，业绩超出市场预期。",
        "fundamentals_report": "基本面：ROE 12%，PE 6倍，处于历史低位，股息率 4.5%。",
    }


def test_factory_returns_callable(real_llm, real_memory):
    node = create_bull_researcher(real_llm, real_memory)
    assert callable(node)


def test_factory_accepts_none_memory(real_llm):
    node = create_bull_researcher(real_llm, None)
    assert callable(node)


def test_builds_bull_argument(real_llm, real_memory, state):
    """真实运行：构建看涨论证，强调增长潜力与竞争优势"""
    node = create_bull_researcher(real_llm, real_memory)
    result = node(state)

    debate = result["investment_debate_state"]
    assert "Bull Analyst" in debate["current_response"]
    assert debate["count"] == 1
    assert len(debate["current_response"]) > 50
