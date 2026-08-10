"""
集成测试 AI.stockAgents.analysts.market_analyst — 市场分析师（个股技术面）。
使用真实 LLM + 真实 Tushare 数据源。
"""

import pytest
from AI.stockAgents.analysts.market_analyst import create_market_analyst


@pytest.fixture
def state():
    return {
        "trade_date": "2026-08-08",
        "company_of_interest": "000001.SZ",
        "messages": [],
        "stock_tech_tool_call_count": 0,
    }


def test_factory_returns_callable(real_llm, real_toolkit):
    node = create_market_analyst(real_llm, real_toolkit)
    assert callable(node)


def test_generates_report(real_llm, real_toolkit, state):
    """真实运行：生成个股技术分析报告，包含均线/MACD/RSI"""
    node = create_market_analyst(real_llm, real_toolkit)
    result = node(state)

    assert "stock_tech_report" in result
    assert len(result["stock_tech_report"]) > 200
    assert result["stock_tech_tool_call_count"] >= 1

    report = result["stock_tech_report"]
    keywords = ["MA", "MACD", "RSI", "布林", "均线"]
    found = [k for k in keywords if k in report]
    assert len(found) >= 2, f"报告应包含至少 2 个技术指标，实际找到: {found}"
