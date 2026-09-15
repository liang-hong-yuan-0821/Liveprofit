"""纯代码风险门控节点图级单测（T6：`market:Risk Gate`，无 LLM/无工具）。

覆盖：
- 节点经最小 StateGraph 接线后 `risk_gate` 正确写入 AgentState 通道；
- 节点返回值与 `market_features.risk_gate_decision` 一致（同一实现，无二次派生）；
- 节点只写 `risk_gate` 一个键（不污染其他状态）；
- 非法/缺失输入不抛异常（不阻塞市场层收尾），落枚举内值（fail-closed → caution）。

规则表全枚举（规则 1–5 短路语义）见
tests/agents/marketAgents/analysts/test_risk_gate.py；结构接线（位置/END/无提示词）
见 tests/graph/test_market_layer_graph.py。
"""

import pytest
from langgraph.graph import END, START, StateGraph

from AI.dataflows import market_features as mf
from AI.marketAgents.market_layer_graph import MarketLayerGraph
from AI.stockAgents.utils.agent_states import AgentState


def _build_gate_graph():
    """最小接线：START → gate（真实节点函数）→ END（可验证状态通道落值）。"""
    workflow = StateGraph(AgentState)
    workflow.add_node("gate", MarketLayerGraph._derive_risk_gate)
    workflow.add_edge(START, "gate")
    workflow.add_edge("gate", END)
    return workflow.compile()


def _quality(ok=True):
    return {
        "degradation_level": "ok" if ok else "insufficient",
        "core_insufficient": not ok,
    }


def _gra(systemic="low", confidence="medium"):
    return {"systemic_risk": systemic, "confidence": confidence}


def _regime(short="适合", wave="进攻"):
    return {
        "short_term": {"level": short},
        "wave": {"level": wave},
        "long_term": {"level": "配置窗口"},
    }


@pytest.mark.parametrize("state,expected", [
    # 规则 1：systemic=high + confidence ∈ {high, medium}
    ({"global_risk_assessment": _gra("high", "high"),
      "market_regime": _regime(), "market_data_quality": _quality()}, "block"),
    # 规则 2：短线 = 回避
    ({"global_risk_assessment": _gra(),
      "market_regime": _regime(short="回避"), "market_data_quality": _quality()}, "block"),
    # 规则 4：systemic=medium
    ({"global_risk_assessment": _gra("medium"),
      "market_regime": _regime(), "market_data_quality": _quality()}, "caution"),
    # 规则 3：质量不足为上限（high/回避 也不 block）
    ({"global_risk_assessment": _gra("high", "high"),
      "market_regime": _regime(short="回避"), "market_data_quality": _quality(False)}, "caution"),
    # 规则 5：全绿
    ({"global_risk_assessment": _gra(),
      "market_regime": _regime(), "market_data_quality": _quality()}, "normal"),
    # 缺失输入 → 结构解析失败 → fail-closed
    ({}, "caution"),
    ({"global_risk_assessment": {}, "market_regime": {},
      "market_data_quality": _quality()}, "caution"),
])
def test_gate_node_writes_enum_to_state_channel(state, expected):
    result = _build_gate_graph().invoke({"messages": [], **state})
    assert result["risk_gate"] == expected
    assert result["risk_gate"] in mf.RISK_GATE_ENUM


def test_gate_node_matches_decision_function():
    """节点只是 decision 函数的转发（派生逻辑单点，无重复实现）。"""
    states = [
        {"global_risk_assessment": _gra("high", "medium"), "market_regime": _regime()},
        {"global_risk_assessment": _gra(), "market_regime": _regime(wave="防御")},
        {"global_risk_assessment": _gra("low", "low"), "market_regime": _regime(
            short="谨慎")},
    ]
    for state in states:
        decision = mf.risk_gate_decision(
            state.get("global_risk_assessment"), state.get("market_regime"),
            state.get("market_data_quality"))
        assert MarketLayerGraph._derive_risk_gate(state)["risk_gate"] == decision["gate"]


def test_gate_node_updates_only_risk_gate():
    assert set(MarketLayerGraph._derive_risk_gate({})) == {"risk_gate"}


@pytest.mark.parametrize("bad_state", [
    {"global_risk_assessment": "文本报告，非 dict", "market_regime": 123,
     "market_data_quality": None},
    {"global_risk_assessment": ["列表"], "market_regime": [1, 2, 3],
     "market_data_quality": "insufficient"},
    {"global_risk_assessment": None, "market_regime": None, "market_data_quality": None},
])
def test_gate_node_never_blocks_on_bad_input(bad_state):
    """任何输入都不抛异常、不越过枚举（市场层收尾不被门控拖垮）。"""
    updates = MarketLayerGraph._derive_risk_gate(bad_state)
    assert updates["risk_gate"] in mf.RISK_GATE_ENUM
