"""
单元测试：个股层循环编排（build_sub_state 逐票态重置 + run_stock_loop 收集）
"""

from unittest.mock import MagicMock

from AI.graph.stock_loop import PER_STOCK_FIELDS, build_sub_state, run_stock_loop


def _global_state():
    return {
        "messages": [MagicMock()],
        "company_of_interest": "000001.SZ",
        "trade_date": "2026-08-14",
        "market_regime": "短线: 正常",
        "sector_shortlist_structured": ["白酒"],
        "candidate_stock_pool": [],
        "stock_tech_report": "上一只票的技术报告",
        "news_report": "上一只票的新闻报告",
        "fundamentals_report": "上一只票的基本面",
        "sentiment_report": "上一只票的情绪",
        "investment_plan": "上一只票的投资计划",
        "trader_investment_plan": "上一只票的交易计划",
        "final_trade_decision": "上一只票的最终决策",
        "investment_debate_state": {"history": "污染", "current_response": "x", "count": 5},
        "risk_debate_state": {"history": "污染", "count": 5},
    }


def _stock():
    return {"code": "600519.SH", "name": "贵州茅台", "sector": "白酒", "last_close": 1501.0}


def test_build_sub_state_resets_per_stock_fields():
    sub = build_sub_state(_global_state(), _stock())

    # 逐票态字段全部重置
    for field in PER_STOCK_FIELDS:
        if field == "company_of_interest":
            assert sub[field] == "600519.SH"
        elif field in ("investment_debate_state", "risk_debate_state"):
            assert sub[field].get("count") == 0
            assert sub[field].get("history") == ""
        else:
            assert field not in sub or sub[field] == ""

    # 全局态保留
    assert sub["trade_date"] == "2026-08-14"
    assert sub["market_regime"] == "短线: 正常"
    assert sub["sector_shortlist_structured"] == ["白酒"]

    # messages 为全新初始消息（不携带上一只票/历史层对话）
    assert len(sub["messages"]) == 1
    assert "贵州茅台" in sub["messages"][0].content


def test_run_stock_loop_collects_results():
    fake_subgraph = MagicMock()
    fake_subgraph.invoke.return_value = {"final_trade_decision": "建议买入"}
    fake_signal = MagicMock(return_value={"action": "买入", "stop_loss": 1400.0})

    pool = [_stock(), {"code": "000858.SZ", "name": "五粮液", "sector": "白酒"}]
    results = run_stock_loop(fake_subgraph, _global_state(), pool, fake_signal)

    assert set(results.keys()) == {"600519.SH", "000858.SZ"}
    assert results["600519.SH"]["final_trade_decision"] == "建议买入"
    assert results["600519.SH"]["decision_json"]["action"] == "买入"
    assert results["600519.SH"]["sector"] == "白酒"
    assert results["600519.SH"]["last_close"] == 1501.0
    # 每只票 invoke 一次
    assert fake_subgraph.invoke.call_count == 2


def test_run_stock_loop_failure_does_not_block_others():
    fake_subgraph = MagicMock()
    fake_subgraph.invoke.side_effect = [RuntimeError("LLM 超时"), {"final_trade_decision": "持有"}]
    fake_signal = MagicMock(return_value={"action": "持有"})

    pool = [_stock(), {"code": "000858.SZ", "name": "五粮液", "sector": "白酒"}]
    results = run_stock_loop(fake_subgraph, _global_state(), pool, fake_signal)

    assert "error" in results["600519.SH"]
    assert "error" not in results["000858.SZ"]
    assert results["000858.SZ"]["decision_json"]["action"] == "持有"


def test_run_stock_loop_signal_failure_keeps_error_json():
    fake_subgraph = MagicMock()
    fake_subgraph.invoke.return_value = {"final_trade_decision": "买入"}
    fake_signal = MagicMock(side_effect=RuntimeError("JSON 解析失败"))

    results = run_stock_loop(fake_subgraph, _global_state(), [_stock()], fake_signal)

    assert "error" in results["600519.SH"]["decision_json"]


def test_run_stock_loop_skips_missing_code():
    fake_subgraph = MagicMock()
    fake_signal = MagicMock()
    results = run_stock_loop(fake_subgraph, _global_state(), [{"name": "无代码"}], fake_signal)
    assert results == {}
    fake_subgraph.invoke.assert_not_called()
