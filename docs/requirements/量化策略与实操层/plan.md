# 0915 量化策略与实操层方案

> **状态**：待确认（2026-09-16；已按后续评审修订量化执行职责边界，按存量表对照修订数据表设计，按用户「不回测、只按条件选股」砍掉行情冻结两张快照表、改为执行时实时扫描，恢复「设计概览」章节并同步更新 plan 模板为五章固定结构，并按用户拍板收敛为纯 frontend+backend 链路——AI 层不接入、量化任务不经 LangGraph，等待用户确认后进入实现）

> **关联文档**：[任务总览](README.md)｜[决策记录](decisions.md)｜[个股层](../../knowledge/ai/个股层.md)｜[API 契约](../../knowledge/backend/API契约.md)

---

## 一、背景与动机

| 维度 | 现状 | 问题 | 目标 |

|------|------|------|------|

| 选股能力 | `AI/screening/screening_node.py` 以顶层 `screening` 图节点按板块短名单、相对强度和成交额门槛形成 `candidate_stock_pool`，随后 `stock_loop.py` 对候选逐票执行个股 Agent；没有用户可维护的量化条件或完整的版本服务。 | 量化通道若与 AI 层耦合（依赖 `candidate_stock_pool` 或 LangGraph 编排），则不在 AI 短名单内的 `000001.SZ` 即使策略返回有效 BUY 也不会被执行和展示，且走通 frontend/backend 全链路的周期与风险上升。 | 新增独立于 AI 层的量化执行通道：量化从 `market.instrument` 枚举全部活跃 CN 股票，用户可创建、校验、发布版本化受限策略，对每只数据完备股票逐股决策并展示全部量化 BUY 与建议订单。AI 层（market/sector/screening/stock）本方案不改造、不依赖，统一接入留待后续任务。 |

| 组合与资金 | `portfolios`/`portfolio_positions`（0001 建）只有 `name`/`version`，没有资金/风控字段；`backend/modules/quant_strategy/` 尚不存在；`AI/position/position_manager.py` 仍读取环境变量和 `data/portfolio.json`。 | 前端 PostgreSQL 组合不会影响现有仓位规划；例如手工组合资金和 `portfolio.json` 不一致时仍按旧文件计算。 | 本任务创建 0008（`quant_strategies`/`quant_strategy_versions` + `portfolios` 风控列）并补全服务、API 与任务快照；订单只使用任务提交时冻结的组合/持仓快照，行情在执行时实时读取。 |

| 卖出路径 | `trading_graph.py` 在 `risk_gate == "block"` 时清空 `stock_results`，`position_manager.py` 仅处理 LLM 的中文“买入”。 | 已持仓不进入逐票循环，仓位层仅处理买入；AI 通道不会产出量化卖出建议。 | 目标集为全市场活跃股票与持仓并集，持仓票由策略逐票评估产出 SELL 建议；资金/仓位/行业风控照常裁剪。风险门控 `risk_gate` 本方案不接线（拒绝码预留，后续接 AI 层任务启用）。 |

| 执行安全 | 当前没有用户 Python 执行器。 | 在 API/Worker 直接 `exec()` 脚本会暴露宿主能力；例如 `import os`、`context.__class__`、`while True` 均不可接受。 | 白名单 `strategy(context)` 在一次性受限子进程以 JSON 通信；异常、超时和数据不足 fail-closed 为 HOLD。 |

| 报告交互 | `artifact_builder.py` 已把 `final_position_plan`、`stock_results` 存至 `analysis_reports.decision`，但报告 API 和 `ReportContent.tsx` 只读取 Markdown 区块。 | 用户看不到策略版本、信号、订单、裁剪原因；结构化决策未投影到 API。 | 结构化量化执行面板展示策略审计、快照、信号、建议订单和警告，明确“需人工确认，未下单”。 |

## 二、架构设计

```text

策略页 / 组合页 / 全市场任务表单（position 层）

    │ strategy_version_id + portfolio_id + expected_portfolio_version

    ▼

提交服务（一个 SQLAlchemy 事务，不经过 LangGraph）

    ├─ 校验 PUBLISHED 策略和 Portfolio.version

    ├─ 冻结策略/组合/持仓进 execution_snapshot（不含行情）

    └─ 原子写 analysis_task + task_outbox

    ▼

Worker claim 后直接执行（不经 AI 图）：backend QuantExecutionService

    ├─ AllMarketUniverseBuilder 执行时实时枚举活跃 CN 股票

    ├─ MarketContextBatchLoader 分批读行情 → BoundedSandboxExecutor 逐票沙箱执行策略

    ├─ 落盘 signals；PositionPlanner 全局排序生成建议订单并回写

    └─ 取消/失租经 ExecutionControl 终止在途子进程并丢弃未落盘批次

    ▼

ArtifactBuilder 写 AnalysisReport.decision["quant_execution"]

    ▼

报告 API 顶层 ReportDTO.quant_execution → QuantExecutionPanel

注：AI 层（market/sector/screening/stock）本方案不改造、不依赖；任务若同选 AI 层则照旧独立运行，两者互不读取。风险门控 `risk_gate` 本方案不接线（后续接入 AI 层任务时启用）。

```

### 2.1 数据模型与单一数据合同

| 资源/字段 | 类型 | 写入者 | 业务语义与示例 |

|------|------|--------|------|

| `portfolios.total_assets` | `NUMERIC(20,4)` | PortfolioService | 账户总资产；`100000.0000` 元；零值禁止 BUY。 |

| `portfolios.available_cash` | `NUMERIC(20,4)` | PortfolioService | 可新买的现金；`35000.0000`；必须在 `[0,total_assets]`。 |

| `portfolios.risk_per_trade_pct` | `NUMERIC(8,6)` | PortfolioService | 单笔风险预算；默认 `0.010000`（1%）。 |

| `portfolios.min_risk_reward_ratio` | `NUMERIC(8,4)` | PortfolioService | BUY 最低盈亏比；默认 `2.0000`。 |

| `portfolios.max_total_position_pct` | `NUMERIC(8,6)` | PortfolioService | 总仓位上限；默认 `0.800000`。 |

| `portfolios.max_single_stock_pct` | `NUMERIC(8,6)` | PortfolioService | 单票市值上限；默认 `0.100000`。 |

| `portfolios.max_sector_pct` | `NUMERIC(8,6)` | PortfolioService | 申万行业风险桶上限；默认 `0.300000`。 |

| `quant_strategies` | 新表（0008） | QuantStrategyService | 策略稳定 ID、名称、描述、version（元数据乐观锁，沿用 watchlists/portfolios 惯例）。 |

| `quant_strategy_versions` | 新表（0008） | QuantStrategyService | `(strategy_id, version_no)` 唯一的草稿/已发布/归档版本、源码和 source_hash；`version` 列是草稿源码乐观锁（与语义版本号 `version_no` 区分）。 |

| `analysis_tasks.request_params.execution_snapshot` | JSONB | QuantTaskSubmissionService | 完整策略版本源码、组合/持仓与契约版本；唯一允许保存策略源码的任务域数据。 |

| `analysis_reports.decision.quant_execution` | JSONB | ArtifactBuilder | 无源码的策略审计、组合快照摘要、信号、订单、拒绝原因和估值时间。 |

| `quant_execution_signals` | 新表（0009） | QuantExecutionService + PositionPlanner | 全市场逐票结果：BUY 行含 `entry_price`/`stop_loss`/`take_profit` 与建议订单列组，持仓 HOLD/SELL 与错误样本分页保存；完整列清单见 4.3.1。 |

| `ReportDTO.quant_execution` | 可空 DTO | reports router | 对 `decision["quant_execution"]` 的只读 API 投影；旧报告为 `null`。 |

历史组合迁移为 `total_assets=0`、`available_cash=0` 和默认风险参数，因此未补齐账户资金只能获得 HOLD/SELL 建议。组合配置和持仓写入使用同一个 `Portfolio.version`。

> 全部表结构与关系见 ER 图：[attachments/er-diagram.html](attachments/er-diagram.html)（自包含 HTML，浏览器打开；悬停实体高亮其关联关系，支持深色模式）。

## 三、设计概览

> 按实现层分组，概览每个具体对象「改什么、在哪改、行为怎么变」，与「四、详细设计」形成索引：概览回答 what/where，详细设计回答 how。

### backend

#### 服务与 Worker

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |

|----------|----------|----------------|--------------|

