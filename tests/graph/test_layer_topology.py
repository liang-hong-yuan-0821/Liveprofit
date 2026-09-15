"""拓扑提取模块单测（dummy 依赖编译，不 invoke、不消耗 LLM）。

与 tests/graph/test_graph_topology.py（编排图集成测试，需 API key）区分：
本文件只测 AI/graph/topology.py 的折叠/排序/顶层组合，纯本地可跑。
"""

from AI.graph.topology import _extract_layer_topology, build_topology


def _edge_set(topo):
    """统一成 (source, target, 第三字段, parallel)：LayerTopology 边为元组
    （第三字段=conditional bool），Topology 边为 dataclass（第三字段=kind str）。"""
    result = set()
    for e in topo.edges:
        if isinstance(e, tuple):
            result.add(e)
        else:
            result.add((e.source, e.target, e.kind, e.parallel))
    return result


# ---- 三层子图折叠断言（清单与三个 builder 源码同步，图定义变更时随动更新） ----

def test_market_layer_primary_nodes_and_edges():
    topo = _extract_layer_topology("market")
    assert [name for name, _ in topo.nodes] == [
        "International Event Extraction Analyst",
        "International News Analyst",
        "CN News Analyst",
        "CN Tech Analyst",
        # T6：纯代码风险门控节点（无 LLM），市场子图末尾
        "Risk Gate",
    ]
    assert topo.edges == (
        ("International Event Extraction Analyst", "International News Analyst", False, False),
        # T6：intl_news 不再注册事件研究工具 → dummy 图中为直接边
        # （真实运行图仍按 toolkit 建条件循环，LLM 未 bind_tools → 恒走 Msg Clear）
        ("International News Analyst", "CN News Analyst", False, False),
        ("CN News Analyst", "CN Tech Analyst", False, False),
        ("CN Tech Analyst", "Risk Gate", False, False),
    )
    # 无自环边（工具循环回源路径丢弃）
    assert not any(s == t for s, t, _, _ in _edge_set(topo))


def test_sector_layer_primary_nodes_and_edges():
    topo = _extract_layer_topology("sector")
    assert [name for name, _ in topo.nodes] == [
        "Sector News Analyst",
        "Sector Tech Analyst",
        "Sector Rotation Analyst",
    ]
    assert topo.edges == (
        ("Sector News Analyst", "Sector Tech Analyst", False, False),
        ("Sector Tech Analyst", "Sector Rotation Analyst", False, False),
    )


def test_sector_layer_structure_same_with_structured_list_flag():
    # enable_structured_list 只影响工厂闭包，不影响结构
    assert (
        _extract_layer_topology("sector", has_screening=True)
        == _extract_layer_topology("sector", has_screening=False)
    )


def test_stock_layer_primary_nodes_and_edges():
    topo = _extract_layer_topology("stock")
    assert [name for name, _ in topo.nodes] == [
        "Stock Tech Analyst", "Social Analyst", "News Analyst",
        "Fundamentals Analyst",
        "Bull Researcher", "Bear Researcher", "Research Manager",
        "Trader",
        "Risky Analyst", "Safe Analyst", "Neutral Analyst", "Risk Judge",
    ]
    assert _edge_set(topo) == {
        ("Stock Tech Analyst", "Social Analyst", False, False),
        ("Social Analyst", "News Analyst", False, False),
        ("News Analyst", "Fundamentals Analyst", False, False),
        ("Fundamentals Analyst", "Bull Researcher", False, False),
        # 辩论双向条件边（parallel 互指）
        ("Bull Researcher", "Bear Researcher", True, True),
        ("Bear Researcher", "Bull Researcher", True, True),
        ("Bull Researcher", "Research Manager", True, False),
        ("Bear Researcher", "Research Manager", True, False),
        ("Research Manager", "Trader", False, False),
        ("Trader", "Risky Analyst", False, False),
        # 风险单向条件环：不标 parallel
        ("Risky Analyst", "Safe Analyst", True, False),
        ("Safe Analyst", "Neutral Analyst", True, False),
        ("Neutral Analyst", "Risky Analyst", True, False),
        ("Risky Analyst", "Risk Judge", True, False),
        ("Safe Analyst", "Risk Judge", True, False),
        ("Neutral Analyst", "Risk Judge", True, False),
    }
    # 确定性顺序：Risk Judge 排最后（声明序 DFS），Safe/Neutral 在 Risky 之后
    assert topo.nodes[8][0] == "Risky Analyst"
    assert topo.nodes[9][0] == "Safe Analyst"
    assert topo.nodes[10][0] == "Neutral Analyst"
    assert topo.nodes[11][0] == "Risk Judge"


# ---- 顶层组合（镜像 trading_graph._build_graph 连线） ----

def test_build_topology_single_stock_mode():
    topo = build_topology(("market", "sector", "stock"))
    rows = {n.id: n.row for n in topo.nodes}
    assert rows["market:International Event Extraction Analyst"] == 0
    assert rows["sector:Sector News Analyst"] == 1
    assert rows["stock:Stock Tech Analyst"] == 2
    # 层间链：market 最后主节点（纯代码 Risk Gate）→ sector 首主节点 → stock 首主节点
    assert ("market:Risk Gate", "sector:Sector News Analyst", "direct", False) in _edge_set(topo)
    assert ("sector:Sector Rotation Analyst", "stock:Stock Tech Analyst", "direct", False) in _edge_set(topo)
    assert not any(e.kind == "loop" for e in topo.edges)


def test_build_topology_screening_mode():
    topo = build_topology(("market", "sector", "screening"))
    rows = {n.id: n.row for n in topo.nodes}
    # screening 任务无条件跑逐票循环：stock 层恒在（与 selected_layers 是否含 stock 无关）
    assert rows["screening:Screening"] == 2
    assert rows["stock:Stock Tech Analyst"] == 3
    assert ("sector:Sector Rotation Analyst", "screening:Screening", "direct", False) in _edge_set(topo)
    assert ("screening:Screening", "stock:Stock Tech Analyst", "loop", False) in _edge_set(topo)


def test_build_topology_market_only():
    topo = build_topology(("market",))
    assert {n.layer for n in topo.nodes} == {"market"}
    # 层内边保留，但无层间边、无 loop 边
    assert {e.source.split(":")[0] for e in topo.edges} == {"market"}
    assert not any(e.kind == "loop" for e in topo.edges)


def test_build_topology_position_layer_ignored():
    # "position" 无图影响：结构与 ("market", "sector") 一致
    assert build_topology(("market", "sector", "position")) == build_topology(("market", "sector"))


def test_build_topology_unknown_layer_ignored():
    topo = build_topology(("market", "unknown_layer"))
    assert {n.layer for n in topo.nodes} == {"market"}


def test_build_topology_lru_cache_same_object():
    assert build_topology(("market", "sector", "stock")) is build_topology(("market", "sector", "stock"))
