# Python 工作区约定

## 工作流程规则

### docs/ 目录结构

```
docs/
├── index.md              ← 主干：三层金字塔架构总览（持续演进）
├── 市场层.md             ← 主干：市场层架构定义
├── 板块层.md             ← 主干：板块层架构定义
├── 个股层.md             ← 主干：个股层架构定义
├── template/             ← 文档模板（起草新方案/任务清单时复制使用）
│   ├── 技术方案文档模板.md
│   └── 开发任务模板.md
├── plans/                ← 进行中的复杂任务方案（每个文件 = 一个任务）
├── tasks/                ← 开发任务清单（方案确认后生成，每个文件 = 一个方案的任务分解）
└── done/                 ← 已完成的方案归档（历史参考，不删除）
    ├── 市场层重构方案.md
    ├── 板块层技术方案.md
    ├── 板块轮动预测分析方案.md
    └── 技术指标T-1交易日限制梳理方案.md
```

**文档类型定位：**

| 类型 | 位置 | 生命周期 | 示例 |
|------|------|----------|------|
| **主干架构文档** | `docs/*.md`（除 template/、plans/、tasks/、done/） | 持续演进，随项目更新 | 市场层.md、板块层.md |
| **文档模板** | `docs/template/*.md` | 稳定，起草新方案时复制使用 | 技术方案文档模板.md、开发任务模板.md |
| **进行中方案** | `docs/plans/*.md` | 任务驱动，实施完成后移入 done/ | — |
| **开发任务清单** | `docs/tasks/*.md` | 方案确认后创建，实施完成随方案归档删除（过程性文档） | — |
| **已完成方案** | `docs/done/*.md` | 归档保留，作为设计决策的历史参考 | 市场层重构方案.md |

### 工作流程

1. **复杂任务（重构/新架构/多文件变更）**
   - **每个复杂任务 = `docs/plans/` 下的一个方案文件**，该文件即为该任务的唯一追踪载体
   - 先写 `docs/plans/<方案名>.md`，包含：背景动机、设计思路、涉及文件清单、接口/字段变更、向后兼容、验证方法
   - **除非能确保 95% 实现无误，否则必须不断向我澄清问题**
   - **方案写完（澄清完毕）后，自动进入评审循环**（见下方"评审循环规则"）：换新 agent 多轮评审直到 verdict PASS 且无 blocker/major，修完剩余 minor + 过自检清单收尾；**评审通过后才请我确认**
   - **我确认后先做任务分解**：生成 `docs/tasks/<方案名>.md` 开发任务清单（复制 [docs/template/开发任务模板.md](docs/template/开发任务模板.md)，见下方"Tasks 文件约定"），把方案拆成可独立验收的开发任务；任务清单就绪后才开始实现
   - **实时状态更新**：实现过程中，每完成一个关键步骤（如：State 字段新增完毕、某个 Agent 写完、子图编译通过）或一个开发任务，**立即更新方案文件与任务清单的状态块**（任务完成 → 勾选验收项 + 同步任务总览表），记录当前进度和下一步
   - **代码写完之后，启动 subagent 做 code review**（见下方"Code Review 规则"）
   - **实现完成 + review 通过后，将方案中的架构变更整合进主干文档（`docs/*.md`），方案文件状态更新为"已完成"，移入 `docs/done/` 归档**

2. **中小改动（单文件、加函数、修 bug）**
   - 直接改代码，不需要 plans 文档
   - 如果改动改变了主干文档中描述的结构/接口，同步更新主干文档

3. **Trivial 改动（错别字、格式化、单行修复）**
   - 直接改，无需任何文档

### Plans 文件约定

- 每个 `docs/plans/<方案名>.md` 代表一个独立的复杂任务
- 文件顶部必须有状态块，格式：

  ```
  > **状态**：<当前阶段>（<最后更新时间>）
  > **进度**：<已完成>/<总步骤> 步骤
  > **下一步**：<接下来要做什么>
  ```

- 状态取值：`方案设计` → `评审中`（写完自动进入评审循环）→ `待确认` → `任务分解`（用户确认后生成任务清单）→ `实现中` → `Code Review` → `已完成`
- **实现过程中每完成一个关键步骤，必须更新状态块的进度和下一步**
- 任务完成后状态改为 `已完成`，**文件移入 `docs/done/`** 归档，架构变更同步进主干文档
- **正文章节结构**：复制 [docs/template/技术方案文档模板.md](docs/template/技术方案文档模板.md) 到 `docs/plans/<方案名>.md`，取舍规则详见模板文件末尾速查表。已有历史方案不做回填改造

### Tasks 文件约定

- **方案经用户确认后**，生成 `docs/tasks/<方案名>.md`（与方案文件同名）——把方案的实施步骤拆解为可独立验收的开发任务，是方案的实施计划载体。任务清单直接由已评审通过的方案拆出，**无需额外评审**
- 文件结构（顶部状态块 + 任务总览表 + 逐任务详情）与任务块结构，复制 [docs/template/开发任务模板.md](docs/template/开发任务模板.md)
- **拆分原则**：
  - 每个任务 = 一个可独立验收的实现单元（新建一个模块 / 改造一个文件 / 写一组单测）
  - 按依赖排序，任务块标注依赖关系；无依赖的任务可并行
  - 通常 3–10 个任务；每个任务的验收标准必须是**可执行的检查项**（单测命令 / 可运行检查 / 写明观察点的人工检查），禁止"完成 XX 功能"式模糊表述
