"""
集成测试 AI.stockAgents.analysts.news_analyst — 新闻分析师（个股新闻）。
使用真实 LLM + 真实 Tushare 数据源。
"""

import pytest
from AI.stockAgents.analysts.news_analyst import create_news_analyst


@pytest.fixture
def state():
    return {
        "trade_date": "2026-08-08",
        "company_of_interest": "000001.SZ",
        "messages": [],
        "news_tool_call_count": 0,
    }


def test_factory_returns_callable(real_llm, real_toolkit):
    node = create_news_analyst(real_llm, real_toolkit)
    assert callable(node)


def test_generates_report(real_llm, real_toolkit, state):
    """真实运行：生成个股新闻分析报告"""
    node = create_news_analyst(real_llm, real_toolkit)
    result = node(state)

    assert "news_report" in result
    assert len(result["news_report"]) > 100
    assert result["news_tool_call_count"] >= 1