| `QuantExecutionService（backend 量化执行服务）【新增】` | 唯一实现执行时实时枚举 universe、分批读取行情、受限 sandbox 执行、signal 持久化及全局订单规划；Worker 每次任务构造一个服务实例并直接调用，不经 LangGraph。 | 新增 `backend/modules/quant_strategy/application/execution.py`、`backend/modules/quant_strategy/infrastructure/`、`AI/strategy_sandbox/` | 量化执行不依赖 AI 层；单票故障隔离、200 标的批次和至多 8 个在途子进程。 |

| `PositionPlanner（backend 领域服务）【新增】` | 由 `QuantExecutionService` 在全部可行动 signal 落库后，按 `score DESC, ts_code ASC, id ASC` 从仓储流式读取，将资金、盈亏比、行业与整手约束转换为建议订单并回写对应 signal。 | `backend/modules/quant_strategy/application/position_planner.py` | 无需在内存聚合全部 BUY，仍严格按全局分数排序生成不自动下单、不写持仓的建议订单；既有 `AI/position/position_manager.py` 仅服务 AI 研究选股旧路径。 |

| `QuantStrategyService（领域服务）【新增】` | 新建 `backend/modules/quant_strategy/`（ORM/仓储/DTO/服务），实现创建、草稿编辑、发布和归档状态机。 | 新增 `backend/modules/quant_strategy/` | 仅已发布版本可被任务引用，已发布版本不可原地修改。 |

| `QuantTaskSubmissionService（提交服务）【新增】` | 用同一 SQLAlchemy `Session` 锁定策略版本、组合与持仓，冻结策略/组合/持仓进 `execution_snapshot`（行情不冻结，执行时实时读取）后暂存 task/outbox。外层是唯一 commit/rollback 边界。 | `backend/modules/analysis/`、`backend/modules/investment_workspace/` | 策略/组合校验、task、outbox 任一失败均不留半成品；提交秒级返回，不感知 LangGraph。 |

| `AllMarketUniverseBuilder（执行期读模型）【新增】` | 每次执行开始时从 `market.instrument` 枚举、排序活跃 CN 股票；不按板块、相对强度或成交额预过滤。 | 新增 `backend/modules/analysis/infrastructure/quant_execution_market_data.py`、`db/instrument/` | 每次执行（含 retry、rerun）使用当时的活跃名单；该对象不是 Graph 节点。 |

| `MarketContextBatchLoader（执行期行情读取器）【新增】` | 按 200 票批次参数化批量读取 `instrument_daily`/`factor_daily`，构造每票 250 根 bars 的 StrategyContext。 | 新增 `backend/modules/analysis/infrastructure/quant_execution_market_data.py` | 消除 N+1；行情每日批处理采集后日内基本静态，扫描中途修订概率极低。 |

| `Analysis Worker wiring（Worker 接线）【修改】` | 从任务快照构造仅闭包持有源码的 `SandboxRunner`、`ExecutionControl` 和 backend `QuantExecutionService`，量化任务绕过图直接执行；每次执行/重跑新建服务实例。 | `backend/workers/wiring.py`、`backend/workers/analysis_executor.py` | Worker 不读取当前策略或组合，源码不进入 checkpoint/artifact；进度、取消、fencing 由 `ExecutionControl` 承担。 |

| `Industry ingestion（采集模块）【新增】` | 采集 SW2021 一级行业及成分，完成校验后原子刷新行业成员。 | `db/instrument/ingest/industries.py` | 为行业仓位上限提供可用性门控的数据基础。 |

#### 数据表与迁移

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |

|----------|----------|----------------|--------------|

| `quant_strategies（表）【新增】` | `0008` 由本任务创建：稳定 ID、名称、描述和 version（元数据乐观锁）；ORM/索引与服务契约一致。 | `backend/migrations/versions/0008_quant_strategy_and_portfolio_risk.py`、`backend/modules/quant_strategy/` | PostgreSQL 保存可维护的量化策略实体。 |

| `quant_strategy_versions（表）【新增】` | `0008` 由本任务创建：版本、状态、源码、source_hash、唯一约束和 partial unique DRAFT 索引；`0009` 增加不可变 `published_at`/可选 `archived_at`，用于版本审计和报告。 | `backend/migrations/versions/0008_quant_strategy_and_portfolio_risk.py`、新增 `backend/migrations/versions/0009_quant_execution_signals.py` | 支持草稿、发布、归档和任务快照审计。 |

| `portfolios（表）【修改】` | `0001` 已有 `name`/`version`；`0008` 新增总资产、可用现金及单笔/总仓位/单票/行业风控字段；本任务接通服务、DTO 与任务快照。 | `backend/modules/investment_workspace/` | 平台组合具备资金与风险预算，历史组合迁移后默认不产生 BUY。 |

| `quant_execution_signals（表）【新增】` | `id` 为表级自增主键；以 `(task_id, attempt_no, id)` 归属执行批次，分页保存全部 BUY、持仓信号、审计错误及其建议订单字段，不保存非持仓正常 HOLD；`signal_kind` 判别行类型，复合索引 `(task_id, attempt_no, score DESC, ts_code, id)` 支撑 cursor，FK 随 task CASCADE。完整列清单见 4.3 节信号保留矩阵。 | `backend/migrations/versions/0009_quant_execution_signals.py` | 全市场结果按 attempt 隔离后完整展示且不把无界数组塞入 state/artifact，`id ASC` 可作稳定游标排序兜底。 |

| `market.ingest_state（表）【新增】` | 以 `(resource, source)` 主键记录行业成员最后成功验收与最近失败观测（单行混存成功/失败字段，互不覆盖）。 | 新增至 `db/instrument/schema.sql` 幂等 DDL（无独立迁移器，老环境重跑 init_schema 生效） | 行业 BUY 门控只接受真实 POC/刷新验证通过且未过期的成员集。 |

#### DTO

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |

|----------|----------|----------------|--------------|

| `QuantStrategy DTO（DTO）【修改】` | 定义策略、版本、发布与归档的请求响应字段和状态校验；区分可返回源码的草稿编辑 DTO 与永不返回源码的列表/审计/报告 DTO。 | `backend/modules/quant_strategy/application/contracts.py`、`backend/api/schemas/` | 前端获得稳定的策略管理数据合同。 |

| `Portfolio DTO（DTO）【修改】` | 扩展组合资金风控字段与 `expected_version` 并发校验。 | `backend/api/schemas/` | 客户端可安全读取和更新组合账户参数。 |

| `ReportDTO.quant_execution（DTO）【修改】` | 将 `decision["quant_execution"]` 映射为无源码的结构化报告字段。 | `backend/api/schemas/` | 报告 API 返回策略审计、信号、订单和告警。 |

#### API

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |

|----------|----------|----------------|--------------|

| `quant strategies router（API）【新增】` | 提供策略、版本发布和归档接口及状态错误码。 | `backend/api/routers/` | 前端可管理策略及版本。 |

| `portfolios router（API）【修改】` | 暴露组合资金风控字段与原子版本校验。 | `backend/api/routers/` | 客户端可更新组合账户参数。 |

| `reports router（API）【修改】` | 输出 `ReportDTO.quant_execution` 及 task/report scoped signal cursor endpoint。 | `backend/api/routers/` | 客户端取得无源码量化摘要，并按当前或指定 attempt 分页读取完整量化结果。 |

### frontend

#### 页面与路由

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |

|----------|----------|----------------|--------------|

| `/ai/strategies（路由）【新增】` | 注册策略管理的懒加载路由与导航入口。 | `frontend/src/routes/` | 用户可从 AI 区域进入策略管理。 |

| `QuantStrategiesPage（页面）【新增】` | 展示策略列表、当前版本状态和操作入口。 | 新增 `frontend/src/modules/analysis/` 下策略页面 | 用户可浏览、创建、发布和归档策略。 |

#### 业务与交互模块

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |

|----------|----------|----------------|--------------|

| `StrategyEditorDialog（Dialog）【新增】` | 编辑策略元数据和受限脚本，展示校验错误。 | 新增策略页面组件 | 用户可保存草稿并获得语法/安全反馈。 |

| `StrategyVersionList（组件）【新增】` | 展示版本历史并触发发布、归档操作。 | 新增策略页面组件 | 用户可追溯策略版本与状态。 |

| `PortfolioSettingsDialog（Dialog）【新增】` | 原子编辑组合资金和风险预算字段。 | `frontend/src/modules/watchlist/` | 用户可配置总资产、现金与仓位约束，冲突时重新拉取。 |

