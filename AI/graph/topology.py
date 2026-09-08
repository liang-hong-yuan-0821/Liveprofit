"""
图拓扑提取模块（只读，backend API 进程延迟导入，先例 real_graph_factory.py）。

以真实层图 builder 为唯一事实来源：用 dummy 依赖编译三层子图（build() 只接线、
不调用 LLM，已核对三个 builder 源码），`get_graph()` 提取节点/边，折叠辅助节点
（Msg Clear */tools_*）为主节点邻接表，再按 selected_layers 组合顶层拓扑。

langgraph 1.2.10 实测（var/_probe_getgraph.py 探针 + builder 结构探针）：
- `compiled.builder.nodes`：dict[str, runnable]，插入序 = 声明序，不含 __start__/__end__；
- `compiled.builder.edges`：**set**（无序）——每源节点至多一条直接出边（已核对三个
  builder 源码），多目标场景顺序无关紧要；
- `compiled.builder.branches[src][router_key].ends`：dict[label, node]，**保留声明序**
  （如 Risky Analyst 的 {"Safe Analyst": ..., "Risk Judge": ...} → Safe 在前）——
  条件边邻接由此重建，避免 set 无序导致辩论链顺序错乱（探针实测 get_graph()
  的 edges 也是 set，Bull/Bear 顺序不可靠）；
- get_graph() 的 draw 模拟（apply_writes）对字典 state 会抛并发写冲突，
  本模块因此不依赖 get_graph()。

已知偏差（可接受，均不进入折叠视图）：
- market 的 intl_news 无条件注册事件研究工具（market_layer_graph.py:147），
  dummy 编译图中 International News Analyst 恒有 tools_intl_news 工具循环；
  折叠规则把回到源节点的工具循环路径丢弃（不产自环）。
- 依赖 toolkit 提供工具的新闻类节点（cn_news/sector_news 等），toolkit=None 时
  tools 为空 → dummy 图中为直接边（真实运行时为条件路由）；折叠视图等价，
  仅 conditional 标记与真实图有差异（工具循环折叠进主节点的语义）。

本模块不 import 进内核运行路径（AI/graph/trading_graph.py 不引用本文件）。
"""

from __future__ import annotations

import functools
from collections import deque
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

# 顶层图的辅助/终点节点名（langgraph 常量）
_START = "__start__"
_END = "__end__"
_AUX_PREFIXES = ("Msg Clear ", "tools_")

# 顶层图执行序（trading_graph._build_graph：market → sector → screening|stock → END）
_TOP_LAYERS = ("market", "sector", "screening", "stock")


class _Dummy:
    """build 期兜底依赖：任意属性读取返回 no-op 函数。

    防"工厂函数在 build 时触碰 llm 属性"（读属性得到可调用 no-op 不炸）；
    若某工厂在 build 时真的调用 LLM 方法，no-op 返回 "" 不抛错——但测试的
    节点/边清单断言会暴露结构缺失，属可控暴露。
    """

    def __getattr__(self, name: str):
        def _noop(*args, **kwargs) -> str:
            return ""

        return _noop


@dataclass(frozen=True)
class LayerTopology:
    """单层子图折叠后的主节点拓扑。"""

    nodes: Tuple[Tuple[str, int], ...]  # (label, order)
    edges: Tuple[Tuple[str, str, bool], ...]  # (src_label, dst_label, conditional)


@dataclass(frozen=True)
class TopologyNode:
    id: str
    label: str
    layer: str
    row: int
    order: int


@dataclass(frozen=True)
class TopologyEdge:
    source: str
    target: str
    kind: str  # "direct" | "conditional" | "loop"
    parallel: bool


@dataclass(frozen=True)
class Topology:
    nodes: Tuple[TopologyNode, ...]
    edges: Tuple[TopologyEdge, ...]


def _compile_layer(layer: str, has_screening: bool = False):
    """dummy 依赖编译单层子图（lru_cache 缓存，编译产物不可变）。"""
    if layer == "market":
        from AI.marketAgents.market_layer_graph import MarketLayerGraph

        return MarketLayerGraph(_Dummy(), None).build()
    if layer == "sector":
        from AI.sectorAgents.sector_layer_graph import SectorLayerGraph

        return SectorLayerGraph(
            _Dummy(), None, enable_structured_list=has_screening
        ).build()
    if layer == "stock":
        from AI.stockAgents.stock_layer_graph import StockLayerGraph

        return StockLayerGraph(
            _Dummy(), _Dummy(), None,
            None, None, None, None, None,  # 五个 memory
            _Dummy(), {},  # conditional_logic（属性读取→no-op）、config
        ).build()
    raise ValueError(f"未知层: {layer}")


