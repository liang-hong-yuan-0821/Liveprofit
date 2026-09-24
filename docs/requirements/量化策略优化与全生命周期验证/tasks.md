# 量化策略优化与全生命周期验证 任务清单

> **状态**：`实现中`（2026-09-24）
> **进度**：0/8 任务
> **下一步**：继续 T1 的元数据、覆盖与上游核验；当前仅完成快照基础层。
> **关联方案**：[plan.md](plan.md)

## 任务总览

| 编号 | 任务 | 依赖 | 状态 |
|---|---|---|---|
| T1 | 数据与快照 | — | 进行中 |
| T2 | 策略契约与 76 组候选 | T1 | 待开始 |
| T3 | 统一执行与三档风险 | T1、T2 | 待开始 |
| T4 | 账本对账与生命周期重放 | T1、T2、T3 | 待开始 |
| T5 | 研究验证与准入 | T1、T2、T3、T4 | 待开始 |
| T6 | 每日账户流水线 | T1、T3、T4、T5 的准入接口 | 待开始 |
| T7 | API 与前端闭环 | T4、T5 的接口、T6 | 待开始 |
| T8 | 验证、Code Review 与交付 | T1–T7 | 待开始 |

## 验收执行约定

- 下列命令均为**后续验收命令，尚未执行**。命令中的“拟新增”文件必须先按任务交付创建，不能以不存在的测试路径声称已通过。
- Python 命令从仓库根执行；前端使用 `pnpm -C frontend`。运行前确认 `.venv` 及依赖可用，集成测试连接隔离测试库和专用 Redis，禁止清理真实账户数据。
- 测试前用 `rg -l "real_llm|real_toolkit" tests backend/tests` 检索真实依赖；只运行本任务定向测试。AI相关用例使用 `-k "not integration"`，任何命中的真实LLM测试文件额外排除。已核验隔离、没有真实LLM依赖的backend数据库集成测试单独运行，不附该过滤表达式，以免把需要验证的integration目录也排除。
- 新增 schema 后先导出 OpenAPI、再生成客户端、再修改前端消费；不手工修改生成客户端。费用、数据和时间夹具固定，不以实时外部接口作为单测依赖。
- 任务范围以 [plan.md](plan.md) 的详细设计及文件清单为准；本文件只拆交付与验收，不另立设计。

## 任务

### T1 数据与快照

- **目标**：建立统一价格基准、历史可投资范围与按字段/证券检查的完整水位；交付可追溯的不可变研究数据集。
- **涉及文件**：修改 `AI/dataflows/providers/base_provider.py`、`AI/dataflows/providers/cn/tushare.py`、`db/instrument/ingest/`、`db/instrument/dao/`、`backend/modules/market_data/infrastructure/refresh_repository.py`、`backend/modules/analysis/infrastructure/quant_execution_market_data.py`；拟新增 `backend/modules/quant_research/application/dataset_builder.py`、`backend/modules/quant_research/infrastructure/dataset_store.py` 与版本元数据、增量迁移。
- **依赖**：无。
- **验收标准**：
  - [ ] 执行 `.venv/Scripts/python.exe -m pytest backend/tests/unit/quant_strategy/test_data_readiness.py backend/tests/unit/market_data/test_quant_refresh_repository.py backend/tests/unit/market_data/test_quant_refresh_ingest.py -q -k "not integration"`，覆盖缺字段、停牌、暖机不足、覆盖率和统一水位。
  - [ ] 拟新增 `backend/tests/unit/quant_research/test_dataset_builder.py`，执行对应 pytest，验证未来数据删除不改变过去输入、退市证券未被现存名单排除、快照发布失败不能标 READY、校验和不符不能读取。
  - [ ] 用独立参考序列对照股票与 ETF 指标、复权转换和公司行为；记录上游端点真实签名、返回字段、排序及日期覆盖。网络连接失败单列，不据此认定端点无能力。
  - [ ] 人工抽查快照清单：版本、来源、截止时间、覆盖缺口、校验和及持久目录齐全；历史 ST/退市/行业/交易规则缺口会阻止收益认证；目录不受短期任务产物清理约束。
- **状态**：`进行中`（2026-09-24）。已新增研究快照构建与Parquet原子发布/校验层；5个新用例与既有T1定向测试合计32个通过。PG元数据、历史覆盖与上游实测尚未完成，验收项不勾选。

### T2 策略契约与 76 组候选

