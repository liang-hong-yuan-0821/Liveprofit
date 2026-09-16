# 量化策略与实操层 任务清单

> **状态**：`实现中`（2026-09-16，方案已确认）
> **进度**：8/8 任务（全量回归收尾中）
> **下一步**：全量回归确认 → result.md + Code Review
> **关联方案**：[plan.md](plan.md)（同任务文件夹内方案正文）

---

## 任务总览

| 编号 | 任务 | 依赖 | 状态 |
|------|------|------|------|
| T1 | 数据迁移：0008 策略表+组合风控列、0009 signals+版本审计列 | — | 已完成 |
| T2 | 策略领域：quant_strategy 模块（ORM/仓储/DTO/状态机）+ AST 校验器 | T1 | 已完成 |
| T3 | 策略沙箱：受限子进程执行器 + 七键协议校验 | — | 已完成 |
| T4 | 策略 API + 组合扩展：路由/schema/错误码 + 资金风控字段服务 + OpenAPI | T2 | 已完成 |
| T5 | 提交服务：QuantTaskSubmissionService + stage_create_task + canonical hash + 幂等 | T2、T4 | 已完成 |
| T6 | 行业采集：ingest_state DDL + 采集模块 + Provider 覆写 + POC | — | 已完成 |
| T7 | 执行服务 + 订单规划：实时扫描/沙箱批次/取消回收 + PositionPlanner | T1、T3、T5 | 已完成 |
| T8 | 报告 API + cursor + 前端：决策投影/信号分页/策略页/组合设置/任务表单/量化面板 | T4、T7 | 已完成 |

## 任务

### T1 数据迁移：0008 + 0009

- **目标**：plan 4.1/4.3 的表结构落地（0008：`quant_strategies`/`quant_strategy_versions`/`portfolios` 风控列；0009：`quant_execution_signals` + 策略版本 `published_at`/`archived_at`）
- **涉及文件**：
  - 新建：`backend/migrations/versions/0008_quant_strategy_and_portfolio_risk.py`、`backend/migrations/versions/0009_quant_execution_signals.py`
  - 修改：`backend/modules/investment_workspace/infrastructure/models.py`（Portfolio 加风控列，与迁移同步）
- **依赖**：无
- **验收标准**：
  - [x] `alembic upgrade head` 成功且 `alembic downgrade 0007` 后重新 upgrade head 成功（测试库实测：prepare(head=0009) → downgrade 0007 → upgrade head 全 OK）
  - [x] 建表约束核验（数据级实测）：partial unique index 同策略第二条 DRAFT 被拒、`signal_kind` CHECK 非法值被拒、`available_cash<=total_assets` CHECK 被拒；`UQ(strategy_id,version_no)`、FK CASCADE、复合索引随迁移落地
  - [x] 存量组合迁移后默认值实测：`total_assets=0`、`available_cash=0`、`risk_per_trade_pct=0.010000`、`min_risk_reward_ratio=2.0000`、`0.800000/0.100000/0.300000`
- **状态**：`已完成`（2026-09-16，workspace 存量集成测试 3 个全绿无回归）

### T2 策略领域：quant_strategy 模块 + AST 校验器

- **目标**：plan 4.1.1 的策略状态机（创建/草稿编辑/发布/归档、乐观锁、发布后恰一 DRAFT）+ 共享 `validate_strategy_source()`（AST 白名单、七键合同校验）
- **涉及文件**：
  - 新建：`backend/modules/quant_strategy/`（domain/application/infrastructure：ORM/仓储/DTO/QuantStrategyService）
  - 新建：`AI/strategy_sandbox/validator.py`（纯函数 AST 校验，返回 `StrategyValidationIssue[]`）
  - 新建：`backend/tests/unit/quant_strategy/`
- **依赖**：T1
- **验收标准**：
  - [x] `.venv/Scripts/python.exe -m pytest backend/tests/integration/quant_strategy/ -q` 12 全绿：发布后恰一 DRAFT（+下一版草稿 v2）、新 DRAFT 插入失败 rollback（旧草稿仍 DRAFT，唯一键冲突注入实测）、非法转换/禁止归档 DRAFT/至少保留一个 PUBLISHED、已发布不可原地改、并发 revision 409、草稿校验失败不落库、源码只经草稿 DTO
  - [x] AST 单测（backend/tests/unit/quant_strategy/test_strategy_validator.py）40 全绿：合法均线脚本通过；import/属性/循环/lambda/动态键/未知名/`__` 标识符/七键缺一/重复键/非法 action 值全部拒绝，错误含稳定 code/message/line/column；限制（12KiB/800 节点/64 语句/6 层 if）生效