@functools.lru_cache(maxsize=None)
def _extract_layer_topology(layer: str, has_screening: bool = False) -> LayerTopology:
    compiled = _compile_layer(layer, has_screening)
    builder = compiled.builder

    names = list(builder.nodes.keys())
    primary = [n for n in names if not n.startswith(_AUX_PREFIXES)]

    # 直接边（set 无序；每源节点至多一条直接出边，多目标顺序无关紧要）
    direct: dict = {}
    for src, tgt in builder.edges:
        direct.setdefault(src, []).append(tgt)

    # 条件边邻接（ends 保留声明序）
    cond_targets: dict = {}
    for src, entries in builder.branches.items():
        tgts = []
        for spec in entries.values():
            for tgt in spec.ends.values():
                if tgt not in tgts:
                    tgts.append(tgt)
        cond_targets[src] = tgts

    # 每节点出边 = 直接出边 + 条件出边（按声明序），(target, conditional)
    adjacency: dict = {}
    for name in names:
        adjacency[name] = (
            [(t, False) for t in direct.get(name, [])]
            + [(t, True) for t in cond_targets.get(name, [])]
        )

    # 折叠：从每个主节点 BFS（FIFO 保持声明序）途经辅助节点直到下一主节点；
    # 路径回到源主节点（工具循环）不产出自环边；路径上任一 conditional → 折叠边 conditional。
    collapsed: dict = {}
    for src in primary:
        out: dict = {}
        queue = deque(adjacency.get(src, []))
        while queue:
            tgt, cond = queue.popleft()
            if tgt == src:
                continue  # 工具循环回源，丢弃
            if tgt in primary:
                out[tgt] = cond or out.get(tgt, False)
            else:
                queue.extend((nxt, cond or c) for nxt, c in adjacency.get(tgt, []))
        collapsed[src] = out

    # 入口主节点：从 __start__ 沿边可达的首个主节点（含途经辅助节点的链）
    entry = None
    stack = list(direct.get(_START, []))
    while stack and entry is None:
        nxt = stack.pop()
        if nxt in primary:
            entry = nxt
        else:
            stack.extend(t for t, _ in adjacency.get(nxt, []))

    # order：入口起 DFS 前序（visited 跳过循环回边），未达节点按声明序兜底附后
    order = []
    visited = set()

    def dfs(node):
        if node in visited:
            return
        visited.add(node)
        order.append(node)
        for nxt, _ in collapsed.get(node, {}).items():  # 折叠邻接表按插入序（= 声明序）
            dfs(nxt)

    if entry is not None:
        dfs(entry)
    for name in primary:
        if name not in visited:
            order.append(name)

    index = {name: i for i, name in enumerate(order)}

    # 折叠边 + parallel 标记（双向互指）
    edge_set = set()
    for src in order:
        for tgt, cond in collapsed.get(src, {}).items():
            edge_set.add((src, tgt, cond))
    edges = []
    for src, tgt, cond in sorted(edge_set, key=lambda e: (index[e[0]], index[e[1]])):
        parallel = (tgt, src, cond) in edge_set or any(
            s == tgt and t == src for s, t, _ in edge_set
        )
        edges.append((src, tgt, cond, parallel))

    return LayerTopology(
        nodes=tuple((name, i) for i, name in enumerate(order)),
        edges=tuple(edges),
    )


def _layer_topology_of(layer: str, has_screening: bool) -> LayerTopology:
    if layer == "screening":
        # Screening 为顶层普通函数节点（纯代码，无 LLM），单节点"层"
        return LayerTopology(nodes=(("Screening", 0),), edges=())
    return _extract_layer_topology(layer, has_screening)


@functools.lru_cache(maxsize=None)
def build_topology(selected_layers: Sequence[str]) -> Topology:
    """按 selected_layers 组合顶层拓扑（镜像 trading_graph._build_graph 连线）。

    - 层序：market → sector → (screening | stock)；"position" 无图影响；
    - screening 模式：stock 不进顶层链，从 Screening 引 kind="loop" 虚线边
      到 stock 首个主节点（对应 propagate 的 run_stock_loop 逐票循环）；
    - 未知 layer 值忽略（防御性兼容）。
    """
    layers = tuple(l for l in selected_layers if l in _TOP_LAYERS)
    has_screening = "screening" in layers

    # row 分配：market=0、sector=1；screening 模式下 screening=2、stock=3（逐票循环
    # 复用 stock 子图、日志落 stock/，与 selected_layers 是否含 "stock" 无关——
    # propagate 对 screening 任务无条件跑 run_stock_loop），否则 stock=2
    graph_layers = [l for l in ("market", "sector") if l in layers]
    if has_screening:
        graph_layers.append("screening")
        graph_layers.append("stock")
    elif "stock" in layers:
        graph_layers.append("stock")

    nodes: List[TopologyNode] = []
    edges: List[TopologyEdge] = []
    first_of: dict = {}
    last_of: dict = {}
    for row, layer in enumerate(graph_layers):
        topo = _layer_topology_of(layer, has_screening)
        for label, order in topo.nodes:
            nodes.append(
                TopologyNode(
                    id=f"{layer}:{label}", label=label,
                    layer=layer, row=row, order=order,
                )
            )
        first_of[layer] = f"{layer}:{topo.nodes[0][0]}"
        last_of[layer] = f"{layer}:{topo.nodes[-1][0]}"
        for src, tgt, cond, parallel in topo.edges:
            edges.append(
                TopologyEdge(
                    source=f"{layer}:{src}", target=f"{layer}:{tgt}",
                    kind="conditional" if cond else "direct",
                    parallel=parallel,
                )
            )

    # 层间边：L 最后主节点 → L+1 首个主节点
    for prev, nxt in zip(graph_layers, graph_layers[1:]):
        if prev == "screening":
            # 逐票循环虚线（可视化补充，非图定义边）
            edges.append(
                TopologyEdge(
                    source=last_of[prev], target=first_of[nxt],
                    kind="loop", parallel=False,
                )
            )
        else:
            edges.append(
                TopologyEdge(
                    source=last_of[prev], target=first_of[nxt],
                    kind="direct", parallel=False,
                )
            )

    return Topology(nodes=tuple(nodes), edges=tuple(edges))