- **目标**：扩展单票与组合目标合同，冻结版本输入和生命周期政策；登记旧策略 28 个与新增家族 48 个预注册配置。
- **涉及文件**：修改 `AI/strategy_sandbox/protocol.py`、`strategy_contract.py`、`validator.py`、`runner.py` 及 `backend/modules/quant_strategy/domain/templates.py`、`application/service.py`、`application/contracts.py`、`infrastructure/models.py`；拟新增 `backend/modules/quant_strategy/domain/portfolio_targets.py` 与相应测试。
- **依赖**：T1。
- **验收标准**：
  - [ ] 执行 `.venv/Scripts/python.exe -m pytest backend/tests/unit/quant_strategy/test_strategy_templates.py backend/tests/unit/quant_strategy/test_strategy_validator.py tests/strategy_sandbox -q -k "not integration"`，验证默认/边界源码、输入字段闭合、旧七键规范化读取及非法输出拒绝。
  - [ ] 拟新增 `backend/tests/unit/quant_strategy/test_portfolio_targets.py`，运行对应 pytest；验证组合权重、有效期、冻结版本、入场区间与 FIXED_TARGET/TRAILING/RULE_BASED 退出政策，不以虚构止盈价满足 2R。
  - [ ] 预注册清单可枚举且恰为 `7×2×2 + 12×4 = 76` 个配置；信号与管理政策分别标记，原缺陷版本仅用于诊断，不参加候选优选。
  - [ ] MACD 恒真加分、圆弧底冻结颈线、均值回归 `close < MA5` 与退出优先均有可触发且可反证的夹具；新版本不改变旧发布源码及历史报告。
- **状态**：`待开始`（2026-09-24）。

### T3 统一执行与三档风险

- **目标**：所有目标统一进入订单规划器，按真实账户资金及冻结风险档计算容量、预留和可交易数量。
- **涉及文件**：修改 `backend/modules/quant_strategy/application/execution.py`、`position_planner.py`、`execution_constraints.py`、`portfolio_risk.py` 及对应 DTO、信号持久化与测试；拟新增同模块 `domain/instrument_rules.py`、`infrastructure/instrument_rule_models.py` 和规则迁移。
- **依赖**：T1、T2。
- **验收标准**：
  - [ ] 拟新增 `backend/tests/unit/quant_strategy/test_position_planner.py`；执行 `.venv/Scripts/python.exe -m pytest backend/tests/unit/quant_strategy/test_execution_constraints.py backend/tests/unit/quant_strategy/test_portfolio_risk.py backend/tests/unit/quant_strategy/test_position_planner.py -q -k "not integration"`，验证三档、三种资金规模、费用、现金、持仓/行业/家族风险与可卖量；测试对象为backend统一规划器，不以旧AI规划器测试替代。
  - [ ] 构造生命周期加仓、同票多策略、部分成交、未完成订单和并发重跑，断言只产生一份归属和资金预留，所有新增风险都经过同一门禁。
  - [ ] 用入场区间上界及最不利 tick、滑点、费用验算风险；T+1 全天成交额改变不得改变开盘模拟容量，容量只依赖 T 日已知 ADV20。
  - [ ] 覆盖节假日、停牌、涨跌停、跳空、T+1、ETF 品种差异、零股及固定目标成本后拒单；退出减仓不被只针对新增风险的准入和熔断阻断。
- **状态**：`待开始`（2026-09-24）。

### T4 账本对账与生命周期重放

- **目标**：真实成交、资金流、账户快照、公司行为和更正形成独立事实，数量、现金、估值与生命周期可以审计重放。
- **涉及文件**：修改 `backend/modules/investment_workspace/application/portfolios.py`、`contracts.py`、`infrastructure/models.py`、`repositories.py`，以及 `backend/modules/quant_strategy/application/lifecycle_service.py`、`position_lifecycle_manager.py`、`infrastructure/lifecycle_models.py`；拟新增 `investment_workspace/application/account_ledger.py`、`reconciliation.py`、`valuation.py` 及账本模型与增量迁移。
- **依赖**：T1、T2、T3。
- **验收标准**：
  - [ ] 执行 `.venv/Scripts/python.exe -m pytest backend/tests/unit/quant_strategy/test_position_lifecycle_manager.py backend/tests/integration/quant_strategy/test_lifecycle_service.py backend/tests/integration/investment_workspace/test_watchlists_portfolios.py -q`，使用经核验的隔离数据库、无真实LLM夹具验证真实成交推动数量与成本，日行情推动观察及保护价；确认集成用例实际被收集执行。
  - [ ] 拟新增 `backend/tests/integration/investment_workspace/test_account_ledger.py` 与 `test_reconciliation_valuation.py`，运行对应 pytest；验证入账基线、资金出入、费用、分红送转及差异处理可重放，份额化净值不把入金算成收益。
  - [ ] 注入部分成交、首笔成交撤销、跨日撤单、更正与重复请求；日事实按修订/替代关系保留历史且仅一个 current，观察天数按有效日期去重。
  - [ ] 人工经旧持仓/现金编辑入口操作，观察只能进入初始化、对账或有原因的调整；导入快照差值不能伪造交易，实际超范围成交仍照实入账并标偏离。
  - [ ] 停牌或跌停连续未退出时，次日 HOLD 不取消退出意图；每个持仓读取自身冻结策略版本，除权前后保护原价映射不触发机械假止损。
