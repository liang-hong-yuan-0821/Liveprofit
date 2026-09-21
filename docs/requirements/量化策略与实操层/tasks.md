# 量化策略与实操层 任务清单

> **状态**：`评审增量实施中`（2026-09-21）
> **进度**：原基础 T1–T8 已完成；维护优化 O1、O3–O5 已完成，O2 待 POSIX 验收；增量 N0 已完成，N1/N2/N8 进行中
> **下一步**：完成 N1 除权 fixture/覆盖 POC 与 N2 第一波另外两模板集成验证，再实施 N3–N7；N9 需观察期，不能即时勾选
> **关联方案**：[plan.md](plan.md)（同任务文件夹内方案正文）

---

## 任务总览

### 2026-09-19 现有代码优化（独立于生命周期新增功能）

| 编号 | 修正 | 验收 | 状态 |
|---|---|---|---|
| O1 | API/服务层新建代码必填与 UTF-8 12KiB 限额，OpenAPI/codegen 同步 | schema 与服务拒绝缺失/空白/多字节超限；客户端类型检查 | 已完成 |
| O2 | isfinite 非数值返回 False；POSIX 独立进程组；登记失败回收进程 | 子进程测试及取消回归；POSIX 专用检查须在 POSIX 运行 | 进行中：POSIX 待验 |
| O3 | 取消/失租后禁止新登记与排队执行，提交前检查、异常 rollback | 模拟 signal 已暂存后取消，断言零 commit、一 rollback | 已完成 |
| O4 | 已持仓行业未知时拒绝新 BUY，SELL_PARTIAL 仍允许 | 纯规划器风险/卖出回归 | 已完成 |
| O5 | 格式化失败不透传含源码 stderr，声明 ruff 运行依赖 | 错误边界检查及本地 ruff 可用性 | 已完成 |

### 评审修订增量（不得被原 8/8 完成记录覆盖）

| 编号 | 任务 | 依赖 | 验收重点 | 状态 |
|---|---|---|---|---|
| N0 | 修复当前执行阻断项：因子日期对齐、数据错误 sample、订单成本价后二次风险校验 | 原 T7 | 专门 loader 测试能检出全 null/错日；跳空后不会出现成本高于目标仍 ELIGIBLE | 已完成 |
| N1 | DataReadinessGate + qfq/raw 双口径 + 交易状态/复权采集 | N0 | 共同水位、错位/停牌/暖机错误码、除权日前后 fixture、按模板覆盖 POC | 进行中 |
| N2 | 十项 qfq context、strategy_contract、模板实际窗口与七模板注册 | N1 | 四向字段合同；不再统一要求 250 根；第一波三模板先通过真实 runner | 进行中 |
| N3 | ExecutionConstraintEvaluator、费用/滑点、T+1、涨跌停、ST、流动性、订单三价 | N1 | 原始信号与订单分离；成本后盈亏比、现金和可卖数量准确 | 待开始 |
| N4 | 组合开放风险、行业开放风险、熔断和未完成订单预留 | N3 | 多票同时止损压力；熔断只阻止新增风险，不阻止退出 | 待开始 |
| N5 | 生命周期策略版本、成交/订单/活跃意图/逐日事实数据模型与 API 合同 | N2、N3、N4 | 独立 lifecycle_policy_version；更正/撤销/部分成交/跨日未完成去重 | 待开始 |
| N6 | PositionLifecycleManager：首仓、确认、弱化、止损、获利减仓、MA5 三日兑现 | N5 | 七模板优先级、真实成交推进、同日/跨日幂等、缺数据 fail-closed | 待开始 |
| N7 | 报告/API/前端展示数据水位、双价格、成本、可交易性、开放风险和生命周期 | N6 | 明确实验/影子/人工建议状态；不出现收益承诺；完整 cursor 可审计 | 待开始 |
| N8 | 真实 6,000 标的全链路性能、行业/因子 POC、完整栈 E2E | N1–N7 | 数据库侧逐票窗口；冻结 P95/内存/返回行数预算；无僵尸进程 | 待开始 |
| N9 | 分三波 forward shadow 与晋级评审 | N8 | 预注册口径；成本后期望、回撤、MAE/MFE、可成交率、市场状态；未达门槛不晋级 | 待开始 |

