"""
集成测试 AI.sectorAgents.analysts.sector_tech_analyst — 板块技术分析师。
使用真实 LLM + 真实 Tushare 数据源。
"""

import pytest
from AI.sectorAgents.analysts.sector_tech_analyst import create_sector_tech_analyst


@pytest.fixture
def state():
    return {
        "trade_date": "2026-08-08",
        "messages": [],
        "sector_tech_tool_call_count": 0,
        "market_regime": "",
        "sector_shortlist": "",
    }


def test_factory_returns_callable(real_llm, real_toolkit):
    node = create_sector_tech_analyst(real_llm, real_toolkit)
    assert callable(node)


def test_generates_report(real_llm, real_toolkit, state):
    """真实运行：生成板块技术分析报告"""
    node = create_sector_tech_analyst(real_llm, real_toolkit)
    result = node(state)

    assert "sector_tech_report" in result
    assert len(result["sector_tech_report"]) > 50
    assert "sector_tech_tool_call_count" in result