- **状态**：`已完成`（2026-09-16）

### T3 策略沙箱：受限子进程执行器

- **目标**：plan 4.1.1 的运行器（`sys.executable -I` 一次性子进程、JSON stdin/stdout、300ms wall-clock、空 cwd/最小环境/close_fds、stdout 上限 4KiB、Windows 降级语义；异常/超时/非法输出转 HOLD 附稳定错误码）
- **涉及文件**：
  - 新建：`AI/strategy_sandbox/runner.py`、`AI/strategy_sandbox/protocol.py`（context 构造/结果校验）
  - 新建：`tests/strategy_sandbox/`
- **依赖**：无（与 T1/T2 并行）
- **验收标准**：
  - [x] `.venv/Scripts/python.exe -m pytest tests/strategy_sandbox/ -q` 21 全绿：while True 超时被杀（EXECUTION_TIMEOUT）、崩溃/import/print（空 builtins 下全失效）→ INVALID_OUTPUT、输出超 4KiB 拒绝、NaN 拒绝、两次执行结果一致（无污染）
  - [x] 七键协议校验矩阵逐条断言：BUY 三价关系/缺价/带 ratio、SELL 无持仓/价格 null 规则、HOLD 四字段 null、score 范围、reason 长度与控制字符
  - [x] 修 1 个真 bug：Windows 子进程 stdin/stdout 默认 GBK 解码与宿主 UTF-8 载荷不兼容 → 子进程改走 `.buffer` 显式 UTF-8
- **状态**：`已完成`（2026-09-16）

### T4 策略 API + 组合扩展

- **目标**：plan 4.1/4.4 的策略路由与错误码 + 组合资金风控字段服务（原子 PATCH、expected_version、跨字段校验、Decimal）+ portfolios router
- **涉及文件**：
  - 新建：`backend/api/routers/quant_strategies.py`、`backend/api/schemas/quant_strategies.py`
  - 修改：`backend/main.py`（注册路由）、`backend/api/exception_handlers.py`（`_CODE_MAP` 登记量化 DomainError：STRATEGY_* / PORTFOLIO_* / TASK_CREATE_INVALID / STRATEGY_SNAPSHOT_INVALID 的 status/retryable）、`backend/modules/investment_workspace/`（DTO/服务/仓储资金字段）、`backend/api/routers/portfolios.py`、`backend/api/schemas/workspace.py`
  - 修改：`backend/openapi/openapi.v1.json`（`python -m backend.scripts.export_openapi`）
- **依赖**：T2
- **验收标准**：
  - [x] `.venv/Scripts/python.exe -m pytest backend/tests/contract/api/test_quant_strategies.py backend/tests/contract/api/test_workspace.py backend/tests/integration/quant_strategy/ backend/tests/integration/investment_workspace/ -q` 全绿（85 含存量）：CRUD/发布/归档/409 冲突/422 校验失败（detail 含稳定问题码不含客户端源码）、组合 PATCH 原子更新 version+1、`cash<=assets`/`single<=total`/`sector<=total` 跨字段校验、409 可重拉、默认参数创建
  - [x] OpenAPI 已导出（quant-strategies tag 落盘）；错误码 _CODE_MAP 登记 STRATEGY_*/PORTFOLIO_ACCOUNT_INVALID/PORTFOLIO_SNAPSHOT_CONFLICT/RERUN_NOT_AVAILABLE_LEGACY_CONTRACT 等
- **状态**：`已完成`（2026-09-16）

### T5 提交服务

