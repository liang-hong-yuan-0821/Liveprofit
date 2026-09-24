# 量化前行情就绪编排 任务清单

> **状态**：`已完成`（2026-09-24）
> **进度**：7/7 任务
> **下一步**：任务记录已归档
> **关联方案**：[plan.md](plan.md)

---

## 任务总览

| 编号 | 任务 | 依赖 | 状态 |
|------|------|------|------|
| T1 | 统一量化活动股票目录查询 | — | 已完成 |
| T2 | 增加私有量化刷新资源与公开 API 隔离 | — | 已完成 |
| T3 | 实现四组件覆盖、冻结规格与执行操作单位 | T1、T2 | 已完成 |
| T4 | 实现量化数据采集校验、qfq fallback 与 Tushare 路由 | T3 | 已完成 |
| T5 | 接入 Worker 操作进度、恢复与刷新终态 | T3、T4 | 已完成 |
| T6 | 接入量化任务预检、deadline partial 与股票池 hash 门控 | T1、T2、T3、T5 | 已完成 |
| T7 | 完成跨链路回归、Code Review、知识文档与归档 | T1–T6 | 已完成 |

## 任务

### T1 统一量化活动股票目录查询

- **目标**：把 `AllMarketUniverseBuilder` 的活跃 CN 股票 predicate 收敛到 market DAO，作为后续覆盖仓库共用的目录查询；对应方案 §4.2.1。
- **涉及文件**：
  - 修改：`db/instrument/dao/instrument.py`（增加共用活跃 CN 股票查询/predicate）
  - 修改：`backend/modules/analysis/infrastructure/quant_execution_market_data.py`（改用共用查询）
  - 测试：DAO 与量化执行读模型单测
- **依赖**：无
- **验收标准**（全部勾选才算完成）：
  - [x] `python -m pytest backend/tests/unit/analysis/test_quant_stock_universe.py -q`（2 passed）；共享查询过滤/排序正确，执行器读模型委托 DAO。
- **状态**：`已完成`（2026-09-23）

### T2 增加私有量化刷新资源与公开 API 隔离

- **目标**：在既有刷新策略/服务中增加仅由量化准入使用的内部资源，保留原六项公开 API contract；对应方案 §4.1.1、§4.4.1。
- **涉及文件**：
  - 修改：`backend/modules/market_data/application/refresh_policy.py`、`refresh_service.py`
  - 修改：`backend/api/schemas/market_refresh.py`、`backend/api/routers/market_refresh.py`
  - 测试：refresh policy/service 与 market refresh contract 测试
- **依赖**：无
- **验收标准**（全部勾选才算完成）：
  - [x] `backend/tests/unit/market_data/test_refresh_policy.py` 与 `test_refresh_service.py`：验证 aware 时间锚定、naive 拒绝、公共 ensure 拒绝私有资源。
  - [x] `backend/tests/contract/api/test_market_refresh.py::test_public_resource_enum_stays_six_values_and_hides_quant_jobs`：六值 OpenAPI、内部请求 422、内部 job 404。
  - [x] `backend/tests/contract/api/test_market_refresh.py::test_status_is_read_only_and_empty_stock_catalog_is_blocked`：页面刷新状态仍只显示六项公开资源。
- **状态**：`已完成`（2026-09-24）

### T3 实现四组件覆盖、冻结规格与执行操作单位

- **目标**：按日线 100%、qfq/adj_factor/trade_status 各 95% 判定量化资源就绪，并冻结 universe digest、组件事实缺项和 Redis 执行操作；对应方案 §4.2.1。
- **涉及文件**：
  - 修改：`backend/modules/market_data/infrastructure/refresh_repository.py`
  - 修改：`backend/modules/market_data/application/refresh_service.py`（消费内部资源 coverage/spec）
  - 测试：refresh coverage/repository/service 测试
- **依赖**：前置任务 T1、T2
- **验收标准**（全部勾选才算完成）：
  - [x] `backend/tests/unit/market_data/test_quant_refresh_repository.py`（6 passed）：覆盖独立组件 95%、非相交缺口、可信停牌、qfq/status 单操作单位及容许缺行成功。
  - [x] `backend/tests/integration/market_data/test_refresh_coverage.py::test_quant_refresh_uses_execution_universe_and_separate_component_thresholds`：独立 market_test PG 中验证真实 SQL、四组件覆盖及与执行股票目录相同。
  - [x] `backend/tests/unit/market_data/test_refresh_policy.py`、`test_refresh_service.py`、`backend/tests/unit/analysis/test_quant_stock_universe.py` 合计 44 passed。
- **状态**：`已完成`（2026-09-24）

### T4 实现量化数据采集校验、qfq fallback 与 Tushare 路由

- **目标**：安全补齐日线、adj_factor、qfq 与状态；严格验证日期/代码/键/值，保留 qfq/status 原子事务；对应方案 §4.2.1–§4.2.3。
- **涉及文件**：
  - 修改：`db/instrument/ingest/refresh.py`、`frames.py`、`stock_factors.py`、`guard.py`
  - 修改：相关 DAO（仅有需要时；复用既有 `adj_factor.py`、`factor_daily.py`）
  - 测试：`tests/db/instrument/test_refresh_ingestion.py`、`test_refresh_ingestion_db.py`、因子/provider 测试
