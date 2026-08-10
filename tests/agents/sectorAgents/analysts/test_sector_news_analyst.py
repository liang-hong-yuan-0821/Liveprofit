"""
集成测试 AI.sectorAgents.analysts.sector_news_analyst — 板块新闻分析师。
使用真实 LLM + 真实 Tushare 数据源。
"""

import pytest
from AI.sectorAgents.analysts.sector_news_analyst import create_sector_news_analyst


@pytest.fixture
def state():
    return {
        "trade_date": "2026-08-08",
        "messages": [],
        "sector_news_tool_call_count": 0,
        "market_regime": "",
        "market_event_calendar": "",
    }


def test_factory_returns_callable(real_llm, real_toolkit):
    node = create_sector_news_analyst(real_llm, real_toolkit)
    assert callable(node)


def test_generates_report(real_llm, real_toolkit, state):
    """真实运行：生成板块新闻分析报告，包含候选板块短名单"""
    node = create_sector_news_analyst(real_llm, real_toolkit)
    result = node(state)

    assert "sector_news_report" in result
    assert len(result["sector_news_report"]) > 100

    # 必须产出候选板块短名单
    assert "sector_shortlist" in result
    assert len(result["sector_shortlist"]) > 0

    assert result["sector_news_tool_call_count"] >= 1
