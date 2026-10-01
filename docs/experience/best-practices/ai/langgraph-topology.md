# LangGraph 图结构提取约定（2026-09-08 拓扑图功能踩坑）

> 一句话结论：确定性拓扑必须用 `compiled.builder`（nodes/edges/branches 保留声明序），`get_graph()` 的 edges 是 set 无序。

## get_graph() 不能用于确定性顺序提取

- **根因**：langgraph 1.2.10 的 `get_graph()` 返回 langchain_core Graph，`edges` 是 set（无序），且 draw 模拟（apply_writes）对无 reducer 的 dict state 抛并发写冲突。
- **正确姿势**：要确定性拓扑（含条件边声明序）用 `compiled.builder`：
  - `builder.nodes`（dict 声明序、不含 __start__/__end__）
  - `builder.edges`（set，直接边）
  - `builder.branches[src][router_key].ends`（dict 保留条件目标声明序，如 Risky 的 Safe 在 Risk Judge 前）
- 实现见 AI/graph/topology.py。
