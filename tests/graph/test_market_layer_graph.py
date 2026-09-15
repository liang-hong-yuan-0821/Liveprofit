"""市场层子图结构单测（T6：COUNTRIES 裁剪 + 纯代码 Risk Gate 节点接线）。

以 dummy 依赖编译（`build()` 只接线、不调用 LLM；先例 AI/graph/topology.py 的
`_compile_layer`），断言：
- `COUNTRIES=["cn"]`：市场子图只含 CN 节点，无 US/KR；
- 门控节点 `Risk Gate` 位于市场层末尾并接入 END（CN Tech 之后）；
- 事件研究工具 `search_similar_events` 不再注册（即使 toolkit 暴露该工具）；
- 门控节点为非 LLM 节点：无默认提示词条目、无 toolkit/llm 依赖、仅在
  `_NODE_LAYER` 登记 layer 前缀（checkpoint/重跑快进前提）。

不依赖真实 LLM / toolkit。
"""

import inspect

from AI.graph.topology import _Dummy
from AI.marketAgents.market_layer_graph import MarketLayerGraph
from AI.utils.llm_callbacks import _NODE_LAYER
from AI.utils.prompts import DEFAULT_PROMPTS

_EXPECTED_NODES = [
    "International Event Extraction Analyst",
    "Msg Clear International Event Extraction",
    "International News Analyst",
    "Msg Clear International News",
    "CN News Analyst",
    "Msg Clear CN News",
    "CN Tech Analyst",
    "Msg Clear CN Tech",
    "Risk Gate",
]


class _Toolkit:
    """工具名 → 同名哨兵值（断言 `_get_tools` 筛选结果用）。"""

    def __init__(self, names):
        for name in names:
            setattr(self, name, name)


def _compiled():
    return MarketLayerGraph(_Dummy(), None).build()


# ---------------- 国家裁剪 + 节点清单 ----------------

def test_countries_only_cn():
    """`COUNTRIES=["cn"]`：US/KR 分析师不接入市场子图（保留文件、不接线）。"""
    assert MarketLayerGraph.COUNTRIES == ["cn"]
    names = list(_compiled().builder.nodes.keys())
    assert names == _EXPECTED_NODES
    assert not [n for n in names if n.startswith(("US ", "KR "))]


# ---------------- 门控节点接线 ----------------

def test_gate_node_is_last_and_wired_to_end():
    """门控节点位于市场层末尾：Msg Clear CN Tech → Risk Gate → END，无条件边。"""
    compiled = _compiled()
    builder = compiled.builder
    assert list(builder.nodes.keys())[-1] == "Risk Gate"
    assert ("Msg Clear CN Tech", "Risk Gate") in set(builder.edges)
    assert ("Risk Gate", "__end__") in set(builder.edges)
    assert "Risk Gate" not in builder.branches  # 纯代码直连，无路由分支


def test_gate_node_registered_for_checkpoint_and_rerun():
    """`_NODE_LAYER` 登记 `market:Risk Gate`（guard_checkpoint 与重跑 entry 前提）。"""
    assert _NODE_LAYER["Risk Gate"] == "market"


# ---------------- 非 LLM 节点契约 ----------------

def test_gate_node_has_no_prompt_and_no_dependencies():
    """纯代码节点：无默认提示词条目、节点函数只消费 state。"""
    assert "market:Risk Gate" not in DEFAULT_PROMPTS
    params = list(inspect.signature(MarketLayerGraph._derive_risk_gate).parameters)
    assert params == ["state"]


# ---------------- 事件研究工具下线 ----------------

def test_event_study_tool_not_registered():
    """`_get_tools("intl_news")` 不再包含 `search_similar_events`（T6 直接替换）。"""
    toolkit = _Toolkit([
        "search_similar_events",
        "get_global_macro_news", "get_central_bank_calendar",
        "get_macro_indicators", "get_commodity_fx_overview",
        "get_event_calendar_history",
    ])
    graph = MarketLayerGraph(_Dummy(), toolkit)
    assert set(graph._get_tools("intl_news")) == {
        "get_global_macro_news", "get_central_bank_calendar",
        "get_macro_indicators", "get_commodity_fx_overview",
        "get_event_calendar_history",
    }


def test_cn_news_tools_unchanged():
    """CN News 日历工具注册不受影响（资金日历仍为工具循环）。"""
    toolkit = _Toolkit([
        "get_ipo_calendar", "get_share_unlock_calendar",
        "get_futures_expiry_calendar", "get_margin_trading_balance",
    ])
    graph = MarketLayerGraph(_Dummy(), toolkit)
    assert set(graph._get_tools("cn_news")) == {
        "get_ipo_calendar", "get_share_unlock_calendar",
        "get_futures_expiry_calendar", "get_margin_trading_balance",
    }
