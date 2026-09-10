# 单Agent重跑与提示词编辑方案

> **状态**：已完成（2026-09-10，Code Review 3 轮收敛——第 1 轮 2 major+6 minor/2 major+5 minor、第 2 轮 1 blocker+1 major+4 minor、第 3 轮 PASS 无 blocker/major，4 条 minor 已收尾；全部修复经内核 61/backend 162+27/前端 207 测试与 typecheck/build 回归验证）
> **进度**：5/5 步骤（初稿完成 → 评审循环完成 → 任务分解完成 → 实现完成 → Code Review 完成）
> **下一步**：无。遗留：T9 手工验收清单待用户执行（真实 LLM 任务观察 checkpoint/重跑/提示词编辑生效）
> **关联文档**：[任务拓扑图方案](../done/任务拓扑图方案.md)｜[任务执行调用日志方案](../done/任务执行调用日志方案.md)｜[产品需求分析](产品需求分析.md)｜[前端平台技术方案](../done/前端平台技术方案.md)

---

## 一、背景与动机

### 1.1、现状

- **任务只能整图重跑**：`backend/modules/analysis/application/task_lifecycle.py:435` 的 `fail_or_retry` 内部重试 = 整图重跑（attempt_no+1，上限 3 次，仅可重试错误）；`backend/modules/analysis/domain/state_machine.py:36-38` 中 SUCCEEDED/FAILED/CANCELLED 无任何出边。用户对单个 Agent 结果不满意（如某板块分析师口径不对）只能重建整个任务，浪费全部上游 LLM 调用。
- **提示词硬编码在 23 个 LLM Agent 工厂闭包内**：市场层 8 个（`AI/marketAgents/analysts/*.py`）、板块层 3 个（`AI/sectorAgents/analysts/*.py`）、个股层 12 个（`AI/stockAgents/{analysts,researchers,managers,trader,risk_mgmt}/*.py`）。形态分三类：ChatPromptTemplate system 串（如 `cn_news_analyst.py:43-83`）、模块级常量（`sector_rotation_analyst.py:16` `_ROTATION_SYSTEM_PROMPT`）、运行时 f-string 插值（`bull_researcher.py:49-77`、`bear_researcher.py:49`、`research_manager.py:42`、`risk_manager.py:45`、`trader.py:62-93`、`aggresive_debator.py:30` 等）。Agent 工厂不接收 config，无任何运行时改提示词入口。
- **无中间状态持久化**：LangGraph 全图无 checkpointer；仅最终 state 进 artifact store（`var/runs/...`）+ `results/{ticker}/analysis_logs/state_log.json` + 逐节点 req/res 日志（`AI/utils/llm_callbacks.py` 写 `{run_dir}/{layer}/{seq:03d}_{NodeName}/`）。节点级状态快照不存在，无法从中间节点续跑。
- **拓扑基础设施已就绪**：`AI/graph/topology.py` 的 `build_topology(selected_layers)`（lru_cached、dummy 编译、节点 id=`"{layer}:{label}"`、row/order 确定性）已被任务详情页复用（`backend/api/routers/graph_topology.py` + `frontend/src/modules/analysis/pages/task-detail/GraphTopologyPanel.tsx`）。
- **/ai 页无 tab 结构**：`AiDashboardPage.tsx` 只有看板三区块 + 新建分析 + 任务中心入口；事件研究页已有 `?tab=` URL 参数先例（`EventStudyPage.tsx`）。

### 1.2、目标

1. **单 Agent 重跑 = 从该 Agent 续跑**：终态任务可在任务详情页拓扑节点弹窗中点击"重跑此 Agent"——从所选节点起重新执行，上游节点复用上次 attempt 的 checkpoint 状态（不再调用 LLM），目标节点与全部下游重新执行，报告全量重新生成；状态机终态 → PENDING（attempt_no+1，走既有 outbox/dispatcher 链路）。
2. **AI 页顶部 tab 条**：`/ai` 页加 `任务 | Agent` tab（`?tab=` URL 模式，先例 EventStudyPage）；任务 tab = 现有看板内容 + 任务中心入口；Agent tab = 全新静态全局拓扑图（全层 market/sector/screening/stock），复用 `build_topology`，禁止手写第二份拓扑。
3. **提示词可编辑、持久化、未来任务生效**：Agent tab 拓扑节点点击 → 编辑弹窗（查看当前生效提示词 = 覆盖或默认、编辑、保存、恢复默认）。覆盖存 PG 表；新任务每次执行新建图、执行开始时快照覆盖集注入内核；运行中/排队中任务不受影响（快照语义）。
4. **任务详情页支持提示词编辑 + 重跑**：节点弹窗增加"编辑提示词"（全局覆盖，同弹窗组件）与"重跑此 Agent"按钮；旧 attempt 无 checkpoint 时按钮禁用并提示。

## 二、架构设计

```
                          ┌────────────────────────────────────────────┐
  PG                     │  agent_prompt_overrides（node_id PK → text） │
                          └───────────────┬────────────────────────────┘
                                          │ ① 执行开始时快照（worker 每次执行读库）
                                          ▼
  Worker ── claim ──► AnalysisExecutor ──► TradingGraphAdapter
     ▲                                          │ init_state.prompt_overrides
     │ rerun_from_node_id（outbox payload 透传）  ▼
     │                                    TradingAgentsGraph（每次执行新建）
     │                                          │ propagate()/rerun_from_node()
     │                                          ▼
     │                              AI/utils/prompts.py    set_overrides()（进程内注册表）
     │                              AI/utils/checkpoint.py guard+checkpoint 包装器（层构建器接线处）
     │                                          │
     │                                          ▼
     └──────────── {run_dir}/checkpoints/{layer}/{label}.json + rerun.json + complete.json
                                        ▲
  API（重跑入口）POST /analysis-tasks/{id}/rerun {node_id}
        └─ 校验终态/节点存在/entry checkpoint 存在 → 终态→PENDING(attempt+1) + Outbox(rerun_from)
```

- 内核侧两个新模块：`AI/utils/prompts.py`（默认提示词注册表 + 覆盖注册表）、`AI/utils/checkpoint.py`（状态序列化 + guard/checkpoint 节点包装器 + entry 解析）。三个层构建器 + 顶层 Screening 节点在接线处统一包一层包装器，**不改任何节点函数体、不改图拓扑**（包装对 langgraph 透明，`builder.nodes` 不变，`build_topology` 输出不变）。
- 后端新增 `agent_prompt_overrides` 表 + `analysis_tasks.rerun_from_node_id` 列 + `agents` 路由 + rerun 端点；状态机终态 → PENDING 的唯一入口为 `TaskService.rerun_task`（产品已确认的偏离，见 3.4）。
- 前端新增 Agent tab 页 + 共享 PromptEditDialog + 任务详情重跑入口；`/ai`、`/ai/tasks` 路由不变；任务 tab 与看板合并为纯前端改动（analysis_dashboard 为独立后端路由，已核实零后端改动）。

### 2.1 数据模型设计

**2.1.1 新表 `agent_prompt_overrides`（PG，migration 0004）**

