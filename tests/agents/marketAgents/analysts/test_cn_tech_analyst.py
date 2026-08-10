"""
集成测试 AI.marketAgents.analysts.cn_tech_analyst — 中国市场技术分析师。
使用真实 LLM + 真实 Tushare 数据源。
"""

import pytest
from AI.marketAgents.analysts.cn_tech_analyst import create_cn_tech_analyst


@pytest.fixture
def state():
    return {
        "trade_date": "2026-08-08",
        "messages": [],
        "cn_tech_tool_call_count": 0,
    }


def test_factory_returns_callable(real_llm, real_toolkit):
    node = create_cn_tech_analyst(real_llm, real_toolkit)
    assert callable(node)


def test_generates_report(real_llm, real_toolkit, state):
    """真实运行：生成中国市场技术分析报告，包含大盘环境判定"""
    node = create_cn_tech_analyst(real_llm, real_toolkit)
    result = node(state)

    assert "cn_tech_report" in result
    assert len(result["cn_tech_report"]) > 100, f"报告太短: {len(result['cn_tech_report'])} 字符"

    # 大盘环境结构化字段
    assert "market_regime" in result
    assert len(result["market_regime"]) > 0

    assert result["cn_tech_tool_call_count"] >= 1