- **状态取值**：`待开始` → `进行中` → `已完成`；被阻塞时标 `阻塞：<原因>`，解除后恢复流转
- **实时更新**：实现过程中每完成一个任务，立即更新该任务块的状态、勾选验收项，并同步任务总览表与文件顶部的进度
- **生命周期**：任务清单是过程性文档——方案完成归档（移入 `docs/done/`）时，对应任务清单随之**删除**（验收结论已固化进方案文件与代码）。中小改动不需要任务清单

### Code Review 规则

**每次复杂任务的代码写完之后，必须启动 subagent 做 code review**，review 范围以方案文件的"文件变更清单"为准。

流程：

1. 方案文件状态更新为 `Code Review`
2. 启动 subagent（type: `claude`），prompt 包含：
   - 方案文件路径（含变更清单和设计意图）
   - 逐文件对照方案检查：字段命名一致性、接口签名匹配、边界条件处理、向后兼容、缺失占位
   - **代码逻辑正确性**：函数入参/出参是否与调用方匹配、条件分支是否覆盖所有情况、状态流转是否符合设计、是否存在死代码或不可达路径、异常处理是否到位
3. Review 发现的问题在修复后重新 review（最多 2 轮）
4. Review 通过后状态更新为 `已完成`，执行主干文档合并

> 中小改动不需要 code review。

### 测试规则

- **自测禁止运行 AI 测试**：Claude 自测（自己跑 pytest 验证改动）时，一律排除依赖真实 LLM 的测试——即 tests/ 中用到 `real_llm` / `real_toolkit` fixture 的集成用例（如 `test_sector_news_analyst.py`、`test_sector_rotation_analyst.py::test_integration_*`），统一用 `-k "not integration"` 排除。这类测试**只能由用户手动调用**，Claude 不得自动运行。
- **`-k "not integration"` 排除有漏洞（2026-08-24 踩坑）**：部分用真实依赖 fixture 的测试名不含 "integration"（实测 `test_sector_news_analyst.py`、`test_sector_tech_analyst.py` 的全部用例、`test_sector_rotation_analyst.py` 的单元测试以外的集成用例），`-k` 按名排除不掉 → 真实 LLM 调用（timeout=180s）+ Tushare 代理拉取会让全量自测挂起 10 分钟以上。Claude 全量自测时：先 `grep -rl "real_llm\|real_toolkit" tests/` 列出含真实依赖的文件，逐个按文件名排除，或只跑与本任务相关的测试文件 + 无真实依赖的目录（position/screening/risk_gate/graph/utils/templates/event_study/dataflows）

### 评审循环规则（修复 → 换新 agent 评审 → 直到通过）

适用：任何评审驱动的修复循环（方案文档评审、Code Review 发现问题的修复、其他 agent 评审场景）。**复杂任务的方案文件写完（澄清完毕）后自动触发本循环**，评审通过前不进入任务分解与实现。

1. 修复完成后，启动 subagent（type: `claude`）做独立评审，prompt 需给出：被评审文件路径、修复背景（此前发现的问题清单）、评审维度、输出格式（verdict: PASS/FAIL + 按 severity 分级的 findings）
2. **每轮评审必须换新 agent**（不复用已完成评审的 agent，保证独立视角）
3. **评审范围**：**第 1 轮必须全文评审**（带维度清单，力争一轮收掉全部声明级缺口）；从第 2 轮起以 delta 为主——只核验"本轮修复的落地情况 + 修复点与周边文字的交互"。每轮全文重读会让文档越长、可挑刺的表面积越大，导致 minor 无限循环
4. **findings 输出约束**：minor 必须区分"影响实施一致性"与"纯润色"（措辞/示例数值/格式样例），纯润色归并为一条；每轮 findings 上限 8 条，按影响排序
5. **停止条件**：verdict PASS 且无 blocker/major → 主会话一次性修完剩余 minor + 过自检清单（数值推导自洽 / 章节交叉引用措辞同步 / 编号连续 / 测试落点与承诺一一对应 / 新文案与既有约定一致）后**收尾，不再开新轮**；仅当新发现"影响实施一致性"的问题时才再开一轮
6. **轮数上限**：通常 ≤3 轮收敛；超过 5 轮仍未干净 → 停下向用户汇报每轮发现类型的分布趋势，询问是否继续
7. 评审 agent 只读，不修改文件
8. **初稿质量是收敛前提（2026-08-23 踩坑：纯 delta 循环 7 轮才收尾）**：
   - **初稿必须把声明级细节一次写全**，不留猜测空间：每条数据流的接续（如 X 由谁产生、传给哪个参数、最终落到哪个输出）、每个匹配规则的落点（如 checkpoint dir 第几段对应哪个 expander 标签）、每个测试断言的可构造性（fixture 能否真的造出该场景，注意"恒空公式"这类永远测不到的死路径）
   - **修复 findings 时必须同步所有同类表述**：同一概念在代码草图/决策表/契约/测试清单多处出现时全部一起改——残留未同步的同类表述是下一轮 findings 的主要来源（实测第 2 轮 2 条 findings 全是第 1 轮修复的残留）
   - **修复不得引入新矛盾**：改一个公式/口径时，先推演它与上游来源、下游消费的组合是否仍成立（如"以覆盖日期生成的窗口做差集恒为空"）
   - **元组形状改造必须枚举全量随迁点（2026-09-06 踩坑，评审 2 轮才收齐）**：方案描述"改私有方法返回元组/元素形状"时，除调用点解包外，还必须逐点列出函数体内部随迁点——失败路径 return 的值个数、按 index 取元素的排序键（头部插字段导致索引整体移位）、函数自身 docstring 的形状描述；漏任何一类都会让消费侧解包抛 ValueError 或排序静默错序

