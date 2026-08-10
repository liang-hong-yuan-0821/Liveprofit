"""
集成测试 AI.stockAgents.analysts.social_media_analyst — 社交媒体/情绪分析师。
使用真实 LLM（该 Agent 不调用数据工具，纯粹基于已有报告做情绪评估）。
"""

import pytest
from AI.stockAgents.analysts.social_media_analyst import create_social_media_analyst


@pytest.fixture
def state():
    return {
        "trade_date": "2026-08-08",
        "company_of_interest": "000001.SZ",
        "messages": [],
        "sentiment_tool_call_count": 0,
    }


def test_factory_returns_callable(real_llm, real_toolkit):
    node = create_social_media_analyst(real_llm, real_toolkit)
    assert callable(node)


def test_generates_sentiment_report(real_llm, real_toolkit, state):
    """真实运行：生成市场情绪分析报告（无工具调用）"""
    node = create_social_media_analyst(real_llm, real_toolkit)
    result = node(state)

    assert "sentiment_report" in result
    assert len(result["sentiment_report"]) > 100
    # 该 Agent 不调用数据工具
    assert result["sentiment_tool_call_count"] == 0
