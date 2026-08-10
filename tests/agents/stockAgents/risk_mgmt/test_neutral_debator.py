"""
集成测试 AI.stockAgents.risk_mgmt.neutral_debator — 中性风险分析师。
使用真实 LLM。
"""

import pytest
from AI.stockAgents.risk_mgmt.neutral_debator import create_neutral_debator


@pytest.fixture
def state():
    return {
        "risk_debate_state": {
            "history": "Risky: 重仓\nSafe: 轻仓\n",
            "risky_history": "Risky: 重仓\n",
            "safe_history": "Safe: 轻仓\n",
            "neutral_history": "",
            "latest_speaker": "Safe",
            "current_risky_response": "Risky: 重仓\n",
            "current_safe_response": "Safe: 轻仓\n",
            "current_neutral_response": "",
            "count": 2,
        },
        "stock_tech_report": "技术面：横盘整理。",
        "sentiment_report": "情绪面：分歧。",
        "news_report": "新闻面：政策利好。",
        "fundamentals_report": "基本面：低估值。",
        "trader_investment_plan": "交易计划：波段买入。",
    }


def test_factory_returns_callable(real_llm):
    node = create_neutral_debator(real_llm)
    assert callable(node)


def test_balanced_perspective(real_llm, state):
    """真实运行：提供平衡的风险视角"""
    node = create_neutral_debator(real_llm)
    result = node(state)

    debate = result["risk_debate_state"]
    assert "Neutral Analyst" in debate["current_neutral_response"]
    assert debate["count"] == 3
    assert len(debate["current_neutral_response"]) > 50