| `AnalysisTaskForm（表单）【修改】` | 选择仓位层时加载已发布策略和组合，条件提交三项快照参数。 | `frontend/src/modules/analysis/` | 用户只能以完整有效的策略和组合创建量化任务。 |

#### 数据访问与状态

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |

|----------|----------|----------------|--------------|

| `quant strategy queries（查询模块）【新增】` | 基于生成的 API client 封装策略、版本、组合与量化 signal cursor 查询/变更。 | `frontend/src/modules/analysis/`、生成的 API client 消费层 | 页面有一致的缓存、加载、错误与变更后失效行为，并能按 attempt 安全翻页。 |

#### 报告消费与展示

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |

|----------|----------|----------------|--------------|

| `QuantExecutionPanel（组件）【新增】` | 消费 `ReportDTO.quant_execution` 并展示策略审计、信号、拒绝原因、订单和告警。 | `frontend/src/modules/analysis/` | 与 AI 精选并列展示，明确“需人工确认、未下单”的建议订单。 |

## 四、详细设计

### 4.0 模块总览

| 维度 | 问题 | 方案概览 |

|------|------|---------|

| 4.1 策略版本与受限执行 | 直接 `exec` 前端脚本可执行 `import os` 或死循环。 | 策略领域控制版本/发布；AST 白名单与短生命周期子进程隔离运行。 |

| 4.2 组合账户与任务快照 | 平台组合无资金字段，旧仓位层读取 `data/portfolio.json`。 | 原子更新组合；提交时冻结策略/组合/持仓快照（行情执行时实时读取）并经任务 state 传递无源码运行输入。 |

| 4.3 全市场目标集、行情与订单 | 当前仅板块候选股、block 清空循环、行业成分表可能为空、仓位层仅买入。 | 全部活跃 CN 股票+持仓去重；执行时批量实时读取行情与有界并发策略执行；报告展示全部量化 BUY，订单仍经风险门控。 |

| 4.4 API、报告与前端 | API 未返回 `decision`，前端没有策略路由、组合账户编辑或订单面板。 | 强类型 API/错误码/OpenAPI；前端通过生成 client 实现管理、选择和结构化展示。 |

| 4.5 验证与安全回归 | 未覆盖 sandbox、原子快照、block 卖出、资金裁剪和源码泄漏。 | 分层确定性测试和受控 E2E，不运行真实 LLM 测试。 |

### 4.1 策略版本与受限执行

#### 4.1.1 模块设计

新增 `backend/modules/quant_strategy/`（domain/application/infrastructure）及可被 backend `QuantExecutionService` 调用的纯执行内核 `AI/strategy_sandbox/`：

- `QuantStrategy`：名称、描述、version（元数据乐观锁，沿用 portfolios/watchlists 的 `version` 惯例）；名称唯一。

- `QuantStrategyVersion`：`version_no` 自 1 递增，`(strategy_id,version_no)` 唯一；状态仅为 `DRAFT/PUBLISHED/ARCHIVED`。`0008` 由本任务创建，包含这两个表、状态 CHECK、源码长度/哈希约束和 partial unique DRAFT 索引。

- `quant_strategies.version` 只保护策略名称/描述，`quant_strategy_versions.version` 只保护草稿源码（乐观锁列统一 `version`，与 `portfolios.version` 一致；`version_no` 是语义版本号，两者区分）；**不声明不存在的当前草稿指针**，当前草稿由 partial unique index 的唯一 DRAFT 查询。创建策略同时创建 v1 `DRAFT`；partial unique index 仅保证每策略**至多一个** DRAFT，create/publish 的同一事务保证正常业务路径始终至少一个可编辑 DRAFT。发布使用 `WHERE id=:id AND status='DRAFT' AND version=:expected_version`，经共享 `validate_strategy_source()` 校验后在同一事务将旧草稿置 `PUBLISHED`、写入不可变 `published_at` 并插入下一版 DRAFT；新 DRAFT 插入失败必须 rollback，使旧草稿仍为 DRAFT。 同一策略允许多个历史 PUBLISHED，任务可选择任一未归档发布版。仅 `DRAFT → PUBLISHED`、`PUBLISHED → ARCHIVED` 有效；只允许归档 PUBLISHED，禁止归档 DRAFT，且至少保留一个 PUBLISHED。ARCHIVED 不可逆且不能被新任务引用；已发布/被历史任务引用版本不可修改或物理删除，未删除历史任务从其快照重跑。草稿读取/保存 DTO 是唯一可返回 `source_code` 的 API；列表、发布审计、任务、报告和错误 detail 一律不含源码。`0009` 增加 `published_at`/`archived_at`，禁止用会被归档更新的 `updated_at` 代替发布时间。

- API：`GET/POST /api/v1/quant-strategies`、`GET /api/v1/quant-strategies/{id}`、`PUT /api/v1/quant-strategies/{id}/draft`、`POST /api/v1/quant-strategies/{id}/versions/{version_id}/publish`、`POST /api/v1/quant-strategies/{id}/versions/{version_id}/archive`；非法转换返回 `STRATEGY_VERSION_INVALID_STATE`（409）。

策略唯一顶层定义为 `def strategy(context):`。JSON `context` 固定为：

```json

{

  "meta": {"symbol":"600519.SH","effective_trade_date":"2026-09-15","bars_count":250,"price_basis":"raw"},

  "ohlcv": {"trade_date":["..."],"open":[0],"high":[0],"low":[0],"close":[0],"volume":[0],"amount":[0]},

  "indicators": {"ma_bfq_5":[null],"ma_bfq_20":[null],"rsi_bfq_6":[null]},

  "position": {"shares":0,"average_cost":null,"market_value":0}

}

```

数组严格按交易日升序，`[-1]` 是截至有效交易日的最新值；OHLCV 与指标数组同长度，因子缺失用 `null` 对齐，绝不前填。策略不获取 DataFrame、DB/Provider/LLM、环境变量或完整 LangGraph State。

AST 默认拒绝，统一纯函数 `validate_strategy_source(source) -> list[StrategyValidationIssue]` 供草稿预校验、发布和 runner 复用，返回稳定 `code/message/line/column`；明确允许：一个函数、`Assign/AugAssign/If/Return`、有限常量、命名局部变量、已知路径下标、算术/比较/布尔/条件表达式，以及仅为返回结果构造的 `ast.Dict`。结果字典必须且只能含固定字符串键 `action/score/entry_price/stop_loss/take_profit/sell_ratio/reason`，禁止 `**` 解包、动态键、重复键和嵌套可变容器。可访问 context 路径仅为 `meta.symbol/effective_trade_date/bars_count/price_basis`、`ohlcv.{trade_date,open,high,low,close,volume,amount}[<常量整型下标>]`、`indicators.{ma_bfq_5,ma_bfq_20,rsi_bfq_6}[<常量整型下标>]` 与 `position.{shares,average_cost,market_value}`；只允许常量整型下标（含负数），禁止变量下标、slice 和其他键。`Call` 仅允许无关键字参数的裸名称 `abs/min/max/round/isfinite`，其中 `isfinite` 是受控 builtin。禁止 Attribute（故任何属性逃逸都失败）、Import、循环/推导、lambda、嵌套函数、try/raise/with、容器推导、动态 key、未知 name 和包含 `__` 或前导 `_` 的标识符。限制：源码 12KiB、800 AST 节点、64 语句、6 层 if、300 根 bars 与 4KiB 输出。

运行器调用 `sys.executable -I` 一次性子进程：空 cwd、最小环境、`close_fds=True`、JSON stdin/stdout、stdout 上限 4KiB、300ms wall-clock；Unix 额外 `RLIMIT_CPU=1s`、`RLIMIT_AS=128MiB`、`RLIMIT_FSIZE=0`/低 NOFILE，Windows 记录 `RESOURCE_LIMIT_DEGRADED` 且仍 kill 超时进程。子进程只注入空 builtins 和五个安全函数。AST 不是恶意代码的强隔离；本地单用户 V1 用它防误用，未来多租户必须切换 `--network none`、只读根文件系统和无凭据容器。

