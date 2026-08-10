"""
集成测试 AI.marketAgents.analysts.international_event_extraction — 国际事件提取分析师。
使用真实 LLM + 真实数据源。
"""

import pytest
from AI.marketAgents.analysts.international_event_extraction import create_international_event_extraction


@pytest.fixture
def state():
    return {
        "trade_date": "2026-08-08",
        "messages": [],
        "international_event_tool_call_count": 0,
    }


def test_factory_returns_callable(real_llm, real_toolkit):
    node = create_international_event_extraction(real_llm, real_toolkit)
    assert callable(node)


def test_generates_event_report(real_llm, real_toolkit, state):
    """真实运行：生成国际事件提取报告（含历史案例检索）"""
    node = create_international_event_extraction(real_llm, real_toolkit)
    result = node(state)

    assert "international_event_report" in result
    assert len(result["international_event_report"]) > 100
    assert result["international_event_tool_call_count"] >= 1