| 字段 | 类型 | 写入者 | 说明 |
|---|---|---|---|
| node_id | VARCHAR(64) PK | AgentPromptService.upsert | 拓扑节点 id（`"{layer}:{label}"`），真实例：`market:CN News Analyst`（最长标签 International Event Extraction Analyst=38 字符 + 前缀 7=45，64 足够） |
| prompt_text | TEXT NOT NULL | 同上 | 覆盖提示词全文（原样生效，不做插值），真实例：`你是一位专注A股资金日历的分析师，请用中文输出，先给结论再给依据。` |
| created_at / updated_at | TIMESTAMPTZ server_default now() | DB | 行创建/更新时间 |

选单行 JSONB 的否决理由：并发 PUT/DELETE 会锁同一行；逐行 upsert/删除/列举是自然 CRUD，PK 约束防重复，写入放大仅为 1 行。

**2.1.2 `analysis_tasks` 加列（migration 0004）**

| 字段 | 类型 | 写入者 | 说明 |
|---|---|---|---|
| rerun_from_node_id | VARCHAR(64) NULL | rerun_task 写入；claim_for_execution 在非 rerun 消息（rerun_from=None，如重试）时清空 | 当前 attempt 为重跑时记录起点节点 id（如 `market:US News Analyst`）；NULL=普通执行。**该列仅作展示，不是重跑执行的触发源**（触发源为 outbox payload → actor kwarg 的消息级参数，见 3.4.1）。展示"本次由节点 X 重跑，复用第 attempt_no-1 次结果"。**entry checkpoint 查找沿 attempt 目录链从新到旧回溯**（重跑 attempt 只覆盖写目标及下游节点，上游节点最新 checkpoint 仍在其前序 attempt 目录），无需另存 base_attempt |

**2.1.3 checkpoint 及运行标记文件格式（内核写，`{run_dir}/`）**

```
{run_dir}/checkpoints/
├── __init__.json                      # propagate 入口保存的初始 state（重跑第一层首节点的 entry 兜底）
├── market/{Sanitized_Label}.json      # 每主节点完成后覆盖写（最后一次调用态）
├── sector/{Sanitized_Label}.json
├── screening/Screening.json
└── stock/{ticker_code}/{Sanitized_Label}.json   # 个股层按票分目录（code=state["company_of_interest"]）
{run_dir}/rerun.json                   # 重跑元数据：{"rerun_from": "market:US News Analyst", "base_attempt": 2}
{run_dir}/complete.json                # 完成标记：propagate/_propagate_inner 成功收尾原子写（temp+rename，
                                       #   CLAUDE.md 原子写先例）；失败/取消的 attempt 目录无此文件
```

checkpoint 文件内容（`serialize_state` 产出）：

```json
{
  "saved_at": "2026-09-09T14:30:01+08:00",
  "node_id": "market:CN News Analyst",
  "state": {
    "messages": [{"type": "human", "data": {"content": "开始交易分析…", "id": "..."}}],
    "trade_date": "2026-09-08",
    "cn_news_report": "## 〇 事件日历速览\n…",
    "investment_debate_state": {"history": "", "current_response": "", "count": 0}
  }
}
```

序列化规则：`messages` 经 `langchain_core.messages.messages_to_dict`（**必须从 `langchain_core.messages` 包顶层导入**——venv 实测 `messages.utils` 只暴露 `messages_from_dict`）；其余字段经 `json.loads(json.dumps(x, default=str))` 归一（沿用 CLAUDE.md JSONB 教训）；丢弃 `_` 前缀键（`_rerun_from`/`_current_node_id`）与 `prompt_overrides`（不污染快照）。读取用 `messages_from_dict` 还原（AIMessage.tool_calls 还原为可用形态，条件路由只做 `hasattr(tool_calls)` 判空，兼容）。

## 三、详细设计

### 3.1 内核：提示词注册中心 + 覆盖注入（AI/utils/prompts.py 新建）

#### 3.1.1 模块设计

- 职责：默认提示词单一事实来源（backend 展示读取同一份）；进程内覆盖注册表（快照由平台注入）。
- 接口签名：

```python
# AI/utils/prompts.py
DEFAULT_PROMPTS: dict[str, str]          # node_id → 默认提示词（静态指令核心，23 个 LLM 节点）
                                         # 键与 build_topology 节点 id 完全一致，不含 Screening（纯代码无提示词）

def set_overrides(overrides: dict[str, str] | None) -> None:
    """整表替换进程内注册表。每次 propagate/rerun 前由平台快照调用；CLI 不调用 → 恒默认。"""

def get_system_prompt(node_id: str | None, default: str | Callable[[], str]) -> str:
    """覆盖存在且非空 → 原样返回覆盖文本（不做任何插值/拼接）；
    否则返回 default（str 直接返回 / callable 惰性求值，惰性版避免覆盖场景下白跑数据组装）。"""
```

- **node_id 注入方式（决策）**：不为 23 个工厂函数加 `node_id` 参数——由 3.2 的统一节点包装器在执行前写 `state["_current_node_id"] = node_id`，工厂内一律 `get_system_prompt(state.get("_current_node_id"), lambda: <现有组装>)`。3.1/3.2 共享同一个包装器接线点，工厂改动只剩"包一层 getter"。
- **覆盖语义（决策，对所有 23 个节点一致）**：
  - ChatPromptTemplate 类（15 个分析师）：工厂改用 `system_message(node_id, lambda: 模板)`（AI/utils/prompts.py）——**覆盖命中 → 静态 SystemMessage**（花括号原样进 LLM；元组形式会被 langchain 当模板解析，覆盖含 `{xxx}`/JSON 示例即 KeyError，1.5.3 实测）；无覆盖 → `("system", 模板)` 元组（`partial()` 数据注入、date_line/output_format 锚点 replace 不变——覆盖分支绕开模板组装，`MessagesPlaceholder(messages)` 保留）。
  - f-string 插值类（bull/bear/research_manager/risk_manager/risky/safe/neutral）：覆盖原样返回，**无插值**（`{company_name}` 等占位符不会执行；编辑框展示的默认文本即含占位符的模板，UI 注明）。
  - trader：覆盖只替换 system 角色 content；注入投资计划的 user 消息保留（属于动态数据，非提示词）。
- **注册表写入时机（决策）**：仅 `propagate()`/`rerun_from_node()` 入口读 `init_state.pop("prompt_overrides", None)` 后 `set_overrides`。选进程内模块全局而非逐层传 dict 的理由：worker 基线 `dramatiq_threads=1` + `global_llm_concurrency=1`（settings.py），同一进程内图执行严格串行；TradingAgentsGraph 每次执行新建、propagate 只跑一次；注册表写入发生在 stream 前、执行中只读。CLI 与内核测试不含该 key → 注册表为空 → 行为不变。残余风险（未来 worker 并发化时两个图交错初始化）写入 3.1.3。
- 默认提示词迁移方式：每工厂把现有内联字符串整体搬入 `DEFAULT_PROMPTS[node_id]`，工厂组装函数改为引用该常量（保证**字节级一致**）。`sector_rotation_analyst.py:16` 的 `_ROTATION_SYSTEM_PROMPT` 模块常量直接删除改引 `DEFAULT_PROMPTS`。

#### 3.1.2 三方依赖能力评估

无新依赖。langchain_core 仅用字符串；`DEFAULT_PROMPTS` 为纯 dict，backend API 进程可安全 import（先例：graph_topology 路由 import `AI.graph.topology`）。

#### 3.1.3 风险与验证方式