草稿/发布校验失败返回 `STRATEGY_VALIDATION_FAILED`（422，保留客户端文本）；已发布快照若 source_hash 或再次 AST 校验不匹配则任务以不可重试 `STRATEGY_SNAPSHOT_INVALID` 失败。策略逐票超时、子进程崩溃或协议/业务输出错误只转该票 HOLD；Worker 级 runner 装配、数据库或 artifact 基础设施故障沿既有重试分类进入任务级 retry。策略输出必须是上述七键字典。`action∈{BUY,SELL_ALL,SELL_PARTIAL,HOLD}`、`score∈[0,100]`、`reason` 为最多 240 字符且无控制字符的纯文本，非有限数值/超长/控制字符均无效。BUY 必须 `0<stop_loss<entry_price<take_profit` 且 `sell_ratio=null`；SELL_ALL 必须有持仓、全部价格字段和 `sell_ratio` 均为 `null`；SELL_PARTIAL 必须有持仓、价格字段均为 `null` 且 `0<sell_ratio<1`；HOLD 的四个交易字段均为 `null`。任何异常、超时、非法输出、行情或因子不足均转 `HOLD`，附 `EXECUTION_TIMEOUT/INVALID_OUTPUT/INDICATOR_UNAVAILABLE/DATA_UNAVAILABLE`，不终止其他标的。

#### 4.1.2 三方依赖能力评估

- 标准库 `ast/subprocess/resource` 可支持语义白名单、协议和 macOS/Linux 限额；Windows 以 wall timeout 的明确降级语义运行，不能声称同等内存隔离。

- `pandas>=2.2` 只在宿主对齐 DAO 数据；策略端永不接收 pandas。

- 不新增三方依赖；指标来自 `market.factor_daily`，不在策略临时计算。

#### 4.1.3 风险与验证方式

单测允许合法均线脚本，拒绝 import/属性/循环/动态调用；覆盖子进程崩溃、超时、输出过大、非法数字和后续执行未被污染。以唯一源码 sentinel 断言它仅在任务 `request_params`，不出现在 state、checkpoint、执行日志、`AnalysisReport.report_json`、`decision`、artifact `final_state.json`/`report.json`、SSE/Redis payload 或 API 响应。

#### 4.1.4 文件变更清单

- **新建**：`backend/modules/quant_strategy/`（ORM/仓储/DTO/服务）、`backend/migrations/versions/0008_quant_strategy_and_portfolio_risk.py`（本任务创建，含 portfolios 风控列，见 4.2）、`AI/strategy_sandbox/`、策略路由/schema、策略测试。

- **修改**：`backend/main.py`（路由注册）、DI/wiring、新增 `0009` 迁移、`backend/api/exception_handlers.py`（量化 DomainError 映射）。

### 4.2 组合账户与任务快照

#### 4.2.1 模块设计

创建组合接受完整账户配置（也接受默认参数）；`PATCH /portfolios/{id}` 以**一个原子请求**更新名称与全部账户字段：

```json

{"name":"核心仓","total_assets":100000,"available_cash":35000,"risk_per_trade_pct":0.01,"min_risk_reward_ratio":2,"max_total_position_pct":0.8,"max_single_stock_pct":0.1,"max_sector_pct":0.3,"expected_version":7}

```

该请求一次条件更新、成功仅使 version 加 1；持仓 upsert/delete 沿用同一 revision 序列。校验为：`0<=cash<=assets`、各比例 `(0,1]`、`single<=total`、`sector<=total`、`min_risk_reward_ratio>0`。V1 没有完整资产负债表，故定义 `total_assets` 为风控分母、`available_cash` 为唯一可买现金上限；不强制它等于持仓估值加现金，报告将该未计入部分标为 `unallocated_assets`，不把它释放为可买现金。服务使用 Decimal；DB 对非负和比例区间设 CHECK，跨字段关系由服务复验。前端 409 时同时重新拉取组合及其持仓。

`MarketWideCreateRequest` 增加可空的 `strategy_version_id/portfolio_id/expected_portfolio_version`：

- `position in selected_layers`：`strategy_version_id/portfolio_id/expected_portfolio_version` 三者必填；策略必须 PUBLISHED、组合 revision 必须相等。AI 层（market/sector/screening/stock）按存量契约可选、可与 `position` 同选——同选时 AI 层照旧独立运行，量化链路不读取其产物（候选池/risk_gate），两者互不影响。量化链路始终扫描执行时实时枚举的全市场活跃 CN 股票，不依赖板块层是否已选择、短名单是否为空。

- `position not in selected_layers`：三者必须省略/null，非空返回 `TASK_CREATE_INVALID`，避免“落库未生效”。

`QuantTaskSubmissionService` 是跨聚合的唯一提交编排器。API bundle 创建一个 `QuantTaskSubmissionUnitOfWork(session)`，将**同一个** SQLAlchemy `Session` 显式传给 strategy、workspace 与 analysis task/outbox 仓储；不得复用现有各自新建 session 的 UoW。服务先 `SELECT ... FOR UPDATE` 锁定策略版本、Portfolio 与持仓所属组合，复验 `PUBLISHED`、`expected_portfolio_version` 与层级组合；接着在该事务内将策略版本源码、组合与持仓序列化进 canonical `execution_snapshot`（**不做行情冻结**——不枚举 universe、不读取 OHLCV/因子，行情在执行时实时获取，见 4.3.1），构造完整 canonical envelope、预计算 `input_hash` 并暂存 task/outbox，最后仅由外层 `session.commit()` 提交。`TaskService` 新增不提交的 `stage_create_task(command, request_params, input_hash)` 内部入口：**不得调用 `_hash_input()`**，只做入参校验、按预计算 hash 进行幂等查询/比对并暂存 task/outbox；原 `create_task()` 为普通任务兼容地先构造其完整 canonical 输入、调用该入口后提交。`analysis_options` 使用同一 canonicalizer：对象递归 key 排序、数组保持原顺序、UUID/Decimal/日期时间按后述标准归一，不支持的值在写入/计算前拒绝。发生唯一幂等键 `IntegrityError` 时必须先 rollback，再以干净 Session 按键读取既有 task：完整 hash 相同返回 replay，不同返回 `IDEMPOTENCY_KEY_REUSED`。任何校验、canonicalize、hash、JSONB 归一、outbox 插入或提交失败均 `rollback()`，不得留下 task/outbox。快照版本为 `quant_execution_snapshot_v1`，形状：

```json

{

  "schema_version":"quant_execution_snapshot_v1",

  "strategy":{"strategy_id":"uuid","version_id":"uuid","version_no":2,"source_code":"...","source_hash":"..."},

  "portfolio":{"id":"uuid","name":"核心仓","version":7,"total_assets":"100000.0000","available_cash":"35000.0000","risk":{}},

  "positions":[{"market":"CN","symbol":"600519.SH","quantity":"100.0000","average_cost":"1500.0000"}]

}

```

**行情不冻结**：不建 universe/市场上下文快照表。任务创建后目录状态、日线 close 或因子被修订，不影响已提交任务的 `execution_snapshot`（策略/组合/持仓快照不变），但重跑按执行时的最新行情与目录重算（用户不回测、只按条件选股，接受该语义，见已确认决策第 10 条）。

外部 `MarketWideCreateRequest` 只提交 `strategy_version_id/portfolio_id/expected_portfolio_version`，绝不接收 snapshot 或源码；提交服务将策略/组合/持仓快照放入扩展 `CreateAnalysisTaskCommand.request_params`。canonical snapshot 递归按 key 排序：UUID→字符串、Decimal→固定量化字符串、日期/时间→UTC ISO-8601，`source_hash` 对原 UTF-8 源码计算。幂等散列输入固定为 `canonical_task_input_v1`：`task_type`、`ticker`、`requested_trade_date`、语义稳定排序的 `selected_layers`、规范化 `analysis_options` 与 canonical `execution_snapshot` 全部纳入。提交服务在同一 Session 内生成该完整 envelope 和预计算 `input_hash`；`stage_create_task(command, request_params, input_hash)` 只能使用此 hash，禁止再次降级为仅散列 snapshot。故同幂等键下任一任务字段、策略、持仓或版本变化均返回 `IDEMPOTENCY_KEY_REUSED`，仅完整 envelope 相同才重放；行情与目录不参与幂等（重放任务不重扫行情）。

