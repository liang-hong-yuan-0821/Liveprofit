"""
集成测试 AI.stockAgents.risk_mgmt.conservative_debator — 保守风险分析师。
使用真实 LLM。
"""

import pytest
from AI.stockAgents.risk_mgmt.conservative_debator import create_safe_debator


@pytest.fixture
def state():
    return {
        "risk_debate_state": {
            "history": "Risky: 建议重仓买入，目标18元\n",
            "risky_history": "Risky: 重仓买入\n",
            "safe_history": "",
            "neutral_history": "",
            "latest_speaker": "Risky",
            "current_risky_response": "Risky: 建议重仓买入，目标18元\n",
            "current_safe_response": "",
            "current_neutral_response": "",
            "count": 1,
        },
        "stock_tech_report": "技术面：横盘整理，方向不明。",
        "sentiment_report": "情绪面：分歧加大。",
        "news_report": "新闻面：政策面有利好，但执行效果存疑。",
        "fundamentals_report": "基本面：低估值但不良率上升。",
        "trader_investment_plan": "交易计划：波段买入，目标15元。",
    }


def test_factory_returns_callable(real_llm):
    node = create_safe_debator(real_llm)
    assert callable(node)


def test_emphasizes_capital_preservation(real_llm, state):
    """真实运行：强调资本保全和风险规避"""
    node = create_safe_debator(real_llm)
    result = node(state)

    debate = result["risk_debate_state"]
    assert "Safe Analyst" in debate["current_safe_response"]
    assert debate["count"] == 2
    assert len(debate["current_safe_response"]) > 50
