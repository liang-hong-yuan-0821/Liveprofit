# 每日投研自动流程任务清单

> **状态**：`已完成`（2026-09-23）
> **进度**：8/8 个实现任务完成
> **下一步**：代码评审及定向验收完成；混合工作区收敛后按本任务范围提交
> **关联方案**：[plan.md](plan.md)

---

## 任务总览

| 编号 | 任务 | 依赖 | 状态 |
|------|------|------|------|
| T1 | 新闻、事件判断及历史版本存储 | — | 已完成（临时 PostgreSQL 验证旧结构升级和历史数据保留） |
| T2 | 增量新闻采集与双 Agent 判新打标 | T1 | 已完成 |
| T3 | 09:00/21:00 新闻批次与可恢复调度 | T1, T2 | 已完成（部署并发/补跑待实库验收） |
| T4 | 逐事件期限预测、市场展望与兑现验证 | T1, T2, T3 | 已完成（真实行情覆盖需部署观察） |
| T5 | 全策略扫描、事件权重聚合与候选深研 | T3, T4 | 已完成（按本次范围只输出候选排名，不生成组合仓位/订单） |
| T6 | 每日研究 API、事件工作台读模型与人工触发 | T1–T5 | 已完成（API 合同、批次读模型、争议人工复核和更正均已接入） |
| T7 | 今日研究与按批次浏览的事件研究 UI | T6 | 已完成 |
| T8 | 跨链路验收、知识文档、代码审查与归档 | T1–T7 | 进行中 |

## 任务

### T1 新闻、事件判断及历史版本存储

- **目标**：按方案 2.1/4.1 建立来源原文版本与正式判断版本的持久模型，扩展既有 events 投影；验证数据库部署拓扑和事务适配后选定迁移落点。
- **涉及文件**：
  - 修改：`AI/eventStudy/db/schema.sql`、`AI/eventStudy/review/review_dao.py`、相关数据库配置与测试。
  - 新建：事件库迁移/升级脚本、`news` 与 `event_assessment` DAO/测试（按现有迁移机制落点）。
- **依赖**：无。
- **验收标准**：
  - [x] 使用临时测试库执行从旧结构到新结构的升级；原 events/assets/event_impacts 数据保留，旧事件获得确定性 legacy key。
  - [x] PostgreSQL 验证来源内容版本幂等、同一事实 revision/supersedes、assessment 操作重放、事务 rollback 与向量单次补填。
  - [x] 跨来源分别保留原文；canonical_key 与 fact revision 唯一约束阻止重复事实身份；业务新闻/判断只写 PG，不依赖 Redis。
- **状态**：`已完成`（临时 PostgreSQL 测试库完成增量 schema 升级和旧事件/统计保留验收）。

### T2 增量新闻采集与双 Agent 判新打标

- **目标**：实现方案 4.1 的原文先存、Agent A 判新、Agent B 打标、A 必审且辩论最多两轮、结构校验和争议入队。
- **涉及文件**：
  - 修改：`AI/eventStudy/collectors/event_crawler.py`、`AI/eventStudy/review/news_analysis.py`、`AI/eventStudy/review/review_dao.py`、`AI/utils/prompts.py`。
  - 新建：结构化抓取结果、判新/辩论服务与 mock LLM 测试。
- **依赖**：T1。
- **验收标准**：
  - [x] 单测覆盖转载、同 ID 改文、new/update/duplicate/irrelevant 和实体未知场景。
  - [x] 假 LLM 测试证明 A 必审、辩论轮数严格为0/1/2、两轮仍分歧为 disputed、非法输出不落 accepted。
  - [x] 来源失败、部分覆盖、空新闻三种状态在调用者返回值中可区分；持久化失败后不推进来源游标。
- **状态**：`已完成`（mock agent 测试覆盖 A 必审及 0/1/2 轮上限）。

### T3 09:00/21:00 新闻批次与可恢复调度

- **目标**：复用平台 Dispatcher/task/outbox 执行可重启、幂等的早晚新闻分析与人工新闻运行；按 Asia/Shanghai 处理睡眠补跑和迟到新闻。
- **涉及文件**：
  - 修改：`backend/workers/dispatcher.py`、`backend/workers/wiring.py`、`backend/modules/analysis/` 任务生命周期、`backend/cli.py`、`docker-compose.yml`。
  - 新建：`backend/modules/daily_research/` 调度与批次编排、隔离测试。
- **依赖**：T1、T2。
- **验收标准**：
  - [x] 固定时钟测试证明每天 09:00 与 21:00 各只创建一个 due run，双 Dispatcher 与人工双击不重复执行。
  - [x] 过期租约恢复；平台任务有限重试；截止后才首次获知的新闻不进入旧快照。
  - [x] 周末照常新闻分析，部分结果、覆盖缺口和迟到完成版本都有明确状态。
- **状态**：`已完成`（Dispatcher 准入、DB 幂等、间隔采集/研判、有限等待重试已接入；双 Dispatcher 和睡眠补跑需在部署数据库环境验收）。

### T4 逐事件期限预测、市场展望与兑现验证

- **目标**：每个新闻批次按冻结的已知事件集合生成并保存逐事件及市场 1/5/20 交易日预测，严格按 as_of 取证，并独立成熟化实际反应。
- **涉及文件**：
  - 修改：`AI/eventStudy/processing/event_study.py`、`impact_writer.py`、`scheduler/daily_job.py`、`prediction/predictor.py`、`prediction/similarity_search.py`、对应 AgentState/提示词；`backend/modules/event_study/` 与日报适配。
  - 新建：事件预测读模型、成熟化服务及测试。