源码传递采用**非 State 依赖注入**，量化任务不经 LangGraph：`AnalysisExecutor` 在 claim 后创建 `ExecutionControl(task_id, lease_token, is_cancelled, is_fencing_active)`，其协议固定为 `raise_if_inactive()`、`register_process(popen)`、`unregister_process(popen)` 与 `terminate_all()`；heartbeat/fencing 更新和取消轮询只更新该对象。量化执行分支调用 `QuantExecutionServiceFactory(claimed_task, execution_control) -> QuantExecutionService`，从 `task.request_params.execution_snapshot` 重建每次执行专属的 SandboxRunner 与执行服务，随后**直接执行（不经图）**；普通 AI 任务沿既有图路径不变。runner/control 只能由构造参数闭包持有。服务内部的 `BoundedSandboxExecutor` 在提交 future 前后和等待 future 时调用 `raise_if_inactive()`；创建子进程时通过 `register_process()` 登记，完成后 `unregister_process()`。失活时停止提交新任务、调用 `terminate_all()`、等待全部已登记进程回收并丢弃未持久化批次结果：POSIX 以新 session/process group 启动并 `killpg`，Windows 终止直接子进程并记录 `PROCESS_GROUP_TERMINATION_DEGRADED`。`quant_execution_service`、其内部 runner 与 DB connection 均不可被放到日志 payload、logger extra 或异常文本。逐票结果先写 signal repository，最终报告仅写摘要。Worker 在 `AnalysisExecutor._persist_artifact()` 从 claimed snapshot 提取原始 `source_code`，显式作为 `forbidden_source_code` 传入 artifact/checkpoint/SSE/log 序列化边界共用的 guard；guard 递归拒绝完整 `execution_snapshot` 对象、键名（大小写折叠）为 `source_code`/`execution_snapshot` 的任意值和完整 64 位 source_hash，任意字符串须拒绝**等于或包含**完整源码（含 JSON 解码/转义后文本）。sandbox 错误只输出稳定 code/message，不携带源码、AST dump 或 stdin。允许固定 12 位不可逆 `source_hash_prefix` 供报告审计。命中抛继承 `FatalAnalysisError` 的 `ArtifactSourceLeakError(code="ARTIFACT_SOURCE_LEAK_DETECTED")`，由既有 `classify_error()` 落为不可重试任务失败，不以 `INTERNAL_ERROR` 收口，并拒绝发布。取消/失租测试必须断言所有已登记 sandbox PID 退出、后续批次未启动、未持久化批次没有 signal/order 行。重跑时从同一任务 JSONB 重建新的 backend `QuantExecutionService`（策略/组合/持仓快照不变），行情与 universe 按执行时最新数据重新获取——重跑语义为「同一策略与组合快照 × 当前行情」重算。终态任务可重跑，删除任务不可重跑；策略归档不影响未删除历史任务重跑。既有 AI 任务及其 checkpoint/rerun 契约继续有效；量化任务重跑时，AI 层若同选则按存量规则重演。契约测试必须搜索 task 以外的 checkpoint、执行日志、`final_state.json`、`report.json`、SSE/Redis payload 与 API 响应。

#### 4.2.2 三方依赖能力评估

SQLAlchemy/psycopg 已支持 Numeric/JSONB/事务；采用项目已验证的 JSON round-trip。`TaskService` 仍是 task+outbox 唯一写入口，提交服务不能跨独立 UoW 写数据。

#### 4.2.3 风险与验证方式

覆盖组合/持仓并发 revision、双 Session 同幂等键同输入 replay/异快照冲突、策略发布与提交并发、旧组合迁移、任务+outbox+execution_snapshot 原子性、草稿策略/过期组合/层级非法（position 未带策略/组合）、hash/幂等变化与重跑；同一 `Idempotency-Key` 分别只修改 task_type、ticker、requested_trade_date、selected_layers、analysis_options 或任一快照字段时必须断言 `IDEMPOTENCY_KEY_REUSED`，仅完整 envelope 相同允许重放（行情与目录变化不触发冲突）。任务输入允许源码 sentinel，其他所有持久化和 HTTP 投影禁止该 sentinel；另构造嵌入式、JSON 转义、异常包装、日志 payload sentinel 验证共用 guard。

#### 4.2.4 文件变更清单

- **新建**：`backend/migrations/versions/0009_quant_execution_signals.py`（`quant_execution_signals` 表 + 策略版本 `published_at`/`archived_at` 审计列）、量化提交服务及跨聚合同 Session 测试。

- **修改**：`backend/api/schemas/workspace.py`、`backend/api/routers/portfolios.py`、workspace model/contracts/service/repository；`backend/api/schemas/tasks.py`、`backend/api/routers/analysis_tasks.py`、`analysis/application/contracts.py`、`task_lifecycle.py`（stage_create_task + canonical hash）、`analysis/application/errors.py`（ArtifactSourceLeakError）、analysis schema/router；`backend/workers/analysis_executor.py`（量化执行分支 + 传入 forbidden_source_code）、worker wiring、`artifact_store.py`（源码泄漏防御）。

### 4.3 全市场目标集、行情与建议订单

#### 4.3.1 模块设计

量化通道不调用 `portfolio_loader.py`，也不读取 AI 的 `candidate_stock_pool` 或 `risk_gate`（本方案不接 AI 层）；AI 层若与 `position` 同选则照旧独立运行，两者互不读取。`QuantExecutionService` 由 Worker 直接调用执行以下运行期流程，不经 LangGraph 图；不把源码、完整 universe/OHLCV、全量 signal/order 写入日志或进度载荷。`AllMarketUniverseBuilder.list_active_cn_stocks()` 在**每次执行开始时**以单次流式只读查询从 `market.instrument` 枚举 `instrument_type='stock' AND list_status='L' AND ts_code ~ '^[0-9]{6}\\.(SH|SZ|BJ)$'` 的全部 `ts_code`，按 `ts_code` 升序；不使用板块短名单、相对强度或成交额预过滤。每次执行（含 retry、rerun）都是当时的活跃名单——用户不回测、只按条件选股，接受结果随目录/行情变化（见已确认决策第 10 条）。universe 摘要（`universe_as_of`、`universe_total`、枚举合同版本，执行时生成）只用于运行期进度与 `quant_execution` 摘要，不进入日志或报告全量载荷。`build_quant_targets(universe, snapshot.positions)` 将实时枚举的全市场与提交时冻结的持仓按规范 `ts_code` 去重，同一票标注 `is_existing_holding/shares/average_cost`。持仓中无效代码保留 `INVALID_INSTRUMENT_CODE`，不会使全市场扫描中断。目标控制流固定：

```text

universe = AllMarketUniverseBuilder.list_active_cn_stocks()   # 执行开始时实时枚举

holdings = snapshot.positions                                 # 提交时冻结的组合持仓

hold_targets = all normalized CN holdings

scan_targets = dedupe(universe + hold_targets)

for batch in chunks(scan_targets, 200):

    contexts = MarketContextBatchLoader.load_batch(batch, effective_trade_date)  # 实时批量读取 instrument_daily/factor_daily

    execute data-complete contexts through BoundedSandboxExecutor(max_workers=8, execution_control)

    persist BUY / holding signal / auditable error rows, then release batch contexts

所有批次执行完毕后：

    PositionPlanner 从 quant_execution_signals 按 score DESC, ts_code ASC, id ASC 流式读取当前 attempt 的可行动 signal

    → 回写 paged BUY signal rows 的 accepted/rejected BUY orders 与 sell orders

```

风险门控本方案不接线：`risk_gate` 输入缺省（`null` = 不门控），报告 `warnings` 标注「风险门控未启用（AI 层后续接入）」；`BUY_REJECTED_RISK_GATE` 拒绝码与 `caution` 折半逻辑在枚举/DTO 预留，接 AI 层任务时启用。每只数据完备标的均执行策略并展示其 BUY 信号，资金/现金/仓位/行业裁剪照常。持仓仍可形成 SELL_ALL/SELL_PARTIAL 订单。单票行情不足、因子不足、sandbox 超时或非法输出仅记录该票状态，不能中断其他批次。`execution_control` 在每批开始/结束及 future 完成时检查 cancel/fencing lease：取消或失租立即终止未开始任务、杀死全部在途子进程组、丢弃未持久化批次输出并停止后续扫描；每批仅发布不含源码/完整标的列表的进度。