以下 T1–T8 为原基础任务的历史记录。

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

### 维护优化 O1–O5（2026-09-20 验收记录）

- **O1 已完成**：新建策略在 API schema 与服务层均要求非空白源码，并按 UTF-8 字节限制 12 KiB；OpenAPI/codegen 已同步，后端定向回归与前端 `pnpm run typecheck` 通过。
- **O2 进行中**：`isfinite` 非数值/bool 返回 `False`、登记失败回收子进程、POSIX 子进程独立 session 的实现与自动测试均已落地；Windows 上相关回归通过，但 POSIX process-group 用例按平台跳过，部署到 POSIX 后才能勾成完成。
- **O3 已完成**：取消/失租后的进程登记、批次提交和最终订单提交均有失活检查；异常路径执行进程回收与事务 rollback，定向取消测试通过。
- **O4 已完成**：任一现有持仓缺行业归属时，新 BUY fail-closed；`SELL_PARTIAL` 等风险降低建议不被阻断，规划器回归通过。
- **O5 已完成**：格式化错误不回显可能含源码的 stderr，`ruff` 已声明为平台运行依赖，格式化契约回归通过。
- **本次复验**：`.venv\\Scripts\\python.exe -m pytest backend/tests/unit/quant_strategy/test_create_source.py backend/tests/unit/quant_strategy/test_execution_cancellation.py tests/strategy_sandbox/test_runner.py tests/agents/position/test_position_planner.py backend/tests/contract/api/test_quant_strategies.py -q` → `65 passed, 1 skipped`；跳过项仅为 POSIX 专用进程组测试。在 `frontend/` 目录执行 `pnpm run typecheck` → exit 0。

### N0 当前执行阻断项修复

- **目标**：先消除方案 C9/C10/C11 已识别的三个 P0 可信性缺陷，确保旧基础链路不会把错位因子、不可诊断的数据失败或跳空后的失真订单标为可执行。
- **涉及文件（规划）**：
  - 修改：`backend/modules/analysis/infrastructure/quant_execution_market_data.py`（按 `(ts_code, trade_date)` 等值对齐，统一日期类型）
  - 修改：`backend/modules/quant_strategy/application/execution.py`、signals 仓储（数据错误样本及稳定错误码落库）
  - 修改：`backend/modules/quant_strategy/application/position_planner.py`（以实际订单成本价再次校验三价关系、盈亏比与风险预算）
  - 新建/修改：loader、execution、planner 专项单测
- **依赖**：原 T7
- **验收标准**（全部勾选才算完成）：
  - [x] 使用日期和值均不同的日线/因子 fixture，逐日断言 MA/RSI 与 bar 同日；全 null、错日、最新日缺因子均不能调用 Sandbox。
  - [x] `WARMUP_INCOMPLETE`、`STALE_DATA`、`INDICATOR_UNAVAILABLE`/`DATA_UNAVAILABLE` 等失败样本可从 signal cursor 查询，且不会被普通 HOLD 吞掉。
  - [x] 构造当前原始价跳空高于信号 entry、接近或越过 take 的用例；成本价后二次校验失败时保留 BUY 信号、拒绝订单，不得出现成本价高于目标仍 `ELIGIBLE`。
  - [x] 现有量化执行、规划器、signal cursor 回归通过，无新增源码泄漏或持仓写入。
- **状态**：`已完成`（2026-09-21；因子日期键、错误信号落库、成本后二次三价/盈亏比/风险校验及专项回归已落地）

### N1 数据准备门禁、双价格口径与采集底座

- **目标**：落实 C1/C2/C9/C10，为每次执行固定共同市场水位，补齐股票技术因子、复权因子与交易状态采集，并建立 qfq 信号口径与 raw 交易口径的可信映射。
- **涉及文件（规划）**：
  - 修改：`AI/dataflows/providers/base_provider.py`、`AI/dataflows/providers/cn/tushare.py`（结构化股票因子/复权/交易状态接口）
  - 修改：`db/instrument/ingest/`、`db/instrument/dao/factor_daily.py`、市场 schema/增量迁移（按交易日采集、状态与版本水位）
  - 新建：`backend/modules/quant_strategy/application/data_readiness.py` 或同职责模块（`DataReadinessGate`）
  - 修改：`backend/modules/analysis/infrastructure/quant_execution_market_data.py`（qfq OHLCV/十项因子、raw execution market、数据 hash）
  - 新建/修改：采集、数据门禁、除权除息和停牌 fixture