### CLAUDE.md 自我更新规则

- **每次完成一个任务/分析（方案评审收尾、实现完成、Code Review 通过、踩坑解决）后，检查是否有值得沉淀进 CLAUDE.md 的内容**，有则直接更新，无需用户提醒
- 值得写入：新确认的约定或决策、踩过的坑与规避方法、新数据源/新端点的用法（如 Tushare 代理端点）、评审循环暴露的规则缺陷、用户明确要求"记住"的内容
- 不写入：任务本身的状态与进度（属于方案文件状态块）、一次性命令与临时信息、可由代码/git 推导的事实

### Data Provider 接口约定

**接口契约以 `BaseStockDataProvider` 基类为准**（[base_provider.py](AI/dataflows/providers/base_provider.py)）。
所有 Provider（AKShare、Tushare、未来新增）继承该基类。

**目录结构（2026-09-01 起）**：CN 市场提供器统一放在 `AI/dataflows/providers/cn/` 子包
（`cn/tushare.py`、`cn/akshare.py` 及仅被二者使用的纯函数模块 `cn/daily_matrix_utils.py`、
`cn/limit_ladder_utils.py`）；`base_provider.py` 留在 `providers/` 层作跨市场契约。
模块名不含 `_provider` 后缀，类名仍为 `TushareProvider`/`AKShareProvider`；引用一律走
`AI.dataflows.providers.cn.<模块>`，不留旧模块名兼容 shim（见 docs/done/数据提供器目录重构方案.md）。

**设计原则：**
- 基类定义完整接口 + 默认"不支持"返回 → 子类按需覆写
- `interface.py` 通过 `hasattr(prov, 'method_name')` 动态检测可用方法
- 新增数据能力时，**先在基类加方法签名 → 优先在 TushareProvider 覆写**（AKShare 仅在 Tushare 无法覆盖时补充，见强制规则 2）

**强制规则：**

1. **新增方法必须先加到基类** `BaseStockDataProvider`，提供默认 `_not_supported()` 返回
2. **优先 Tushare 实现**：新数据能力默认只在 TushareProvider 覆写；仅当 Tushare 无对应接口/权限、且 AKShare 有对应能力时才在 AKShareProvider 覆写（如 AKShare 独有接口）。两 provider 都覆写时签名完全一致（参数名、默认值、返回类型）
3. **无法提供数据时**，不覆写基类方法即可（自动返回 `"数据不可用：{provider_name} 不支持 <功能>。"`）
4. **仅 `get_stock_data` 和 `get_stock_info` 为抽象方法**（`@abstractmethod`），子类必须实现
5. **返回格式统一为 `str`**（格式化 Markdown），仅 `get_stock_info` 返回 `dict`
6. **类属性** `GLOBAL_TECH_INDICES`、`AI_INDUSTRY_CHAIN`、`A_SHARE_CONCEPT_MAP` 在基类中定义为空 `dict`，子类覆写
7. **多数据块可用性门控契约**（板块轮动预测分析师引入，2026-08）：node 内多块数据独立判定可用性时，各块正常输出统一以 `# ` 开头（代码用 `startswith("#")` 判定），所有不可用/异常返回串（含空串/None）一律**不得以 `#` 开头**——新增数据函数返回不可用/异常串时必须遵守，防止门控误判（见 docs/done/板块层轮动数据增强方案.md 3.3.1）
8. **结构化接口例外**（2026-08-24 板块层热力图引入）：新增**结构化返回**（dict/DataFrame 消费方）的接口方法，基类默认返回 `None`（不返回 `_not_supported()` 的 str，避免破坏 dict/DataFrame 消费方）；不支持/失败时返回 `None`。已有 3 例：`get_industry_daily_returns_matrix`、`get_concept_daily_returns_matrix`（见 docs/done/板块层轮动战术与政策事件流方案.md 2.1）

### 三方依赖能力评估

- **技术方案文档必须包含三方依赖能力评估**：逐项确认方案所依赖的第三方库（AKShare、LangGraph、LangChain 等）是否有足够能力实现诉求
- 评估要点：
  - **不仅看端点是否存在，更要看能否拿到所需数据**：单个 API 能否直接满足？还是需要多个 API 组合（如 IPO 日历 = 申购 + 上市两个接口；资金流向 = 北向 + 主力两个接口）？组合后的覆盖是否完整？
  - 每个新增数据函数的入参和出参与 AKShare/Tushare 端点的实际签名是否匹配（参数名、日期格式、返回字段）
  - LangGraph 的 subgraph / StateGraph 机制是否支持方案的图谱架构
  - 需占位的工具明确标注"占位 + TODO + 缺失原因"，不假装可实现
- 如果某个依赖能力不确定（端点覆盖不全、字段缺失、需要降级），必须在方案中标记为风险并给出降级策略
- 此评估写入方案文档的独立章节（如"三方依赖能力评估"）

## 项目配置

### LangGraph 图结构提取约定（2026-09-08 拓扑图功能踩坑）