| 风险 | 缓解/验证 |
|---|---|
| 覆盖提示词质量差导致下游解析失败（如 CN News 的 `_extract_event_calendar` 依赖 `## 〇 事件日历速览` 段落） | 覆盖只影响 LLM 输出风格/口径，结构化提取失败走既有兜底（截断/空串）；编辑弹窗默认值=当前默认提示词（含输出格式要求），UI 提示"建议保留结构要求"；验证：契约测试 PUT 后 worker 用覆盖跑 fake 图不抛 |
| 进程内全局注册表未来并发风险 | 文档化约束 + 单测断言 `set_overrides` 隔离；若未来 worker 并发化，改造点仅 prompts.py 换 ContextVar |
| 迁移 23 个工厂造成默认行为漂移 | 单测断言：无覆盖时 `get_system_prompt(None, default)` 返回与旧代码字节一致（抽关键节点文案断言）；`-k "not integration"` 全量回归 |

#### 3.1.4 文件变更清单

- **新建**：`AI/utils/prompts.py`（DEFAULT_PROMPTS 23 条 + set_overrides/get_system_prompt/**system_message**）；`tests/graph/test_prompts.py`（含 system_message 覆盖/默认双分支契约测试——覆盖含花括号不抛错）
- **修改**（23 个工厂 + 编排器，仅包一层 getter 与迁字符串）：
  - **A 类 15 工厂（ChatPromptTemplate）改走 `system_message`**：`AI/marketAgents/analysts/` 8 个、`AI/sectorAgents/analysts/` 3 个、`AI/stockAgents/analysts/` 4 个
  - **B 类 8 工厂（f-string 纯字符串 llm.invoke，无模板解析）保持 `get_system_prompt`**：`AI/stockAgents/researchers/`（bull、bear）、`AI/stockAgents/managers/`（research_manager、risk_manager）、`AI/stockAgents/trader/trader.py`、`AI/stockAgents/risk_mgmt/` 3 个
  - `AI/graph/trading_graph.py`（propagate 入口 set_overrides）
- **删除**：无

### 3.2 内核：checkpoint 存档 + 从节点续跑（AI/utils/checkpoint.py 新建）

#### 3.2.1 模块设计

**捕获点决策：层构建器接线处统一包装器（方案 a），不用 langgraph callbacks、不改图拓扑。** 包装器同时承担三职责（guard + 注入 node_id + checkpoint），在三个层构建器每个主节点 `add_node` 处、以及 trading_graph 的 Screening 节点处各包一层：

```python
# AI/utils/checkpoint.py
from langchain_core.messages import messages_to_dict, messages_from_dict  # 包顶层导入（venv 实测）
from AI.utils.llm_callbacks import _sanitize, _NODE_LAYER          # 节点→层映射唯一事实来源

def guard_checkpoint(node_name: str):    # node_name 如 "CN News Analyst"
    def deco(fn):
        def wrapped(state: dict, *args, **kwargs):
            node_id = f"{_NODE_LAYER[node_name]}:{node_name}"
            state["_current_node_id"] = node_id                     # 供 prompts.get_system_prompt
            if _is_upstream(state, node_id):
                return {}                                            # 快进：输出已在 entry 快照中
            result = fn(state, *args, **kwargs) or {}
            _save_checkpoint(_merge_updates(state, result), node_id)
            return result
        return wrapped
    return deco

def set_checkpoint_run_dir(run_dir: Path | None) -> None   # propagate/rerun 入口设置（模块全局，串行执行下安全）
def serialize_state(state: dict, node_id: str) -> dict
def deserialize_checkpoint(path: Path) -> dict            # 读文件 + messages_from_dict
def resolve_entry_checkpoint(run_dirs: list[Path], topology: Topology, node_id: str, ticker: str | None) -> Path | None
    # 内部先做环入口上移（_LOOP_ENTRY）得到 effective，后续一律按 effective 解析。
    # run_dirs = 基座目录链（新→旧，如 [tasks/{id}/2, tasks/{id}/1]，**仅含 complete.json 完成
    #   标记的目录**——失败/取消的部分执行目录被剔除：环中途失败目录可能留有 count<cap 的环中态
    #   checkpoint，被跳过环成员的出口路由读到该态会恒返环内回边（count/speaker 冻结）→ 无限循环）：
    # 逐目录按 (row, order) 全局序向前找最近的前驱节点 checkpoint 文件，首个命中即返回；
    # **某目录未命中 → 继续更旧目录**（不是逐目录 __init__ 兜底——重跑 attempt 只覆盖写
    #   目标+下游，上游前驱 checkpoint 在更旧目录，逐目录兜底会切断回溯链）；
    # 目录链耗尽且 effective 无前驱（全局首节点）→ 最近一次**完整执行**目录（有 complete.json
    #   且无 rerun.json）的 __init__.json（rerun attempt 的 __init__.json = merged entry 态，
    #   含旧输出与旧对话，不可作首节点 entry——须保持"目标消息通道与原运行等价"不变式）；
    # 都没有 → None
```

- `_is_upstream(state, node_id)`：`_rerun_from` 未设置 → False；否则函数级 `from AI.graph.topology import build_topology`（lru_cached；**函数级导入破循环依赖**：topology.py 顶层 import 层构建器，层构建器顶层 import checkpoint.py，checkpoint.py 不得顶层 import topology.py），比较 `(row, order)` < 目标 → True。
- **环入口上移（环成员目标专用）**：`_LOOP_ENTRY = {"Bull Researcher": "Bull Researcher", "Bear Researcher": "Bull Researcher", "Risky Analyst": "Risky Analyst", "Safe Analyst": "Risky Analyst", "Neutral Analyst": "Risky Analyst"}`（环成员 → 环入口成员）。目标为环成员时，entry 前驱解析与 guard 的 `_rerun_from` 一律用环入口成员（`effective = _LOOP_ENTRY.get(node_id, node_id)`）——**环必须整体重演**：跳过只发生在环入口之前，环内成员全部真实执行，被跳过节点不可能出现在环回边路径上（回边重入的环成员均真实执行），出口路由读真实推进的 count/speaker，不会返回 map 外目标。若不做上移，环内中段目标（如 Neutral：entry=Safe cp 且 Risky 被跳过 → Risky 出口路由读 count=2/speaker=Safe 返回 Neutral ∉ Risky 的 path map → KeyError 崩溃）必然失败。
- `_merge_updates(state, result)`：`{**state, **result}`，但 `messages` 用 `state["messages"] + result.get("messages", [])` 拼接（对齐 add_messages reducer 语义）。快照 = 节点完成后、Msg Clear 前的完整态。**messages 全量保留仅服务于快照序列化完整性**（含 RemoveMessage 可解析的历史 id）；replay 时上游各 Msg Clear 节点真实执行（`agent_utils.py:25-34` 清空消息 + "Continue" 占位），目标节点收到的消息通道与原运行等价——不得把"多带历史消息"当作 entry 语义。
- **checkpoint 目录**：见 2.1.3。个股层路径带 `state["company_of_interest"]`；screening 模式（`state["selected_layers"]` 含 "screening"）且层为 stock 时**跳过落盘**（v1 禁止 screening 任务重跑个股层节点，省每票 12 文件 × N 票的写放大）。
- **rerun 执行路径（快进 guard 方案）**：`TradingAgentsGraph.rerun_from_node(init_state, checkpoint_state, node_id, progress_callback)`：
  1. `merged = dict(checkpoint_state)`（entry 态：目标节点的实际输入），覆盖注入新 attempt 元数据（platform_log_dir/attempt_no/task_id 来自新 init_state），设 `merged["_rerun_from"] = effective_node_id`（环成员目标经 `_LOOP_ENTRY` 上移为环入口，跳过只发生在环入口之前）；
  2. `prompts.set_overrides(init_state.pop("prompt_overrides", None) or {})`、`checkpoint.set_checkpoint_run_dir(新 attempt dir)`、写 `{新dir}/rerun.json`；
  3. `progress_callback(f"从节点 {node_id} 续跑：上游复用上次结果")`；
  4. 走与 `propagate` 相同的 `_propagate_inner(merged, progress_callback)`（**propagate 主体重构提取为 `_propagate_inner`**，两入口共用：日期校正防御、stream 循环、`_log_state`、`_write_reports`、screening 模式 stock_loop + position manager、决策提取；**成功收尾原子写 `{run_dir}/complete.json` 完成标记**——propagate 与 rerun 均经此路径，失败/取消的 attempt 目录无标记，被 resolve 目录链剔除，见 resolve 注释）。
- **循环语义验证（实测核对 conditional_logic + 环 path map）**：辩论/风险路由器只读 `investment_debate_state`/`risk_debate_state`（count/current_response/latest_speaker）与分析师报告/tool_call_count 字段——全部存在于 checkpoint 态；环成员 path map 只含邻接下家（`stock_layer_graph.py:165-196`，Bull map={Bear,RM}、Risky map={Safe,Judge} 等）。**环入口上移后**：跳过只发生在环入口之前，环成员全部真实执行、count/speaker 真实推进，回边重入的必是真实执行过的成员（Bull/Bear 回边、Risky 环回边均在环内），出口路由不会返回 map 外目标。以目标=Bear 重跑为例（entry=Bull 前驱 Fundamentals 快照，debate count=0）：Bull 真实执行 count=1 → Bear count=2 → 回边 Bull（真实执行）→ 按 cap 收口 RM，辩论整体重演。上游 no-op 节点的条件路由器读到的是 checkpoint 态（报告已满 100 字 → Msg Clear 直行，不进 tools 循环）；即使未来工具真正绑定（**现状实测：AI/ 全库无任何 bind_tools 调用，新闻类"工具循环"实际首轮即直行**），路由最多多走一轮 tools 往返后因最后消息为 tool message（无 tool_calls）收口，无死循环。
- **决策：rerun entry = 拓扑序上前驱节点的最新 checkpoint（沿 attempt 目录链回溯）；环成员目标上移为环入口的前驱**。线性链等价"前驱完成后状态"；环成员目标 = 该环从入口整体重演（辩论环 Bull 起、风险环 Risky 起，环出口 RM/Risk Judge 及下游照常重算）。第一层首节点 entry = 最近一次完整执行目录的 `__init__.json`（见 resolve 注释的定案规则）。
- **v1 限制**：screening 任务的个股层节点（row=3）禁止重跑（后端 409 + UI 禁用）；market/sector/screening 节点重跑时 stock 逐票循环整体重跑（语义=目标及全部下游重执行）。逐票续跑（从含目标节点的票开始、前票 stock_results 复用 per-ticker checkpoint）已在 checkpoint 布局预留（stock/{code}/），留作后续扩展。

#### 3.2.2 三方依赖能力评估

langchain_core（venv 实测）：`messages_to_dict`/`messages_from_dict` 在 `langchain_core.messages` **包顶层**可用（`messages.utils` 仅暴露 `messages_from_dict`，导入时注意）；无需 langgraph checkpointer、无新依赖。

#### 3.2.3 风险与验证方式

| 风险 | 缓解/验证 |
|---|---|
| guard 与条件路由交互（快进节点仍会被路由） | 3.2.1 循环语义已逐条件核对；单测覆盖工具循环内/外收敛 + **环入口上移后环整体重演**（含 Neutral/Bear 目标在此前设计下 KeyError 崩溃的回归用例，见 3.7） |
| 快照序列化失败（LangChain 对象） | `serialize_state` 只产出纯 JSON（messages_to_dict + default=str 归一）；单测含 AIMessage（带 tool_calls）往返。读取侧：**命中文件 JSON 解析失败 → FatalAnalysisError(RERUN_CHECKPOINT_MISSING)（明确报错，不静默退更旧目录，避免与 API 校验结论分歧）**；坏 JSON 与 complete.json 共存于正常路径结构不可达（标记为成功收尾最后原子写 + 单写者串行） |
| 旧 attempt 无 checkpoint → 重跑不可用 | `resolve_entry_checkpoint` 返回 None → 后端 409 RERUN_NOT_AVAILABLE；UI 禁用并提示"该节点无检查点（旧版本运行）" |
| 部分执行目录（环中途失败/取消）留有 count<cap 的环中态 checkpoint，被跳过环成员出口路由读该态恒返环内回边 → 无限循环 | 完成标记机制：resolve 目录链只接受含 complete.json 的目录（成功收尾原子写），部分执行目录被剔除后命中更旧完整目录；3.7 entry 测试含该回归用例 |
| 快进后上游节点在重跑 attempt 日志目录无痕（拓扑显示未执行） | v1 接受：状态卡显示 rerun_from_node_id 说明；`rerun.json` 提供机器可读证据。节点标记"复用"是后续可选增强 |
| 重跑失败（目标节点 LLM 失败） | 走既有 fail_or_retry（整图重试语义：重试 outbox payload 无 rerun_from → 消息级触发源消失 → 全图重跑，且 claim 时清列 rerun_from_node_id，展示不误导）；重跑成功态继续支持链式重跑（新 attempt 有自己的 checkpoints） |

#### 3.2.4 文件变更清单

- **新建**：`AI/utils/checkpoint.py`；`tests/graph/test_checkpoint.py`、`tests/graph/test_rerun_guard.py`、`tests/graph/test_rerun_entry.py`
- **修改**：
  - `AI/marketAgents/market_layer_graph.py`（8 处 add_node 包 `guard_checkpoint`）、`AI/sectorAgents/sector_layer_graph.py`（3 处）、`AI/stockAgents/stock_layer_graph.py`（12 处，含 Bull/Bear/RM/Trader/Risky/Safe/Neutral/Risk Judge）
  - `AI/graph/trading_graph.py`：Screening 节点包 guard_checkpoint；propagate 入口 `set_checkpoint_run_dir(log_dir)` + 保存 `__init__.json`；新增 `rerun_from_node`；`propagate` 主体重构为 `_propagate_inner`
- **删除**：无

### 3.3 后端：提示词覆盖存储 + agents 端点

#### 3.3.1 模块设计

**迁移** `backend/migrations/versions/0004_agent_prompt_overrides.py`：建表 agent_prompt_overrides + `analysis_tasks` 加列 rerun_from_node_id（alembic 用 `sa.text("now()")` server_default 时间戳，raw SQL 必须 `text()`，沿用 0001 模式）。

**模型/仓库/服务**（对齐既有分层）：
- `backend/modules/analysis/infrastructure/models.py`：新增 `AgentPromptOverride(Base, TimestampMixin)`；`AnalysisTask` 加 `rerun_from_node_id: Mapped[str | None]`
- `backend/modules/analysis/infrastructure/repositories.py`：新增 `SqlAlchemyPromptOverrideRepository`（`get_all()/get(node_id)/upsert(node_id, prompt_text, now)/delete(node_id)/list_as_map()`）；`SqlAlchemyAnalysisUnitOfWork` 增加 `prompts` 属性
- `backend/modules/analysis/application/agent_prompts.py`（新建）：

```python
class AgentPromptService:
    def __init__(self, uow, *, clock=None): ...
    def list_prompts(self) -> list[AgentPromptDTO]: ...        # DEFAULT_PROMPTS 全量 + 覆盖叠加
    def upsert_prompt(self, node_id: str, prompt_text: str) -> AgentPromptDTO: ...
    def reset_prompt(self, node_id: str) -> AgentPromptDTO: ...  # 幂等
```

校验（**判定顺序固定，判定依据 = 拓扑节点集合而非 DEFAULT_PROMPTS**）：① `node_id` 在 `build_topology(("market","sector","screening","stock"))` 节点集合内但不在 DEFAULT_PROMPTS（即纯代码节点 `screening:Screening`）→ 422 `AGENT_PROMPT_NOT_EDITABLE`；② 不在拓扑节点集合内 → 404 `AGENT_NODE_NOT_FOUND`；③ `prompt_text.strip()` 空或 >20000 字符 → 422 VALIDATION_ERROR。PUT 路由与服务校验同步此顺序（否则 Screening 会被 404 先截走，422 成死路径）。**可编辑节点 = 拓扑主节点 ∩ DEFAULT_PROMPTS = 19 个**（market 4 + sector 3 + stock 12；market 的 US/KR 4 分析师被折叠进 International News 工具循环、不在拓扑，v1 不可编辑——DEFAULT_PROMPTS 仍含其条目，覆盖机制对全部 23 个节点生效，拓扑展开后自动可见）。`list_prompts` 的 label/layer 从该拓扑取。

**错误码**（`errors.py` 新增 + `exception_handlers.py _CODE_MAP`）：`AGENT_NODE_NOT_FOUND`(404)、`AGENT_PROMPT_NOT_EDITABLE`(422)、`RERUN_NOT_AVAILABLE`(409)。

**路由** `backend/api/routers/agents.py`（新建，tags=["agents"]，生成 AgentsService）：

| 端点 | 请求/响应 |
|---|---|
| GET `/api/v1/agents/topology` | `Envelope[{nodes:[{id,label,layer,row,order,has_prompt,has_override}], edges:[TopologyEdgeDTO], generated_at}]`；静态全图 = `build_topology(("market","sector","screening","stock"))`（screening 分支 + 逐票循环虚线，一次展示全形态）；has_override 由服务一次查出覆盖集合标注 |
| GET `/api/v1/agents/prompts` | `Envelope[{items:[{node_id,label,layer,default_prompt,override_prompt:null\|str,has_override,updated_at}]}]` |
| PUT `/api/v1/agents/prompts/{node_id}` body `{prompt_text: "你是一位…"}` | `Envelope[AgentPromptDTO]`（upsert 后完整 DTO） |
| DELETE `/api/v1/agents/prompts/{node_id}` | `Envelope[AgentPromptDTO]`（has_override=false，恢复默认） |

注：node_id 含空格/冒号，前端必须 `encodeURIComponent`（FastAPI path param 已解码）。

**worker 注入**（快照点 = 执行开始）：
- `backend/modules/analysis/infrastructure/real_graph_factory.py::build_real_initial_state(task, execution_logs_root=None, prompt_overrides=None)` 增加参数，注入 `init_state["prompt_overrides"] = prompt_overrides or {}`（内核 propagate 入口 pop + set_overrides）。
- `backend/workers/wiring.py::get_worker_executor`：initial_state_factory lambda（现码签名 `Callable[[ClaimedTask], dict]`，由 adapter.execute 内部调用）内：先经 `_worker_container.session_factory` 读 `list_as_map()`，再调 `build_real_initial_state(task, execution_logs_root=..., prompt_overrides=overrides, rerun_from_node_id=claimed.rerun_from_node_id)`。每任务执行时读取 → 运行中/排队任务不受后续编辑影响。**rerun 判定单源**：ClaimedTask.rerun_from_node_id 与 executor 收到的 rerun_from kwarg 同源（均取自消息，claim 时写入/清列），adapter.execute 只按 rerun_from 参数分派 propagate/rerun_from_node，factory 不自行读任务行列作第二判定源。

**DTO/契约变更**：`backend/api/schemas/tasks.py::TaskDTO` 加 `rerun_from_node_id: str | None`（contracts.TaskDTO、`_to_task_dto` 同步）；`backend/api/schemas/graph_topology.py::TopologyNodeDTO` 加 `rerun_available: bool = False`（**生产者 = 任务拓扑端点**，计算规则见 3.4.1「任务拓扑 rerun_available」）；新增 `backend/api/schemas/agents.py`。

#### 3.3.2 三方依赖能力评估

无新依赖（SQLAlchemy/Alembic/FastAPI 既有）。路由层 import `AI.graph.topology.build_topology` 与 `AI.utils.prompts.DEFAULT_PROMPTS`（延迟导入，先例 graph_topology.py）。

#### 3.3.3 风险与验证方式

| 风险 | 缓解/验证 |
|---|---|
| 覆盖文本入库后被 worker 读到非法内容 | 长度/非空校验 + 内核 get_system_prompt 原样透传（无模板注入）；契约测试 PUT→GET 往返 |
| DEFAULT_PROMPTS 与拓扑漂移（改节点名/加节点） | 内核 test_prompts 断言键集合 == 拓扑 LLM 节点集合；后端 list_prompts 以 DEFAULT_PROMPTS 为准 |
| upsert 并发 | PK + 短事务 get+add/merge，契约测试并发双 PUT 不炸 |

#### 3.3.4 文件变更清单

- **新建**：`backend/migrations/versions/0004_agent_prompt_overrides.py`；`backend/modules/analysis/application/agent_prompts.py`；`backend/api/routers/agents.py`；`backend/api/schemas/agents.py`；`backend/tests/unit/analysis/test_agent_prompts.py`；`backend/tests/contract/api/test_agents_api.py`
- **修改**：models.py、repositories.py、errors.py、exception_handlers.py、contracts.py（TaskDTO）、real_graph_factory.py、wiring.py、schemas/tasks.py、schemas/graph_topology.py、backend/main.py（注册 agents 路由）、backend/tests/unit/analysis/fakes.py（FakeUoW 加 prompts 假仓库）
- **删除**：无

### 3.4 后端：任务重跑（rerun 端点 + 状态机 + worker 链路）

#### 3.4.1 模块设计

**状态机偏离（产品已确认，文档化）**：`state_machine.py::ALLOWED_TRANSITIONS` 为 SUCCEEDED/FAILED/CANCELLED 增加出边 `{TaskStatus.PENDING}`，注释标明"仅 rerun_task 唯一入口（单Agent重跑方案）；generic 路径仍以 is_terminal 收口"。

**服务方法** `TaskService.rerun_task(task_id, node_id) -> TaskDTO`（task_lifecycle.py）：
1. get task（404）；`status not in {SUCCEEDED, FAILED, CANCELLED}` → 409 TASK_NOT_TERMINAL；
2. `conditional_update(expect={"status": 终态集合, "attempt_no": base}, changes={"status": PENDING, "attempt_no": base+1, "rerun_from_node_id": node_id, "error_code": None, "error_summary": None, "next_retry_at": None, "started_at": None, "finished_at": None, "lease_token": None, "lease_expires_at": None, "heartbeat_at": None, "cancel_requested_at": None, "updated_at": now})`——失败 → 409 TASK_STATE_CONFLICT（并发防护）；
3. 同事务插入 `TaskOutbox(attempt_no=base+1, payload={"task_id", "attempt_no": base+1, "rerun_from": node_id, "base_attempt": base})`；
4. commit；不发布 SSE（QUEUED 由 dispatcher confirm 发布，与 create_task 一致）；返回新 TaskDTO。

**路由**（analysis_tasks.py）：`POST /api/v1/analysis-tasks/{task_id}/rerun` body `{node_id}` → 200 `Envelope[TaskDTO]`。路由层前置（FS/拓扑校验，服务保持 DB 纯净）：
- `topology = build_topology(tuple(task.selected_layers))`；node_id 不在 → 404 AGENT_NODE_NOT_FOUND；
- screening 任务且 node.layer=="stock" → 409 RERUN_NOT_AVAILABLE（message："全市场逐票循环暂不支持从个股层节点重跑"）；
- `resolve_entry_checkpoint(run_dirs, topology, node_id, task.ticker)` 为 None → 409 RERUN_NOT_AVAILABLE（attempt 链上均无 checkpoint）；`run_dirs = [resolve_execution_logs_root(...)/tasks/{task_id}/{n} for n in range(attempt_no, 0, -1) 且目录存在且含 complete.json]`（与 graph_topology 路由同解析函数；重跑 attempt 目录只有目标+下游 checkpoint——环成员目标另含环整体重演成员 cp——上游经回溯命中更早 attempt 目录；失败/取消的部分执行目录被剔除，防环中态 entry 致无限循环，见 3.2.1 resolve 注释）。

**worker 链路**（**rerun 触发源 = 消息级参数，DB 列仅作展示，见 2.1.2**）：
- `task_lifecycle.py::OutboxDispatcherService.dispatch_due`（def :767；现码 :779 手工组装 `{task_id, attempt_no}`）改为透传完整 `record.payload`（含 `rerun_from`）；`backend/modules/analysis/infrastructure/dramatiq_task_message_publisher.py::publish` 改为 `analysis_task_actor.send(payload["task_id"], payload["attempt_no"], rerun_from=payload.get("rerun_from"))`；
- `analysis_actor.py`：`analysis_task_actor(task_id, attempt_no, rerun_from=None)` → `run_analysis_task(..., rerun_from=...)` → `executor.execute(claimed, rerun_from=rerun_from)`；`claim_for_execution(task_id, attempt_no, rerun_from=None)`：ClaimedTask 增加 `rerun_from_node_id`（contracts.py，**取自消息 kwarg 而非任务行**）；claim 更新行时 rerun_from 非 None → 写列 `rerun_from_node_id=rerun_from`，None → 清列（重试 attempt 全图重跑，展示归零）；
- `analysis_executor.py::execute/_run_graph` 透传 rerun_from 给 adapter；
- `trading_graph_adapter.py`：`GraphPort` 协议加 `rerun_from_node(init_state, checkpoint_state, node_id, progress_callback)`；`execute(task, on_progress, rerun_from=None)`：**判定依据 = rerun_from 参数（不读 init_state/task 列）**——非 None → `graph.rerun_from_node(init_state, init_state.pop("checkpoint_state"), rerun_from, ...)`，否则 propagate；
- `build_real_initial_state(task, execution_logs_root=None, prompt_overrides=None, rerun_from_node_id=None)`：`rerun_from_node_id` 非 None 时构造 attempt 目录链（**claim 时行 attempt_no 已递增为 base+1，故链起点 = `attempt_no-1`（=base）至 1**，存在的目录**且含 complete.json**，与路由侧 3.4.1 同规则——**建议抽共享 chain-builder 单点构造覆盖全部三处消费点**（路由前置校验、本处 worker factory、rerun_available 计算），杜绝口径分叉：任一处置漏过滤即复活环中态无限循环）、经内核 `resolve_entry_checkpoint` 定位 + `deserialize_checkpoint` 读入，注入 `init_state["checkpoint_state"]`；**缺失或 JSON 解析失败 → `FatalAnalysisError("RERUN_CHECKPOINT_MISSING", code="RERUN_CHECKPOINT_MISSING")`**（现码构造签名 code 为 kwarg、默认 ANALYSIS_INTERNAL——须显式传 code 防止落默认错误码；明确报错而非静默退更旧目录，避免与 API 校验结论分歧——API 已校验 entry 存在，worker 侧失败即异常场景）。

**任务拓扑 rerun_available 计算**（3.3.1 字段的生产者，`backend/modules/analysis/application/graph_topology.py::scan_run_status` 扩展 + graph_topology 路由装配）：
- 非终态任务 → 全部 false；终态任务 → 逐节点沿 attempt 目录链（仅含 complete.json 标记目录，与重跑前置校验同规则）调 `resolve_entry_checkpoint(run_dirs, topology, node_id, task.ticker)`，命中 → true，耗尽 → false；
- screening 任务 stock 层节点（row=3）恒 false（与重跑前置校验同规则）；
- 每节点仅文件存在性探测（无内容解析），~19 主节点 × 链长 ≤3 无性能问题。

**报告/产物语义（沿用现状，零改动）**：`complete_task` → `ReportService.save_version` 新行（attempt_no=新，report_version 自增，唯一约束 task_id+report_version）；GET report 取 latest = 重跑新报告；artifact 按新 attempt 目录持久化；旧 attempt 报告保留为历史。graph-topology GET 读当前 attempt 目录（重跑后为新目录，上游节点显示"未执行"，配合 rerun_from_node_id 展示）。

**取消交互**：重跑 attempt RUNNING 期 cancel 走既有 lease/协作取消链路；恢复作业对 RUNNING 重跑 attempt 与普通 attempt 同语义。

#### 3.4.2 三方依赖能力评估

无新依赖。Dramatiq actor 增加可选 kwarg 属框架既有能力（pyproject 下限 >=1.17，venv 实测 2.2.1；actor.send 传 kwargs 两版均支持）。

#### 3.4.3 风险与验证方式

| 风险 | 缓解/验证 |
|---|---|
| 终态→PENDING 被其他路径误用 | 唯一入口 rerun_task；state_machine 表注释 + 单测断言其他服务方法不产生该迁移 |
| API 校验与执行之间 checkpoint 被清理 | 删除任务才有目录清理且仅终态；重跑请求与删除并发由状态机条件更新收口（TASK_STATE_CONFLICT） |
| 重跑消息重复投递 | Outbox (task_id, attempt_no) 唯一 + claim 条件领取幂等（既有机制） |

#### 3.4.4 文件变更清单

- **新建**：`backend/tests/unit/analysis/test_rerun_service.py`
- **修改**：state_machine.py、task_lifecycle.py（rerun_task + `OutboxDispatcherService.dispatch_due` payload 透传）、analysis_tasks.py（rerun 路由 + _to_task_dto）、backend/modules/analysis/infrastructure/dramatiq_task_message_publisher.py（actor.send 加 rerun_from kwarg）、analysis_actor.py、analysis_executor.py、trading_graph_adapter.py、real_graph_factory.py、wiring.py、contracts.py（ClaimedTask.rerun_from_node_id）、schemas（tasks/graph_topology）、backend/modules/analysis/application/graph_topology.py + backend/api/routers/graph_topology.py（rerun_available 计算/装配）、backend/tests/contract/api/test_analysis_tasks.py（扩展）、backend/tests/contract/api/test_graph_topology_api.py（扩展 rerun_available 断言）、backend/tests/unit/analysis/test_task_service.py、test_real_graph_factory.py、backend/tests/unit/analysis/test_graph_topology.py（扩展）
- **删除**：无

### 3.5 前端：/ai tab 条 + Agent 拓扑页 + 提示词编辑弹窗

#### 3.5.1 模块设计

**tab 条**（`AiDashboardPage.tsx`，纯前端改动，dashboard 后端零改动——已验证 `analysis_dashboard.py` 为独立路由，看板内容仍由既有三区块 Query 提供）：复制 EventStudyPage 的 `?tab=` 惯用法（`tab === 'agents' ? <AgentTopology /> : 现有内容`），任务 tab 默认（无参），切 tab 保留 `?create=1`。

**Agent 拓扑页**（新建 `src/modules/analysis/pages/agents/AgentTopologyPage.tsx` + `queries.ts`）：
- `useAgentsTopologyQuery`（GET agents/topology）、`useAgentsPromptsQuery`（GET agents/prompts）、`useUpdateAgentPromptMutation(nodeId)`、`useResetAgentPromptMutation(nodeId)`；queryKeys 新增 `queryKeys.agentsTopology`、`queryKeys.agentPrompts`（另加 `agentPrompts.detail(nodeId)`）。
- 渲染：抽取共享 echarts option 构造器 `src/modules/analysis/components/topologyChartOption.ts`（`buildTopologyChartOption(nodes, edges, {nodeColor, nodeLabelFormatter, tooltipFormatter})`），`GraphTopologyPanel.tsx` 改为消费它（布局常量 X_STEP=170/Y_STEP=120/X_ORIGIN=70/Y_ORIGIN=30、graphic 层标签、虚线/曲线逻辑原样迁移，测试同步微调）。Agent 页节点色：has_override → `#f59e0b`（琥珀），has_prompt → `#38bdf8`，screening → `#475569`；标签加 ` · 已自定义` 后缀；图例说明。
- 点击节点（has_prompt=false 的 Screening 不响应）→ 打开 `PromptEditDialog`。

**PromptEditDialog**（新建 `src/modules/analysis/components/PromptEditDialog.tsx`，Agent 页与任务详情共用）：
- Props：`{ node: {node_id,label,layer} | null, onClose }`；打开时订阅 `useAgentsPromptsQuery`（全局列表缓存，`enabled` 门控防 refetchOnMount 二次拉取，沿用 NodeLogsDialog 教训）。
- 内容：当前生效提示词 = `override_prompt ?? default_prompt` 填入 textarea（**纯受控 textarea + 字符计数 + 长度校验，不引入 react-hook-form**——单字段长文本无结构化校验需求）；MarkdownView 预览切换（`frontend/src/shared/ui/markdown.tsx`，所有提示词 markdown 统一走它）；按钮：保存（PUT，成功后 invalidate agentsTopology + agentPrompts）、恢复默认（仅 has_override 显示，DELETE）、取消。默认提示词含 `{占位符}` 时展示提示"默认提示词中的 {xxx} 为运行时注入变量"。

#### 3.5.2 三方依赖能力评估

无新依赖（echarts-for-react、Radix Dialog、react-query、MarkdownView 全部既有）。openapi 重导出链条：`python -m backend.scripts.export_openapi` → `pnpm run generate:api`（生成 AgentsService + AnalysisTasksService.rerun 方法 + 枚举 namespace 按值导入）。

#### 3.5.3 风险与验证方式

| 风险 | 缓解/验证 |
|---|---|
| 抽取 option 构造器破坏拓扑面板 | 迁移后 GraphTopologyPanel.test.tsx 原断言全绿（option 结构断言保持） |
| node_id 含空格/冒号路径编码 | 请求前 `encodeURIComponent`；契约测试覆盖中文/空格 node_id 往返 |

#### 3.5.4 文件变更清单

- **新建**：`src/modules/analysis/pages/agents/AgentTopologyPage.tsx`、`queries.ts`、`AgentTopologyPage.test.tsx`；`src/modules/analysis/components/PromptEditDialog.tsx`、`PromptEditDialog.test.tsx`；`src/modules/analysis/components/topologyChartOption.ts`（+`topologyChartOption.test.ts`）
- **修改**：AiDashboardPage.tsx（+tab 条，测试同步）、GraphTopologyPanel.tsx（改用共享构造器）、queryKeys.ts、src/api/generated/**（重导出产物）
- **删除**：无

### 3.6 前端：任务详情重跑 + 提示词编辑

#### 3.6.1 模块设计

- `task-detail/queries.ts` 新增 `useRerunTaskMutation(taskId)`：POST rerun；`onSuccess(dto)`：`setQueryData(queryKeys.analysisTask.detail(taskId), dto)` —— **轮询恢复机制（已核实可行）**：`useTaskQuery` 的 `refetchInterval` 回调读 `query.state.data?.status`（queries.ts:21-24），setQueryData 后 status 变 PENDING → 5s 轮询自动恢复；`eventsEnabled`（AiTaskDetailPage.tsx）随 terminal 翻 false → useTaskEvents 重新建流；同时 invalidate graphTopology/executionLogs（新 attempt 目录）+ analysisTasks.all + analysisDashboard.all。
- `GraphTopologyPanel.tsx`：`NodeLogsDialog` 增加 footer 两个按钮（props 传入回调）：**编辑提示词**（打开共享 PromptEditDialog，编辑的是全局覆盖，对本次任务不生效，弹窗注明"对新建任务生效"）与**重跑此Agent**（ConfirmDialog 确认："将重新执行该节点及全部下游分析并重新生成报告"）。重跑按钮可见条件：`task.status` 终态 && `node.rerun_available`（TopologyNodeDTO 新字段）；不可用时禁用 + hint（"该节点无检查点，无法重跑（旧版本运行）" / screening 个股层节点提示逐票循环限制）。非终态不显示重跑按钮。
- 重跑成功后：状态卡展示 rerun_from_node_id（`TaskStatusCard` 或详情 header 加一行"本次从 {node_id} 重跑（基于第 N-1 次 attempt）"）；SUCCEEDED 后 report 区随 enabled 翻转自动拉取新报告（reportQuery enabled=status==='SUCCEEDED'，翻转触发 fetch）。

#### 3.6.2 三方依赖能力评估

无新依赖。

#### 3.6.3 风险与验证方式

| 风险 | 缓解/验证 |
|---|---|
| 终态→非终态翻转后页面仍冻结 | vitest：setQueryData 后断言 refetchInterval 重启（fake timers）+ eventsEnabled 翻转（AiTaskDetailPage.test.tsx 扩展用例） |
| 重跑竞态（连点） | ConfirmDialog pending 态禁用按钮（沿用删除确认模式） |

#### 3.6.4 文件变更清单

- **修改**：task-detail/queries.ts（+useRerunTaskMutation）、NodeLogsDialog.tsx（+footer 按钮 props）、GraphTopologyPanel.tsx（接线 + rerun_available gating）、AiTaskDetailPage.tsx（rerun 确认流 + 状态卡提示）、TaskStatusCard.tsx（可选展示 rerun_from）、对应 .test.tsx（GraphTopologyPanel.test.tsx、AiTaskDetailPage.test.tsx）
- **新建**：无（PromptEditDialog 见 3.5）
- **删除**：无

### 3.7 测试与验证

| 层 | 用例 | 落点/命令 |
|----|------|-----------|
| 内核 | prompts：拓扑 LLM 主节点 ⊆ DEFAULT_PROMPTS 键且共 23 条（含 US/KR 4 折叠节点条目）；覆盖解析（存在→原样返回、空→default、None→default）；set_overrides 隔离；**system_message 双分支契约（覆盖→静态 SystemMessage 花括号原样 + from_messages 不抛错；默认→元组模板 partial 可填充）** | `tests/graph/test_prompts.py`（新建，3.1.4） |
| 内核 | checkpoint：serialize/deserialize 往返（含 AIMessage 带 tool_calls）；_merge_updates messages 拼接语义；checkpoint 文件落盘位置（layer/ticker 子目录）；screening 模式 stock 层跳过落盘；**complete.json 成功收尾原子写入、执行中途抛错不写** | `tests/graph/test_checkpoint.py`（新建，3.2.4） |
| 内核 | guard：快进命中（(row,order) 小于目标 → 返回 {}）；未设 _rerun_from 全执行；工具循环内外收敛；**环入口上移：_LOOP_ENTRY 映射断言（Bull/Bear→Bull、Risky/Safe/Neutral→Risky）；目标=Neutral（默认 risk cap）与目标=Bear（debate cap≥4）重跑不崩、环整体重演（回边重入成员真实执行、出口路由不返 map 外目标）** | `tests/graph/test_rerun_guard.py`（新建，3.2.4） |
| 内核 | entry 解析：线性链前驱 checkpoint；**多目录链回溯（新→旧：新目录只有下游 checkpoint 时上游前驱命中旧目录、逐目录不 __init__ 兜底、耗尽返回 None）；链中含环中途失败目录（无 complete.json，留 count<cap 环中态 cp）→ 跳过并命中更旧完整目录；坏 JSON checkpoint 文件 → FatalAnalysisError（截断文件构造）；首节点 → 最近完整执行目录（complete.json 且无 rerun.json）的 __init__.json（重跑目录的 __init__ 不被误用）**；无 checkpoint → None；rerun_from_node 走 _propagate_inner（fake LLM 下报告/rerun.json/complete.json 落盘） | `tests/graph/test_rerun_entry.py`（新建，3.2.4） |
| 内核 | 回归：test_graph_topology.py / test_layer_topology.py 不变全绿；`grep -rl "real_llm\|real_toolkit" tests/` 名单外全量 `-k "not integration"` | 既有测试 |
| backend | agent_prompts 服务：list 默认+覆盖叠加；upsert/reset；校验（未知节点 404 域错误、Screening 422、空/超长 422） | `backend/tests/unit/analysis/test_agent_prompts.py`（新建，3.3.4） |
| backend | agents API：GET topology（节点/边/ has_override 标注）；PUT→GET 往返；DELETE 恢复默认；node_id 含空格/中文 encode 往返；**任务拓扑 rerun_available：非终态全 false、终态 has_entry→true、旧 attempt 无 checkpoint→false、screening 任务 stock 行恒 false** | `backend/tests/contract/api/test_agents_api.py`（新建，3.3.4）；`backend/tests/contract/api/test_graph_topology_api.py`（既有，扩展） |
| backend | rerun：终态→PENDING+attempt+1+outbox payload 含 rerun_from；非终态 409 TASK_NOT_TERMINAL；未知节点 404；无 checkpoint 409 RERUN_NOT_AVAILABLE；并发冲突 409 TASK_STATE_CONFLICT；fake graph 下 executor 走 rerun_from_node 分支；build_real_initial_state 注入 checkpoint_state/prompt_overrides；**重试 attempt（payload 无 rerun_from）走 propagate 全图且 claim 清列 rerun_from_node_id；重跑 attempt 再重跑上游节点时 entry 沿目录链回溯命中基座目录；失败/取消 attempt 目录（无 complete.json）不参与 resolve（环中态剔除）** | `backend/tests/unit/analysis/test_rerun_service.py`、test_task_service.py、test_real_graph_factory.py 扩展；`backend/tests/contract/api/test_analysis_tasks.py` 扩展 |
| backend | openapi 导出一致性 | `python -m backend.scripts.export_openapi` + contract gate |
| 前端 | AgentTopologyPage：mock AgentsService；节点渲染/has_override 着色/「已自定义」后缀/点击→弹窗/Screening 不响应 | `AgentTopologyPage.test.tsx`（新建，3.5.4） |
| 前端 | PromptEditDialog：默认/覆盖预填；保存 PUT 成功后 invalidate；恢复默认仅 has_override 显示；字符计数校验 | `PromptEditDialog.test.tsx`（新建，3.5.4） |
| 前端 | topologyChartOption 抽取后 GraphTopologyPanel 原断言全绿；AiDashboardPage tab 切换（?tab=agents 渲染 Agent 页） | `topologyChartOption.test.ts`、既有 GraphTopologyPanel.test.tsx / AiDashboardPage.test.tsx 扩展 |
| 前端 | 任务详情：终态翻转恢复轮询（setQueryData 后 refetchInterval 重启 + eventsEnabled 翻转）；重跑按钮可见性 gating（终态 && rerun_available）；ConfirmDialog pending 禁用 | `AiTaskDetailPage.test.tsx`、`GraphTopologyPanel.test.tsx` 扩展 |
| 手工 | 真实小任务：跑完 → 节点重跑（上游无新 LLM 目录、rerun.json、报告版本+1、拓扑新 attempt）；编辑提示词 → 新建任务生效；旧任务禁用态；全市场任务个股层禁用 | 用户验收 |

## 四、已确认决策 / 待确认问题

- **已确认决策**（2026-09-09，用户拍板）：
  1. 单Agent重跑 = 从该节点续跑：上游复用 checkpoint、目标+下游重执行、报告重生成、终态可重开（attempt_no+1，终态 → PENDING 走既有 outbox/dispatcher 链路）；
  2. /ai 页顶部 tab 条（任务 | Agent），`?tab=` URL 模式；Agent tab 复用 `AI/graph/topology.build_topology`（禁止手写第二份拓扑）；
  3. Agent tab 拓扑 = 静态架构图（不叠加运行状态），has_override 节点标记「已自定义」；
  4. 提示词覆盖持久化 PG，执行开始快照注入，运行中/排队任务不受影响；覆盖 = 完整系统提示词原样生效（无插值、无 output_format 追加）。
- **方案内决策**（如有异议请指出）：注册表为进程内模块全局（worker 串行基线）；checkpoint 捕获 = 层构建器接线处统一包装器（guard + checkpoint 二合一，不改图拓扑、不加 langgraph checkpointer）；重跑执行 = 快进 guard（上游节点返回 {}，条件路由在 checkpoint 态上收敛）；**环成员目标 = 入口上移环整体重演**（Bull/Bear→Bull 起、Risky/Safe/Neutral→Risky 起，跳过不发生在环回边路径上）；entry = 拓扑序前驱最新 checkpoint（沿 attempt 目录链回溯，**链只含 complete.json 完成标记目录——部分执行目录剔除防环中态无限循环**，首节点取最近完整执行目录 __init__.json）；rerun 触发源 = 消息级参数（DB 列仅展示，claim 时非 rerun 消息清列）；v1 screening 任务个股层节点不可重跑；重跑报告 = 新版本行（旧版本保留）；旧 attempt 无 checkpoint → 409/禁用。
