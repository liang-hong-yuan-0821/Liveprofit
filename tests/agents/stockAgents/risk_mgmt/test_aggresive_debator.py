"""
集成测试 AI.stockAgents.risk_mgmt.aggresive_debator — 激进风险分析师。
使用真实 LLM（仅需 LLM，不需要 toolkit 或 memory）。
"""

import pytest
from AI.stockAgents.risk_mgmt.aggresive_debator import create_risky_debator


@pytest.fixture
def state():
    return {
        "risk_debate_state": {
            "history": "",
            "risky_history": "",
            "safe_history": "",
            "neutral_history": "",
            "latest_speaker": "",
            "current_risky_response": "",
            "current_safe_response": "",
            "current_neutral_response": "",
            "count": 0,
        },
        "stock_tech_report": "技术面：横盘末期，MACD即将金叉，成交量萎缩至地量。",
        "sentiment_report": "情绪面：北向资金持续增持，两融余额回升。",
        "news_report": "新闻面：央行降准信号明确，利好银行流动性。",
        "fundamentals_report": "基本面：PE仅6倍处于历史低位，股息率4.5%。",
        "trader_investment_plan": "交易计划：波段买入，目标价15元，止损13.5元。",
    }


def test_factory_returns_callable(real_llm):
    node = create_risky_debator(real_llm)
    assert callable(node)


def test_advocates_high_risk_reward(real_llm, state):
    """真实运行：倡导高回报高风险机会"""
    node = create_risky_debator(real_llm)
    result = node(state)

    debate = result["risk_debate_state"]
    assert "Risky Analyst" in debate["current_risky_response"]
    assert debate["latest_speaker"] == "Risky"
    assert debate["count"] == 1
    assert len(debate["current_risky_response"]) > 50
