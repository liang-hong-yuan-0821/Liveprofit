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

    # 结构化事件（T6：0-5 条同构事件；历史统计只引用 LLM 前预取结果）
    events = result["international_events"]
    assert isinstance(events, list) and len(events) <= 5
    for event in events:
        assert event.get("event_scope") == "market"
        assert event.get("fact")
        # 未匹配到预取候选时必须显式 unmatched 且无统计（不得补写）
        if event.get("history_match_status") == "unmatched":
            assert event.get("historical_impact") is None
