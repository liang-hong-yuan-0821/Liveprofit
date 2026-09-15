"""
集成测试 AI.marketAgents.analysts.international_news_analyst — 国际新闻分析师。
使用真实 LLM + 真实 Tushare 数据源。
"""

import pytest
from AI.dataflows import market_features as mf
from AI.marketAgents.analysts.international_news_analyst import create_international_news_analyst


@pytest.fixture
def state():
    return {
        "trade_date": "2026-08-08",
        "messages": [],
        "international_news_tool_call_count": 0,
    }


def test_factory_returns_callable(real_llm, real_toolkit):
    node = create_international_news_analyst(real_llm, real_toolkit)
    assert callable(node)


def test_generates_report(real_llm, real_toolkit, state):
    """真实运行：生成国际市场新闻分析报告"""
    node = create_international_news_analyst(real_llm, real_toolkit)
    result = node(state)

    assert "international_news_report" in result
    assert len(result["international_news_report"]) > 100
    assert result["international_news_tool_call_count"] >= 1

    # 结构化全球风险评估（T6：第六章字段；无事件/无覆盖时按枚举降级）
    assessment = result["global_risk_assessment"]
    assert isinstance(assessment, dict)
    assert assessment.get("risk_appetite") in mf.RISK_APPETITE_ENUM
    assert assessment.get("systemic_risk") in mf.SYSTEMIC_RISK_ENUM
    assert assessment.get("confidence") in (None, *mf.CONFIDENCE_ENUM)
    assert isinstance(assessment.get("data_quality"), dict)
    # 事件类证据只消费上游结构化事件（本节点不产出事件列表）
    assert "international_events" not in result
