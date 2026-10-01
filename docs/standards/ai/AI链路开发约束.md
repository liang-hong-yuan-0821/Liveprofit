# AI链路开发约束

> **适用**：AI图、State、提示词、checkpoint/重跑、事件历史检索和批处理。
> **规则来源**：承接原根 AGENTS 指引；正文非链接源码路径均以仓库根为基准。
> **入口**：[规范索引](../index.md)；门禁摘要见 [AGENTS.md](../../../AGENTS.md)。

## LangGraph 图结构提取约定

- **确定性拓扑必须用 `compiled.builder`**（`builder.nodes` 声明序、`builder.branches[src][router_key].ends` 保留条件目标声明序）——`get_graph()` 的 edges 是 set 无序，不能用于确定性顺序提取。实现见 AI/graph/topology.py
- 正确姿势详见 [docs/experience/best-practices/ai/langgraph-topology.md](../../../docs/experience/best-practices/ai/langgraph-topology.md)

## 事件研究严格历史检索

- **每日事件快照必须分别校验来源 `published_at`/`first_seen_at <= news_cutoff_at`，判断 `available_at <= report_as_of`，以及向量 `embedding_available_at <= report_as_of`；缺少向量可用时间时按当时无向量处理，禁止用事后回填向量改变历史召回。** 详见 [docs/experience/pitfalls/ai/strict-event-vector-as-of.md](../../../docs/experience/pitfalls/ai/strict-event-vector-as-of.md)

## 单Agent重跑与提示词编辑

- **提示词单一事实来源 = `AI/utils/prompts.py`**（`DEFAULT_PROMPTS`，键 = 拓扑节点 id）；checkpoint guard 在层构建器接线处统一注入（`AI/utils/checkpoint.py`）
- 关键不变量：平台保留 key（`_rerun_from` 等）必须在 AgentState 声明；重跑目标为环成员时入口上移到环入口（`_LOOP_ENTRY`）；resolve 目录链只接受含 complete.json 的目录
- 完整机制与坑见 [docs/experience/pitfalls/ai/prompts-checkpoint-rerun.md](../../../docs/experience/pitfalls/ai/prompts-checkpoint-rerun.md)

## 市场层证据驱动与事件路由（T6）

- **templates md 含 JSON 结论块保留单花括号**，工厂一律 `prompt.partial(output_format=...)` 注入；**纯代码节点**必须在 `AI/utils/llm_callbacks._NODE_LAYER` 登记 layer 前缀；**结构化 State 字段**（market_regime 等）消费方一律经 `format_*_summary` 渲染，`risk_gate` 枚举 fail-closed → caution
- 完整约定见 [docs/experience/best-practices/ai/market-t6.md](../../../docs/experience/best-practices/ai/market-t6.md)

## 每日批处理触发方式（2026-08-31 起）

- **方案 B（推荐）：常驻自调度**——APScheduler 挂在 eventStudy FastAPI lifespan，每天 08:30 以子进程触发 `python -m AI.eventStudy.scheduler.daily_job`；睡眠/宕机靠三层补跑（cron 触发、服务启动自检、每 15 分钟周期自检）补救
- 运行约束：uvicorn **单 worker、禁用 --reload**（否则调度器重复启动）；Windows 守护用 NSSM
- 完整机制细节（防重复标记、完成标记、方案 A schtasks）见 [docs/experience/best-practices/ai/eventstudy-scheduler.md](../../../docs/experience/best-practices/ai/eventstudy-scheduler.md)

## 结构化 State 换代

- **结构化 State 字段换代：直接替换、不搞 v1/v2 并存（2026-09-11 决策）**：存量文本字段升级为结构化 dict 时**原地改类型**（同名、同语义），派生逻辑（如 `derive_risk_gate`）原地重写；新语义字段直接用语义名，不新增 `_v2` 后缀字段、不设运行期回退开关（回滚走代码版本回退）。前提：全部消费方（含 backend 展示、JSON 落盘、测试样本）可在同一变更内同步改造；全文报告字段保留仅作展示。并存迁移（双字段+回退开关）仅在消费方不可控（跨团队接口）时采用