执行期的行情读取 `MarketContextBatchLoader.load_batch(ts_codes: tuple[str, ...], effective_date: date, lookback: int=250)` 每批参数化 `= ANY(:codes)` 查询 `market.instrument_daily`/`factor_daily`，以任务已校正的 `effective_trade_date` 为上界，按 code/date 升序；每票固定取 250 根 bars，少于 250 根该票记 `DATA_UNAVAILABLE`（执行时判定，不落快照）；三项 V1 合同指标 `ma_bfq_5/ma_bfq_20/rsi_bfq_6` 任一缺失按行填 `null`，策略实际访问字段在对应窗口全不可用时才为 `INDICATOR_UNAVAILABLE`。DB 连接绝不进入 State 或子进程。组合持仓上限为 500 条：提交服务锁定后检测第 501 条，返回 `PORTFOLIO_POSITION_LIMIT_EXCEEDED`；全市场扫描不以标的总数拒绝任务，而以 200 code 为固定批次读取和释放上下文。扫描每批完成策略执行及写入结果后立即释放该批上下文；量化路径不调用旧 `stock_loop.py`，不得填充 `stock_results`；artifact 与内存结果列表均不得容纳全市场 bars 或无界信号。所有批次完成后，`PositionPlanner` 才从当前 attempt 的 signal 表按 `score DESC, ts_code ASC, id ASC` 做一次全局有序的流式读取并回写订单字段，禁止按批预扣额度。执行器固定至多 8 个在途子进程，任一子进程 300ms wall-clock。最新持仓估值首选 `effective_trade_date` 当日 close；任一持仓无 close 时 `average_cost` **仅作为展示降级**，该票不为任何 BUY 释放总/单票/行业额度，整体新 BUY fail-closed，`total_position_pct=null` 并警告 `STALE_POSITION_VALUATION`。

代码归一不复用 `StockUtils.normalize_code()` 的 SH/SZ 启发式：全市场枚举仅接受 `market.instrument` 已存的六位数字 `.SH/.SZ/.BJ` `ts_code`；快照持仓若无后缀，通过 `market.instrument` 中 `instrument_type='stock'` 的唯一六位前缀反查，恰一行才转其真实 `ts_code`。无匹配、多匹配、非法后缀均生成该持仓 `INVALID_INSTRUMENT_CODE`，不执行策略；全市场与持仓以归一后的 `ts_code` 去重。

行业风险桶固定 `source="SW2021"`。新增 `market.ingest_state`（PK `(resource, source)`；`status`、`successful_at`、`coverage`、`member_hash`、`failure_code`、`summary_json`、`observed_at`）及 DAO；DDL 写入 `db/instrument/schema.sql`（幂等 `IF NOT EXISTS`，`db.py init_schema` 可重复执行，老环境重跑即生效——`db/instrument/migration/` 没有 DDL 迁移器，不为其发明新机制），不得混入 backend Alembic；单行混存最近成功验收与最近失败观测，成功事务只写成功字段、失败短事务只写失败字段，互不覆盖。BUY 门控谓词固定为 `status='SUCCESS' AND successful_at >= now()-interval '8 days' AND coverage>=0.95 AND member_hash` 与当前 `industry_member` 集合 hash 相等；冷启动、上次成功已过期、无成员或最近集合不匹配都为 `INDUSTRY_BUCKET_UNAVAILABLE`。新增结构化 Provider 接口 `BaseStockDataProvider.get_industry_members_df(industry_index_code: str) -> DataFrame | None`（默认 `None`）及 Tushare 同签名覆写：实际调用代理 `index_member(index_code="801010.SI", fields="index_code,con_code")`，只接受可归一至 CN 股票的 `con_code`。此接口是**部署前 POC 门禁**：提供不进入自动 pytest 的 `python -m db.instrument.ingest.industries --poc`，以真实 `TUSHARE_TOKEN` 记录代理 31 个一级行业的请求、返回字段、空/重复响应、限流和间隔，并把成功标准（31 次请求均成功、字典匹配、覆盖率≥95%、无活跃票多归属）和结果摘要写入 `market.ingest_state(resource='industry_member', source='SW2021')`。未获得成功状态即不启用行业上限 BUY，首发保留旧集合并以 `INDUSTRY_BUCKET_UNAVAILABLE` fail-closed，绝不以官方文档代替实测。新增 `db.instrument.ingest.industries.collect_industries`：先拉 `index_classify(src='SW2021', level='L1')`，要求它与现有 31 条 seed 的 `(source, industry_code, name)` 完全一致；`801010.SI` 仅在 Provider 请求边界补后缀、写库/匹配统一规范为 `801010`；任何字典漂移即拒绝 member 替换。随后全部 31 个 `index_member` 拉入内存，`con_code` 先归一为 CN `ts_code` 再按 `market.instrument` 中 `instrument_type='stock' AND list_status='L'` 校验唯一归属、覆盖率≥95%；任一行业请求失败、空响应或活跃票多归属即先 rollback 成员替换事务，再用**新的短事务**只更新 `failure_code/summary_json/observed_at`，不得覆盖旧 `successful_at/member_hash/coverage`；旧成功成员集合保持不变。全部成功后在**一个事务**内 `DELETE FROM market.industry_member WHERE source='SW2021'`、upsert 新成员、按成员数更新 `market.industry.count`、写入成员 hash/coverage/successful_at 后 commit。提供 `python -m db.instrument.ingest.industries --refresh`，为现有 `collect_incremental(..., refresh_industries: bool | None)`、CLI 和 `daily_job.step_collect_market()` 同名接线，默认周一周刷；新上市的暂未覆盖票、行业表为空或 ingest_state 无最后成功验收时 BUY 被 `INDUSTRY_BUCKET_UNAVAILABLE` 拒绝，SELL 继续允许。绝不把未知行业按零暴露绕过上限。

BUY 必须：

\[

0 < stop\_loss < entry\_price < take\_profit

\]

\[

\frac{take\_profit-entry\_price}{entry\_price-stop\_loss} \ge min\_risk\_reward\_ratio

\]

\[

shares_{risk}=\left\lfloor\frac{total\_assets\times risk\_per\_trade\_pct}{entry\_price-stop\_loss}\div100\right\rfloor\times100

\]

持仓估值为 `effective_trade_date` 当日 close；买入成本估值 `order_cost_price=max(entry_price, effective_close)`，因此不会以低于建议入场价的 close 虚增可买额度。定义 `existing_market_value=Σ(quantity×close)`、`unallocated_assets=total_assets-available_cash-existing_market_value`；`unallocated_assets<0` 写 `PORTFOLIO_VALUE_INCONSISTENT` 并拒绝全部新 BUY，已有市值超过 `total_assets×max_total_position_pct` 写 `PORTFOLIO_ALREADY_OVER_LIMIT` 并拒绝全部新 BUY。非 CN 持仓在提交期返回 `PORTFOLIO_UNSUPPORTED_HOLDING`，不得静默忽略。每笔买入按固定顺序计算：信号校验 → 风险手数 → 现金/总仓位/单票/行业上限取最小值 → 整手向下取整 → 用最终股数及 `order_cost_price` 重算 notional/风险并再次断言全部上限。每一余量先扣按规范 `ts_code` 和 `{source:"SW2021",industry_code}` 聚合的已有持仓市值与已接受 BUY，不把建议卖出所得计作现金。`total_assets` 只作总/单票/行业风险分母，`available_cash` 单独限制每笔及累计 BUY，未分配资产不参与可买现金。`caution` 使总仓位上限乘 0.5；`block` 只拒 BUY。拒绝项保存结构化原因而非只输出汇总文本。

信号保留矩阵固定为：非持仓正常 HOLD 只计入 summary；全部 BUY 分页保存并含 `order_status`；持仓的 HOLD/SELL/错误全量分页保存；已接受建议订单作为其对应 BUY/SELL signal 的订单字段持久化，不另建重复行；买入拒绝只作为对应 BUY 的 `order_status`，不得在另一 signals 集合重复；非持仓数据/执行错误保存错误码计数和每码至多 100 个按 `ts_code` 排序 sample。`quant_execution_signals.id` 为表级自增主键；行以 `signal_kind` 判别（BUY/持仓信号/错误样本）。**完整列清单**：`id` BIGSERIAL PK、`task_id` FK CASCADE、`attempt_no` INT、`signal_kind`、`ts_code`、`action`、`score` NUMERIC(5,2)、`reason` VARCHAR(240)、`entry_price`/`stop_loss`/`take_profit` NUMERIC(18,4)（仅 BUY 行有值，SELL/HOLD 恒 NULL）、`sell_ratio` NUMERIC(8,6)（仅 SELL_PARTIAL 有值）、`order_status` VARCHAR(32)、建议订单列组 `shares`/`notional`/`order_cost_price`/`valuation_price`/`risk_bucket`（PositionPlanner 回写，仅 ELIGIBLE 及卖出订单行有值）、`error_code` VARCHAR(64)（错误样本行）、`created_at`；复合索引 `(task_id, attempt_no, score DESC, ts_code, id)` 支撑 cursor。结果按 attempt 隔离：默认读取任务最新成功 attempt，也允许显式 `attempt_no`；cursor 查询强制 `WHERE task_id=:task_id AND attempt_no=:attempt_no`。「最新成功 attempt」判定 = `analysis_reports` 存在该 attempt_no 的报告行（artifact 持久化即成功边界）；失败/取消 attempt 残留的已持久化批次行由读取层按该谓词排除，不物理删除。BUY cursor 固定以 `(score DESC, ts_code ASC, id ASC)` 排序并编码这三个值，持仓审计/订单以各自唯一稳定排序键编码，禁止复用仅支持 `(datetime, UUID)` 的任务列表 cursor。错误 sample 为 `ts_code ASC`。该 endpoint 提供所有可审计行及完整建议订单；`ReportDTO.quant_execution` 只放摘要、统计、首 50 条 BUY 预览、首 50 条持仓信号预览和首 50 条建议订单预览，禁止无界数组。`SELL_ALL` 卖出现有全部数量（含零股）；`SELL_PARTIAL` 为 `floor(shares*ratio/100)*100`，结果不足一手则不生成订单并标 `SELL_PARTIAL_REJECTED_LOT_SIZE`；无持仓卖出信号为 `SELL_REJECTED_NO_POSITION`。不写 `portfolio_positions`，不接券商，不把卖出建议净额结算到同批买入。