- **状态**：`待开始`（2026-09-24）。

### T5 研究验证与准入

- **目标**：交付共同回放内核、滚动样本外评估、最终留出、统计和资格管理；软件能力与长周期模拟观察分别验收。
- **涉及文件**：拟新增 `backend/modules/quant_research/application/replay.py`、`evaluation.py`、`infrastructure/models.py` 及研究/模拟持久化模块、测试；拟新增 `backend/modules/quant_strategy/application/strategy_admission.py`；修改策略版本资格投影与相关服务。
- **依赖**：T1、T2、T3、T4。
- **验收标准（软件与历史研究）**：
  - [ ] 拟新增 `backend/tests/unit/quant_research/test_replay.py`、`test_evaluation.py`、`test_admission.py` 与 `backend/tests/integration/quant_research/`；执行 `.venv/Scripts/python.exe -m pytest backend/tests/unit/quant_research backend/tests/integration/quant_research -q`，使用固定数据、经核验的隔离库及无真实LLM夹具验证回放、统计、持久化及资格隔离；确认集成用例实际执行。
  - [ ] 历史/模拟/每日执行在相同输入下目标一致；模拟事实不写真实账户；删除未来数据不改变过去选择；只有此前已经结束的验证区间可参与该时点选参和组合。
  - [ ] 以冻结快照生成研究记录：756/126/126、步长126、至少四个外层测试窗口，最后252日留出；先冻结候选、参数和资金分配，再评估留出，不根据留出结果重选赢家。
  - [ ] 日收益统一采用扣费单位净值，时间块20日、5,000次、随机种子20260924；DSR记录全 trial，分别执行交易型/配置型样本门槛，缺证据不得晋级。
  - [ ] 研究报告包含成本翻倍、延迟一天、邻近参数、去除最佳月份/证券、三档风险及10/30/100万元压力结果；允许所有候选均不通过。
- **持续观察清单（独立状态，不用于伪造软件完成）**：
  - [ ] 对历史通过的交易型版本积累至少60个交易日及30笔完整交易；配置型至少120个交易日及6次计划调仓，并逐版本记录未达原因。
  - [ ] 观察结果按净收益、回撤、账本一致性、可交易性形成“通过／失败／证据不足”结论；证据不足持续观察，不能按日历到期自动通过。
- **状态**：`待开始`（2026-09-24；软件、历史研究和模拟观察均未开始）。软件验收完成后可记录该子阶段结果；本任务完成状态须与持续观察的真实结论一致，不隐去尚未完成的观察。

### T6 每日账户流水线

- **目标**：复用现有任务、调度、outbox、重试及取消机制，按账户驱动对账、估值、生命周期和次日建议。
- **涉及文件**：修改 `backend/modules/daily_research/application/scheduler.py`、`quant_pipeline.py`、`backend/workers/dispatcher.py`、`analysis_executor.py`；拟新增 `backend/modules/daily_research/application/portfolio_pipeline.py` 及每日账户运行事实。
- **依赖**：T1、T3、T4、T5 的准入接口；不依赖候选已经取得实盘资格。
- **验收标准**：
  - [ ] 执行 `.venv/Scripts/python.exe -m pytest backend/tests/unit/daily_research/test_scheduler.py backend/tests/unit/daily_research/test_quant_snapshot.py -q -k "not integration"`，确认保留旧扫描与新闻刷新行为且不重复建设调度系统。
  - [ ] 拟新增 `backend/tests/unit/daily_research/test_portfolio_pipeline.py`，运行对应 pytest；覆盖行情迟到、账户未确认、同日重复、漏跑数日、重试、取消、成交回写后修订及同输入不同任务 ID。
  - [ ] 同业务输入 hash 幂等；数据更正产生新修订；漏跑只补观察和保护状态，不造历史成交。全市场缺数不能整体阻断数据足够的已有持仓保护。
  - [ ] 人工观察一个完整日运行：账户事实/截止时间可见，估值风险先于建议，退出减仓先于新增仓，未完成订单预留及拒绝原因完整。