- **依赖**：N0
- **验收标准**：
  - [x] 任务开始先得到唯一 `market_as_of_trade_date`；日线、因子、交易状态与复权版本任一全局水位未齐时整任务不执行（行业为订单层独立 fail-closed 门禁）。
  - [x] 上下文明确记录 requested/as-of/latest-bar/factor 日期和数据 hash；暖机不足、停牌旧 bar、字段空值、非有限值分别返回稳定错误码。
  - [x] 技术信号只使用同版本 qfq OHLCV/指标，订单与成交只使用 raw 价格；复权缺失/版本不一致时 BUY fail-closed。
  - [ ] 除权除息日前后 fixture 不产生机械假金叉、假突破或假止损；结构价映射回 raw 后保持严格三价关系。
  - [ ] POC 同时输出 250 日历史质量和按模板实际窗口的可执行覆盖、暖机不足、缺失代码、截断、限流与重试；未达 100% 的模板不得晋级。
- **状态**：`进行中`（2026-09-21；真实 POC：最新两日 qfq 因子覆盖约 98.95%，交易状态 100%；申万当前成员覆盖 93.62% 未过 95% 门槛，故买点保留、订单拒绝。待补除权 fixture、250 日质量报告及不足 100% 的暖机分类）

### N2 策略合同、模板注册与七套参考源码

- **目标**：落实 C3/C4 与 §4.6–§4.14，建立无业务依赖的字段合同、实际窗口门禁、不可变模板元数据和七套可验证参考策略；先验证第一波，不把 250 根误作统一资格门槛。
- **涉及文件（规划）**：
  - 新建：`AI/strategy_sandbox/strategy_contract.py`（允许路径、常量索引、七键合同）
  - 修改：`AI/strategy_sandbox/validator.py`、`AI/strategy_sandbox/protocol.py`（从唯一合同读取）
  - 新建：`backend/modules/quant_strategy/domain/templates.py`（七模板注册、参数 schema、required_fields、渲染器）
  - 修改：策略版本迁移/ORM/DTO/service、`QuantTaskSubmissionService`（冻结 template 摘要、renderer version、source SHA）
  - 新建：`backend/tests/unit/quant_strategy/test_strategy_templates.py` 及四向合同测试
- **依赖**：N1
- **验收标准**：
  - [x] 十项 qfq 指标、OHLCV 常量下标和七键输出均由 `strategy_contract.py` 唯一声明；Sandbox 不反向依赖 backend 模板模块。
  - [x] 每模板满足“源码实际读取集合 = required_fields ⊆ context 投影字段 ∩ Sandbox 允许字段”，动态下标、越界下标和未知路径发布失败。
  - [x] 七模板参数默认值、边界、相邻越界、未知/缺失键、交叉约束和规范化 JSON 均有测试。
  - [x] 七套默认/边界源码均通过真实 validator 与一次性 runner；BUY/SELL_ALL/HOLD 七键、固定原因码、分数和价格关系符合 §2.5。
  - [x] 执行资格按模板实际索引和指标暖机判断；2/6/20/41 根需求分别覆盖，上市不足记 `WARMUP_INCOMPLETE`，不统一要求 250 根。
  - [ ] 第一波 `ma_trend_cross_v1`、`trend_pullback_v1`、`volume_surge_confirm_v1` 通过真实 loader + runner 集成验证；其余模板保持实验注册态。
  - [ ] 修改注册表显示名/渲染器后，旧任务 rerun 与报告仍使用 execution snapshot 冻结摘要。
- **状态**：`进行中`（2026-09-21；七模板、模板 API/UI、不可变元数据和实际窗口已落地；真实浏览器已验证 ma_trend_cross_v1 全市场 loader+runner，另两项第一波模板及旧任务注册表变更回归待补）

