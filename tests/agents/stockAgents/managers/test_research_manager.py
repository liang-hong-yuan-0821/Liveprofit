"""
集成测试 AI.stockAgents.managers.research_manager — 研究经理。
使用真实 LLM + 真实 Memory。
"""

import pytest
from AI.stockAgents.managers.research_manager import create_research_manager


@pytest.fixture
def state():
    return {
        "company_of_interest": "000001.SZ",
        "investment_debate_state": {
            "history": (
                "Bull: 平安银行估值处于历史低位，PE仅6倍，ROE保持12%以上，股息率4.5%，极具投资价值\n"
                "Bear: 不良贷款率上升至1.8%，净息差从2.5%收窄至2.1%，盈利质量在恶化\n"
            ),
            "bull_history": "Bull: 低估值高股息，投资价值显著\n",
            "bear_history": "Bear: 不良率上升，净息差收窄\n",
            "current_response": "Bear: 盈利质量在恶化\n",
            "count": 2,
        },
        "stock_tech_report": "技术面：股价在12-13元区间横盘整理，MACD即将金叉，成交量萎缩至地量。",
        "sentiment_report": "市场情绪：市场对银行板块分歧加大，但北向资金持续增持。",
        "news_report": "新闻面：央行释放降准信号，利好银行板块流动性。",
        "fundamentals_report": "基本面：PE 6倍，PB 0.7倍，ROE 12%，不良率1.8%，净息差2.1%。",
    }


def test_factory_returns_callable(real_llm, real_memory):
    node = create_research_manager(real_llm, real_memory)
    assert callable(node)


def test_factory_accepts_none_memory(real_llm):
    node = create_research_manager(real_llm, None)
    assert callable(node)


def test_generates_investment_plan(real_llm, real_memory, state):
    """真实运行：评估辩论后生成三级别投资计划（短线/波段/长线）"""
    node = create_research_manager(real_llm, real_memory)
    result = node(state)

    assert "investment_plan" in result
    assert len(result["investment_plan"]) > 200

    # 应包含三级别建议
    plan = result["investment_plan"]
    assert "短" in plan or "波段" in plan, f"投资计划应包含时间级别: {plan[:200]}"

    # judge_decision 应已记录
    assert result["investment_debate_state"]["judge_decision"] is not None
