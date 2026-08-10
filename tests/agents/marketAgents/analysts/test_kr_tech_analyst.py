"""
集成测试 AI.marketAgents.analysts.kr_tech_analyst — 韩国市场技术分析师。
使用真实 LLM + 真实 Tushare 数据源。
"""

import pytest
from AI.marketAgents.analysts.kr_tech_analyst import create_kr_tech_analyst


@pytest.fixture
def state():
    return {
        "trade_date": "2026-08-08",
        "messages": [],
        "kr_tech_tool_call_count": 0,
    }


def test_factory_returns_callable(real_llm, real_toolkit):
    node = create_kr_tech_analyst(real_llm, real_toolkit)
    assert callable(node)


def test_generates_report(real_llm, real_toolkit, state):
    """真实运行：生成韩国市场技术分析报告"""
    node = create_kr_tech_analyst(real_llm, real_toolkit)
    result = node(state)

    assert "kr_tech_report" in result
    assert len(result["kr_tech_report"]) > 50
    assert "kr_tech_tool_call_count" in result