### N3 订单可执行性、费用与流动性约束

- **目标**：落实 C5/C10/C11，把 Sandbox 原始信号与人工建议订单明确分层，以 raw 订单三价、交易状态、T+1、费用、滑点和流动性完成最终可执行性判断。
- **涉及文件（规划）**：
  - 新建：`OrderPriceNormalizer`、`PriceBasisMapper`、`ExecutionConstraintEvaluator`（落位于 quant_strategy application/domain）
  - 修改：`PositionPlanner`、signals/建议订单 DTO 与仓储（raw signal 三价和 normalized order 三价并存）
  - 修改：组合/任务快照合同（版本化费用、滑点、参与率与板块规则）
  - 新建/修改：低价股、涨跌停、ST、停牌、T+1、费用和成交额参与率测试
- **依赖**：N1；与 N2 可在字段合同稳定后并行收尾
- **验收标准**：
  - [ ] 信号始终保留 qfq `entry/stop/take`；订单单独保存 raw `order_entry/order_stop/order_take`，规范化不得覆盖信号原值。
  - [ ] Decimal/tick 规范化、滑点和费用后重新校验严格三价、最低盈亏比、单笔风险和现金；失败保留信号并写稳定拒绝码。
  - [ ] 停牌、ST/*ST、板块差异、涨跌停、100 股整手、T+1 可卖数量和 earliest execution date 均有 fixture。
  - [ ] 佣金最低收费、印花税、过户费、显式滑点和成交额参与率纳入数量/现金计算；费用配置版本随任务快照冻结。
  - [ ] SELL 风险降低意图在不可成交时仍保留并标明原因，不伪造已成交或已清仓。
- **状态**：`待开始`（2026-09-20）

### N4 组合开放风险、熔断与订单预留

- **目标**：落实 C12，在现有市值比例约束之外加入组合/行业开放风险、单日新增风险、回撤/单日损失熔断和未完成订单预留。
- **涉及文件（规划）**：
  - 修改：组合迁移、ORM、DTO、service 与前端配置合同（新增风险参数和净值水位）
  - 修改：`PositionPlanner`（开放风险逐单重算、行业桶、未完成订单预留、熔断）
  - 新建/修改：组合风险计算器、仓储查询与压力 fixture
- **依赖**：N3
- **验收标准**：
  - [ ] 开放风险按实际持仓到当前有效止损的损失额计算，不以持仓市值替代；缺止损/净值事实时拒绝新增风险。
  - [ ] BUY/ADD 每接受一笔后重算组合与行业开放风险，并扣除未完成买卖订单的现金、数量和风险预留。
  - [ ] 多票同方向、同一行业及相关标的同时止损压力用例不突破冻结上限。
  - [ ] 触发组合回撤、单日损失或新增风险熔断后，只阻止 BUY/ADD；SELL_REDUCE/SELL_EXIT 继续评估。
  - [ ] 组合参数采用 Decimal、乐观锁和任务快照冻结；历史任务不读取后来修改的账户配置。
- **状态**：`待开始`（2026-09-20）

### N5 生命周期数据模型、成交回写与 API 合同

- **目标**：落实 §2.6、C6–C8 的持久化前置，先冻结物理合同，再实现独立 lifecycle policy、建议订单、成交、活跃目标意图、逐日事实与人工/券商成交回写。
- **涉及文件（规划）**：
  - 修改/新建：增量迁移、quant_strategy/investment_workspace ORM 与仓储
  - 新建：lifecycle policy、suggested orders、fills、active intents、daily facts、lifecycle states、expectations、trailing stops 等实体（最终表名在本任务开始时写入 decisions.md）
  - 新建/修改：成交确认/更正/撤销/对账 application service、API schema/router、OpenAPI 与 generated client
  - 新建：迁移、并发、幂等、部分成交与跨日订单集成测试
- **依赖**：N2、N3、N4
- **验收标准**：
  - [ ] 开工前在 `decisions.md` 固定表名、FK、唯一约束、状态机、保留周期、撤销/更正/对账语义，`issues.md` 第 7/8 项不再留物理合同 TBD。
  - [ ] `lifecycle_policy_version_id` 独立且不可变；修改过参考源码的版本不得仅凭 `template_id` 自动继承生命周期。
  - [ ] 首笔/追加/部分成交、拒绝、取消、更正均可审计；只有真实 fill 在同一事务更新实际仓位、订单剩余量与 `state_version`。
  - [ ] 活跃意图跨日复用并扣除未完成订单；同一持仓/交易日/目标/原因不得重复创建建议。
  - [ ] 逐日事实保存实际消费的行情、因子、价格口径、数据时间与 hash；同持仓同日重跑复用事实。
  - [ ] downgrade/upgrade、并发 revision 冲突、事务回滚、OpenAPI 契约及 generated client typecheck 全部通过。
- **状态**：`待开始`（2026-09-20）

### N6 PositionLifecycleManager 与七模板成交后状态机

- **目标**：落实 C6–C8 与 §4.15–§4.17，实现真实成交后的首仓 50%、确认/弱化、成本与移动止损、首次获利减仓、MA5 三日兑现及最终目标仓位仲裁。
- **涉及文件（规划）**：
  - 新建：`backend/modules/quant_strategy/application/position_lifecycle_manager.py`
  - 修改：`QuantExecutionService`、`PositionPlanner`、生命周期仓储与成交回写服务
  - 新建：`backend/tests/unit/quant_strategy/test_position_lifecycle_manager.py` 及事务/并发集成测试
- **依赖**：N5
- **验收标准**：
  - [ ] 七模板首次真实成交均冻结风险容量、实际成本、初始止损、收益目标、模板/策略版本和 50% 初始目标；未成交建议不得初始化状态。
  - [ ] 固定优先级为“成本/移动止损 → 模板完全失效或 MA5 超时 → 首次获利减仓 → 模板弱化 → 模板确认加仓”，同日只产生一个最终目标。
  - [ ] 移动止损按前一交易日有效止损判断，`high_water_mark`/`active_stop_price` 单调不减；b/a/d 未显式合法配置时不启用。
  - [ ] MA5 成交日不计数，只按成交后完整对齐 bar 消耗 3 日窗口；兑现、弱化 25%、超时清仓、缺数不计日与终态不可回退均有 fixture。
  - [ ] 获利目标首次触达后目标不高于 50%；部分成交/拒绝不提前推进 `confirmation_completed`、`profit_trim_completed` 或终态。
  - [ ] 通用 `SELL_PARTIAL` 与平台目标换算为绝对目标股数并取最低目标；实际持仓扣除未完成订单后只补差额。
  - [ ] 圆弧底使用 BUY 同次上下文冻结的 `arc_neckline_price`，不得从后续滚动行情重算。
  - [ ] 重复 worker、同日重跑、并发和缺成交/行情/因子全部 fail-closed，无双卖单或状态回退。
- **状态**：`待开始`（2026-09-20）

### N7 报告、API 与前端全链路展示

- **目标**：把 N1–N6 的数据水位、双价格、可交易性、费用、开放风险、订单与生命周期完整投影到 API/报告/前端，并明确实验、影子和人工建议边界。
- **涉及文件（规划）**：
  - 修改：quant signals/持仓审计 cursor、`ArtifactBuilder`、`ReportDTO`、reports/strategies/portfolios routers 与 OpenAPI
  - 修改：`frontend/src/api/generated/`（仅由 codegen 生成）
  - 修改：策略页、组合设置、任务详情量化面板、报告 mapper 与原因码文案
  - 新建/修改：后端契约、前端组件和完整分页测试
- **依赖**：N6
- **验收标准**：
  - [ ] 报告明确展示 requested/as-of/latest-bar、qfq 信号三价、raw 订单三价、费用/滑点、交易状态、可卖数量、开放风险与拒绝码。
  - [ ] 生命周期展示实际/目标仓位、阶段、冻结风险容量、初始/活动止损、高水位、确认/减仓/预期状态、关联订单与成交。
  - [ ] signal、order、fill、intent、daily fact cursor 均按 attempt/position 隔离，稳定分页无重复无漏项；旧报告和旧任务保持兼容。
  - [ ] UI 区分“实验”“forward shadow”“人工建议”，明确“日线信号、需人工确认、非收益承诺”，禁止“推荐”“高胜率”等未验证表述。
  - [ ] OpenAPI 导出、codegen、后端契约测试、前端 typecheck/build/component tests 全绿；generated 文件不手改。
- **状态**：`待开始`（2026-09-20）

### N8 真实数据 POC、6,000 标的性能与完整栈 E2E

- **目标**：完成 C1/C14 和历史遗留人工验收，在真实部署环境证明采集、数据库窗口、一次性 runner、取消回收及 frontend→backend→worker→报告全链路可用。
- **涉及文件（规划）**：
  - 修改：`MarketContextBatchLoader` SQL（数据库侧逐票 top-N/实际模板窗口）
  - 新建：可重复执行的 benchmark/POC 脚本与结果记录（不纳入自动 pytest 的真实外部调用）
  - 修改：`frontend/e2e/task-flow.spec.ts` 或独立量化 E2E
  - 更新：`log.md`、`result.md`（环境、预算、实测数据与门禁结论）
- **依赖**：N1–N7
- **验收标准**：
  - [ ] 真实 Tushare 代理 POC 完成行业覆盖与七模板所需股票因子/复权/交易状态覆盖报告；缺失、暖机、限流和重试均可追踪。
  - [ ] 行情 SQL 在数据库侧按每票窗口裁剪；6,000 标的完整 context + 真实 runner + signal/order 落库记录 P50/P95、返回行数、峰值内存、失败率和取消回收时间。
  - [ ] 用户在部署机冻结可接受的 P95、内存、数据库返回行数和取消回收预算；未达标时不以盲目增加并发掩盖问题。
  - [ ] 完整栈 E2E 覆盖创建/发布策略、全市场扫描、BUY 信号、风控/可交易性拒绝、cursor 翻页、生命周期成交确认和报告展示。
  - [ ] 取消/失租/异常路径无僵尸进程、无半提交订单、无意外持仓变更；POSIX 独立进程组用例通过后同步完成 O2。
- **状态**：`进行中`（2026-09-21；真实完整栈已完成创建/发布策略→组合快照→全市场扫描→BUY/数据错误 cursor→报告页面：5565 标的、5507 完备、25 买点、58 数据拒绝、耗时约 73 秒；行业门禁未过故 0 建议订单。生命周期与用户冻结性能预算仍待 N3–N7 后验收）

### N9 分波 forward shadow 与策略晋级评审

- **目标**：落实 C13，在不自动下单的前提下按三波收集前向样本，使用观察前冻结的口径评估数据质量、可成交性、成本后表现和增量价值。
- **涉及文件（规划）**：
  - 新建/修改：shadow run 配置、观察记录、评估汇总和审计导出
  - 修改：策略状态/前端标签（实验 → shadow → 人工建议）
  - 更新：`decisions.md`（预注册门槛）、`log.md`、`result.md`（每波评审结论）
- **依赖**：N8
- **验收标准**：
  - [ ] 每波开始前由用户冻结最小观察期、最小信号数、数据完整性、风险和晋级阈值；观察后不得回改同一批样本口径。
  - [ ] 第一波：`ma_trend_cross_v1`、`trend_pullback_v1`、`volume_surge_confirm_v1`；先验证最短数据/成交/生命周期闭环。
  - [ ] 第二波：`boll_volume_breakout_v1`、`macd_rsi_reversal_v1`、`ma5_pre_cross_v1`；验证信号非高度重复及三日预期状态机。
  - [ ] 第三波：`arc_bottom_75a_v1`；单独报告误触发、行业集中与相对第一波的增量价值，默认不启用。
  - [ ] 每波至少报告成本后期望、收益风险比、最大回撤、MAE/MFE、换手、信号到可成交转化率、拒绝原因、行业集中和分市场状态表现。
  - [ ] 未达预注册门槛的模板保持实验/shadow，不进入人工建议、不默认启用；报告不得把 score 当作概率或收益承诺。
  - [ ] 只有三波各自形成可审计结论后，才允许把统一方案状态改为完成并归档。
- **状态**：`待开始`（2026-09-20；需先完成 N8，并在首轮 shadow 前取得用户对评估门槛的确认）

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