- **依赖**：T1、T2、T3。
- **验收标准**：
  - [x] 固定交易日历测试覆盖周末/长假 1/5/20 期限和不完整窗口。
  - [x] 历史快照测试证明迟到新闻、后续更正和未来价格不改变旧版报告。
  - [x] 利空后反弹/利好后回落保留事实判断与价格反应两种方向；无足够事件、来源失败、个股无基准分别显示 unknown/coverage gap。
- **状态**：`已完成`（固定交易日窗口、基准收益、覆盖不足和事件方向兑现单测通过；行业/板块历史成分不可追溯时明确数据不足）。

### T5 全策略扫描、事件权重聚合与候选深研

- **目标**：绑定每个启用策略的一个发布版本，纯扫描所有策略，统一按事件证据调整候选排序，并对排名靠前的候选复用现有个股 Agent 深研链路。按用户 2026-09-23 明确的一期边界，不生成组合仓位或建议订单。
- **涉及文件**：
  - 修改：`backend/modules/quant_strategy/application/execution.py`、`infrastructure/signals.py`、`backend/modules/daily_research/application/quant_pipeline.py` 及候选 Agent 适配器。
  - 新建：`backend/modules/daily_research/` 策略聚合、打分及规划服务与测试。
- **依赖**：T3、T4。
- **验收标准**：
  - [x] 单测验证同股票多策略只生成一份候选，重复来源不重复加分，方向与半衰期计算符合方案数值例。
  - [x] 扫描阶段无建议订单/持仓生命周期副作用；缺失/部分事件证据关闭风险门控，不生成新增 BUY 建议。
  - [x] 同输入同一任务/attempt 仅生成一份候选读模型；扫描不写建议订单或改变持仓生命周期。
  - [x] 中性/多空分化事件保留为零权重证据，并显示其与“没有事件证据”的区别。
  - [x] 事件查询截断、未结构化存量事件和新闻未完成均显式报告覆盖缺口并关闭事件风险门控。
- **状态**：`已完成`（组合级持仓规划和建议订单明确留在后续独立能力，不属于一期候选排名交付）。

### T6 每日研究 API、事件工作台读模型与人工触发

- **目标**：提供批次、事件预测、候选与调度配置查询，新闻/量化两种独立手动入口，以及争议更正与有限重试。
- **涉及文件**：
  - 修改：`backend/api/routers/`、`backend/api/schemas/`、OpenAPI 与事件审核适配器。
  - 新建：`backend/modules/daily_research/` API contract/读模型/应用服务和接口测试。
- **依赖**：T1–T5。
- **验收标准**：
  - [x] API contract tests 验证 `news`/`quant`、Idempotency-Key、批次状态/分页；平台任务生命周期提供有限自动重试，人工重新运行创建新批次。
  - [x] 事件详情只返回本 run 冻结的 assessment；未纳入事件返回明确原因，不回退读取当前 events 投影。
  - [x] 导出 OpenAPI 后与 schema/client 生成结果一致，既有单事件 API 与 legacy draft_id 合同回归通过。
- **状态**：`已完成`（每日手动入口、批次读模型、争议标签人工更正、API/client 合同及相关 UI 已实现并通过定向验收）。

### T7 今日研究与按批次浏览的事件研究 UI

- **目标**：实现新闻/量化两个独立入口，以及按 09:00/21:00/人工版本查看逐事件预测的页面，移除手工录入新闻事件作为主流程。
- **涉及文件**：
  - 修改：`frontend/src/modules/event-study/pages/EventStudyPage.tsx`、`PredictionTab.tsx`、`PredictionForm.tsx`、宏观卡片跳转和 Sidebar。
  - 新建：`frontend/src/modules/daily-research/` 页面、查询 hooks、事件列表/详情组件与测试。
- **依赖**：T6。
- **验收标准**：
  - [x] 前端测试验证按日期/批次切换、09:00/21:00进度、两个按钮各自提交、运行状态及 partial/empty/failed 状态。
  - [x] 事件研究首页没有自由输入事件文本/目标资产/窗口表单；`event_id` 深链定位指定批次的持久事件。
  - [x] 争议/更正入口可见，人工修改后新旧预测版本并存；响应式页面保持事件列表/详情可读。
- **状态**：`已完成`（每日研究总览、新闻/量化独立按钮、按批次事件预测页及 event_id 定位已实现；相关页面测试和 typecheck 通过）。

### T8 跨链路验收、知识文档、代码审查与归档

- **目标**：完成端到端回归、运行说明和架构知识随迁，按任务文件变更清单完成 code review，修复 findings 并归档。
- **涉及文件**：
  - 修改：`docs/knowledge/backend/`、`docs/knowledge/ai/`、`docs/knowledge/frontend/`、任务文件夹所有过程文档。
- **依赖**：T1–T7。
- **验收标准**：
  - [x] mock Agent/collector 单测覆盖来源与判新打标；PostgreSQL 集成用例从 `news` 原文和 `event_assessment` 版本追踪到排序候选的事件证据。
  - [x] 按 AGENTS.md 排除真实 LLM fixture 后，后端/API、事件库 PostgreSQL、前端相关测试及 typecheck 通过；命令和结果写入 result.md。
  - [x] 量化报告按 API/页面读取的 `daily_research` 键落库；新闻积压清空后用原策略扫描候选幂等刷新事件权重，不重复执行策略扫描。
  - [x] 更新领域知识文档与严格历史向量经验；本轮候选刷新 R2 复核 PASS，回归覆盖 09:00 补齐、行情降级保留候选及报告区块契约。
  - [x] 归档任务文件夹；已核对并行工作区混合差异，因共享文件含其他任务改动，暂缓提交以避免误纳入范围。
- **状态**：`已完成`（实现、定向验收、代码复核和任务资料归档完成；提交待工作区收敛后按本任务范围执行）。