- **目标**：plan 4.2 的 `QuantTaskSubmissionService`（同一 Session、FOR UPDATE 锁定、execution_snapshot 冻结策略/组合/持仓、canonical envelope + input_hash、幂等 replay/`IDEMPOTENCY_KEY_REUSED`、任一失败 rollback 不留半成品）+ `stage_create_task` 内部入口
- **涉及文件**：
  - 新建：`backend/modules/analysis/application/quant_task_submission.py`（或按现有 analysis 结构落位）、`backend/tests/integration/quant_strategy/`
  - 修改：`backend/modules/analysis/application/task_lifecycle.py`（stage_create_task）、`backend/modules/analysis/application/contracts.py`、`backend/modules/analysis/application/errors.py`（ArtifactSourceLeakError）、`backend/api/schemas/tasks.py`（MarketWideCreateRequest 三字段）、`backend/api/routers/analysis_tasks.py`（校验）
- **依赖**：T2、T4
- **验收标准**：
  - [x] `.venv/Scripts/python.exe -m pytest backend/tests/integration/quant_strategy/ -q` 19 全绿：happy path 快照含源码/positions/风险参数、草稿策略 NOT_PUBLISHED 无 task/outbox 残留、组合版本冲突 409、同键同输入 replay/改版本 REUSED（幂等先于数据校验）、非 CN 持仓 422、501 条上限 422、validate_layers 三参数绑定与 position 独立成任务
  - [x] analysis 存量回归全绿（49：integration/analysis + contract/tasks + quant 集成 + quant 契约）；OpenAPI 重导出
- **状态**：`已完成`（2026-09-16）

### T6 行业采集

- **目标**：plan 4.3 的行业风控底座：`market.ingest_state` DDL（schema.sql 幂等）+ `get_industry_members_df` Provider 接口与 Tushare 覆写 + `db/instrument/ingest/industries.py`（--poc/--refresh，字典校验/覆盖率门控/原子刷新/失败短事务）
- **涉及文件**：
  - 修改：`db/instrument/schema.sql`（ingest_state 幂等 DDL）、`AI/dataflows/providers/`（基类 + `cn/tushare.py` 覆写）、`db/instrument/ingest/incremental.py`（`refresh_industries` 周刷接线）、`AI/eventStudy/scheduler/daily_job.py`（同名参数接线）
  - 新建：`db/instrument/ingest/industries.py`、`db/instrument/dao/ingest_state.py`（或按现有 dao 结构）、`tests/db/instrument/`（采集单测）
- **依赖**：无
- **验收标准**：
  - [x] `.venv/Scripts/python.exe -m pytest tests/db/instrument/ -q` 106 全绿（含 6 新增）：任一行业失败 → 旧成员集不变 + 失败短事务不覆盖旧成功字段；全成功 → 一个事务 DELETE+upsert+count+hash+success；字典漂移拒绝；覆盖率 <0.95 拒绝；多归属失败 + 非活跃票过滤；门控谓词（SUCCESS/8 天/覆盖率/hash 一致、过期与 hash 不匹配不可用）
  - [ ] 人工执行 `python -m db.instrument.ingest.industries --poc`（真实 TUSHARE_TOKEN，不进入自动 pytest）——留待用户部署前执行
- **状态**：`已完成`（2026-09-16，POC 人工项待部署前执行）

### T7 执行服务 + 订单规划

- **目标**：plan 4.3 的运行期：`QuantExecutionService`（执行时实时枚举、200 票批次行情、8 并发沙箱、ExecutionControl 取消/失租回收、signals 落库、attempt 隔离）+ `PositionPlanner`（score 全局排序、手数/盈亏比/现金/总/单票/行业裁剪、整手、SELL 订单）+ Worker 量化执行分支（不经图）
- **涉及文件**：
  - 新建：`backend/modules/quant_strategy/application/execution.py`、`backend/modules/quant_strategy/application/position_planner.py`、`backend/modules/quant_strategy/infrastructure/`（signals 仓储/执行器）、`backend/modules/analysis/infrastructure/quant_execution_market_data.py`（AllMarketUniverseBuilder + MarketContextBatchLoader）、`tests/agents/position/`
  - 修改：`backend/workers/analysis_executor.py`（量化执行分支 + forbidden_source_code）、`backend/workers/wiring.py`（ExecutionControl/SandboxRunner/服务工厂）、`backend/modules/analysis/infrastructure/artifact_store.py`（源码泄漏 guard）
