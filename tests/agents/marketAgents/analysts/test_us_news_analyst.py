"""
集成测试 AI.marketAgents.analysts.us_news_analyst — 美国市场新闻分析师。
使用真实 LLM + 真实 Tushare 数据源。
"""

import pytest
from AI.marketAgents.analysts.us_news_analyst import create_us_news_analyst


@pytest.fixture
def state():
    return {
        "trade_date": "2026-08-08",
        "messages": [],
        "us_news_tool_call_count": 0,
    }


def test_factory_returns_callable(real_llm, real_toolkit):
    node = create_us_news_analyst(real_llm, real_toolkit)
    assert callable(node)


def test_generates_report(real_llm, real_toolkit, state):
    """真实运行：生成美国市场新闻分析报告"""
    node = create_us_news_analyst(real_llm, real_toolkit)
    result = node(state)

    assert "us_news_report" in result
    assert len(result["us_news_report"]) > 50
    assert "us_news_tool_call_count" in result
