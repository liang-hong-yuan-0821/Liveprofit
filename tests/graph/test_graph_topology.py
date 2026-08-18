"""
集成测试：编排图拓扑（不 invoke、不消耗 LLM）

验证单票模式向后兼容（拓扑与改造前一致）与全市场模式（Screening 节点替代
Stock Layer 进顶层图，stock_subgraph 保留供逐票循环）。
"""

import pytest

from AI.default_config import load_config
from AI.graph.propagation import Propagator


def _top_nodes(graph):
    return [n for n in graph.get_graph().nodes if not n.startswith("__")]


@pytest.fixture(scope="module")
def _skip_without_api_key():
    if not load_config().get("api_key"):
        pytest.skip("未配置 LIVEPROFIT_API_KEY，跳过拓扑集成测试")


@pytest.mark.usefixtures("_skip_without_api_key")
def test_single_stock_mode_topology_unchanged():
    from AI.graph.trading_graph import TradingAgentsGraph
    g = TradingAgentsGraph(selectedLayer=["market", "sector", "stock"])
    assert _top_nodes(g.graph) == ["Market Layer", "Sector Layer", "Stock Layer"]


@pytest.mark.usefixtures("_skip_without_api_key")
def test_screening_mode_topology():
    from AI.graph.trading_graph import TradingAgentsGraph
    g = TradingAgentsGraph(selectedLayer=["market", "sector", "screening", "position"])
    assert _top_nodes(g.graph) == ["Market Layer", "Sector Layer", "Screening"]
    assert hasattr(g, "stock_subgraph"), "stock_subgraph 应保留供逐票循环复用"


@pytest.mark.usefixtures("_skip_without_api_key")
def test_market_only_mode_topology():
    """回归：仅选 market 层时，个股层不得隐式挂进顶层图"""
    from AI.graph.trading_graph import TradingAgentsGraph
    g = TradingAgentsGraph(selectedLayer=["market"])
    assert _top_nodes(g.graph) == ["Market Layer"]


@pytest.mark.usefixtures("_skip_without_api_key")
def test_invalid_layer_raises():
    """回归：selectedLayer 不含任何有效层时应显式报错，而非静默回退"""
    from AI.graph.trading_graph import TradingAgentsGraph
    with pytest.raises(ValueError, match="selectedLayer"):
        TradingAgentsGraph(selectedLayer=["position"])


def test_initial_state_has_new_fields():
    init = Propagator().create_initial_state("2026-08-14")
    assert init["risk_gate"] == "normal"
    assert init["sector_shortlist_structured"] == []
    assert init["candidate_stock_pool"] == []
    assert init["stock_results"] == {}
    assert init["final_position_plan"] == {}