#### 4.3.2 三方依赖能力评估

`instrument_daily`/`factor_daily` 现有 DAO 已升序；执行期以 200 code 批次参数化批量读取消除 N+1。行情每日 08:30 批处理采集后日内基本静态，扫描（分钟级）中途被修订的概率极低、影响单票，可接受。`market.instrument` 已是全市场本地目录，流式只读枚举不依赖 Tushare 区间端点，规避代理全市场区间拉取静默截断。行业字典已种子化但行业成员未采集，故新增采集、持久化 ingest_state 与代理端点 POC 是行业仓位 BUY 门控的前置交付，不以“表存在”假定数据可用。LangGraph 仅编排背景与量化入口；策略/订单保持可测的纯函数和有界进程执行器。

#### 4.3.3 风险与验证方式

测试全市场活跃 CN 枚举（过滤非 stock、退市、非 CN 格式，按 `ts_code` 稳定排序）+持仓去重（含 `.BJ`、无后缀唯一反查、非法 suffix/多匹配）、任务创建后新增/退市标的和修订 close/因子并 rerun 时按最新行情/目录重算（策略/组合快照不变）、量化路径未调用 `stock_loop.py`/写入 `stock_results`、风险门控缺省不门控（warnings 标注，拒绝码预留）、position 未带策略/组合的请求被拒绝、单票错误隔离、行业 POC 成功/失败状态、行业空/重复/缺失、行业全量刷新 rollback/差集删除/覆盖率门控、停牌/250 根窗口不足/因子缺行、200-code 分批与至多 8 个在途 sandbox、扫描第 N 批取消/失租和无僵尸子进程、全量信号落库后按 `score DESC, ts_code ASC, id ASC` 全局排序规划订单、6,000 标的全 BUY/全错误压力下峰值内存与 attempt 隔离 cursor 分页无漏项、全市场 HOLD 不落 artifact、风险/现金/总/单票/行业余量、非 CN 持仓、负 `unallocated_assets`、entry 与 close 偏离、整手与零股；执行后断言组合持仓无写入。

#### 4.3.4 文件变更清单

- **新建**：执行期 `AllMarketUniverseBuilder` 与 `MarketContextBatchLoader`（`backend/modules/analysis/infrastructure/quant_execution_market_data.py`）、backend `QuantExecutionService`/`PositionPlanner`、按 attempt 隔离的 `quant_execution_signals` cursor API、行业成员采集/POC/ingest_state（`BaseStockDataProvider`、`cn/tushare.py` 覆写、`db.instrument.ingest.industries`）、有界 sandbox 批次调度、target builder、signal-to-order 与测试。

- **修改**：`db/instrument/schema.sql`（`market.ingest_state` 幂等 DDL）、`db/instrument/ingest/incremental.py`（`refresh_industries` 周刷接线）、`AI/eventStudy/scheduler/daily_job.py`（同名参数接线）、`analysis_executor.py`（量化执行分支）、`artifact_builder.py`；量化路径不改造或调用 AI 层（screening/stock_loop/position_manager 等均不触碰），也不读取板块候选池。

### 4.4 API、报告与前端交互

#### 4.4.1 模块设计

稳定错误码与前端行为：`STRATEGY_NOT_FOUND`(404)、`STRATEGY_VERSION_NOT_PUBLISHED`(409)、`STRATEGY_VERSION_INVALID_STATE`(409)、`STRATEGY_VALIDATION_FAILED`(422，仅草稿校验响应可回显客户端提交源码；其他响应 detail 不含源码)、`STRATEGY_REVISION_CONFLICT`(409，可重拉)、`PORTFOLIO_SNAPSHOT_CONFLICT`(409，可重拉)、`PORTFOLIO_POSITION_LIMIT_EXCEEDED`(422)、`PORTFOLIO_UNSUPPORTED_HOLDING`(422)、`TASK_CREATE_INVALID`(422，position 未带策略/组合等入参非法)、`STRATEGY_SNAPSHOT_INVALID`(500，不重试，源码快照散列/校验不符)、`RERUN_NOT_AVAILABLE_LEGACY_CONTRACT`(409)、`INDUSTRY_BUCKET_UNAVAILABLE`（报告内拒绝原因而非任务 HTTP 错误）。全市场扫描没有标的数上限；枚举或执行期行情读取基础设施失败走既有任务级 retry，单票数据/策略失败只记录信号状态。为每项定义对应 DomainError 并登记 `backend/api/exception_handlers.py::_CODE_MAP` 的 status/retryable；API contract test 断言 code/status/retryable 与 detail 不含源码。

报告的结构化合同只有量化一个：`ArtifactBuilder` 将量化结果投影为固定大小的 `AnalysisReport.decision["quant_execution"]`（不写全量信号）；`backend/api/schemas/reports.py` 新增可空嵌套 DTO `quant_execution`；`backend/api/routers/reports.py` 对旧/损坏 decision 防御解析为 `quant_execution=null`，映射 API 顶层字段，而非额外 Markdown section。全量量化信号由新增 task/report scoped cursor endpoint 从 `quant_execution_signals` 读取：未指定 `attempt_no` 时解析任务最新成功 attempt（判定见 4.3.1），指定时必须属于该 task；cursor payload 含排序字段和 `attempt_no`，其值与请求不一致即拒绝。`quant_execution` 嵌套 DTO 包含：

- `strategy`：name、version_no、source_hash_prefix、published_at；

- `portfolio_snapshot`：name、version、total_assets、available_cash、风险参数、snapshot_at；

- `matching_buy_preview[]`：全市场中策略返回 BUY 的前 50 个 symbol、score、reason、entry/stop/take、`order_status`（`ELIGIBLE` 或拒绝码）；按 score 降序、symbol 升序，**不因风险门控隐藏**；完整列表走 cursor endpoint；

- `holding_signal_preview[]`：最多 50 条持仓卖出/拒绝/异常审计预览；完整列表走 cursor endpoint；

- `suggested_order_preview[]`：前 50 条已获风控接受的 symbol、BUY/SELL action、shares、notional、`order_cost_price`、valuation_price、stop/take、风险桶 `{source,industry_code,industry_name}`；完整订单走同一 cursor endpoint；

- `summary`：`universe_total/data_complete/scanned/buy_matches/suggested_buy_orders/suggested_sell_orders/failed_count`；另含 `warnings[]`、`valued_at`。

`quant_execution` DTO 不含 source code。旧报告的对应字段为 `null`，不渲染面板；新量化报告无订单也渲染空态和拒绝/告警。

API 改造后先执行 `python -m backend.scripts.export_openapi`，再在 `frontend/` 执行 `pnpm run generate:api`。前端不得手写服务端 DTO。

前端变更：

- `frontend/src/routes/index.tsx` 注册懒加载 `/ai/strategies`；`frontend/src/modules/analysis/pages/dashboard/AiDashboardPage.tsx` 提供入口，一级 Sidebar 保持不扩张。

- 策略页按页面/列表/编辑 Dialog/版本历史拆分，使用 React Query 与生成 Service；脚本仅等宽 textarea/纯文本展示，原因说明才走 `MarkdownView`。

- 组合页新增 `PortfolioSettingsDialog` 原子编辑账户字段，列表显示资产/现金/风险摘要和“任务仅读取提交时快照”的提示。

- `AnalysisTaskForm` 保留全部层选择；选择 `position` 时加载已发布策略/组合，未选不可提交；取消 `position` 时清空三字段；AI 层可与 `position` 同选（照旧独立运行，互不读取），idempotency snapshot 包含 strategy version、portfolio ID/version。