- **状态**：`待开始`（2026-09-24）。

### T7 API 与前端闭环

- **目标**：承接旧任务 N7，提供研究、每日账户、对账、持仓建议、成交及版本资格可操作界面。
- **涉及文件**：修改 `backend/api/routers/lifecycle.py`、`portfolios.py`、`quant_strategies.py` 与对应 schema；拟新增研究和每日账户 router/schema。修改 `frontend/src/modules/analysis/pages/strategies/`、`frontend/src/modules/watchlist/portfolios/`、`frontend/src/modules/analysis/pages/task-detail/QuantExecutionPanel.tsx`，生成 `frontend/src/api/generated/`；拟新增研究/日建议/对账/成交组件及测试。
- **依赖**：T4、T5 的接口、T6。
- **验收标准**：
  - [ ] 执行 `.venv/Scripts/python.exe -m pytest backend/tests/contract/api/test_lifecycle.py backend/tests/contract/api/test_workspace.py backend/tests/contract/api/test_quant_strategies.py backend/tests/contract/api/test_report.py -q -k "not integration"`；新增研究/每日账户契约测试同时验证分页、修订、幂等和稳定错误。
  - [ ] 按顺序执行 `.venv/Scripts/python.exe -m backend.scripts.export_openapi`、`pnpm -C frontend run generate:api`、`pnpm -C frontend run typecheck`、`pnpm -C frontend run build`，确认生成类型与消费一致。
  - [ ] 执行 `pnpm -C frontend run test -- src/modules/analysis/pages/strategies src/modules/watchlist/portfolios src/modules/analysis/pages/task-detail/QuantExecutionPanel.test.tsx`；断言实际/可卖/目标股数、次日区间与有效期、保护政策、费用、账户/行情时间以及证据不足状态均能展示。
  - [ ] 拟新增 `frontend/e2e/portfolio-lifecycle.spec.ts`，隔离完整栈执行 `pnpm -C frontend run e2e -- e2e/portfolio-lifecycle.spec.ts`；覆盖“导入账户→查看建议→部分成交→次日建议→更正成交→退出”，同时核验页面与账本投影一致。
- **状态**：`待开始`（2026-09-24）。

### T8 验证、Code Review 与交付

- **目标**：承接旧任务 N8/N9 的未验项，完成正确性、全市场性能、取消回收、文档与真实阶段交付，观察完成后再最终归档。
- **涉及文件**：T1–T7 涉及的定向测试、性能基准、原任务引用、本任务八文件；后续实现验收通过时再更新 `docs/knowledge/` 对应领域事实，本轮不改。
- **依赖**：T1–T7；可在 T5 长期观察期间完成软件交付部分，但不提前标记整体完成。
- **验收标准**：
  - [ ] 汇总 T1–T7 实际命令与结果；检查真实 LLM 排除、隔离库、共享连接异常 rollback、不可变版本、所有写入口和新旧报告兼容，不能只勾汇总而遗漏失败用例。
  - [ ] 在隔离环境以6,000标的运行完整扫描/规划/分页，记录 P95、峰值内存、读取行数、取消/失租进程回收与无僵尸进程；与原N8基线对比。原N8数值预算尚未冻结，须先取得部署机基准和具体阈值确认记录，再判定门禁，不能声称已有预算通过。POSIX专属进程组测试须在POSIX实测，Windows跳过不能算通过。
  - [ ] 主会话自查后按 plan 文件变更清单启动只读 subagent Code Review；修复 findings 并记录复核结果，最多两轮，未通过不得标记实现完成。
  - [ ] 将软件完成、历史研究结论、模拟观察状态分别写入 result；旧 N7–N9 仅以可验证证据承接，不以本轮文档生成代替验收。
  - [ ] 长期观察形成可审计结论后补齐 result/retrospective，整合知识库、核对所有任务实际状态、修正归档相对链接；届时按仓库规则只暂存本任务路径并提交。若仍证据不足则继续观察，保留未完成状态，不归档。
- **状态**：`待开始`（2026-09-24）。

## 拆分与维护规则

- 任务由已确认方案直接拆解，不另开任务清单评审；发生设计修订时同步相关任务，不静默改变预注册研究口径。
- 每完成任务立即更新任务块、总览、README 状态与 log；未执行验收保留空框。
- 当前进度0/8指实现任务进度，不把文档建立计为业务交付。
- 本轮不实现、不归档、不提交；后续任务完成后按仓库归档与显式路径提交规则收尾。