- **langgraph 1.2.10 的 `get_graph()` 不能用于确定性顺序提取**：返回 langchain_core Graph，`edges` 是 set（无序），且 draw 模拟（apply_writes）对无 reducer 的 dict state 抛并发写冲突。要确定性拓扑（含条件边声明序）用 `compiled.builder`：`builder.nodes`（dict 声明序、不含 __start__/__end__）、`builder.edges`（set，直接边）、`builder.branches[src][router_key].ends`（dict 保留条件目标声明序，如 Risky 的 Safe 在 Risk Judge 前）——实现见 AI/graph/topology.py
- **openapi 重导出链条**：backend 新增/修改路由后必须 `python -m backend.scripts.export_openapi` + `pnpm run generate:api` 再动前端消费代码；并发编辑下他人前端代码依赖新枚举（如 action approve/ignore）时，未重导出会导致其 typecheck 失败（2026-09-08 实测）
- **openapi-typescript-codegen 把 Literal 生成 enum namespace**（如 `TopologyNodeDTO.status.EXECUTED`），测试/组件需值导入（不能 import type），mock 数据用枚举成员不用字符串字面量
- **可选 Literal（`Literal[...] | None`）生成 union 类型而非 enum namespace**（2026-09-08 实测）：pydantic `Literal[...] | None` 进 OpenAPI 为 anyOf → codegen 落为 `'x' | 'y' | null`（如 `RefreshData.skipped_reason`），无枚举成员可导入，字符串比较即类型安全；与必选 Literal 的 enum namespace 形态不同，写 mock/断言时勿套用枚举成员写法
- **React Query refetchOnMount 默认 true**：同 query key 的观察者晚于首个观察者挂载（如弹窗在数据到达后才挂载）会触发一次额外 refetch（staleTime 0 下数据即陈旧）——弹窗类共享缓存订阅用 `enabled` 门控（打开才订阅），见 NodeLogsDialog

### 单Agent重跑与提示词编辑（2026-09-10）

- **提示词注册表**：`AI/utils/prompts.py` 是 23 个 LLM 节点默认提示词的单一事实来源（`DEFAULT_PROMPTS`，键 = 拓扑节点 id；US/KR 4 分析师为折叠节点仍入表、v1 不可编辑）。A 类 15 工厂（ChatPromptTemplate）走 `system_message(node_id, lambda: 模板)`——覆盖命中返回**静态 SystemMessage**（花括号原样进 LLM；langchain 元组 `("system", 文本)` 会被当模板解析，含 `{xxx}`/JSON 示例即 KeyError，实测 1.5.3）；B 类 8 工厂（f-string 纯字符串 `llm.invoke`）走 `get_system_prompt`。覆盖注册表经 `init_state["prompt_overrides"]` 快照注入（propagate 入口 set_overrides），执行开始读库 → 排队/运行中任务不受后续编辑影响
- **checkpoint 存档**：`AI/utils/checkpoint.py`——guard_checkpoint 包装器在层构建器接线处统一注入（快进 + `_current_node_id` + 节点后落盘）；`{run_dir}/checkpoints/{layer}[/{ticker}]/{Sanitized}.json` + `__init__.json` + `complete.json`（成功收尾原子写，**resolve 目录链只接受含 complete.json 的目录**——部分执行目录的环中态 checkpoint 会让被跳过环成员出口路由无限循环）+ `rerun.json`
- **AgentState 白名单坑**：langgraph 按 schema channels 白名单**静默丢弃**未声明输入键——`_rerun_from`/`_current_node_id`/`selected_layers` 等平台保留 key 必须在 AgentState 声明，否则快进 guard 失效/跳过落盘判定恒空（Code Review 实测）
- **环入口上移**：重跑目标为辩论/风险环成员（Bull/Bear/Risky/Safe/Neutral）时 entry 与 `_rerun_from` 上移环入口（`_LOOP_ENTRY`，键为完整 node_id）——环整体重演，否则被跳过环成员出口路由返回 map 外目标 KeyError 崩溃
- **重跑触发源**：消息级参数（outbox payload → actor kwarg），`analysis_tasks.rerun_from_node_id` 列仅展示（claim 时非 rerun 消息清列）；entry 查找沿 attempt 目录链回溯（worker 侧链起点 = attempt_no-1，claim 时已递增）；attempt 链构造共享 `attempt_chain_dirs`（三处消费，禁止各自内联）
- **前端坑**：`RegExp.test` 不得带 `g` 标志（lastIndex 跨求值残留）；弹窗回填用显式 `userEditedRef` 标记交互（不得以 text 是否为空推断——清空后 entry refetch 会回写服务端文本）

### 前端包管理器（pnpm）

- **frontend 是 pnpm 布局**（pnpm-lock.yaml + node_modules/.pnpm 符号链接），依赖操作一律用 `pnpm add` / `pnpm install` / `pnpm run`——`npm install` 会报 `Cannot read properties of null (reading 'matches')`（npm arborist 无法处理 pnpm 布局，2026-09-06 踩坑）
- 前端 API client 由 `pnpm run generate:api` 生成；openapi-typescript-codegen 按 **tags 分组**生成 Service——同 tag 的多个端点会归进同一个 Service 类（如 execution-logs 端点按 tags=["analysis-tasks"] 归入 AnalysisTasksService），不存在独立 Service 文件属正常，消费时按 tag 找方法

