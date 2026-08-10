"""
集成测试 AI.stockAgents.trader.trader — 交易员。
使用真实 LLM + 真实 Memory。
"""

import pytest
from AI.stockAgents.trader.trader import create_trader


@pytest.fixture
def state():
    return {
        "company_of_interest": "000001.SZ",
        "investment_plan": (
            "研究经理决策：\n"
            "短线：观望，等待MACD金叉确认\n"
            "波段（主判断）：买入，目标价16元，止损14元\n"
            "长线：配置，PE处于历史10%分位，股息率4.5%"
        ),
        "stock_tech_report": "技术面：股价12.5元，均线粘合，MACD即将金叉，布林带收窄至年内最窄。",
        "sentiment_report": "情绪面：北向资金连续5日净流入，两融余额触底回升。",
        "news_report": "新闻面：央行降准预期明确，利好银行板块。公司年报超预期。",
        "fundamentals_report": "基本面：PE 6.2倍，PB 0.7倍，ROE 12.3%，不良率1.8%，净息差2.1%。",
    }


def test_factory_returns_callable(real_llm, real_memory):
    trade_node = create_trader(real_llm, real_memory)
    assert callable(trade_node)


def test_factory_accepts_none_memory(real_llm):
    trade_node = create_trader(real_llm, None)
    assert callable(trade_node)


def test_generates_trade_decision(real_llm, real_memory, state):
    """真实运行：生成三级别交易决策（短线/波段/长线），包含止损位"""
    trade_node = create_trader(real_llm, real_memory)
    result = trade_node(state)

    assert "trader_investment_plan" in result
    assert result["sender"] == "Trader"
    assert len(result["trader_investment_plan"]) > 200

    plan = result["trader_investment_plan"]
    # 三级别建议
    assert "短线" in plan or "波段" in plan or "长线" in plan
    # 最终建议
    assert "最终交易建议" in plan
    # 应有操作
    has_action = any(w in plan for w in ["买入", "卖出", "持有"])
    assert has_action, f"交易决策应包含操作建议: {plan[:200]}"
