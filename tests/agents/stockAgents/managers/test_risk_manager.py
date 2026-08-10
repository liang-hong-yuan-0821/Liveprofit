"""
集成测试 AI.stockAgents.managers.risk_manager — 风险经理。
使用真实 LLM + 真实 Memory。
"""

import pytest
from AI.stockAgents.managers.risk_manager import create_risk_manager


@pytest.fixture
def state():
    return {
        "company_of_interest": "000001.SZ",
        "risk_debate_state": {
            "history": (
                "Risky: 银行板块估值修复行情启动，建议仓位80%，目标价18元\n"
                "Safe: 不良率上升值得警惕，建议仓位不超过30%，严格止损14元\n"
                "Neutral: 多空因素交织，建议50%仓位，分批建仓降低风险\n"
            ),
            "risky_history": "Risky: 建议80%仓位\n",
            "safe_history": "Safe: 建议30%仓位严格控制\n",
            "neutral_history": "Neutral: 建议50%仓位分批建仓\n",
            "latest_speaker": "Neutral",
            "current_risky_response": "Risky: 建议80%仓位\n",
            "current_safe_response": "Safe: 建议30%仓位\n",
            "current_neutral_response": "Neutral: 建议50%仓位\n",
            "count": 3,
        },
        "investment_plan": (
            "短线：观望等待MACD金叉确认\n"
            "波段：买入，目标价16元，止损14元\n"
            "长线：配置，估值处于历史底部区间"
        ),
        "stock_tech_report": "技术面：横盘整理末端，MACD即将金叉。",
        "sentiment_report": "情绪面：分歧加大，北向资金增持。",
        "news_report": "新闻面：降准预期利好银行。",
        "fundamentals_report": "基本面：低估值高股息，不良率略有上升。",
    }


def test_factory_returns_callable(real_llm, real_memory):
    node = create_risk_manager(real_llm, real_memory)
    assert callable(node)


def test_factory_accepts_none_memory(real_llm):
    node = create_risk_manager(real_llm, None)
    assert callable(node)


def test_generates_final_decision(real_llm, real_memory, state):
    """真实运行：评估三位风险分析师辩论后做出最终决策"""
    node = create_risk_manager(real_llm, real_memory)
    result = node(state)

    assert "final_trade_decision" in result
    assert len(result["final_trade_decision"]) > 200

    # 最终决策应该明确
    decision = result["final_trade_decision"]
    has_action = any(w in decision for w in ["买入", "卖出", "持有", "观望"])
    assert has_action, f"决策应包含操作建议: {decision[:200]}"

    # 标记为 Judge
    assert result["risk_debate_state"]["latest_speaker"] == "Judge"