- **依赖**：前置任务 T3
- **验收标准**（全部勾选才算完成）：
  - [x] `tests/db/instrument/test_refresh_ingestion.py` 与 `backend/tests/unit/market_data/test_quant_refresh_ingest.py`（36 passed）：错日期、缺列、重复键、越界批次代码、截断批次、Infinity/非法布尔值和非法限价均 fail-closed。
  - [x] fake Tushare 覆盖全市场 qfq 达 6000 行后 100 代码分批、批次仍截断失败及覆盖不足拒绝写入。
  - [x] `LIVEPROFIT_DATA_SOURCE=akshare` 环境下内部量化资源显式只构造 Tushare；Tushare 无效回包不写入；公开资源 provider 路由未改。
  - [x] qfq/status PostgreSQL 集成验证两表与 ingest_state 同事务写入；非法状态验证全部回滚；采集级回归还验证可恢复写入错误触发 rollback。
- **状态**：`已完成`（2026-09-24）

### T5 接入 Worker 操作进度、恢复与刷新终态

- **目标**：让 `RefreshSpec`、worker collector、Redis 进度和租约恢复对组件事实与唯一执行操作使用一致模型；对应方案 §4.2.1。
- **涉及文件**：
  - 修改：`db/instrument/ingest/refresh.py`、`backend/workers/market_refresh.py`
  - 视实际代码需要修改：`backend/modules/market_data/application/refresh_service.py`
  - 测试：market refresh worker/isolation/coverage 集成与单测
- **依赖**：前置任务 T3、T4
- **验收标准**（全部勾选才算完成）：
  - [x] PostgreSQL 采集集成验证 qfq/status 一次组调用落为 `total=processed=completed=1`；Redis 生命周期集成验证一个 `qfq_status` 组操作的 `total=1`、心跳计数和 SUCCEEDED 终态。
  - [x] Worker 与 dispatcher recovery 统一以 `freshness == FRESH` 判 SUCCEEDED；量化 repo 集成覆盖独立 95% 门槛，公共 Worker 与 lease recovery 集成通过。
  - [x] 未达阈值保留缺项操作供冻结 spec 重跑；自动重试、手动 PARTIAL/FAILED 使用既有 Redis 终态规则。
- **状态**：`已完成`（2026-09-24）

### T6 接入量化任务预检、deadline partial 与股票池 hash 门控

- **目标**：量化任务在市场 REPEATABLE READ 快照和策略执行前确保输入就绪、时间有效、股票目录一致；缺数在 deadline 内重试，截止后落零策略 partial；对应方案 §4.3。
- **涉及文件**：
  - 修改：`backend/modules/daily_research/application/quant_pipeline.py`
  - 修改：`backend/workers/analysis_executor.py`、`backend/workers/wiring.py`
  - 测试：`backend/tests/unit/daily_research/`、`backend/tests/unit/analysis/` 相关任务/workflow 测试
- **依赖**：前置任务 T1、T2、T3、T5
- **验收标准**（全部勾选才算完成）：
  - [x] `backend/tests/unit/daily_research/test_quant_snapshot.py` 覆盖预检早于行情连接、pending 截止前 retry、到期零策略 partial、无 DB 打开、schedule/wait aware 校验、水位不一致及 universe hash 变化。
  - [x] 日常 Worker wiring 注入 shared market refresh service；手动使用 retry 准入，定时使用 auto 准入；市场数据共同水位及冻结 universe hash 在任何策略扫描前核对，使用同一股票集继续过滤候选。
  - [x] `quant_news_refresh` 不触发市场补齐，原量化父扫描和候选基分逻辑保持不变。
- **状态**：`已完成`（2026-09-24）

### T7 完成跨链路回归、Code Review、知识文档与归档

- **目标**：验证方案所有链路，完成代码审查与知识库更新，产出可审阅实施结果；对应方案 §4.4。
- **涉及文件**：
  - 测试：涉及市场 refresh、DB ingestion、每日量化及 API contract 的单元/集成测试
  - 文档：`docs/knowledge/backend/` 中 API/数据库或架构事实文档；`docs/knowledge/ai/` 中每日量化现状文档；本任务目录 8 份记录
- **依赖**：前置任务 T1–T6
- **验收标准**（全部勾选才算完成）：
  - [x] 运行相关单元/集成/契约测试；未执行 AI 集成测试。
  - [x] API contract 测试确认公开资源枚举仍为六项；`git diff --check` 通过。
  - [x] 按方案文件变更清单完成 subagent code review；两轮 minor 对齐问题已修复，审查确认 readiness 逻辑与方案一致。
  - [x] 量化预检、重试截止、无效时间与 16:00 实际行情日 pipeline 测试通过；测试覆盖与方案承诺一致。
  - [x] 更新 knowledge、README/result/retrospective 与进度，并将任务目录整体归档。共享工作树包含多个未区分来源的并行改动，本次不创建会夹带无关内容的 commit。
- **状态**：`已完成`（2026-09-24）