### 前端 markdown 渲染约定（2026-09-06 起）

- **统一组件 `MarkdownView`**（`frontend/src/shared/ui/markdown.tsx`）：所有 markdown 字符串内容（LLM 提示词/输出、DP 出参 res.md、报告分区正文等）一律经它渲染，不得再手写 `whitespace-pre-wrap` pre 或 `dangerouslySetInnerHTML`/v-html 式注入
- 技术栈：`react-markdown` + `remark-gfm`（GFM 表格）+ `remark-breaks`（单换行→`<br>`，对齐内核输出的换行习惯）；样式走 `@tailwindcss/typography`（styles.css 里 `@plugin` 接入）的 `prose prose-sm prose-invert max-w-none [&_table]:block [&_table]:overflow-x-auto`——**prose-invert 无条件加载**（应用 dark-first，`:root` 直接深色 token）；`[&_table]` 两个类兜底宽表格在窄容器的横向溢出（typography 本身不处理）
- **内容类型分流**（ExecutionLogsPanel.FileContent 与后续新增消费点遵守）：`kind=md` → MarkdownView；`kind=json` → 格式化 pre（含 200KB 渲染保护）；`kind=txt` → 纯文本 pre；缺失/空内容显示占位
- raw HTML 默认转义（react-markdown 无 rehype-raw），内核内容为受信 markdown 无需 DOMPurify；新增 markdown 消费点直接复用 MarkdownView，勿重复引入 marked 等第二引擎（见 docs/done/任务详情页markdown渲染方案.md）

### pyproject.toml 优先
- **所有 Python 项目使用 `pyproject.toml` 管理依赖**，不使用 `requirements.txt` 作为主要依赖声明
- 构建后端：`setuptools.build_meta`（`setuptools>=61.0`）
- 安装命令：`pip install -e .` 或 `uv pip install -e .`

### 每日批处理触发方式（2026-08-31 起）

- **方案 B（推荐）**：常驻自调度——`AI/eventStudy/scheduler/app_scheduler.py` 的 APScheduler 挂在 eventStudy FastAPI lifespan（`AI/eventStudy/api/main.py`），每天 `EVENT_STUDY_DAILY_TIME`（默认 08:30，本地时区）以**子进程**触发 `python -m AI.eventStudy.scheduler.daily_job`；防重复用 `logs/daily_job.running` 标记文件（写子进程 pid，服务启动时按 pid 存活清理陈旧标记）+ 进程内 threading.Lock；misfire 补跑窗口 30 分钟
- **补跑机制（2026-08-31，电脑睡眠场景）**：完成标记 `logs/daily_job_done.YYYYMMDD`（子进程退出码 0 时原子写入）＋三层触发——cron 08:30 正常触发、服务启动自检、**每 15 分钟周期自检**（睡眠期间调度器冻结无启动事件，唤醒后靠周期自检补跑）；当日自动尝试上限 `EVENT_STUDY_DAILY_MAX_ATTEMPTS`（默认 3）防持续故障空跑，次日自动恢复
- 运行约束：uvicorn **单 worker、禁用 --reload**（否则调度器重复启动）；Windows 守护用 NSSM 注册 Windows 服务（见 AI/eventStudy/scheduler/scheduler_setup.md）
- **方案 A**：schtasks 每日 08:30 触发（无常驻服务时用，注册命令见 scheduler_setup.md）；A/B 同时开启安全（标记文件防重复），但建议只用一个

### 调试步进模式（Debug Step Mode）

- 开启：运行分析前设 `LIVEPROFIT_DEBUG_STEP=true`（default_config → `debug_step`），分析进程在 **DP 响应 / LLM 调用前（on_llm_start，API 请求发出前）/ 节点 res** 三个检查点暂停
- 页面确认：`streamlit run AI/logviewer/app.py` 的「调用时序」tab 内容区顶部右侧按钮区（1s 轮询）点【✅ 下一步】/【⏭ 跳过全部】后继续；无限等待无超时；检查点指向的 layer/node/DP 展开自动展开并加「⏸」前缀
- 两个进程通过 `logs/{ts}/.debug_checkpoint.json` 文件协调（status: waiting → confirmed/skip_all），双方 temp+rename 原子写（tmp 名带 pid 后缀防并发交错）；文件名常量单点定义于 `AI/utils/step_gate.py`
- 图外 LLM 调用（决策抽取 process_signal / Reflector）不产生检查点（节点名不在 `_NODE_LAYER` 表 → unknown 过滤）
- 页面按钮有 stale 防护：比对 session_state 记存的渲染 seq 与文件当前 seq，不一致不写（防连点把确认写进下一个检查点）


### 后端平台踩坑（backend/，2026-09-05 起）