- OpenAPI 重导出后，生成的 `ReportDTO`/量化嵌套 model 是前端唯一类型来源；既有 `frontend/src/modules/analysis/pages/task-detail/reportMappers/toReportViewModels.ts` 的 `toReportViewModel` 将可空字段映射为 `quantExecution`。`ReportContent` 在报告元信息之后、sections 之前挂载 `QuantExecutionPanel`（非空时），不伪造 section。量化面板展示“全市场量化买点”及扫描统计，以 cursor 分页加载完整 BUY/持仓审计结果，再独立展示“建议订单（需人工确认，未下单）”；`block`/资金/仓位/行业拒绝的 BUY 仍显示在买点列表，无下单按钮。

#### 4.4.2 三方依赖能力评估

React Query、生成 client、Dialog/Card/Badge 满足需求，不新增 Monaco/CodeMirror。前端 pnpm/OpenAPI codegen 及 MarkdownView 约定保持不变。

#### 4.4.3 风险与验证方式

组件测试：策略校验/发布/冲突、组合参数边界/409、任务表单选择 `position` 并可同选 AI 层、报告旧数据/null、全市场扫描统计、空订单、BUY 命中但被资金/仓位/行业拒绝、HOLD-only、裁剪原因与 sandbox 告警、风险门控未启用标注；以 codegen 实际可空类型构造 mock。API 契约测试确认 quant_execution 被投影、全市场 BUY 不因拒绝漏展示、错误码正确且源码不泄漏。

#### 4.4.4 文件变更清单

- **新建**：策略页面和查询、`frontend/src/modules/watchlist/portfolios/PortfolioSettingsDialog.tsx`、量化报告面板、按 attempt 隔离的信号 cursor API/queries 与测试。

- **修改**：`backend/api/schemas/reports.py`、`backend/api/routers/reports.py`、应用 ReportDTO、`backend/openapi/openapi.v1.json`、`backend/api/exception_handlers.py`；前端 `frontend/src/routes/index.tsx`、`frontend/src/modules/analysis/pages/dashboard/AiDashboardPage.tsx`、task form/queries、watchlist queries/list、`frontend/src/modules/analysis/pages/task-detail/reportMappers/toReportViewModels.ts`（`toReportViewModel`）、`ReportContent`、queryKeys、`frontend/src/api/generated/`。策略 router 固定 `tags=["quant-strategies"]`，先执行 `python -m backend.scripts.export_openapi`，再于 `frontend/` 执行 `pnpm run generate:api` 后按实际生成的 Service 名消费。

### 4.5 验证与安全回归

#### 4.5.1 模块设计

测试分层：sandbox AST/协议/wall-time（操作系统相关 resource 限制单独标记）、策略服务、组合聚合、任务原子快照、批量行情/订单、信号 cursor 分页/6,000 标的压力、API/OpenAPI、组件与受控 E2E。fixture 表必须为每个关键合同写明输入表行、任务 snapshot、执行前后 DB 变更与断言：修订目录/行情后 rerun 按最新数据重算、双 Session 幂等竞争、position 未带策略/组合、非 CN 持仓、entry/close 偏离、取消/失租、嵌入式源码泄漏。先按经验规则查 `real_llm/real_toolkit`，只跑本任务的 fake fixture 测试，不运行真实 LLM。

#### 4.5.2 三方依赖能力评估

现有 pytest/pytest-asyncio、Vitest、Playwright 足够；使用固定 JSON/Pandas/fake graph，不用 Tushare/LLM。

#### 4.5.3 风险与验证方式

- 策略/组合快照任一校验失败时断言没有 task/outbox；成功时断言 execution_snapshot、outbox task_id、hash 一致；创建后修改目录、close、因子并 rerun 按最新行情重算（策略/组合快照不变）。

- 唯一源码 sentinel 仅见 task request JSONB，不能见 checkpoint、执行日志、`AnalysisReport.report_json`、decision、artifact `final_state.json`/`report.json`、SSE/Redis payload 或 API；任意键承载源码、完整 snapshot、敏感键/完整 source_hash、嵌入式或 JSON 转义源码均触发 `ARTIFACT_SOURCE_LEAK_DETECTED`，12 位 `source_hash_prefix` 必须可正常落盘和展示。

- 后端单测/API 契约、前端 lint/typecheck/test 通过；受控 E2E 断言全市场 BUY 命中、被风控拒绝的买点与可建议订单均可见、cursor 翻页无漏项，且 `portfolio_positions` 未变化。

#### 4.5.4 文件变更清单

- **新建/修改**：`backend/tests/unit/quant_strategy/`（发布后恰一 DRAFT、新 DRAFT 插入失败回滚、`QuantExecutionService`/`PositionPlanner` 唯一业务实现）、`backend/tests/integration/quant_strategy/`（跨 Session 原子提交、完整 canonical envelope 幂等竞争/冲突、rerun 按最新行情重算、错误码与删除后不可重跑）、`backend/tests/unit/analysis/test_artifact_store.py`（artifact 源码 sentinel/嵌入式/转义/敏感对象拒绝与 `source_hash_prefix` 放行）、`tests/strategy_sandbox/`、`tests/agents/position/`、信号 cursor 的 attempt 隔离/稳定编码/分页压力测试、`frontend/e2e/task-flow.spec.ts` 与相关组件测试；只运行本任务 fake fixture 目录，先排除 `real_llm`/`real_toolkit` 文件。

## 五、已确认决策 / 待确认问题

### 已确认决策

1. 决策链路固定为“用户策略脚本是唯一选股逻辑：全市场枚举 → 逐票沙箱执行 → 命中票按 score 排序 → 资金/仓位/行业风控裁剪 → 建议订单（不自动下单）”。AI 层（market/sector/screening/stock）本方案不改造、不依赖，统一接入留待后续任务。

2. 策略为版本化、受限 `strategy(context)`，已发布版本不可原地修改。

3. 仅输出 `BUY/SELL_ALL/SELL_PARTIAL/HOLD` 与建议订单，不自动下单、不改持仓。

4. 组合保存总资产、可用现金、单笔风险、最低盈亏比、总/单票/行业上限。

5. 默认值：风险 1%，最低盈亏比 1:2，总仓位 80%，单票 10%，行业 30%。

6. V1 仅支持 CN 股票和 100 股整手；行业使用 SW2021，行业缺失时 BUY fail-closed、SELL 继续。

7. 量化通道与存量 AI 通道互不读取：量化不消费 `candidate_stock_pool`、`risk_gate` 或 LLM `confidence`；同任务同选 AI 层时 AI 层照旧独立运行。报告只输出量化结果。

8. 量化策略执行、signal 持久化和订单规划只有 backend `QuantExecutionService` 一份实现；量化任务由 Worker 直接调用执行，不经 LangGraph 图。

9. 本地单用户 V1 使用 AST 白名单和一次性子进程；全市场扫描以 200 标的批次和至多 8 个在途子进程执行，报告保存量化 BUY、卖出、拒绝和错误，不保存全量 HOLD；多租户/不可信脚本上线前必须升级容器隔离。

10. 用户不回测、只按条件选股：行情与全市场目录**不在提交时冻结**，每次执行（含 retry/rerun）在执行时实时枚举 universe、批量读取行情；仅策略/组合/持仓随任务提交冻结进 `execution_snapshot`。因此不建 universe/市场上下文快照表（`quant_execution_universe_snapshots`/`quant_execution_context_snapshots` 从方案中删除），重跑语义为「同一策略与组合快照 × 当前行情」重算，报告以 `valued_at` 如实标注执行时刻。回测需求出现时再恢复行情冻结。

11. 本方案先走通 frontend + backend 全链路，AI 层暂不接入：量化任务不经 LangGraph、由 Worker 直接执行；风险门控 `risk_gate` 缺省不门控（报告 `warnings` 标注，`BUY_REJECTED_RISK_GATE`/`caution` 在枚举/DTO 预留）；AI 接入（market 层门控传入、命中票深度研究、screening 统一为策略脚本）留待后续方案。

### 待确认问题

无。全量结果分页、组合估值、行业刷新状态、取消回收、源码防泄漏、OpenAPI/codegen 随迁均已纳入本任务的实现范围和可验证合同；行情冻结经用户确认「不回测、只按条件选股」后改为执行时实时读取（决策第 10 条）；AI 层接入与风险门控接线留待后续方案（决策第 11 条）。