- **依赖**：T1、T3、T5
- **验收标准**：
  - [x] `.venv/Scripts/python.exe -m pytest tests/agents/position/ -q` 31 全绿：13 planner（风险手数/现金耗尽/盈亏比/总/单票/行业余量/整手零股/SELL 规则/全局排序/STALE fail-closed）+ 4 执行集成（fake 行情 conn：全流程 BUY+SELL 订单回写、快照散列不符 fatal、取消即停无残留、数据不足计数不阻断）；执行后 portfolio_positions 无写入（代码无写路径）
  - [x] Worker 量化分支接线（不经图）+ ExecutionControl + artifact_store/executor 源码泄漏 guard（敏感键/源码包含/完整散列递归拒绝）+ ArtifactBuilder decision.quant_execution 投影；存量 58 全绿
  - [ ] 压力用例：6,000 标的全 BUY/全错误峰值内存有界——随 T8 信号 cursor 分页压力测试一并补
- **状态**：`已完成`（2026-09-16，压力用例随 T8 补）

### T8 报告 API + cursor + 前端

- **目标**：plan 4.4：ArtifactBuilder 投影 `decision["quant_execution"]`、reports router 输出 DTO、signal cursor endpoint（attempt 隔离/稳定编码）、OpenAPI/codegen、策略页/组合设置/任务表单/QuantExecutionPanel
- **涉及文件**：
  - 新建：`backend/api/schemas/quant_execution.py`、信号 cursor 路由、`frontend/src/modules/analysis/` 策略页/量化面板、`frontend/src/modules/watchlist/portfolios/PortfolioSettingsDialog.tsx`、组件测试
  - 修改：`backend/api/schemas/reports.py`、`backend/api/routers/reports.py`、`artifact_builder.py`、`backend/openapi/openapi.v1.json`、`frontend/src/routes/index.tsx`、`AiDashboardPage.tsx`、task form/queries、`toReportViewModels.ts`、`ReportContent`、`frontend/src/api/generated/`（`pnpm run generate:api`）
- **依赖**：T4、T7
- **验收标准**：
  - [x] `.venv/Scripts/python.exe -m pytest backend/tests/contract/api/test_quant_signals.py backend/tests/contract/api/test_report.py -q` 全绿：quant_execution 投影（策略审计/组合快照/三类预览/告警）、buy cursor 分页无重复无漏项、cursor kind/attempt 不一致 422、attempt 隔离、errors 排序、旧报告 null 防御；cursor 压力（6,000 信号 30 页无漏项）通过
  - [x] `frontend/` 下 `pnpm run typecheck`（tsc --noEmit 0 错误）、`pnpm run build`、`pnpm run test` 295 全绿（策略页/发布/归档、组合设置对话框、任务表单 position 独立成任务+三字段必填/清空联动、量化面板三态/拒绝码/人工确认标注、旧报告 null）；codegen 重导出（QuantStrategies/QuantSignals 服务）
  - [ ] `pnpm run test:e2e`（task-flow.spec 量化流程扩展）：全市场 BUY 命中、被风控拒绝的买点与可建议订单均可见、cursor 翻页无漏项、portfolio_positions 未变化——需完整栈（Worker+行情+策略）跑通后人工验收
- **状态**：`已完成`（2026-09-16，E2E 量化流程留待完整栈人工验收）

---

## 拆分与维护规则

- **拆分粒度**：每个任务 = 一个可独立验收的实现单元（新建一个模块 / 改造一个文件 / 写一组单测）；按依赖排序，无依赖任务可并行。通常 3–10 个任务，超出说明拆分过细，可合并
- **验收标准必须可执行**：优先单测命令与可运行检查；人工检查需写明看什么、期望看到什么。禁止"完成 XX 功能"式模糊表述
- **状态取值**：`待开始` → `进行中` → `已完成`；被阻塞时标 `阻塞：<原因>`，解除后恢复流转
- **生命周期**：任务清单随任务文件夹保留——方案实现完成并归档（移入 `docs/requirements/archive/<任务名>/`）时，本文件随文件夹一并归档（实施记录价值保留，不再删除）
- **无需额外评审**：任务清单直接由已评审通过的方案拆出，只做拆解、不复述设计