- **alembic.ini 必须纯 ASCII**：alembic 以 locale 编码读取配置文件，Windows GBK locale 下含中文注释会抛 UnicodeDecodeError（表象为 pytest 卡死在配置解析）；ini 注释用英文，说明写进 backend/migrations/env.py
- **Windows 上 psycopg async 需要 SelectorEventLoop + uvicorn loop="none"**：ProactorEventLoop 下 psycopg async 直接 InterfaceError；create_app 内设 policy 对 uvicorn 场景太晚（uvicorn 在加载工厂前建循环），且 uvicorn 的 loop="auto" 在 Windows 会强制装回 Proactor 覆盖你的策略——正确姿势：cli 里先 set_event_loop_policy(WindowsSelectorEventLoopPolicy) 再 uvicorn.run(..., loop="none")；redis.asyncio 两种循环都可用，但连接串 host 也须归一化（`_normalize_loopback_host` 注意 `:pass@host` 形式 username 为空串，重建 netloc 时不得丢密码——曾因此把 redis 密码丢了）
- **PG_HOST=localhost 必须归一化为 127.0.0.1**：Docker 端口代理仅监听 IPv4 loopback，psycopg 优先尝试 `::1` 被黑洞且默认无 connect_timeout → 无限挂起（faulthandler 定位在 psycopg wait_conn）；backend.bootstrap.settings 的 resolved_database_url/resolved_redis_url 已做归一化，bootstrap 引擎统一带 connect_timeout=5
- **Windows 管道下 Python stdout 全缓冲**：pytest 经 `| tail/head` 观察输出会误判"卡死"（实际在跑）；排查挂起用 `-o faulthandler_timeout=30` 抓真实栈
- **JSONB 写入前必须归一为纯 JSON 基本类型**（2026-09-06 踩坑）：psycopg 的 JSONB dump 用标准 `json.dumps`（**无 default 兜底**），LangChain 对象（如 HumanMessage）进 JSONB 会抛 `Object of type HumanMessage is not JSON serializable`；"序列化校验"若只 `json.dumps(payload, default=str)` 而不**回写结果**等于没校验（raw 对象仍入列）——归一写法：`payload = json.loads(json.dumps(payload, ensure_ascii=False, default=str))`，并在 Service 写入边界再做一次防御
- - **pydantic-settings 带 `validation_alias` 的字段不叠加 `env_prefix`**（2026-09-06 踩坑）：env 变量名 = alias 原样，prefix 被忽略——backend CoreSettings 曾写 `validation_alias="QUICK_MODEL"` + `env_prefix="LIVEPROFIT_"`，实际读的是无前缀的 `QUICK_MODEL`，.env 里的 `LIVEPROFIT_QUICK_MODEL` 被静默无视、落默认 gpt-4o-mini（表象为 LLM 400 无效模型名）。alias 必须写完整环境变量名（`validation_alias="LIVEPROFIT_QUICK_MODEL"`）；新增 aliased 字段时先实测 `Settings().field` 确认读到的是哪个 env
- **Thread 子类禁用 `_stop` 属性名**：threading.Thread 内部方法 `_stop()` 在 join 时被调用，子类用 `self._stop = threading.Event()` 覆盖后 join 抛 `TypeError: 'Event' object is not callable`；心跳/控制线程的事件命名用 `_stop_requested`
- **Dramatiq 在 Windows 上禁用 CLI WorkerProcess**：CLI 的 spawn 子进程经 pickle 传递 RedisBroker，认证信息丢失导致 consumer 反复报 "HELLO must be called with the client already authenticated"（主进程 ping 正常、诊断日志密码正常也一样）——改用进程内模型：`Worker(broker, queues=["default"], worker_threads=1, worker_timeout=1000)` + `worker.start()` 后主线程 `while running: sleep(1)` 保活（start() 不阻塞、join() 语义不是等待消费）；注意 dramatiq 1.17 `broker.enqueue` 只收 Message，投递必须走 `actor.send(...)`；队列实际 key 为 `dramatiq:default`（带前缀）
- **Dramatiq CLI 传参**：`dramatiq.cli.main(args)` 的 args 会被直接当 Namespace 用（`args.path` AttributeError）；必须 `sys.argv = ["liveprofit-worker", "模块", "--processes", ...]` 后无参调用 `main()`，且以 try/except SystemExit 收口
- **concurrent.futures.Future 不能直接 wait_for/shield**：需 `asyncio.wrap_future()` 桥接；超时用 `wait_for(shield(wrapped))` 语义（inner 继续跑，完成回调才释放名额）；注意 `asyncio.run()` 结束后回调 `call_soon_threadsafe` 会抛 `Event loop is closed`——测试必须等名额归零再退出
- **FastAPI 注册 SSE 协议 Schema 进 OpenAPI**：StreamingResponse 端点的响应 Schema 默认不进 components；给端点加 `response_model=SSEContract`（文档用途）即可把 BusinessEventData/ResetEventData/HeartbeatEventData 注册进 components，运行时 StreamingResponse 不经 JSON 序列化不受影响；`responses={...content: {schema: <ModelClass>}}` 反而会把 ModelMetaclass 编进 schema 报 PydanticSerializationError
- **prometheus_client 导入路径**：`GaugeMetricFamily` 在 `prometheus_client.metrics_core`，顶层包不导出
- **Alembic 迁移内 raw SQL**：必须 `text()` 包装（SQLAlchemy 2.0 拒绝裸字符串 + params）；ORM 模型 Python 端 `default=uuid.uuid4` 不作用于迁移 SQL——INSERT 需显式 `gen_random_uuid()`（PG13+ 内置）
- **SQLAlchemy 条件 UPDATE 含比较运算**：WHERE 用 `<`/`>` 比较时，默认 synchronize_session="auto"→evaluate 会在 Python 层比较 naive/aware datetime 抛 TypeError；一律显式 `synchronize_session="fetch"`（既避免异常又保持会话内对象新鲜；False 会让后续 get 读到旧状态）
- **module 级共享 DB 的集成测试**：必须 autouse fixture 逐用例 TRUNCATE + flushdb 隔离（否则前用例遗留 Outbox/任务被后用例 claim，如 dispatch 数量断言翻倍）；pytest 输出经管道时用 `-o faulthandler_timeout` 或写文件排查挂起
- **契约测试复用 AI 侧全局 config 的注入点（2026-09-08 起，事件研究审核平台集成引入）**：AI 侧 `AI.eventStudy.collectors.config` 的 `pg_dsn()`/`redis_uri()` 在**调用时读模块全局**（env 仅导入时捕获进模块常量），故契约测试可 `monkeypatch.setattr(es_config, "PG_CONNECTION_STRING"/"REDIS_CONNECTION_STRING", 测试串)` + `es_config._redis_client = None`（懒加载客户端重建指向 db 11）把 AI 侧连接切到契约测试库；配合 AI 侧函数级 import，补丁在调用时生效。先例：`backend/tests/contract/api/test_event_study_review.py` 的 `_review_test_env` fixture（含 events/assets/event_impacts 最小列集 DDL + 逐用例 TRUNCATE）
- **E2E/脚本向真实 Redis 写草稿前必须先检查现存 key（2026-09-08 踩坑，曾覆盖 3 条真实草稿）**：爬虫 `events:draft_seq` 已分配大量号段，`SET events:pending:<id>` 无条件覆盖会毁掉真实待审草稿（当日靠 dump.rdb 快照 + 临时容器恢复）。安全做法：先 `SCAN events:pending:*` + 读 `events:draft_seq`，用**远高于 seq 的 draft_id**（如 9001+）并事后 `DELETE` 清理；向真实 PG 写行同理先确认无同标题行、事后按明确条件 DELETE
- **Redis 数据误覆盖恢复手法（2026-09-08 验证有效）**：`docker cp liveprofit-redis:/data/dump.rdb <本地目录>` → `docker run --rm -v <本地目录>:/data -p 6390:6379 redis:7-alpine redis-server --appendonly no --save ""` → `docker exec redis-cli -p 6390 --raw GET <key>`。两个坑：Git Bash 的 `/tmp` 路径 Docker Desktop 挂载无效（必须 Windows 形式 `C:/Users/...`）；Windows GBK locale 下 Python subprocess 读中文输出必须显式 `encoding="utf-8"`

