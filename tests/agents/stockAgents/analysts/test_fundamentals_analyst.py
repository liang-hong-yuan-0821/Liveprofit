"""
集成测试 AI.stockAgents.analysts.fundamentals_analyst — 基本面分析师。
使用真实 LLM + 真实 Tushare 数据源。
"""

import pytest
from AI.stockAgents.analysts.fundamentals_analyst import create_fundamentals_analyst


@pytest.fixture
def state():
    return {
        "trade_date": "2026-08-08",
        "company_of_interest": "000001.SZ",
        "messages": [],
        "fundamentals_tool_call_count": 0,
    }


def test_factory_returns_callable(real_llm, real_toolkit):
    node = create_fundamentals_analyst(real_llm, real_toolkit)
    assert callable(node)


def test_generates_report(real_llm, real_toolkit, state):
    """真实运行：生成基本面分析报告，包含 ROE/毛利率/估值等"""
    node = create_fundamentals_analyst(real_llm, real_toolkit)
    result = node(state)

    assert "fundamentals_report" in result
    assert len(result["fundamentals_report"]) > 200, f"报告太短: {len(result['fundamentals_report'])} 字符"
    assert result["fundamentals_tool_call_count"] >= 1

    # 应包含基本面分析关键术语
    report = result["fundamentals_report"]
    keywords = ["ROE", "毛利率", "PE", "营收", "资产"]
    found = [k for k in keywords if k in report]
    assert len(found) >= 2, f"报告应包含至少 2 个基本面指标，实际找到: {found}"