## 项目结构约定

```
项目根/
├── pyproject.toml          # 项目配置（必需）
├── README.md
├── main.py                 # 入口（被 pyproject.toml 的 scripts 引用）
└── 包名/                   # Python 包（与 pyproject.toml name 对应）
```

## 环境配置
- 使用 `.env` 或 `.bash` 文件管理环境变量
- `python-dotenv` 用于加载 `.env` 文件
- 敏感信息（API Key）不提交到 Git

### Git Bash 运行脚本注意（Windows）

- `cmd.exe /c "start ..."` 在 Git Bash 下的 `/c` 会被 MSYS 路径转换破坏，可能让 cmd 进入**交互式会话挂起脚本**（终端出现 cmd 横幅后卡住，后续步骤不再执行）。打开浏览器/外部程序改用 `powershell.exe -NoProfile -Command "Start-Process '...'"`，并加 `</dev/null` 兜底防交互式挂起（见 run.sh）

### Tushare 代理端点（自定义 URL）

- 位置：`TushareProvider._connect()`（`AI/dataflows/providers/cn/tushare.py`）——`ts.set_token(TUSHARE_TOKEN)` + `ts.pro_api()` 之后，覆写私有属性指向自定义端点：

  ```python
  self.api = ts.pro_api()
  self.api._DataApi__http_url = "https://ts.gyzcloud.top/api"  # 自定义 Tushare 端点
  ```

- `_DataApi__http_url` 是 name-mangled 私有属性，写法必须保持双下划线形式；换回官方端点删掉该行即可（默认 `http://api.tushare.pro`）
- Token 通过 `.env` 的 `TUSHARE_TOKEN` 配置（`LIVEPROFIT_DATA_SOURCE=tushare` 时生效）
- **代理端点能力可能与官方有差异** → 新增数据函数做"三方依赖能力评估"时必须对代理端点**实测**（真实 token 探测），不能只看 tushare 官方文档
- **日线接口返回降序（2026-08-23 踩坑）**：sw_daily / dc_daily / ths_daily / index_daily 实测按 trade_date **降序**（新→旧）返回，直接 `tail(N)` 会取到最旧数据（曾导致行业排名拿到 17 天前的数据）。所有日线消费点必须先经 `cn/tushare.py` 的 `_sort_asc_by_trade_date(df)` 升序归一，再 tail/iloc；新增日线消费点必须遵守，单测需含"降序输入"回归用例
- **端点子日志（2026-08-25 引入）**：`wrap_tushare_api(api)`（dataprovider_log.py）在 `_connect`/trading_calendar 建 api 后包装 `query`，端点调用落在当前 DP 调用目录的 `tushare/{seq:03d}_{api_name}/`（req/res/meta.json，viewer 在 dataprovider 展开内嵌套展示）；仅 tushare 数据源 + run 内有 DP 上下文时落盘，`LIVEPROFIT_TUSHARE_LOG=0` 可关闭
  - **tushare DataApi 的 `__getattr__` 对任意未知属性返回 `partial(self.query, name)`（truthy）** → 包装器幂等/探测标志判定必须查实例 `__dict__`，`getattr` 会误判"已包装"导致完全不落盘
  - **ThreadPoolExecutor 不自动传播 contextvars（Py3.12 实测）** → `_run_with_timeout` 已用 `contextvars.copy_context()` + `ctx.run` 显式带入；任何新增"在 worker 线程内读 contextvar"的代码必须同样显式复制，勿假设自动传播
  - 端点调用超时（`_api_call` 返回 None）后 worker 仍会补写日志（上下文已复制），「DP res 显示超时、tushare 展开却有成功记录」并存属预期诊断行为
  - `_connect` 的连通性探测调用（stock_basic limit=1）在 DP 上下文内会被记录，meta 带 `probe=true`（viewer 显示「🔌 连通性探测」角标）
  - 单次结果 records 超 500 行截断（`truncated=true` + `row_count` 元数据）
- **全市场拉取禁止区间查询（2026-08-30 踩坑）**：代理端点 `daily(start,end 区间，无 ts_code)` 2 天区间仅回 6000 行（应 ~11000，**静默截断**）、`fund_daily` 区间返回 0 行——全市场日线/因子拉取必须 `trade_date` 单日查询；单日行数 ≥6000 视为截断，自动降级为分批补拉（每批 100 代码逗号分隔 + trade_date，store/backfill.fetch_day_frames 已实现该降级，新增全市场消费点必须复用）
- **概念成分接口参数硬约束（实测）**：ths 用 `ths_member(ts_code=概念代码)`（`code=` 参数被代理忽略，恒回全量截断 6000 行）；dc 用 `dc_member(ts_code=板块代码, trade_date=最近交易日)` 组合过滤（仅 ts_code 返回跨 5 日快照、全量拉截断 8000 行）

### 全市场日线本地库（store 包，2026-08-30）

- 位置：`AI/dataflows/store/`（`schema.sql` / `db.py` / `stock_daily_dao.py` / `concepts.py` / `backfill.py` / `incremental.py`），六张表建在 liveprofit 库 **public schema**，与事件研究同库不同表（`market_data` 及消费方零改动）：`stock_basic` / `fund_basic` / `stock_daily` / `adj_factor` / `concept` / `concept_member`
- **分类约定**：`stock_daily` / `adj_factor` 股票与场内基金**共用**，分类一律走 `is_fund_ts_code()` 前缀函数（沪 5 开头、深 15/16/18 开头 = 基金），查询不 JOIN 基本信息表白名单过滤（避免退市股/异常代码被静默漏掉）
- **写入约定**：批量写入 COPY → 临时表（表名带 pid+随机后缀）→ `INSERT ... ON CONFLICT`；清洗顺序 close NaN 行显式 drop（close 列 NOT NULL）→ 其余 NaN→None（**必须先转 object dtype**——float64 列上 `where(cond, None)` 会把 None 压回 NaN）；幂等策略由调用方控制（决策 5）：回填 `DO NOTHING`、增量最近 3 交易日 `DO UPDATE`（覆盖 tushare 日终修正）
- **实测踩坑（2026-08-30 集成验证）**：
  - `CREATE TEMP TABLE (LIKE 表)` **默认不复制 DEFAULT 表达式** → 带 `DEFAULT now()` 的 NOT NULL 列（updated_at）在 COPY 时被填 NULL 违例；必须写 `LIKE ... INCLUDING DEFAULTS`
  - pandas `Series.apply` 的 dtype 推断不可靠：**单行且结果均匀时推 int64、多行含 None 时把 int 整体压回 float64**（单测会骗过）——整数列转换（如 concept.count，tushare 返回 float 300.0）必须用显式 `dtype=object` 的列表推导，否则 COPY 报 `invalid input syntax for type integer`
  - 批量写入的 SQL 级失败会使事务 abort，**下一段 DB 写入报 "current transaction is aborted"**——按来源/批次隔离的采集循环中，每段失败分支必须 rollback 恢复干净状态，且**成功段必须独立 commit**（否则后段失败的回滚把前段成果一并清空，实测 dc 失败清空 ths 899 概念）
  - 概念成分响应含重复 con_code 时，同一 INSERT 批次提出两行相同 PK 会报 `ON CONFLICT DO UPDATE cannot affect row a second time`——Provider 归一化与 DAO 双保险 drop_duplicates
- **事务约定**：单日提交（库内无"半截日"）；概念采集按来源独立提交、基本信息刷新独立提交（逐日失败 rollback 不得静默丢弃 35 分钟概念采集）；commit 失败（事务可能已 abort）需 rollback 恢复干净状态再继续
- **回填**：`python -m AI.dataflows.store.backfill [--start 2016-01-01] [--end 今天] [--retry-missing] [--skip-concepts]`；断点续跑按库内**已入库交易日集合**跳过（存在即完整，"完整日才入库"不变式）——**禁用 max(trade_date) 截断**：库内只有尾部几日时会把全部历史误判跳过（2026-08-30 实测踩坑）；`--skip-concepts` 在概念数据已新鲜时跳过重采（省 ~35 分钟）；失败清单 `logs/stock_backfill_failures.json`（temp+rename 原子写）
- **增量**：daily_job 步骤 3（`--skip` 名 `store`），最近 3 交易日窗口；概念体系周一自动周刷（`collect_incremental(refresh_concepts=None/True/False)`）；akshare 数据源下结构化方法返回 None → 记 warning 跳过不阻断
- 实现详见归档方案：docs/done/全市场日线本地库方案.md
