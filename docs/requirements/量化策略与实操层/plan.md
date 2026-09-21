# 量化策略与实操层统一方案

> **状态**：方案评审修订（2026-09-19；已纳入数据可信、交易可执行性、组合总风险与影子验证门禁；既有执行基础保留，新增部分尚未实施）
>
> **修订范围**：本文件为统一方案，包含原实操层、策略参考规则和成交后生命周期。保留用户已确认的自定义代码输入、执行期读取行情、frontend/backend 优先；历史 8/8 完成记录只适用于原基础任务。

> **关联文档**：[任务总览](README.md)｜[决策记录](decisions.md)｜[个股层](../../knowledge/ai/个股层.md)｜[API 契约](../../knowledge/backend/API契约.md)

---

## 一、背景与动机

| 维度 | 现状 | 问题 | 目标 |

|------|------|------|------|

| 选股能力 | `AI/screening/screening_node.py` 以顶层 `screening` 图节点按板块短名单、相对强度和成交额门槛形成 `candidate_stock_pool`，随后 `stock_loop.py` 对候选逐票执行个股 Agent；没有用户可维护的量化条件或完整的版本服务。 | 量化通道若与 AI 层耦合（依赖 `candidate_stock_pool` 或 LangGraph 编排），则不在 AI 短名单内的 `000001.SZ` 即使策略返回有效 BUY 也不会被执行和展示，且走通 frontend/backend 全链路的周期与风险上升。 | 新增独立于 AI 层的量化执行通道：量化从 `market.instrument` 枚举全部活跃 CN 股票，用户可创建、校验、发布版本化受限策略，对每只数据完备股票逐股决策并展示全部量化 BUY 与建议订单。AI 层（market/sector/screening/stock）本方案不改造、不依赖，统一接入留待后续任务。 |

| 组合与资金 | `portfolios`/`portfolio_positions` 在 0001 建立，0008 已增加资金/风控字段；`backend/modules/quant_strategy/` 已有执行基础；`AI/position/position_manager.py` 仍读取环境变量和 `data/portfolio.json`。 | 前端 PostgreSQL 组合不会影响现有仓位规划；例如手工组合资金和 `portfolio.json` 不一致时仍按旧文件计算。 | 本任务复用已存在的 0008（`quant_strategies`/`quant_strategy_versions` + `portfolios` 风控列）并补全服务、API 与任务快照；订单只使用任务提交时冻结的组合/持仓快照，行情在执行时实时读取。 |

| 卖出路径 | `trading_graph.py` 在 `risk_gate == "block"` 时清空 `stock_results`，`position_manager.py` 仅处理 LLM 的中文“买入”。 | 已持仓不进入逐票循环，仓位层仅处理买入；AI 通道不会产出量化卖出建议。 | 目标集为全市场活跃股票与持仓并集，持仓票由策略逐票评估产出 SELL 建议；资金/仓位/行业风控照常裁剪。风险门控 `risk_gate` 本方案不接线（拒绝码预留，后续接 AI 层任务启用）。 |

| 执行安全 | 已有 `AI/strategy_sandbox/` 受限执行器。 | 在 API/Worker 直接 `exec()` 脚本会暴露宿主能力；例如 `import os`、`context.__class__`、`while True` 均不可接受。 | 白名单 `strategy(context)` 在一次性受限子进程以 JSON 通信；异常、超时和数据不足 fail-closed 为 HOLD。 |

| 报告交互 | `artifact_builder.py` 已把 `final_position_plan`、`stock_results` 存至 `analysis_reports.decision`，现有报告已接入量化结构化面板。 | 已有面板还需补成交、实际/目标仓位及状态阶段；例如无法解释某次确认加仓是否已成交。 | 结构化量化执行面板展示策略审计、快照、信号、建议订单和警告，明确“需人工确认，未下单”。 |

| 策略内容 | 现有七键仅描述接口，缺少具体选股规则；例如 MA5 金叉是否要求 MA20>MA60 未定义。 | 将用户文稿七套规则的参数、公式、评分和退出条件统一到正文。 | 用户可参考规则编写代码，源码仍是执行依据。 |
| 持仓生命周期 | 现有 signal 和平均成本不能证明实际成交，也不能记录已完成的加仓阶段。 | 未成交建议跨日重跑可能重复生成；移动止损需要持仓日事实。 | 增加成交确认、持仓状态、目标意图、未完成订单去重和逐日审计。 |

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

    ├─ DataReadinessGate 固定本次共同 market_as_of_trade_date，校验日线/因子/行业水位

    ├─ AllMarketUniverseBuilder 执行时实时枚举活跃、可交易 CN 股票

    ├─ MarketContextBatchLoader 分批读行情 → BoundedSandboxExecutor 逐票沙箱执行策略

    ├─ 落盘原始 signals；ExecutionConstraintEvaluator 校验停牌/涨跌停/T+1/流动性

    ├─ OrderPriceNormalizer 生成订单三价并复核成本后盈亏比

    └─ PositionPlanner 复核组合总风险/资金/仓位/行业，生成建议订单并回写

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

> 原基础阶段表结构与关系见 ER 图：[attachments/er-diagram.html](attachments/er-diagram.html)。该图是历史基线，尚未覆盖 C9–C14、qfq/raw 双口径、费用/风险版本、影子观察和生命周期增量；新增对象一律以 §2.6 为准，不得把图中 bfq 三因子误作统一方案最终合同。

### 2.2 上线前置合同

| 合同 | 必须新增/确认的能力 | 验收标准 | 未满足时行为 |

|------|--------------------|----------|--------------|

| C1 股票因子可用性 | **外部硬依赖，归属本统一任务**：先完成 `stock_factor_ingestion_v1` 子任务，TushareProvider 通过项目代理端点采集股票技术因子、复权因子和交易状态，按交易日批量落入本地市场读模型。新增接口先在 `BaseStockDataProvider` 以结构化 `DataFrame | None` 默认值声明，再由 `TushareProvider` 同签名覆写。 | POC 同时报告 250 日历史质量和“按模板实际最小窗口”的当日可执行覆盖。分母按模板分别计算：活跃、规范 CN 代码且已满足对应指标暖机期；分子必须 100% 完成该模板所需字段、共同交易日及复权版本对齐。上市时间不足导致的合法暖机不足单列 `WARMUP_INCOMPLETE`，不得混入采集缺口。采集固定 `trade_date` 单日查询；疑似截断时按每批最多 100 个代码补拉，直至分母逐码覆盖；逐批异常必须 rollback，POC 产出 coverage/缺失代码/暖机不足/限流/重试记录。 | 任一准备上线模板的可执行分母未达 100% 即阻断该模板晋级；不因其他模板所需的更长窗口排除本模板本可执行的股票，也不把缺失票静默算作正常 HOLD。 |

| C2 执行期输入与持仓事实 | `MarketContextBatchLoader` 在每次执行时读取当前目录、日线和因子，按 200 票批次构造上下文；任务只冻结策略/组合配置。生命周期按持仓/处理日保存所消费的价格、因子、数据时间和 hash 作为审计事实。 | 数组同长度、日期升序、缺失值为 `null`；扫描重跑可以变化；同一持仓同日重复推进使用已保存事实且不重复计数。 | 单票缺数据不调用 Sandbox；持仓缺日线/因子记录错误，不猜测阶段。无需全市场快照表。 |

| C3 受限字段路径 | 新增无业务依赖的 `AI/strategy_sandbox/strategy_contract.py` 作为唯一字段清单：声明所有可访问路径、合法常量下标区间和输出七键。Sandbox validator 只单向导入它；`backend/modules/quant_strategy/domain/templates.py` 单向导入该清单并为每模板声明**精确** `required_fields`，Sandbox 不得反向导入 backend 模板。 | 默认模板渲染源码全部通过真实 `validate_strategy_source()`；未知路径、超出负索引范围或动态下标被拒绝；四向契约测试断言“源码实际读取集合 = 模板 `required_fields` ⊆ 执行期 context 投影字段 ∩ Sandbox 允许字段”。上下文可以保留未被某模板使用的数组，模板不得为凑门控而读取无业务意义的字段。 | 发布期 `STRATEGY_VALIDATION_FAILED`。 |

| C4 策略版本化 | 本方案创建的 `quant_strategy_versions` 记录模板标识、规范化参数、模板渲染器版本与源码 SHA；已发布版本不可变。`QuantTaskSubmissionService` 在创建任务时把模板摘要写入 canonical `execution_snapshot.strategy`，rerun 只读 任务策略快照；报告 DTO 只从 snapshot/signal 投影，禁止回查可变注册表。 | 相同版本重跑得到相同 `template_id`、显示名快照、参数、renderer version、源码 SHA ；信号按重跑时行情重新计算。发布后修改注册表显示名或渲染器不影响历史报告。 | 不允许创建任务或发布。 |

| C5 组合边界 | `PositionPlanner` 只在模板输出 BUY 后复核现金、仓位、行业、最小风险收益和 `risk_gate`；同一服务中的 `OrderPriceNormalizer` 负责将已通过 Sandbox 原始校验的信号价格规范为订单价格。 | BUY 信号即使被组合拒绝也可分页审计；策略不读取组合额度。信号保留 raw `entry/stop/take`，订单另存规范化后的 `order_entry_price/order_stop_price/order_take_price`；两者均可在报告中区分展示。 | 模板照常产生信号，订单标注拒绝原因。 |

| C6 移动止损 | `PositionLifecycleManager` 维护成交后止损状态：只消费确认成交均价、初始订单止损、持仓数量及按日冻结 high/close，持久化 `high_water_mark`、`active_stop_price`、阶段、配置快照和 `last_processed_trade_date`。 | 同一持仓同日重跑不会降低止损、不会重复产生意图；浮盈达到冻结阈值后进入保本/跟踪，跌破前日有效止损时以最高优先级把目标仓位设为 0。 | 未接入真实成交回写、状态迁移或日线价格时不启用移动止损；保留初始止损和结构失效，不以 `average_cost` 或策略信号价冒充成交事实。 |

| C7 MA5 预上穿兑现时限 | Lifecycle Manager 为已确认成交、来源为 `ma5_pre_cross_v1` 的持仓建立预期状态：冻结 `confirmation_window_trading_days=3`、`fill_trade_date`、观察日数和兑现/退出状态；只按 `market.instrument_daily` 中成交后真实日线顺序计数，不调用 fail-open 的 `TradingCalendar` 推算日期。 | 第 1–3 个有效交易日首次 `MA5>MA20` 标记 `FULFILLED` 并将目标从 50% 提至 100%；未兑现时 MA5/MACD 走弱降至 25%；第 3 日仍未上穿则目标为 0。清仓优先于减仓和加仓。 | 缺确认成交、连续日线或 MA5/MA20 时，不按自然日、周末或缺失因子猜测超时；写 `EXPECTATION_DATA_UNAVAILABLE` 并保留既有止损/结构保护。 |

| C8 全流程目标仓位 | 每笔首次实际成交时冻结 `risk_capacity_shares`、初始止损、实际成本、`profit_take_price` 与生命周期版本；七模板定义初始、确认、弱化、获利及失效的目标比例。圆弧底 75A 的 `arc_neckline_price` 由 `QuantExecutionService` 基于产生 BUY 的同一次执行上下文 派生为受控 `lifecycle_seed`，随 signal/order 审计元数据及其执行输入 hash 保存；首笔 fill 的同一事务只复制该 seed 至持仓状态，禁止从当前滚动行情重算。 | 初始全部为 50%；MA5 兑现加至 100%、未兑现弱化至 25%；其余模板弱化至 50%；首次达到目标价全模板减至 50%；清仓为 0。首次触达收益目标即冻结 `profit_target_reached`，此后目标不得高于 50%，即使当日无需卖出；`profit_trim_completed` 只记录实际减仓成交。Planner 按实际与目标的差额、`state_version` 和唯一键生成订单，订单部分成交/拒绝后不错误推进阶段或重复下单。 | 未有真实首笔成交、风险容量、价格、所需因子或圆弧底 seed 时，只审计 `LIFECYCLE_DATA_UNAVAILABLE`，不臆造仓位/成交；不启用生命周期加减仓。 |

| C9 共同数据水位与日期对齐 | `DataReadinessGate` 在任务开始时从采集状态确定唯一 `market_as_of_trade_date`；日线、因子、行业成员和交易状态均以该日为上界。Loader 必须按 `(ts_code, trade_date)` 等值对齐，不允许以行号、字符串日期或整行对象作为替代键。 | 每个上下文显式保存请求日期、实际最新 bar 日期、因子日期及数据 hash；日线/因子日期错位、最新 bar 落后共同水位、字段为空或非有限时分别产生稳定错误码。专门 loader 测试必须使用不同日期和值，断言不会把全量指标静默对齐成 `null`。 | 全局水位未就绪则整任务不执行；单票停牌/缺数则 `STALE_DATA`/`DATA_UNAVAILABLE`，不调用 Sandbox。持仓票仍保留审计，但不可生成声称可立即成交的订单。 |

| C10 信号复权与交易原价双口径 | 技术形态使用前复权、同一复权版本的 OHLCV/指标；订单、成交、现金、涨跌停和止损使用原始可交易价格。`PriceBasisMapper` 基于同日复权因子把结构价位映射回原价，禁止混用 `*_bfq` 指标与复权收盘。 | context 和 signal 同时记录 `signal_price_basis`、`execution_price_basis=raw`、`adj_factor_version`；除权除息日前后 fixture 不产生机械假金叉、假突破或假止损；映射后三价仍须满足严格关系。 | 复权因子缺失或版本不一致时，该票不产生 BUY；已有持仓仅保留基于原价成交事实的初始/移动止损，不用错误结构价替代。 |

| C11 可交易性、费用与流动性 | 建议订单在组合裁剪前校验停牌、上市板块、ST/*ST、涨跌停价格、最小价位、100 股整手、卖出可用数量和 A 股 T+1。现金和风险预算计入佣金最低收费、印花税、过户费及显式滑点假设；新增订单不得超过配置的成交额参与率。 | 每条建议记录 `signal_trade_date`、`earliest_execution_trade_date`、交易状态、涨跌停价、可卖数量、费用/滑点估计、参与率与拒绝码。费用参数版本化并随任务快照冻结。 | 任一关键交易状态或费用配置缺失时拒绝 BUY；SELL 保留退出意图并标记不可成交原因，不伪造已卖出。 |

| C12 组合总风险与熔断 | 在单笔风险之外新增 `max_portfolio_open_risk_pct`、行业开放风险上限、单日新增风险上限、组合回撤/单日损失熔断和未完成订单预留。开放风险按实际持仓到当前有效止损的损失额计算，不以市值上限替代。 | Planner 处理每笔 BUY/ADD 后重算组合开放风险；相关标的同时触发止损的压力 fixture 不突破冻结上限；触发熔断后只允许减仓/清仓。 | 风险事实、有效止损或净值水位缺失时拒绝新开仓和加仓；不阻断风险降低订单。 |

| C13 影子运行与策略晋级 | 七套规则不是收益承诺。策略按波次进入只记录、不下单的 forward shadow；评估口径和样本门槛在观察开始前冻结，禁止看完结果后改口径。 | 至少报告成本后期望、收益风险比、最大回撤、MAE/MFE、换手、信号到可成交转化率、拒绝原因、行业集中和不同市场状态表现；达到预设数据完整性、样本量和风险门槛后才可进入“人工建议”阶段。 | 未完成影子样本或结果不稳定时保持实验状态，前端不得标注“推荐”“高胜率”或默认启用。 |

| C14 全市场性能门禁 | 行情 SQL 必须在数据库侧限制为每票所需的最近窗口，不得把每批股票的全部历史读入内存；Windows 一次性子进程开销纳入真实基准。 | 以真实 6,000 标的、完整 context、真实 runner 跑全链路，记录 P50/P95 总耗时、数据库返回行数、峰值内存、子进程失败率和取消回收时间；部署前先冻结可接受预算。 | 未达到冻结预算不得扩大并发掩盖数据库/进程开销；先优化查询与执行模型，且不得削弱隔离边界。 |

### 2.3 固定 `StrategyContext` 扩展合同

既有实现的初始示例和 AST 白名单只列 `ma_bfq_5`、`ma_bfq_20`、`rsi_bfq_6`，不足以表达本方案七个模板。基础能力必须将十项因子纳入执行期上下文和 AST 白名单。

```json

{

  "meta": {

    "symbol": "600519.SH",

    "requested_trade_date": "2026-09-15",

    "market_as_of_trade_date": "2026-09-15",

    "latest_bar_trade_date": "2026-09-15",

    "bars_count": 250,

    "signal_price_basis": "qfq",

    "execution_price_basis": "raw",

    "adj_factor_version": "2026-09-15"

  },

  "ohlcv": {

    "trade_date": ["..."],

    "open": [0], "high": [0], "low": [0],

    "close": [0], "volume": [0], "amount": [0]

  },

  "indicators": {

    "ma_qfq_5": [null], "ma_qfq_20": [null], "ma_qfq_60": [null],

    "boll_mid_qfq": [null], "boll_upper_qfq": [null], "boll_lower_qfq": [null],

    "macd_dif_qfq": [null], "macd_dea_qfq": [null], "macd_qfq": [null],

    "rsi_qfq_6": [null]

  },

  "position": {"shares": 0, "average_cost": null, "market_value": 0}

}

```

- `ohlcv` 为策略信号使用的前复权序列；原始最新价、涨跌停价和交易状态放入宿主侧 `execution_market` 段，只供订单层消费，AST 不允许策略脚本访问，避免脚本混用价格口径。所有数组严格按交易日升序，`[-1]` 是实际最新完整交易日；因子与 OHLCV 以 `(ts_code, trade_date)` 等值对齐、同长度且不前填。

- Loader 最多提供 250 根用于审计和后续扩展，但执行资格按模板实际访问的最早索引与指标暖机状态判断，不再设统一 250 根硬门槛。上市时间不足记 `WARMUP_INCOMPLETE`；停牌或最新 bar 落后共同水位记 `STALE_DATA`，二者不得混同。

- `TemplateInputGuard` 先根据模板的正式 `required_fields` schema 检查：`path`、精确常量索引集合、`must_be_finite`、`must_be_positive`；该集合必须等于渲染源码的实际读取集合。MA5 预上穿登记其推导临界价所需的 `ohlcv.close[-5]` 和 `ohlcv.close[-20]`；圆弧底 75A 精确登记正值 `ohlcv.close[-41,-31,-21,-11,-1]`、正值 `ohlcv.volume[-6]` 至 `ohlcv.volume[-2]` 的五个常量下标、`ma_qfq_20[-2,-1]`、`ma_qfq_60[-1]`、`macd_qfq[-2,-1]` 与 `rsi_qfq_6[-1]`；布林突破和成交量骤增模板也登记它们实际使用的 `ohlcv.volume[-6]` 至 `ohlcv.volume[-2]` 的五个常量下标（`volume` 必须为正）与对应因子索引。`None`、NaN、Infinity、非正值、数组错位、缺字段或索引不足不进入脚本；作为执行审计状态落盘，正常非持仓 HOLD 不需伪造为策略返回的异常原因。

- 模板脚本不负责错误码判断，故不依赖 `isfinite(None)` 的运行时语义；`isfinite` 若继续暴露，基础 Sandbox 必须保证非数值输入返回 `False` 且不抛异常。

### 2.4 模板版本数据合同

模板不是稳定策略实体的属性，而是**策略版本**的不可变定义。基础迁移创建版本表时增加如下字段（若基础迁移已先合并，则后续增量迁移添加）：

| 字段 | 类型 | 写入者 | 含义与示例 |

|------|------|--------|------------|

| `template_id` | `VARCHAR(64)`，可空 | QuantStrategyService | 可选的七套规则来源标识，如 `ma_trend_cross_v1`；用户自行输入代码时允许为空。它不证明当前源码仍与原模板一致。 |

| `template_params` | `JSONB`，可空 | 服务端模板渲染器 | 规范化参数；例如 `{"stop_pct":"0.06","reward_multiple":"2.50"}`。键固定、数值用字符串避免 JSON 浮点漂移。 |

| `template_renderer_version` | `VARCHAR(32)`，可空 | 服务端模板渲染器 | 渲染规则版本；例如 `template_renderer_v1`。 |

| `source_code` | `TEXT`，必填且非空白 | 用户代码编辑器 / QuantStrategyService | 用户提交、编辑并发布的受限源码；可从参考模板填入后修改。 |

| `source_hash` | `CHAR(64)` | QuantStrategyService | 对最终提交的 UTF-8 源码计算 SHA-256；以实际源码为准。 |

创建/编辑草稿 API 接受策略元数据和必填 `source_code`；代码输入、着色、深色编辑器和格式化继续保留。七套规则可提供代码起点和参数说明，模板元数据仅记录来源；用户修改源码后不得据此自动套用原模板的持仓规则。生命周期配置须独立绑定发布版本并冻结。任务提交保存版本源码、hash、可选模板来源和生命周期配置；报告只投影无源码摘要。版本元数据采用增量迁移，禁止改写已执行的 0008/0009。

### 2.5 标准结果、目标仓位与交易时点合同

通用 Sandbox 保留既有七键及 `BUY/SELL_ALL/SELL_PARTIAL/HOLD`。下表是七套参考规则默认使用的输出子集，不代表删除自定义脚本的 `SELL_PARTIAL`。V1 不向脚本注入成交、止损、阶段或未完成订单等生命周期字段；`PositionLifecycleManager` 是这些事实和目标仓位的唯一裁决者。脚本卖出意图与平台目标统一换算为绝对目标股数，同日取最低目标，任何清仓目标优先；成交前不推进状态。圆弧底等规则需要的 `lifecycle_seed` 必须来自生成信号的同一上下文，作为受控审计元数据持久化，不增加自由输出键。

| 情形 | Sandbox `action` | `score` | `entry_price` / `stop_loss` / `take_profit` | `sell_ratio` | `reason` |

|------|------------------|---------|------------------------------------------------|--------------|----------|

| 无持仓初始形态 | `BUY` | 模板定义的 0–100 整数 | 三者均为有限正数，且 `stop < entry < take` | `null` | 稳定原因码，如 `MA_TREND_CROSS`。 |

| 价格/结构清仓形态 | `SELL_ALL` | `0` | 全部 `null` | `null` | `STOP_LOSS` 或模板固定失效码。 |

| 正常不动作/生命周期由服务处理 | `HOLD` | `0` | 全部 `null` | `null` | `NO_SIGNAL`。 |

`TargetPositionIntent` 不是 Sandbox 自由输出，字段固定为 `position_id`、`template_id`、`effective_trade_date`、`risk_capacity_shares`、`target_exposure_pct`、`target_shares`、`lifecycle_phase`、`reason_code`、`source_signal_id` 和 `state_version`。`target_exposure_pct` 只能是 `0/0.25/0.50/1.00`，含义是相对该持仓**初始成交时冻结**的风险允许最大仓位 `risk_capacity_shares` 的目标比例；不是账户总资产比例。`target_shares=lot_floor(risk_capacity_shares×target_exposure_pct)`，再由组合上限、可用现金和最小交易单位裁剪。目标与实际相同不产生订单；差额为正生成 `BUY_ADD`，差额为负生成 `SELL_REDUCE`，目标为零生成 `SELL_EXIT`。每次建议订单保存 `state_version` 与 `(position_id,effective_trade_date,target_shares,reason_code)` 幂等键，确认成交后才推进生命周期，该按日唯一键不足以阻止跨日未成交重复建议；还须按 §2.6 对活跃意图和未完成订单去重。

- 七套参考规则的 `reason` 使用固定 ASCII 原因码，便于中文展示；通用脚本仍允许不超过 240 字符的纯文本。平台另存受控 `reason_code`：注册模板使用模板码，自定义脚本统一记 `CUSTOM_SIGNAL` 并保留展示文本，任何幂等键都不得依赖自由文本。

- 该日线策略只在共同水位日收盘数据与全部因子完成后计算。信号日不得假设以同一根 K 线收盘价成交；建议订单的 `earliest_execution_trade_date` 至少是下一可交易日，实际成交、费用、可用现金和平均成本由人工/券商确认回写。

- `take_profit` 是入场风险收益校验的参考价，也是生命周期“首次获利减仓”的触发口径。首笔 fill 初始化前必须满足 `0 < initial_stop_price < initial_fill_price`；若实际成交价 \(P_0\le S_0\)，写唯一 `INITIAL_STOP_BREACHED_ON_FILL` 的 0% 退出目标，禁止初始化风险容量、确认加仓和收益阶段。其余情况以 \(P_0\)、冻结初始止损 \(S_0\) 和模板 \(k=reward\_multiple\) 重算 `profit_take_price=P_0+k(P_0-S_0)`；收盘首次达到该价即原子写入 `profit_target_reached` 并把目标上限锁定为 50%，即使当前仓位已经不高于 50% 而无需卖单。实际减仓成交才写 `profit_trim_completed`；价格后续回落/重上目标均不重复减仓，且 `profit_target_reached` 后不得通过迟到确认反向加仓。

- Sandbox 先校验并持久化策略**未改写的前复权信号价** `signal_entry/signal_stop/signal_take`、`signal_price_basis=qfq`、复权版本和严格大小关系。宿主 `PriceBasisMapper` 使用同日复权因子映射为未取整原价，`OrderPriceNormalizer` 再以 `Decimal` 和证券最小价位生成独立的 `order_entry_price/order_stop_price/order_take_price`；随后叠加显式滑点/费用，重新校验严格价格关系、成本后盈亏比和每股风险。顺序固定为“原始七键校验 → 价格口径映射 → tick 规范化 → 费用/滑点 → 风险收益复核 → 可交易性与组合裁剪”。不得为满足盈亏比擅自抬高目标价或改写信号。任何阶段失败时 BUY 信号仍可审计，订单写稳定拒绝码且不生成建议。


### 2.6 统一后的执行与持仓数据边界

现有 `QuantExecutionService`、策略服务、Sandbox、0008/0009 及前端代码编辑器已经存在；本次在其上增加规则参考和成交后状态机，不重建旧迁移。量化仍由 Worker 直接执行；AI 图、screening 和 risk_gate 接线不在本次范围。

策略版本发布 → 冻结任务策略/组合/费用/风险配置 → 固定共同市场水位 → 读取同日 qfq 信号数据和 raw 交易数据 → 单票信号 → 可交易性/价格/成本/组合开放风险 → 建议订单 → 用户确认真实成交 → 原子更新持仓及生命周期 → 后续日线处理生成目标仓位及差额建议。

普通扫描沿用任务组合快照；已启用生命周期的持仓推进必须读取最新确认成交和当前生命周期版本，不能拿旧任务的持仓快照当真实数量。任何建议生成都不修改实际持仓；只有显式成交确认在一个事务内写成交事实、实际持仓、订单成交状态和生命周期版本。不接券商自动下单。

| 数据对象 | 关键字段与关系 | 写入与约束 |
|---|---|---|
| `quant_strategy_versions`（扩展） | 可选模板来源/参数/渲染器版本、`lifecycle_policy_version_id`、策略阶段与配置快照；沿用 `source_code/source_hash` | 代码必填；已发布不可修改；修改参考代码后 template_id 只作来源审计，未显式兼容绑定则使用基础信号模式。 |
| `quant_execution_signals`（扩展） | 七键保持 `action/score/entry_price/stop_loss/take_profit/sell_ratio/reason`，并保存 `signal_price_basis=qfq`、复权版本、共同水位、最新 bar 日；新增 raw 订单三价、费用/滑点、可交易状态、最早执行日、参与率、受控 seed 与审计字段 | 全部 BUY 保留；未持仓正常 HOLD 只统计。原文 signal_entry/stop/take 只是说明性简称，不新增同义列；信号列不得被订单规范化覆盖。 |
| 费用与风险配置版本（新增设计） | 佣金率/最低佣金、印花税、过户费、滑点模型、最大成交额参与率、组合/行业开放风险、单日新增风险和熔断阈值 | 任务提交时冻结版本；无明确版本不得产生新增风险订单；历史报告始终引用当时版本。 |
| `position_lifecycle_states`（新增设计） | id、position_id、strategy_version_id、initial_fill_id、初始成交/止损、risk_capacity_shares、target_exposure_pct、target_shares、phase、profit_take_price、profit_target_reached、confirmation_completed、profit_trim_completed、arc_neckline_price、state_version、last_processed_trade_date、closed_at | 一个持仓周期仅一个活跃状态；清仓实际成交后关闭，重新建仓开启新周期；任务删除不得级联删除成交事实与持仓状态。 |
| `position_trailing_stops`（新增设计） | lifecycle_id、initial_stop_price、high_water_mark、active_stop_price、phase、trailing_config、last_processed_trade_date、exit_intent_id | lifecycle_id 一对一；止损只升不降，使用前日有效值判断今日收盘。 |
| `position_expectations`（新增设计） | lifecycle_id、fill_trade_date、window_trading_days、observed_trading_days、status、fulfilled_trade_date、last_processed_trade_date | 每个周期一个预期；终态不回退；缺失因子不能跳过后续日继续累计，须先补齐该日或记录不可用。 |
| 成交事实记录（新增，物理表名实施设计时核对） | fill_id、order_id、portfolio_id、position_id、side、quantity、fill_price、fill_trade_date、确认请求幂等键 | 同一确认请求仅生效一次；保留实际成交，不能以 average_cost 推测。 |
| 生命周期意图与建议订单（新增，物理表名实施设计时核对） | intent_id、lifecycle_id、source_signal_id（可空）、target_shares、reason_code、state_version、status；订单数量/已确认数量/剩余预留量、规范化价格 | 生命周期可独立触发止损，无原始脚本信号时仍可审计；建议订单与信号来源通过引用关联，不复制成第二条策略信号。 |
| 持仓逐日审计事实（新增，物理表名实施设计时核对） | lifecycle_id、trade_date、实际使用的 high/close/因子、data_as_of、input_hash、规则版本、计算前后版本、最终目标 | 唯一 (lifecycle_id, trade_date)；同日重复处理复用事实，不推进两次；不保存全市场 250 根行情副本。 |
| 影子观察记录（新增设计） | strategy_version_id、signal_id/order_id、信号日、最早执行日、假设成交、费用/滑点、后续日价格路径、MAE/MFE、退出原因、市场状态、评价口径版本 | 只追加，不回填信号前数据；评价口径在 shadow 开始前冻结；实验结果不得改写历史订单/成交事实。 |

首次建议先按账户风险预算计算候选容量并申请 50% 首仓；真实首笔成交后才根据成交价、接受订单初始止损与冻结预算确定并持久化风险容量。部分成交不表示首仓已完成，且不得把“当前部分成交数量”覆盖成计划最终目标。剩余数量须扣除仍有效订单的预留部分。

生成差额建议时锁定生命周期和该周期活跃意图，计算 `目标数量 - 实际数量 - 有符号的未完成订单预留数量`；同一目标已有活跃意图时复用。日内唯一键仅提供审计去重，跨日重复扫描也必须检查活跃意图。目标变更先使旧建议失效并释放预留；若旧订单已被用户标记执行中而状态不明，则等待对账，不生成冲突订单。拒绝/取消可释放预留；部分成交只释放已成交部分，不将未完成阶段误标完成。真正清仓成交后才关闭生命周期。

历史 report/signal 不因任务 rerun 改写成交事实；task/source_signal 引用删除采用保留审计所需摘要与可空引用或删除限制，不沿用 signal 随 task 删除的规则级联到已确认成交。具体 FK、索引、增量迁移编号和 API 路由在生命周期实施设计中补齐，不把这些新增设计标为现有数据库事实。

前端在现有策略页保留新建必填源码、暗色着色和格式化；七套策略是代码参考入口。组合页增加成交确认与实际/目标仓位展示；报告区分 qfq 原始信号、raw 建议订单、数据水位、可交易性、费用/滑点、组合开放风险、生命周期原因、未完成订单和已确认成交。策略状态明确显示 `DESIGN/SHADOW/MANUAL_ADVISORY`，未晋级策略不默认启用。新增 API 先导出 OpenAPI 再生成前端 client。生命周期功能尚未接入时显示未启用，不能仅靠修改平均成本开启。

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

| `DataReadinessGate + MarketContextBatchLoader（执行期数据门禁/读取器）【新增】` | 先冻结共同市场水位，再按 200 票批次在数据库侧读取每票“模板实际所需窗口（最多 250 根）”，按 `(ts_code, trade_date)` 对齐复权 OHLCV、指标、原始交易价、交易状态与复权因子。 | 新增/修改 `backend/modules/analysis/infrastructure/quant_execution_market_data.py`、市场 ingest state/DAO | 消除 N+1 和无界历史读取；水位、日期、复权版本及最新 bar 不一致时 fail-closed。 |

| `ExecutionConstraintEvaluator + OrderPriceNormalizer【新增】` | 将原始信号转换为可审计的下一可交易日建议：检查停牌、涨跌停、ST/板块规则、T+1、可卖数量、流动性、费用与滑点，再生成订单三价并复核成本后盈亏比。 | `backend/modules/quant_strategy/application/`、signals/订单 DTO | 原始信号不被改写；不可成交或成本后不合格时保留信号并写稳定拒绝码。 |

| `Analysis Worker wiring（Worker 接线）【修改】` | 从任务快照构造仅闭包持有源码的 `SandboxRunner`、`ExecutionControl` 和 backend `QuantExecutionService`，量化任务绕过图直接执行；每次执行/重跑新建服务实例。 | `backend/workers/wiring.py`、`backend/workers/analysis_executor.py` | Worker 不读取当前策略或组合，源码不进入 checkpoint/artifact；进度、取消、fencing 由 `ExecutionControl` 承担。 |

| `Industry ingestion（采集模块）【新增】` | 采集 SW2021 一级行业及成分，完成校验后原子刷新行业成员。 | `db/instrument/ingest/industries.py` | 为行业仓位上限提供可用性门控的数据基础。 |

#### 数据表与迁移

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |

|----------|----------|----------------|--------------|

| `quant_strategies（表）【新增】` | `0008` 已由基础阶段创建：稳定 ID、名称、描述和 version（元数据乐观锁）；ORM/索引与服务契约一致。 | `backend/migrations/versions/0008_quant_strategy_and_portfolio_risk.py`、`backend/modules/quant_strategy/` | PostgreSQL 保存可维护的量化策略实体。 |

| `quant_strategy_versions（表）【新增】` | `0008` 已由基础阶段创建：版本、状态、源码、source_hash、唯一约束和 partial unique DRAFT 索引；`0009` 增加不可变 `published_at`/可选 `archived_at`，用于版本审计和报告。 | `backend/migrations/versions/0008_quant_strategy_and_portfolio_risk.py`、新增 `backend/migrations/versions/0009_quant_execution_signals.py` | 支持草稿、发布、归档和任务快照审计。 |

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


### 本次统一方案的增量

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|---|---|---|---|
| 七套策略参考与输入门控 | 参数、公式、评分、精确字段需求及代码参考 | `backend/modules/quant_strategy/domain/templates.py`、`AI/strategy_sandbox/strategy_contract.py` | 提供七套可校验规则，同时保留用户输入代码。 |
| 股票因子采集 | 十项因子、250 日对齐、全市场覆盖 POC | `db/instrument/ingest/`、Provider 与 factor DAO | 缺数据逐票审计，采集 POC 与功能实现分开验收。 |
| 订单价格规范化 | 原始信号价与 tick 规范化价分开 | `application/position_planner.py`、signals ORM/DTO | 不改写原始信号，规范化后重新核验风险收益。 |
| PositionLifecycleManager | 首仓、确认、弱化、收益目标、移动止损、三日兑现 | `application/position_lifecycle_manager.py` | 基于真实成交管理目标仓位；跨日未完成订单去重。 |
| 持仓/成交/意图持久化 | 周期状态、止损、预期、日事实、成交及建议订单 | 后续 Alembic 增量迁移、仓储与成交确认服务 | 不改 0008/0009，确认成交事务推进状态，保留审计历史。 |
| 前端策略/组合/报告 | 代码参考、成交确认、实际与目标仓位、阶段及订单状态 | 策略页、组合页、QuantExecutionPanel、生成 client | 继续支持深色代码编辑；建议与成交清楚区分。 |
| AI 层 | 暂不接入 | 无 AI 图改造 | 复用沙箱目录不表示接入 AI Agent。 |

## 四、详细设计

### 4.0 模块总览

| 维度 | 问题 | 方案概览 |

|------|------|---------|

| 4.1 策略版本与受限执行 | 直接 `exec` 前端脚本可执行 `import os` 或死循环。 | 策略领域控制版本/发布；AST 白名单与短生命周期子进程隔离运行。 |

| 4.2 组合账户与任务快照 | 平台组合无资金字段，旧仓位层读取 `data/portfolio.json`。 | 原子更新组合；提交时冻结策略/组合/持仓快照（行情执行时实时读取）并经任务 state 传递无源码运行输入。 |

| 4.3 全市场目标集、行情与订单 | 当前仅板块候选股、block 清空循环、行业成分表可能为空、仓位层仅买入。 | 全部活跃 CN 股票+持仓去重；执行时批量实时读取行情与有界并发策略执行；报告展示全部量化 BUY，订单仍经风险门控。 |

| 4.4 API、报告与前端 | API 未返回 `decision`，前端没有策略路由、组合账户编辑或订单面板。 | 强类型 API/错误码/OpenAPI；前端通过生成 client 实现管理、选择和结构化展示。 |

| 4.5 验证与安全回归 | 未覆盖 sandbox、原子快照、block 卖出、资金裁剪和源码泄漏。 | 分层确定性测试和受控 E2E，不运行真实 LLM 测试。 |

| 4.6 模板注册、参数与输入门控 | 当前 `strategy(context)` 只允许三项因子，例如 MA60、布林带、MACD 路径会被现有 AST 白名单拒绝。 | 用单一模板注册表声明参数、必需字段和渲染器；执行期上下文、AST 与输入门控复用该声明。 |

| 4.7 均线趋势交叉策略 | 已定义 MA5/20/60 列，但没有“首次金叉 + 中期趋势 + 动量确认”的统一交易逻辑。 | 用 MA5 上穿 MA20、MA20 高于 MA60、RSI/MACD 确认形成日线趋势策略。 |

| 4.8 强势回撤反弹策略 | 已定义布林下轨和 RSI 列，但没有“上升趋势内回撤结束”而非接飞刀的判定。 | 以 MA20>MA60、前日跌至下轨、当日收回下轨和 RSI/MACD 修复作为买入条件。 |

| 4.9 布林放量突破策略 | 有 OHLCV 与布林上轨，尚无“首次突破 + 有效量能基线”的统一定义。 | 以首次上破上轨、前五日最大成交量为基线、RSI 不过热形成突破策略。 |

| 4.10 MACD-RSI 动量反转策略 | 已定义 MACD/RSI 列，但没有“低位金叉且真实脱离超卖”的精确条件。 | 以 0 轴下 MACD 金叉、RSI 从超卖阈值上穿和价格不显著偏离 MA20 形成低优先级反转策略。 |

| 4.11 MA5 预上穿 MA20 提前布局 | 当前均线策略只在金叉已发生后买入，未定义如何以固定历史窗口审计“明日上涨即上穿”的提前布局条件。 | 以 MA5/MA20 明日滚动公式推导交叉临界收盘价；仅当该价高于现价且在可配置的上涨距离内、并满足中期趋势与动量过滤时 BUY。 |

| 4.12 成交量骤增确认策略 | 当前布林突破把量能绑定在上轨突破，无法表达“相对前五日显著放量但不要求触及布林上轨”的趋势确认。 | 用当日成交量相对前五日最大量的倍数、正价格涨幅、MA20/MA60 与 RSI/MACD 过滤形成独立的放量确认策略。 |

| 4.13 圆弧底 75A 策略 | 当前模板没有把“左侧回落—中部筑底—右侧修复—放量突破颈线”转为受限脚本可执行的日线合同。 | 使用 40 日固定锚点近似圆弧形态，以突破时冻结的颈线管理确认、减仓和清仓，避免滚动窗口移动后改变持仓理由。 |

| 4.14 策略目录边界与验证 | 单票上下文不含 PE、财报、分钟行情或横截面排名；例如不能求“全市场 PE 最低 10%”。 | 限定首批七模板，列明禁用策略、后续准入门槛与逐模板可执行验收。 |

| 4.15 盈利阶段移动止损 | 无状态模板不能可靠维护成交后的最高价和有效止损；例如重跑不能用今日价格覆盖昨日高点。 | 在实际成交后的持仓风控层，以初始风险 \(R\) 驱动“保护→保本→跟踪”状态机；止损只上调，卖出建议按持仓/交易日幂等。 |

| 4.16 MA5 预上穿预期兑现时限 | 预上穿是“短期将金叉”的前置假设；例如已持仓 3 个交易日仍 `MA5≤MA20` 时，现有 `HOLD` 不会退出。 | 对该模板的确认成交持仓冻结 3 个有效交易日；窗口内金叉即兑现并加仓，到期未金叉以 `EXPECTATION_TIMEOUT` 离场。 |

| 4.17 全流程持仓生命周期 | 当前模板只返回单次 BUY/SELL_ALL，不能表达首仓、确认加仓、弱化减仓、获利减仓与差额订单。 | 用统一 `PositionLifecycleManager` 管理目标仓位和成交确认状态；七模板各自声明确认/弱化/失效条件，Planner 仅执行实际仓位到目标仓位的差额。 |


> §4.1–4.5 保留基础阶段的完整执行合同，文件清单中的“新建/新增”描述其原始交付；当前这些基础模块已有实现，禁止按历史清单重复创建 0008/0009。本次增量见 §4.6–4.17 与 §2.6。

### 4.1 策略版本与受限执行

#### 4.1.1 模块设计

扩展已有 `backend/modules/quant_strategy/`（domain/application/infrastructure）及可被 backend `QuantExecutionService` 调用的纯执行内核 `AI/strategy_sandbox/`：

- `QuantStrategy`：名称、描述、version（元数据乐观锁，沿用 portfolios/watchlists 的 `version` 惯例）；名称唯一。

- `QuantStrategyVersion`：`version_no` 自 1 递增，`(strategy_id,version_no)` 唯一；状态仅为 `DRAFT/PUBLISHED/ARCHIVED`。`0008` 已由基础阶段创建，包含这两个表、状态 CHECK、源码长度/哈希约束和 partial unique DRAFT 索引。

- `quant_strategies.version` 只保护策略名称/描述，`quant_strategy_versions.version` 只保护草稿源码（乐观锁列统一 `version`，与 `portfolios.version` 一致；`version_no` 是语义版本号，两者区分）；**不声明不存在的当前草稿指针**，当前草稿由 partial unique index 的唯一 DRAFT 查询。创建策略同时创建 v1 `DRAFT`；partial unique index 仅保证每策略**至多一个** DRAFT，create/publish 的同一事务保证正常业务路径始终至少一个可编辑 DRAFT。发布使用 `WHERE id=:id AND status='DRAFT' AND version=:expected_version`，经共享 `validate_strategy_source()` 校验后在同一事务将旧草稿置 `PUBLISHED`、写入不可变 `published_at` 并插入下一版 DRAFT；新 DRAFT 插入失败必须 rollback，使旧草稿仍为 DRAFT。 同一策略允许多个历史 PUBLISHED，任务可选择任一未归档发布版。仅 `DRAFT → PUBLISHED`、`PUBLISHED → ARCHIVED` 有效；只允许归档 PUBLISHED，禁止归档 DRAFT，且至少保留一个 PUBLISHED。ARCHIVED 不可逆且不能被新任务引用；已发布/被历史任务引用版本不可修改或物理删除，未删除历史任务从其快照重跑。草稿读取/保存 DTO 是唯一可返回 `source_code` 的 API；列表、发布审计、任务、报告和错误 detail 一律不含源码。`0009` 增加 `published_at`/`archived_at`，禁止用会被归档更新的 `updated_at` 代替发布时间。

- API：`GET/POST /api/v1/quant-strategies`、`GET /api/v1/quant-strategies/{id}`、`PUT /api/v1/quant-strategies/{id}/draft`、`POST /api/v1/quant-strategies/{id}/versions/{version_id}/publish`、`POST /api/v1/quant-strategies/{id}/versions/{version_id}/archive`；非法转换返回 `STRATEGY_VERSION_INVALID_STATE`（409）。

策略唯一顶层定义为 `def strategy(context):`。JSON `context` 固定为：

```json

{

  "meta": {"symbol":"600519.SH","requested_trade_date":"2026-09-15","market_as_of_trade_date":"2026-09-15","latest_bar_trade_date":"2026-09-15","bars_count":250,"signal_price_basis":"qfq","execution_price_basis":"raw","adj_factor_version":"2026-09-15"},

  "ohlcv": {"trade_date":["..."],"open":[0],"high":[0],"low":[0],"close":[0],"volume":[0],"amount":[0]},

  "indicators": {"ma_qfq_5":[null],"ma_qfq_20":[null],"ma_qfq_60":[null],"boll_mid_qfq":[null],"boll_upper_qfq":[null],"boll_lower_qfq":[null],"macd_dif_qfq":[null],"macd_dea_qfq":[null],"macd_qfq":[null],"rsi_qfq_6":[null]},

  "execution_market": {"raw_close":0,"limit_up":0,"limit_down":0,"trade_status":"OPEN","is_st":false,"sellable_shares":0},

  "position": {"shares":0,"average_cost":null,"market_value":0}

}

```

数组严格按交易日升序，`[-1]` 是截至有效交易日的最新值；OHLCV 与指标数组同长度，因子缺失用 `null` 对齐，绝不前填。策略不获取 DataFrame、DB/Provider/LLM、环境变量或完整 LangGraph State。

AST 默认拒绝，统一纯函数 `validate_strategy_source(source) -> list[StrategyValidationIssue]` 供草稿预校验、发布和 runner 复用，返回稳定 `code/message/line/column`；明确允许：一个函数、`Assign/AugAssign/If/Return`、有限常量、命名局部变量、已知路径下标、算术/比较/布尔/条件表达式，以及仅为返回结果构造的 `ast.Dict`。结果字典必须且只能含固定字符串键 `action/score/entry_price/stop_loss/take_profit/sell_ratio/reason`，禁止 `**` 解包、动态键、重复键和嵌套可变容器。可访问 context 路径由独立 `strategy_contract.py` 唯一声明：包括新版 meta、前复权 `ohlcv`、十项 qfq 指标和 position 字段；宿主侧 `execution_market` 明确不在白名单中。只允许常量整型下标（含负数），禁止变量下标、slice 和其他键。`Call` 仅允许无关键字参数的裸名称 `abs/min/max/round/isfinite`，其中 `isfinite` 是受控 builtin。禁止 Attribute、Import、循环/推导、lambda、嵌套函数、try/raise/with、动态 key、未知 name 和包含 `__` 或前导 `_` 的标识符。限制：源码 12KiB、800 AST 节点、64 语句、6 层 if、最多 250 根 bars 与 4KiB 输出。

运行器调用 `sys.executable -I` 一次性子进程：空 cwd、最小环境、`close_fds=True`、JSON stdin/stdout、stdout 上限 4KiB、300ms wall-clock；Unix 额外 `RLIMIT_CPU=1s`、`RLIMIT_AS=128MiB`、`RLIMIT_FSIZE=0`/低 NOFILE，Windows 记录 `RESOURCE_LIMIT_DEGRADED` 且仍 kill 超时进程。子进程只注入空 builtins 和五个安全函数。AST 不是恶意代码的强隔离；本地单用户 V1 用它防误用，未来多租户必须切换 `--network none`、只读根文件系统和无凭据容器。

草稿/发布校验失败返回 `STRATEGY_VALIDATION_FAILED`（422，保留客户端文本）；已发布快照若 source_hash 或再次 AST 校验不匹配则任务以不可重试 `STRATEGY_SNAPSHOT_INVALID` 失败。策略逐票超时、子进程崩溃或协议/业务输出错误只隔离该票；Worker 级 runner 装配、数据库或 artifact 基础设施故障沿既有重试分类进入任务级 retry。策略输出必须是上述七键字典。`action∈{BUY,SELL_ALL,SELL_PARTIAL,HOLD}`、`score∈[0,100]`、`reason` 为最多 240 字符且无控制字符的纯文本，非有限数值/超长/控制字符均无效。BUY 必须 `0<stop_loss<entry_price<take_profit` 且 `sell_ratio=null`；SELL_ALL 必须有持仓、全部价格字段和 `sell_ratio` 均为 `null`；SELL_PARTIAL 必须有持仓、价格字段均为 `null` 且 `0<sell_ratio<1`；HOLD 的四个交易字段均为 `null`。脚本真实返回 HOLD 才计正常 HOLD；异常、超时、非法输出、行情/因子不足分别落 `EXECUTION_TIMEOUT/INVALID_OUTPUT/INDICATOR_UNAVAILABLE/DATA_UNAVAILABLE/STALE_DATA/WARMUP_INCOMPLETE` 审计状态，不伪造成策略 HOLD，也不终止其他标的。

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

执行期先由 `DataReadinessGate` 固定 `market_as_of_trade_date`；若日线、因子、复权因子、交易状态或行业水位未达到该日，整任务暂不扫描。`MarketContextBatchLoader.load_batch(...)` 每批参数化读取 200 票，并用窗口函数或 lateral query 在数据库侧限制为每票模板实际所需窗口（最多 250 根），禁止先拉取全部历史再在 Python 截断。日线和因子只按 `(ts_code, trade_date)` 等值对齐；每票实际最新 bar 必须等于共同水位，否则记 `STALE_DATA`。模板实际访问窗口不足记 `WARMUP_INCOMPLETE`，字段缺失记 `INDICATOR_UNAVAILABLE`，不得调用 Sandbox。DB 连接绝不进入 State 或子进程。

组合持仓上限为 500 条：提交服务锁定后检测第 501 条，返回 `PORTFOLIO_POSITION_LIMIT_EXCEEDED`。全市场扫描以 200 code 为固定批次读取和释放上下文；量化路径不调用旧 `stock_loop.py`，artifact 与内存结果列表均不得容纳全市场 bars 或无界信号。所有批次完成后，`ExecutionConstraintEvaluator` 先标注下一可交易日、交易状态、T+1/可卖数量、涨跌停、参与率、费用与滑点；`OrderPriceNormalizer` 再生成订单三价并复核严格价格关系和成本后盈亏比；最后 `PositionPlanner` 从当前 attempt 的 signal 表按 `score DESC, ts_code ASC, id ASC` 流式裁剪组合风险，禁止按批预扣额度。执行器初始上限仍为 8 个在途子进程，但最终并发由 C14 真实基准决定。持仓缺共同水位原始 close 时，`average_cost` 仅作展示，拒绝全部新增风险并警告 `STALE_POSITION_VALUATION`。

代码归一不复用 `StockUtils.normalize_code()` 的 SH/SZ 启发式：全市场枚举仅接受 `market.instrument` 已存的六位数字 `.SH/.SZ/.BJ` `ts_code`；快照持仓若无后缀，通过 `market.instrument` 中 `instrument_type='stock'` 的唯一六位前缀反查，恰一行才转其真实 `ts_code`。无匹配、多匹配、非法后缀均生成该持仓 `INVALID_INSTRUMENT_CODE`，不执行策略；全市场与持仓以归一后的 `ts_code` 去重。

行业风险桶固定 `source="SW2021"`。新增 `market.ingest_state`（PK `(resource, source)`；`status`、`successful_at`、`coverage`、`member_hash`、`failure_code`、`summary_json`、`observed_at`）及 DAO；DDL 写入 `db/instrument/schema.sql`（幂等 `IF NOT EXISTS`，`db.py init_schema` 可重复执行，老环境重跑即生效——`db/instrument/migration/` 没有 DDL 迁移器，不为其发明新机制），不得混入 backend Alembic；单行混存最近成功验收与最近失败观测，成功事务只写成功字段、失败短事务只写失败字段，互不覆盖。BUY 门控谓词固定为 `status='SUCCESS' AND successful_at >= now()-interval '8 days' AND coverage>=0.95 AND member_hash` 与当前 `industry_member` 集合 hash 相等；冷启动、上次成功已过期、无成员或最近集合不匹配都为 `INDUSTRY_BUCKET_UNAVAILABLE`。新增结构化 Provider 接口 `BaseStockDataProvider.get_industry_members_df(industry_index_code: str) -> DataFrame | None`（默认 `None`）及 Tushare 同签名覆写：实际调用代理 `index_member(index_code="801010.SI", fields="index_code,con_code")`，只接受可归一至 CN 股票的 `con_code`。此接口是**部署前 POC 门禁**：提供不进入自动 pytest 的 `python -m db.instrument.ingest.industries --poc`，以真实 `TUSHARE_TOKEN` 记录代理 31 个一级行业的请求、返回字段、空/重复响应、限流和间隔，并把成功标准（31 次请求均成功、字典匹配、覆盖率≥95%、无活跃票多归属）和结果摘要写入 `market.ingest_state(resource='industry_member', source='SW2021')`。未获得成功状态即不启用行业上限 BUY，首发保留旧集合并以 `INDUSTRY_BUCKET_UNAVAILABLE` fail-closed，绝不以官方文档代替实测。新增 `db.instrument.ingest.industries.collect_industries`：先拉 `index_classify(src='SW2021', level='L1')`，要求它与现有 31 条 seed 的 `(source, industry_code, name)` 完全一致；`801010.SI` 仅在 Provider 请求边界补后缀、写库/匹配统一规范为 `801010`；任何字典漂移即拒绝 member 替换。随后全部 31 个 `index_member` 拉入内存，`con_code` 先归一为 CN `ts_code` 再按 `market.instrument` 中 `instrument_type='stock' AND list_status='L'` 校验唯一归属；覆盖率 = 有行业归属的活跃股票数/活跃股票总数 ≥95%（防「非空但被截断」帧漏放，用户 2026-09-16 拍板）；任一行业请求失败、空响应、活跃票多归属或覆盖率不足即先 rollback 成员替换事务，再用**新的短事务**只更新 `failure_code/summary_json/observed_at`，不得覆盖旧 `successful_at/member_hash/coverage`；旧成功成员集合保持不变。全部成功后在**一个事务**内 `DELETE FROM market.industry_member WHERE source='SW2021'`、upsert 新成员、按成员数更新 `market.industry.count`、写入成员 hash/coverage/successful_at 后 commit。提供 `python -m db.instrument.ingest.industries --refresh`，为现有 `collect_incremental(..., refresh_industries: bool | None)`、CLI 和 `daily_job.step_collect_market()` 同名接线，默认周一周刷；新上市的暂未覆盖票、行业表为空或 ingest_state 无最后成功验收时 BUY 被 `INDUSTRY_BUCKET_UNAVAILABLE` 拒绝，SELL 继续允许。绝不把未知行业按零暴露绕过上限。

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

持仓估值使用共同水位日的原始 close；建议买入价是“下一可交易日限价基准”，不能把信号日收盘视为已成交价格。订单层先以 tick 规范化的 `order_entry_price/order_stop_price/order_take_price` 加费用和滑点复核严格关系、成本后盈亏比及每股风险，再计算风险手数；若当前原始参考价高于止盈价或使成本后盈亏比失效，保留 BUY 信号并拒绝订单，禁止仅用 `max(entry, close)` 后沿用旧风险距离。

定义 `existing_market_value=Σ(quantity×raw_close)`、`portfolio_open_risk=Σ(max(0, raw_close-active_stop)×quantity)+未完成买单风险预留`、`unallocated_assets=total_assets-available_cash-existing_market_value`。每笔买入按固定顺序计算：原始信号校验 → 可交易性/T+1/流动性 → 订单三价与费用规范化 → 成本后盈亏比 → 单笔风险手数 → 组合开放风险/单日新增风险 → 现金/总仓位/单票/行业市值与行业开放风险上限 → 整手向下取整 → 最终重算并断言全部上限。卖出所得不提前计入同批现金；未完成订单先预留现金、股数和开放风险。拒绝项保存结构化原因而非只输出汇总文本。

信号保留矩阵固定为：非持仓正常 HOLD 只计入 summary；全部 BUY 分页保存并含 `order_status`；持仓的 HOLD/SELL/错误全量分页保存；已接受建议订单作为其对应 BUY/SELL signal 的订单字段持久化，不另建重复行；买入拒绝只作为对应 BUY 的 `order_status`，不得在另一 signals 集合重复；非持仓数据/执行错误保存错误码计数和每码至多 100 个按 `ts_code` 排序 sample。`quant_execution_signals.id` 为表级自增主键；行以 `signal_kind` 判别（BUY/持仓信号/错误样本）。**完整列清单**：`id` BIGSERIAL PK、`task_id` FK CASCADE、`attempt_no` INT、`signal_kind`、`ts_code`、`action`、`score` NUMERIC(5,2)、`reason` VARCHAR(240)、`entry_price`/`stop_loss`/`take_profit` NUMERIC(18,4)（仅 BUY 行有值，SELL/HOLD 恒 NULL）、`sell_ratio` NUMERIC(8,6)（仅 SELL_PARTIAL 有值）、`order_status` VARCHAR(32)、建议订单列组 `shares`/`notional`/`order_cost_price`/`valuation_price`/`risk_bucket`（PositionPlanner 回写，仅 ELIGIBLE 及卖出订单行有值）、`error_code` VARCHAR(64)（错误样本行）、`created_at`；复合索引 `(task_id, attempt_no, score DESC, ts_code, id)` 支撑 cursor。结果按 attempt 隔离：默认读取任务最新成功 attempt，也允许显式 `attempt_no`；cursor 查询强制 `WHERE task_id=:task_id AND attempt_no=:attempt_no`。「最新成功 attempt」判定 = `analysis_reports` 存在该 attempt_no 的报告行（artifact 持久化即成功边界）；失败/取消 attempt 残留的已持久化批次行由读取层按该谓词排除，不物理删除。BUY cursor 固定以 `(score DESC, ts_code ASC, id ASC)` 排序并编码这三个值，持仓审计/订单以各自唯一稳定排序键编码，禁止复用仅支持 `(datetime, UUID)` 的任务列表 cursor。错误 sample 为 `ts_code ASC`。该 endpoint 提供所有可审计行及完整建议订单；`ReportDTO.quant_execution` 只放摘要、统计、首 50 条 BUY 预览、首 50 条持仓信号预览和首 50 条建议订单预览，禁止无界数组。`SELL_ALL` 卖出现有全部数量（含零股）；`SELL_PARTIAL` 为 `floor(shares*ratio/100)*100`，结果不足一手则不生成订单并标 `SELL_PARTIAL_REJECTED_LOT_SIZE`；无持仓卖出信号为 `SELL_REJECTED_NO_POSITION`。不写 `portfolio_positions`，不接券商，不把卖出建议净额结算到同批买入。

#### 4.3.2 三方依赖能力评估

`instrument_daily`/`factor_daily` 现有 DAO 已升序；执行期以 200 code 批次参数化批量读取消除 N+1。行情每日 08:30 批处理采集后日内基本静态，扫描（分钟级）中途被修订的概率极低、影响单票，可接受。`market.instrument` 已是全市场本地目录，流式只读枚举不依赖 Tushare 区间端点，规避代理全市场区间拉取静默截断。行业字典已种子化但行业成员未采集，故新增采集、持久化 ingest_state 与代理端点 POC 是行业仓位 BUY 门控的前置交付，不以“表存在”假定数据可用。LangGraph 仅编排背景与量化入口；策略/订单保持可测的纯函数和有界进程执行器。

#### 4.3.3 风险与验证方式

测试全市场活跃 CN 枚举与持仓去重、共同数据水位、日线/因子按真实日期等值对齐、错位日期、停牌/暖机不足/因子缺行、除权日前后、数据库侧每票窗口裁剪、200-code 分批、取消/失租和无僵尸子进程、全局排序、6,000 标的真实 runner 全链路性能、attempt 隔离 cursor、风险/现金/总/单票/行业/组合开放风险、未完成订单预留、非 CN 持仓、负 `unallocated_assets`、entry 与当前原价偏离、涨跌停、ST、T+1、费用/滑点、成交额参与率、整手与零股；执行后断言组合持仓无写入。至少一组 loader fixture 必须让不同日期具有不同 MA/RSI 值，并直接断言上下文非空且对应正确日期，避免“测试策略未读取指标”掩盖对齐错误。

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

- 策略页按页面/列表/编辑 Dialog/版本历史拆分，使用 React Query 与生成 Service；脚本使用已有深色 CodeMirror 编辑器，支持 Python 着色、缩进和格式化，新建时必须输入非空代码，原因说明才走 `MarkdownView`。

- 组合页新增 `PortfolioSettingsDialog` 原子编辑账户字段，列表显示资产/现金/风险摘要和“任务仅读取提交时快照”的提示。

- `AnalysisTaskForm` 保留全部层选择；选择 `position` 时加载已发布策略/组合，未选不可提交；取消 `position` 时清空三字段；AI 层可与 `position` 同选（照旧独立运行，互不读取），idempotency snapshot 包含 strategy version、portfolio ID/version。

- OpenAPI 重导出后，生成的 `ReportDTO`/量化嵌套 model 是前端唯一类型来源；既有 `frontend/src/modules/analysis/pages/task-detail/reportMappers/toReportViewModels.ts` 的 `toReportViewModel` 将可空字段映射为 `quantExecution`。`ReportContent` 在报告元信息之后、sections 之前挂载 `QuantExecutionPanel`（非空时），不伪造 section。量化面板展示“全市场量化买点”及扫描统计，以 cursor 分页加载完整 BUY/持仓审计结果，再独立展示“建议订单（需人工确认，未下单）”；`block`/资金/仓位/行业拒绝的 BUY 仍显示在买点列表，无下单按钮。

#### 4.4.2 三方依赖能力评估

React Query、生成 client、Dialog/Card/Badge 满足需求，复用已有 CodeMirror 编辑器。前端 pnpm/OpenAPI codegen 及 MarkdownView 约定保持不变。

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

### 4.6 模板注册、参数与输入门控

> §4.7–4.13 的开仓公式与参数按用户提供文稿保留。成本止损已确定由平台 `PositionLifecycleManager` 使用确认成交与冻结订单止损裁决；脚本只负责技术结构信号。所有持仓规则依赖真实成交；不得仅凭建议订单或模板来源标签推进状态。

#### 4.6.1 模块设计

服务端参考规则注册表 `StrategyTemplateDefinition` 保存七套示例的标识、参数 schema、字段需求、渲染器和固定原因码。用户创建策略必须输入代码，可以借用示例并修改；发布和执行以实际源码为准。示例的参数校验及 required_fields 适用于相应渲染源码；自定义脚本从实际 AST 读取集合建立输入校验，不能直接沿用已被修改的模板声明。

| `template_id` | 展示名 | 最少 bars | `required_indicators` | 日线定位 |

|------|----------|-----------|-----------------------|----------|

| `ma_trend_cross_v1` | 均线趋势交叉 | 2 根完整对齐 bar，且 MA60 已暖机 | `ma_qfq_5, ma_qfq_20, ma_qfq_60, rsi_qfq_6, macd_qfq` | 数日到数周的日线波段。 |

| `trend_pullback_v1` | 强势回撤反弹 | 2 根完整对齐 bar，且 MA60/布林已暖机 | `ma_qfq_20, ma_qfq_60, boll_lower_qfq, rsi_qfq_6, macd_qfq` | 数日到两周的日线波段。 |

| `boll_volume_breakout_v1` | 布林放量突破 | 6 根完整对齐 bar，且 MA60/布林已暖机 | `ma_qfq_20, ma_qfq_60, boll_mid_qfq, boll_upper_qfq, rsi_qfq_6, macd_qfq` | 数日到两周的日线波段。 |

| `macd_rsi_reversal_v1` | MACD-RSI 动量反转 | 2 根完整对齐 bar，且 MA20/MACD 已暖机 | `ma_qfq_20, macd_dif_qfq, macd_dea_qfq, macd_qfq, rsi_qfq_6` | 数日到一周；反转优先级低于趋势/突破策略。 |

| `ma5_pre_cross_v1` | MA5 预上穿 MA20 提前布局 | 20 根完整对齐 bar，且 MA60 已暖机 | `ma_qfq_5, ma_qfq_20, ma_qfq_60, rsi_qfq_6, macd_qfq` | 数日到两周；以明日交叉临界价为预判，不承诺次日一定上穿；确认成交后须在 3 个有效交易日内实际金叉。 |

| `volume_surge_confirm_v1` | 成交量骤增确认 | 6 根完整对齐 bar，且 MA60 已暖机 | `ma_qfq_20, ma_qfq_60, rsi_qfq_6, macd_qfq` | 数日到两周；不要求布林上轨突破。 |

| `arc_bottom_75a_v1` | 圆弧底 75A | 41 根完整对齐 bar，且 MA60 已暖机 | `ma_qfq_20, ma_qfq_60, macd_qfq, rsi_qfq_6` | 数周到两月的日线右侧突破；`75A` 是项目内模板名称，不假定为外部标准公式。 |

表中的最少 bars 是源码实际索引需求，不等于指标计算暖机期；两者均满足才可执行。Loader 可以保留最多 250 根审计窗口，但不得把 250 根变成所有模板共同的资格门槛。

参数在发布期严格校验；未知键、缺失键、非数值、NaN/Infinity、越界、类型错误和不满足交叉约束均返回 `STRATEGY_VALIDATION_FAILED`，不自动修正。

| 模板 | 参数 | 类型/范围 | 默认 | 是否可编辑 | 公式位置 |

|------|------|-----------|------|------------|----------|

| 均线趋势交叉 | `stop_pct` | Decimal，`0.03≤x≤0.10` | `0.06` | 是 | 成本止损、BUY stop。 |

|  | `reward_multiple` | Decimal，`2.00≤x≤3.00` | `2.50` | 是 | BUY target。 |

| 强势回撤反弹 | `rsi_buy_low` | 整数，`20≤x≤45` | `35` | 是 | 当日 RSI 下界。 |

|  | `rsi_buy_high` | 整数，`45≤x≤70` 且 `low<high` | `55` | 是 | 当日 RSI 上界。 |

|  | `stop_pct` / `reward_multiple` | 同上 | `0.05` / `2.20` | 是 | 成本止损、BUY target。 |

| 布林放量突破 | `volume_multiple` | Decimal，`1.20≤x≤3.00` | `1.50` | 是 | 当日量能阈值。 |

|  | `rsi_ceiling` | 整数，`60≤x≤85` | `75` | 是 | RSI 上界；下界固定 50。 |

|  | `floor_stop_pct` / `reward_multiple` | Decimal，`0.03≤x≤0.10` / `2.00≤x≤3.00` | `0.06` / `2.00` | 是 | 固定风险下限、BUY target。 |

| MACD-RSI 动量反转 | `rsi_oversold` | 整数，`20≤x≤40` | `35` | 是 | 前日 RSI 超卖阈值。 |

|  | `rsi_entry_ceiling` | 整数，`40≤x≤65` 且 `oversold<ceiling` | `55` | 是 | 当日 RSI 上界。 |

|  | `stop_pct` / `reward_multiple` | 同上 | `0.05` / `2.00` | 是 | 成本止损、BUY target。 |

| MA5 预上穿 MA20 | `max_projected_cross_pct` | Decimal，`0.005≤x≤0.05` | `0.02` | 是 | 明日交叉临界价相对现价的最大上涨距离。 |

|  | `confirmation_window_trading_days` | 固定整数 `3` | `3` | 否 | 确认成交后的有效交易日兑现时限；第 3 日仍未 `MA5>MA20` 则 `EXPECTATION_TIMEOUT`。 |

|  | `stop_pct` / `reward_multiple` | Decimal，`0.03≤x≤0.10` / `2.00≤x≤3.00` | `0.05` / `2.20` | 是 | 成本止损、BUY target。 |

| 成交量骤增确认 | `volume_multiple` | Decimal，`1.20≤x≤5.00` | `2.00` | 是 | 当日量相对前五日最大量的阈值。 |

|  | `min_price_gain_pct` | Decimal，`0.005≤x≤0.08` | `0.015` | 是 | 当日收盘相对昨日收盘的最小涨幅，排除放量下跌/弱反弹。 |

|  | `stop_pct` / `reward_multiple` | Decimal，`0.03≤x≤0.10` / `2.00≤x≤3.00` | `0.06` / `2.00` | 是 | 成本止损、BUY target。 |

| 圆弧底 75A | `min_left_decline_pct` | Decimal，`0.05≤x≤0.20` | `0.08` | 是 | 40 日窗口左侧起点到中部基准价所需的最小回落幅度。 |

|  | `min_recovery_pct` | Decimal，`0.03≤x≤0.15` | `0.06` | 是 | 中部基准价到右侧基准价所需的最小回升幅度。 |

|  | `neckline_breakout_pct` | Decimal，`0.005≤x≤0.05` | `0.01` | 是 | 收盘相对冻结颈线的最小有效突破幅度。 |

|  | `volume_multiple` | Decimal，`1.20≤x≤5.00` | `1.50` | 是 | 突破日成交量相对前五日最大量的阈值。 |

|  | `stop_pct` / `reward_multiple` | Decimal，`0.03≤x≤0.10` / `2.00≤x≤3.00` | `0.06` / `2.20` | 是 | 成本止损、BUY target。 |

MA 周期、布林带、MACD 字段和评分分档是模板定义常量，V1 不开放修改；若未来开放周期参数，必须先扩充因子采集、上下文合同和字段白名单，不能在脚本内自算。

移动止损**不是 Sandbox 模板参数**：模板只定义初始 `stop_loss` 与 `reward_multiple`。首笔成交确认时，Lifecycle Manager 冻结 `risk_capacity_shares`、实际成本、初始止损、`profit_take_price`，并将版本关联的 `TrailingStopConfig` 冻结入状态；其中 `breakeven_activation_r`、`trailing_activation_r`、`trailing_drawdown_pct` 不设置推荐默认值，用户显式配置且校验通过后才启用。`confirmation_window_trading_days=3` 是 `ma5_pre_cross_v1` 的版本常量：成交时复制进预期状态，后续版本变动不影响持仓。

#### 4.6.2 三方依赖能力评估

本模块不新增第三方依赖。模板渲染仅输出基础 Sandbox 已允许的条件、常量索引、数值运算、比较、布尔表达式、`min/max` 和固定字符串字面量；不使用循环、变量下标、属性、f-string、`str()`、动态键或网络调用。全市场因子采集的代理能力必须通过 C1 POC 实测，不能以官方接口文档替代。

#### 4.6.3 风险与验证方式

- 四向契约测试：每个模板的“渲染源码实际访问路径/最早负索引”必须等于 `required_fields`，并同时出现在 C2 上下文投影和 C3 `strategy_contract.py` 允许路径中；任一漏配使测试失败。

- 参数测试：每个参数最小/最大值、相邻越界、未知键、缺失键、交叉约束和规范化 JSON 都有 fixture。

- 渲染测试：默认参数与边界参数生成的七套源码逐个经过真实 AST validator 与一次性 runner，返回结果严格符合 §2.5 七键矩阵。

- 快照/报告回归：修改注册表显示名或渲染器后重跑旧任务，断言任务快照、signal cursor 与 ReportDTO 仍展示创建任务时冻结的模板摘要。

- 价格双口径测试：分别断言 raw signal 和 normalized order 价格；覆盖低价股、风险距离不足一 tick、布林中轨贴近 entry、组合最小收益比高于模板倍数与 `INVALID_PRICE_RANGE` 拒绝。

#### 4.6.4 文件变更清单

| 类别 | 路径 | 改动说明 |

|------|------|----------|

| 基础前置（新建） | `AI/strategy_sandbox/strategy_contract.py`、`AI/strategy_sandbox/` | 无业务依赖的字段/索引/七键声明、AST 路径注册、受限 runner 与模板源码实际校验；在既有模块上扩展。 |

| 基础前置（新建） | `backend/modules/quant_strategy/` | 策略版本、模板字段、服务、仓储与执行服务；已有实现，按增量修改。 |

| 基础前置（修改） | `AI/dataflows/providers/base_provider.py`、`AI/dataflows/providers/cn/tushare.py` | 增加受控股票因子 DataFrame 接口及代理端点 POC/采集实现。 |

| 基础前置（修改） | `db/instrument/ingest/`、`db/instrument/dao/factor_daily.py` | 全市场股票因子按日采集、升序对齐、批量入库与失败 rollback。 |

| 模板层（新建） | `backend/modules/quant_strategy/domain/templates.py` | 静态注册表、参数 schema、模板渲染器和输入要求。 |

| 模板层（新建） | `backend/tests/unit/quant_strategy/test_strategy_templates.py` | 参数、渲染、七键协议和七套策略公式测试。 |

| 跨链路（修改） | 本方案创建的策略版本迁移、ORM、DTO、router、`QuantTaskSubmissionService`、任务策略快照、signal cursor、报告 DTO | 增加 `template_id/template_params/template_renderer_version`；创建任务冻结显示名/参数/渲染器版本/源码 SHA，rerun 与报告只读快照；信号和订单分别保存 raw/normalized 价格及订单拒绝码。具体文件以本统一任务落地路径为准。 |

| 前端（新建/修改） | 本方案新增的策略管理页面及其 generated API client 消费层 | 模板选择、参数表单、公式说明、创建/发布和报告摘要；后端 OpenAPI 导出后执行 codegen，禁止手改 `frontend/src/api/generated/`。 |

### 4.7 均线趋势交叉策略

#### 4.7.1 模块设计

**策略意图**：在中期趋势向上时，只捕捉 MA5 对 MA20 的首次金叉；长期下跌趋势里的短暂反弹不构成 BUY。

令 \(F=MA5\)、\(M=MA20\)、\(S=MA60\)、\(R=RSI6\)、\(H=MACD\)。无持仓 BUY 必须同时满足：

\[

F_t>M_t \land F_{t-1}\le M_{t-1}

\]

\[

M_t>S_t \land C_t>M_t \land 45\le R_t\le70 \land H_t>0

\]

其中首式保证“当日首次金叉”；第二式保证中期趋势、价格位置与正动量。参数为 \(p=stop\_pct\)、\(k=reward\_multiple\)：

\[

entry=C_t,\quad stop=entry(1-p),\quad take=entry+k(entry-stop)

\]

BUY 基础分固定为 70；`R_t∈[50,65]` 加 5 分、`H_t>H_{t-1}` 加 5 分、`C_t>F_t` 加 5 分，因此分数范围为 70–85，所有分档均可构造。

**中文操作规则**：

- **开仓**：昨天 MA5 还没有高过 MA20，今天首次上穿；同时 MA20 高于 MA60、收盘站上 MA20、RSI 在 45–70、MACD 为正。信号被接受并实际成交后，以风险允许最大仓位的 50% 建立首仓。

- **加仓**：首仓后的下一有效交易日，MA5 仍在 MA20 上方、MA20 仍在 MA60 上方，并且收盘站在 MA5 上方，说明金叉没有立刻走弱；只补一次差额，使目标仓位达到 100%。

- **减仓**：未触发清仓时，MA20 不再高于 MA60，或 MACD 比前一日走弱，说明趋势/动量变弱；将目标仓位降至 50%。

- **清仓**：收盘跌到初始止损价以下、跌破前一交易日已经生效的移动止损价，或 MA5 从上向下跌破 MA20 时，目标仓位归零。

持仓退出业务优先级如下（不表示现行脚本已经能读取初始成交事实）：

1. 成交后成本止损由平台 `PositionLifecycleManager` 按真实初始成交与已接受订单止损处理，触发时生成目标 0 / `STOP_LOSS`；脚本不重复计算该条件；

2. 否则若 \(F_t<M_t\land F_{t-1}\ge M_{t-1}\)，`SELL_ALL / MA_DEATH_CROSS`；

3. 否则 `HOLD / NO_SIGNAL`。

**例子**：若 `MA5[-2]=10.00≤MA20[-2]=10.05`、`MA5[-1]=10.20>MA20[-1]=10.10>MA60[-1]=9.80`、`close[-1]=10.30`、`RSI6[-1]=56`、`MACD[-1]=0.08`，则产生 BUY；以 `p=0.06,k=2.50` 计算 raw `stop=9.682`、raw `take=11.845`，最终订单层按 §2.5 规范价格。

#### 4.7.2 三方依赖能力评估

依赖 C1–C3 已验收的 MA、RSI、MACD 字段；不请求在线 Tushare，也不在策略中计算均线。

#### 4.7.3 风险与验证方式

- 前日相等、当日刚上穿应 BUY；前日已上穿应 HOLD。

- `M_t≤S_t`、RSI=71、MACD≤0 任一情形 HOLD。

- 成本止损与死亡交叉同时触发时由平台按“成本/移动止损清仓优先于模板结构失效”裁决；脚本不读取初始成交价，平台集成测试必须覆盖该优先级。

#### 4.7.4 文件变更清单

模板逻辑在 `templates.py` 的 `ma_trend_cross_v1` 定义中实现，测试加入 `test_strategy_templates.py`；不单独创建无复用价值的每模板 Python 模块。

### 4.8 强势回撤反弹策略

#### 4.8.1 模块设计

**策略意图**：只在 \(MA20>MA60\) 的中期上行状态，识别价格前一日触及/跌破布林下轨、当日收回下轨后的回撤修复；持续处于下轨下方时不接飞刀。

令 \(B_l=BOLL\_LOWER\)、\(H=MACD\)、\(R=RSI6\)，参数为 \(r_l=rsi\_buy\_low\)、\(r_h=rsi\_buy\_high\)、\(p\)、\(k\)。无持仓 BUY 必须同时满足：

\[

MA20_t>MA60_t

\]

\[

C_{t-1}\le B_{l,t-1}\land C_t>B_{l,t}

\]

\[

r_l\le R_t\le r_h\land R_t>R_{t-1}\land H_t\ge H_{t-1}

\]

价格：\(entry=C_t\)、\(stop=entry(1-p)\)、\(take=entry+k(entry-stop)\)。评分固定起点 68；`C_t>MA20_t` 加 8 分、`R_t∈[40,50]` 加 7 分、`H_{t-1}<0\land H_t\ge0` 加 7 分，范围 68–90。

**中文操作规则**：

- **开仓**：中期趋势向上（MA20 高于 MA60）时，昨天收盘触及或跌破布林下轨，今天重新收回下轨；RSI 位于允许范围且比昨天回升，MACD 不再变弱。实际成交后以风险允许最大仓位的 50% 试仓。

- **加仓**：下一有效交易日仍保持中期上升趋势、收盘仍在布林下轨上方、RSI 与 MACD 没有继续走弱，且收盘不低于首笔实际成交价；目标加至 100%。

- **减仓**：未触发清仓时，收盘再次回到或跌到布林下轨，RSI 比昨天下降，或 MACD 比昨天走弱，任一成立即把目标仓位降至 50%。

- **清仓**：收盘跌破初始止损价、跌破前一交易日已生效的移动止损价，或收盘跌破布林下轨且 RSI 同时下降，说明回撤修复失败，目标仓位归零。

持仓时，平台先裁决初始/移动止损；未触发时脚本只在 \(C_t<B_{l,t}\land R_t<R_{t-1}\) 返回 `PULLBACK_INVALIDATION`，其余 HOLD。

**例子**：`MA20=12.20>MA60=11.70`，昨日 `close=11.90≤boll_lower=12.00`，当日 `close=12.15>boll_lower=12.05`，RSI6 从 38 升至 44，且 MACD 柱不再减弱，则 BUY；当日仍未收回下轨时 HOLD。

#### 4.8.2 三方依赖能力评估

依赖 C1–C3 的 MA、布林、RSI、MACD 字段。不计算布林标准差、不请求 ATR。

#### 4.8.3 风险与验证方式

- `MA20≤MA60` 时，下轨回收也 HOLD。

- 前日触轨、当日没有收回下轨时 HOLD。

- 下轨失守且 RSI 下降为结构卖出；RSI/MACD 任一缺失由输入门控拦截，不调用脚本。

#### 4.8.4 文件变更清单

模板逻辑与测试收敛在 §4.6.4 的模板注册表和参数化单测中。

### 4.9 布林放量突破策略

#### 4.9.1 模块设计

**策略意图**：在中期趋势向上时，捕捉收盘价首次突破布林上轨且成交量显著高于最近五日基线的行情。该基线使用固定索引，可在当前 AST 约束下展开，不需要循环。

令 \(B_m=BOLL\_MID\)、\(B_u=BOLL\_UPPER\)，并定义：

\[

V_{base}=max(V_{t-1},V_{t-2},V_{t-3},V_{t-4},V_{t-5})

\]

参数为 \(q=volume\_multiple\)、\(r_c=rsi\_ceiling\)、\(p=floor\_stop\_pct\)、\(k=reward\_multiple\)。无持仓 BUY 必须同时满足：

\[

MA20_t>MA60_t\land C_{t-1}\le B_{u,t-1}\land C_t>B_{u,t}

\]

\[

V_{base}>0\land V_t>0\land V_t\ge qV_{base}\land 50\le RSI6_t\le r_c

\]

价格为：

\[

entry=C_t,\quad stop=max(B_{m,t},entry(1-p)),\quad take=entry+k(entry-stop)

\]

入场条件保证正常布林带中 \(C_t>B_{u,t}>B_{m,t}\)。若数据异常导致 `stop>=entry`，模板不产生 BUY；输入/输出校验记录 `signal_audit_code=INVALID_PRICE_RANGE`，与订单层的 `order_status=INVALID_PRICE_RANGE` 明确区分。评分起点 72；`V_t≥2V_{base}` 加 8 分、`RSI6_t∈[55,70]` 加 5 分、`C_t>1.03MA20_t` 加 5 分，范围 72–90。

**中文操作规则**：

- **开仓**：MA20 高于 MA60 的上升趋势中，昨天收盘尚未突破布林上轨，今天收盘首次站上上轨；当日成交量至少达到前五日最大量的设定倍数，且 RSI 在允许范围。实际成交后以风险允许最大仓位的 50% 建仓。

- **加仓**：下一有效交易日收盘仍在布林上轨上方、没有低于首笔实际成交价、MACD 没有变弱，且成交量至少不低于此前五日量能基线；目标加至 100%。

- **减仓**：未触发清仓时，收盘回到或跌到布林上轨内、成交量低于此前五日基线，或 MACD 比昨天走弱，任一成立即将目标仓位降至 50%。

- **清仓**：收盘跌破初始止损价、跌破前一交易日已生效的移动止损价，或收盘跌破布林中轨且 MACD 为负，说明突破失败，目标仓位归零。

持仓时，平台先裁决初始/移动止损；未触发时脚本只在 \(C_t<B_{m,t}\land MACD_t<0\) 返回 `BREAKOUT_INVALIDATION`；仅收回上轨以内但仍在中轨上方时 HOLD。

**例子**：前五日最大成交量为 80 万手、当日 130 万手，昨收 20.00 未越过上轨 20.10，当日收 20.50 越过上轨 20.30，且 MA20>MA60、RSI6=63，则 BUY；当日仅 100 万手时因不满足 `1.5×80` 万手而 HOLD；前五日全为零时也 HOLD。

#### 4.9.2 三方依赖能力评估

成交量来自 `market.instrument_daily`，布林/MA/RSI/MACD 来自 C1 的因子入库。日线消费维持升序归一；不得为了成交量均线新增本地技术指标计算。

#### 4.9.3 风险与验证方式

- 前收等于上轨、当日上破可触发；前收已在上轨上方则 HOLD。

- 验证 `Vbase=0`、`Vt=0`、恰好 `q×Vbase`、略低于阈值四种量能边界。

- 验证中轨异常时不会输出非法 BUY 价格。

#### 4.9.4 文件变更清单

模板逻辑与测试收敛在 §4.6.4 的模板注册表和参数化单测中。

### 4.10 MACD-RSI 动量反转策略

#### 4.10.1 模块设计

**策略意图**：在 RSI 先进入超卖、随后回升时，等待 MACD 在 0 轴下金叉和柱体改善再介入；这是反转策略，执行层可使用其较低的评分上限区分优先级。

令 \(D=DIF\)、\(E=DEA\)、\(H=MACD\)、\(R=RSI6\)，参数为 \(r_o=rsi\_oversold\)、\(r_c=rsi\_entry\_ceiling\)、\(p\)、\(k\)。无持仓 BUY 必须同时满足：

\[

D_{t-1}\le E_{t-1}\land D_t>E_t\land D_t<0

\]

\[

H_t>H_{t-1}\land R_{t-1}<r_o\le R_t\le r_c

\]

\[

C_t\ge0.97MA20_t

\]

因此“超卖后反转”是可验证条件，而不是仅看当日 RSI 落在区间。价格：\(entry=C_t\)、\(stop=entry(1-p)\)、\(take=entry+k(entry-stop)\)。评分起点 65；`H_t>0` 加 8 分、`R_t≥40` 加 7 分、`C_t>MA20_t` 加 5 分，范围 65–85，不存在恒真加分项。

**中文操作规则**：

- **开仓**：昨天 RSI 低于超卖阈值，今天 RSI 向上回到允许区间；同时 DIF 在 0 轴下方上穿 DEA、MACD 柱比昨天改善，且收盘没有显著低于 MA20。实际成交后以风险允许最大仓位的 50% 试仓。

- **加仓**：DIF 第一次从 0 轴下方上穿到 0 轴上方，代表反转由早期修复转为更强的上行动量；目标加至 100%。

- **减仓**：未触发清仓时，DIF 与 DEA 的差距比前一日缩小，或 RSI 比昨天下降，代表反转动量在衰减；目标仓位降至 50%。

- **清仓**：收盘跌破初始止损价、跌破前一交易日已生效的移动止损价，或 DIF 再次跌到 DEA 下方且 MACD 为负，说明反转失败，目标仓位归零。

持仓时，平台先裁决初始/移动止损；未触发时脚本只在 \(D_t<E_t\land H_t<0\) 返回 `MACD_REVERSAL_FAILURE`；其余 HOLD。

**例子**：昨日 `DIF=-0.30≤DEA=-0.25`、`RSI6=31<35`，当日 `DIF=-0.18>DEA=-0.22`、MACD 柱从 -0.10 改善到 0.04、RSI6 升至 42 且收盘不低于 MA20 的 97%，则 BUY；若昨日 RSI=50，则即使 MACD 金叉也 HOLD。

#### 4.10.2 三方依赖能力评估

只使用 C1–C3 已验收的 MACD、RSI、MA 和 OHLCV；不使用资金流、新闻、情绪等未进入单票上下文的数据。

#### 4.10.3 风险与验证方式

- DIF 必须在 0 轴下金叉；0 轴上金叉不属于本模板。

- RSI 必须满足“昨日低于超卖阈值、当日上穿”；只在区间内上升但未经历超卖时 HOLD。

- 覆盖成本止损、MACD 失败卖出、价格跌离 MA20 超过 3% 和因子缺失门控。

#### 4.10.4 文件变更清单

模板逻辑与测试收敛在 §4.6.4 的模板注册表和参数化单测中。

### 4.11 MA5 预上穿 MA20 提前布局策略

#### 4.11.1 模块设计

**策略意图**：不把“明日会涨”当作事实，而是根据已冻结的 MA5、MA20 与固定历史收盘价，计算使明日 MA5 首次严格高于 MA20 的**临界收盘价**。只有临界价比今日收盘高、且所需上涨幅度不超过用户设定上限时，才在趋势和动量确认下提前布局；若次日没有达到临界价，策略不得声称已经金叉。

令 \(F=MA5\)、\(M=MA20\)、\(S=MA60\)，\(C_i\) 为第 \(i\) 日收盘价，\(X=C_{t+1}\) 为假设的明日收盘价。滚动窗口的精确关系为：

\[

F_{t+1}=F_t+\frac{X-C_{t-4}}{5},\qquad M_{t+1}=M_t+\frac{X-C_{t-19}}{20}

\]

因此明日 MA5 与 MA20 相等的临界价为：

\[

X_{cross}=\frac{20(M_t-F_t)+4C_{t-4}-C_{t-19}}{3}

\]

无持仓 BUY 必须同时满足，其中 \(d=max\_projected\_cross\_pct\)：

\[

F_t\le M_t\land F_t>F_{t-1}\land M_t>S_t\land C_t>F_t

\]

\[

0<X_{cross}-C_t\le dC_t\land 45\le RSI6_t\le70\land MACD_t\ge0

\]

第二式意味着“若明日收盘严格高于 \(X_{cross}\) 则会金叉（等于时两均线相等）”，而不是预测明日价格；`X_cross <= C_t`、今天已经金叉或需要上涨超过上限时均 HOLD。参数为 \(p=stop\_pct\)、\(k=reward\_multiple\)：

\[

entry=C_t,\quad stop=entry(1-p),\quad take=entry+k(entry-stop)

\]

评分起点为 68；若所需上涨距离 \((X_{cross}-C_t)/C_t\le1\%\) 加 8 分、\(RSI6_t\in[50,65]\) 加 5 分、\(MACD_t>MACD_{t-1}\) 加 5 分，范围为 68–86。

**中文操作规则**：

- **开仓**：今天 MA5 还没有超过 MA20，但 MA5 正在上升、MA20 高于 MA60、收盘站在 MA5 上方；按历史 20 日数据推算，明天只需在允许涨幅内上涨就可能形成金叉；同时 RSI 在 45–70、MACD 不为负。实际成交后以风险允许最大仓位的 50% 试仓。

- **加仓**：成交后的前三个完整有效交易日内，只要第一次出现 MA5 高于 MA20，即确认实际金叉，目标加至 100%。

- **减仓**：尚未确认金叉时，如果 MA5 不再上升，或 MACD 比昨天走弱，表示提前布局的预期变弱；目标降至 25%。后续重新转强但仍未金叉时，最多恢复到 50%，不能提前加满仓。首次达到收益目标价时也要减仓至 50%，但不会为凑到 50% 而反向买入。

- **清仓**：收盘跌破初始止损价、跌破前一交易日已生效的移动止损价、出现 MA5 向下跌破 MA20，或第三个有效交易日结束后 MA5 仍未高于 MA20，任一成立即清仓。最后一种是“预期落空”退出，而不是等待无限期持有。

**全流程目标仓位**：实际首笔成交后冻结风险允许最大仓位 \(Q_{risk}\)（按 §2.5 和组合风险预算计算），并进入 `PROBE`，目标仓位为 \(0.50Q_{risk}\)。成交日不计入窗口；从其后的 `market.instrument_daily` 实际有效交易日依次计为第 1、2、3 日，停牌无日线不计数，不能用自然日代替。

- **加仓 / 预期兑现**：第 1–3 日任一天首次满足 \(F_t>M_t\) 时，标记 `FULFILLED`，目标仓位从 \(0.50Q_{risk}\) 提至 \(Q_{risk}\)，原因码 `PRE_CROSS_CONFIRMED_ADD`。只有加仓差额实际成交后才转入 `CONFIRMED`；若现金/组合上限裁剪加仓，则保留未完成目标并在后续日重新评估，不重复把同一已成交部分再计入。

- **减仓 / 预期弱化**：仍处于 `PROBE`、尚未兑现且 \(F_t\le F_{t-1}\) **或** \(MACD_t<MACD_{t-1}\) 时，目标仓位降至 \(0.25Q_{risk}\)，原因码 `PRE_CROSS_WEAKENING_REDUCE`。弱化发生在第 1 或第 2 个观察日时立即提出差额减仓；若次日重新转强但尚未实际金叉，目标最多恢复到 \(0.50Q_{risk}\)，不允许在预期兑现前增至满仓。

- **清仓 / 预期落空**：第 3 个有效交易日收盘仍 \(F_t\le M_t\) 时，前置假设“短期会金叉”已被证伪，目标仓位为 0，生成 `SELL_EXIT / EXPECTATION_TIMEOUT`；这不是因价格亏损而卖出，而是因建仓理由失效而退出。

- **获利减仓**：任一已成交阶段收盘首次达到冻结 `profit_take_price` 时，目标仓位为 \(0.50Q_{risk}\)，原因码 `PROFIT_TARGET_TRIM`，且 `profit_trim_completed` 后不因再次触价重复减仓；余仓继续走移动止损。

同一交易日的固定优先级为：① `STOP_LOSS` / `TRAILING_STOP_LOSS`（目标 0），② `EXPECTATION_TIMEOUT`（目标 0），③ `MA_DEATH_CROSS`（目标 0），④ `PROFIT_TARGET_TRIM`（目标 50%），⑤ `PRE_CROSS_WEAKENING_REDUCE`（目标 25%），⑥ `PRE_CROSS_CONFIRMED_ADD`（目标 100%）。同层级若目标低于当前仓位则取更低目标；前一项已产生目标 0 时不再计算后项。因子缺失或成交后第 3 个应观察交易日缺失时只记录 `EXPECTATION_DATA_UNAVAILABLE`，不伪造超时卖单，原有止损与结构失效保护仍继续运行。

**例子**：`MA5[-1]=10.00`、`MA20[-1]=10.08`、`close[-5]=9.80`、`close[-20]=9.20` 时，\(X_{cross}=(20×0.08+4×9.80-9.20)/3=10.533\)。若 `close[-1]=10.35`，所需上涨约 1.77%，在默认 2% 上限内；再满足趋势、RSI 与 MACD 条件时 BUY。若随后确认成交，后续三个有效交易日中任一日 `MA5>MA20` 即兑现；若第三日仍 `MA5≤MA20` 且未先触发价格/移动止损，则输出 `EXPECTATION_TIMEOUT`。若现价为 10.20，则需上涨约 3.26%，默认参数下 HOLD。

#### 4.11.2 三方依赖能力评估

只使用 C1–C3 已验证的 MA、RSI、MACD 与本次 OHLCV；`close[-5]` 和 `close[-20]` 是固定常量索引，AST 无需变量窗口或循环。不得用实时行情、未来日线或本地重算均线补足预测。

#### 4.11.3 风险与验证方式

- 用手工窗口数据断言上述 \(X_{cross}\) 公式，并构造 `X_cross` 恰高于、恰等于、低于当前收盘的三种边界。

- `MA5[-1]>MA20[-1]`、`MA5[-1]≤MA5[-2]`、`MA20[-1]≤MA60[-1]`、临界涨幅超过上限任一情形均 HOLD。

- 对确认成交后的持仓构造三个连续有效交易日：第 1/2/3 日任一首次 `MA5>MA20` 均标记 `FULFILLED`，并生成从 50% 到 100% 的唯一加仓差额；第 3 日仍未上穿则目标为 0，仅产生一条 `EXPECTATION_TIMEOUT`。成交日、周末、节假日和无日线停牌日均不消耗窗口。

- 覆盖未兑现时 `MA5≤前日MA5`、MACD 变弱两类条件均从 50% 降至 25%，转强但未金叉仅恢复至 50%，不得直接加至 100%。

- 覆盖 `STOP_LOSS`/`TRAILING_STOP_LOSS → EXPECTATION_TIMEOUT → MA_DEATH_CROSS → PROFIT_TARGET_TRIM → PRE_CROSS_WEAKENING_REDUCE → PRE_CROSS_CONFIRMED_ADD` 优先级：前项退出后不得继续评估后项；同一持仓同一交易日重跑不重复生成加/减/退出建议。

- 验证 `close[-20]`、MA5/MA20 或成交后第 3 个应观察交易日缺失时，分别由输入门控或持仓风控记录数据不可用，绝不以自然日猜测超时。

#### 4.11.4 文件变更清单

模板逻辑与测试收敛在 §4.6.4 的模板注册表和参数化单测中；兑现时限状态、跨日计数、目标仓位优先级和报告审计由 §4.16/§4.17 的 `PositionLifecycleManager` 实现。

### 4.12 成交量骤增确认策略

#### 4.12.1 模块设计

**策略意图**：识别相对前五个交易日显著放量、同时价格向上推进的趋势确认，而不是把放量下跌或仅由布林上轨突破定义的行情当作买点。

令 \(V_{base}=max(V_{t-1},V_{t-2},V_{t-3},V_{t-4},V_{t-5})\)，参数为 \(q=volume\_multiple\)、\(g=min\_price\_gain\_pct\)、\(p=stop\_pct\)、\(k=reward\_multiple\)。无持仓 BUY 必须同时满足：

\[

V_{base}>0\land V_t\ge qV_{base}\land C_t\ge C_{t-1}(1+g)

\]

\[

MA20_t>MA60_t\land C_t>MA20_t\land 50\le RSI6_t\le75\land MACD_t>0

\]

其中第一式的前五日基线只使用截至昨日的量，避免将当日量重复计入比较；第二式排除放量下跌、弱反弹和中期趋势向下的放量。价格为：

\[

entry=C_t,\quad stop=entry(1-p),\quad take=entry+k(entry-stop)

\]

评分起点为 70；`V_t≥3V_base` 加 10 分、`C_t≥1.03C_{t-1}` 加 5 分、`RSI6_t∈[55,68]` 加 5 分，范围为 70–90。

**中文操作规则**：

- **开仓**：当日成交量至少达到前五个交易日最大成交量的设定倍数，并且当日收盘相对昨天达到最低涨幅；同时 MA20 高于 MA60、收盘站在 MA20 上方、RSI 在 50–75、MACD 为正。实际成交后以风险允许最大仓位的 50% 建仓。

- **加仓**：下一有效交易日收盘继续站在 MA20 上方、不低于首笔实际成交价，并且 MACD 没有走弱；目标加至 100%。

- **减仓**：未触发清仓时，收盘回到或跌到 MA20、当日成交量低于前五日量能基线，或 MACD 比昨天走弱，任一成立即将目标仓位降至 50%。

- **清仓**：收盘跌破初始止损价、跌破前一交易日已生效的移动止损价，或收盘跌破 MA20 且 MACD 为负，说明放量上涨没有得到延续，目标仓位归零。

持仓时，平台先裁决初始/移动止损；未触发时脚本只在 \(C_t<MA20_t\land MACD_t<0\) 返回 `VOLUME_SURGE_FAILURE`；其余 HOLD。

**例子**：前五日最大成交量为 80 万手、当日为 170 万手，昨日收盘 10.00、当日收盘 10.20，且 `MA20>MA60`、收盘在 MA20 上方、RSI6=60、MACD=0.06，则默认 `q=2.0,g=1.5%` 下 BUY。若当日 170 万手但收盘为 9.90，或当日收盘仅为 10.10，则分别因价格下跌、涨幅不足而 HOLD。

#### 4.12.2 三方依赖能力评估

成交量来自 `market.instrument_daily`，MA/RSI/MACD 来自 C1 的因子入库；固定 `volume[-6]` 至 `volume[-2]` 五个常量下标 可被受限 AST 展开。该模板不依赖布林带，也不得因“放量”自行请求分钟成交明细。

#### 4.12.3 风险与验证方式

- 覆盖 `Vbase=0`、`Vt=q×Vbase`、略低于阈值、放量但收跌、放量且涨幅恰等于 \(g\) 五类边界。

- `MA20≤MA60`、收盘不高于 MA20、RSI=76、MACD≤0 任一情形 HOLD。

- 覆盖止损优先于趋势失效卖出，及 `volume[-6]` 至 `volume[-2]` 五个常量下标 缺失/非正值被输入门控拒绝。

#### 4.12.4 文件变更清单

模板逻辑与测试收敛在 §4.6.4 的模板注册表和参数化单测中。

### 4.13 圆弧底 75A 策略

#### 4.13.1 模块设计

**定位与命名**：`arc_bottom_75a_v1` 是项目内定义的“圆弧底右侧突破”日线模板；“75A”仅保留为产品名称，不宣称是市场通用公式或收益等级。为适配当前受限 Sandbox，形态不扫描任意低点，而是使用**跨 40 个交易日间隔的 41 根锚点窗口**：每 10 个交易日取一个固定锚点，以“左侧回落—中部低位—右侧修复—突破颈线”形成可复现的近似定义。

设 `close[-41]`、`close[-31]`、`close[-21]`、`close[-11]`、`close[-1]` 分别为窗口的左端、左中部、底部、右中部和当前收盘；颈线基准 `N` 为左端和右中部收盘价中较高者；前五日量能基线 `Vbase` 为昨天起向前五日中的最大成交量。参数 `min_left_decline_pct`、`min_recovery_pct`、`neckline_breakout_pct`、`volume_multiple` 分别控制左侧最小回落、右侧最小修复、有效突破幅度和突破量能。

无持仓 BUY 必须同时满足：中部 `close[-21]` 相对左端至少回落默认 8%，且不高于左中部和右中部；右中部相对中部至少回升默认 6%；当前收盘至少高于颈线默认 1%，当日成交量至少为前五日最大量的 1.5 倍；同时 `MA20[-1] > MA20[-2]`、MA20 高于 MA60、RSI6 位于 50–75、MACD 为正且不弱于昨日。价格以当前收盘作为参考入场价，止损取冻结颈线和按 `stop_pct` 计算的价格止损中较高者，收益目标按 `reward_multiple` 计算。若止损不低于入场价，模板不产生 BUY，并按 §2.5 记录 `INVALID_PRICE_RANGE`。

评分起点为 70；当日成交量达到前五日最大量 2 倍加 8 分、RSI6 位于 55–68 加 5 分、当前收盘比颈线高至少 3% 加 5 分，范围为 70–88。

**中文操作规则**：

- **开仓**：观察跨 40 个交易日间隔的五个固定锚点，价格先从左侧回落到中部低位，再在右侧逐步回升；当前收盘以至少 1%（默认值）突破左端/右侧基准中较高的颈线，同时放量、MA20 严格高于昨日且高于 MA60、MACD 为正且不走弱、RSI 未过热。信号接受后，`QuantExecutionService` 在同一次执行上下文派生 `arc_neckline_price` seed；信号被实际成交后，以风险允许最大仓位的 50% 建立首仓并复制该颈线，后续不随窗口滚动改变。

- **加仓**：**仅首仓后的下一完整有效交易日**检查一次：收盘严格守在冻结颈线上方、成交量不低于前五日量能基线，且 MACD 不比前一日走弱时，只补一次差额，使目标仓位达到 100%。该日未通过即写 `confirmation_missed`，之后不得迟到加仓。

- **减仓**：未触发清仓时，收盘等于冻结颈线、跌到或低于 MA20、当日成交量低于前五日量能基线，或 MACD 比前一日走弱，任一成立即将目标仓位降至 50%。

- **清仓**：收盘跌破初始止损价、跌破前一交易日已生效的移动止损价、**严格跌破**冻结颈线，或 MA20 低于 MA60 且 MACD 为负，任一成立即目标仓位归零。跌破冻结颈线表示右侧突破已失败，不能因滚动窗口改变而放宽退出标准。

**例子**：若 `close[-41]=10.00`、`close[-31]=9.45`、`close[-21]=9.10`、`close[-11]=9.80`，则中部相对左端回落 9%，右侧相对中部回升约 7.7%，颈线为 10.00。若今日收盘为 10.15、当日成交量 150 万手而前五日最大量 90 万手、MA20 高于 MA60、RSI6=61、MACD 为正且不低于昨日，则在默认参数下 BUY；若今日收盘仅 10.05，或量能仅 120 万手，则 HOLD。

#### 4.13.2 三方依赖能力评估

不新增第三方库或数据端点。形态只消费 C1–C3 已验收且本次读取的 OHLCV、MA20/MA60、MACD、RSI6；使用固定常量索引和 `max`，不自算技术指标、不查询实时行情、不使用循环或变量下标。`market.instrument_daily` 与 `market.factor_daily` 必须按 `trade_date` 升序对齐，日线降序响应必须先经 `_sort_asc_by_trade_date` 归一后再构造上下文。

#### 4.13.3 风险与验证方式

- 构造左侧回落、中部最低、右侧恢复、当前突破的确定性 fixture，分别验证回落幅度、恢复幅度、颈线突破幅度恰好达到和略低于阈值的边界。

- 分别构造中部不低于两侧锚点、`MA20[-1]≤MA20[-2]`、MA20 不高于 MA60、RSI6=76、MACD 为负或走弱、`Vbase=0`、量能刚好/略低于阈值，均应 HOLD。

- 断言 BUY 时受控 `lifecycle_seed.arc_neckline_price` 等于 `max(close[-41], close[-11])`，并与 `source_signal_id`/执行输入 hash 随订单审计；首笔 fill 原子复制该 seed。后续窗口数据变化不得改写该值。只在下一完整有效交易日守住颈线且量能/MACD 确认时产生一次 50%→100% 加仓差额；下一日失败、第二日恢复仍不得加仓。

- 覆盖 `close=arc_neckline_price` 的 50% 弱化、`close<arc_neckline_price` 的 0% 清仓、成本止损、移动止损和趋势转空清仓的优先级；缺失任一锚点、量能、因子或 seed 时由输入门控/生命周期写 `LIFECYCLE_DATA_UNAVAILABLE`，不猜测减仓或清仓。

- 覆盖实际首笔成交价低于或等于初始止损时只产生 `INITIAL_STOP_BREACHED_ON_FILL` 的退出目标，以及首仓先达到 `profit_take_price`、随后满足确认条件时仍不得加仓至 100%。

#### 4.13.4 文件变更清单

模板注册、参数 schema、固定索引白名单和单测均收敛在 §4.6.4 的 `templates.py`、`strategy_contract.py` 与 `test_strategy_templates.py`；成交后需在本方案的 `position_lifecycle_states` 加入冻结 `arc_neckline_price`，并由后续 `PositionLifecycleManager` 按本节规则计算确认、弱化与失效目标仓位。

### 4.14 策略目录边界与验证

#### 4.14.1 模块设计

七套参考策略各自有一个标识，用户可以复制代码修改参数，也可以输入自己的受限条件组合。源码必须通过同一 AST 和七键校验。可选的模板来源不能替代实际源码校验；自定义脚本只有显式绑定并通过兼容性校验的 `lifecycle_policy_version_id` 才能启用阶段加减仓，否则只运行基础信号模式。

以下合同把每个模板的开仓、加仓、减仓与清仓分开定义，是 `PositionLifecycleManager` 的统一业务口径（本任务不新增回测引擎）。设实际首笔成交价为 \(P_0\)、风险允许最大股数为 \(Q_{risk}\)、当前收盘为 \(C_t\)；“下一有效日”均指成交日后的下一根完整日线。开仓信号经组合层接受并**实际成交**后，才建立生命周期状态和 \(Q_{risk}\)，初始目标均为 \(0.50Q_{risk}\)。除 MA5 预上穿的未兑现阶段外，减仓目标均为 \(0.50Q_{risk}\)；加仓只在尚未完成确认加仓时发生一次，清仓目标均为 0。

| 策略 | 开仓条件（目标 50%） | 加仓条件（目标 100%） | 减仓条件 | 清仓条件 |

|------|---------------------|----------------------|----------|----------|

| 均线趋势交叉 | \(F_t>M_t\land F_{t-1}\le M_{t-1}\land M_t>S_t\land C_t>M_t\land45\le RSI6_t\le70\land MACD_t>0\)。 | 下一有效日 \(F_t>M_t\land M_t>S_t\land C_t>F_t\)。 | 未清仓且 \(M_t\le S_t\) **或** \(MACD_t<MACD_{t-1}\)。 | \(C_t\le S_0\)（成本止损）、前日有效移动止损被跌破，或 \(F_t<M_t\land F_{t-1}\ge M_{t-1}\)（死亡交叉）。 |

| 强势回撤反弹 | \(MA20_t>MA60_t\land C_{t-1}\le B_{l,t-1}\land C_t>B_{l,t}\land r_l\le RSI6_t\le r_h\land RSI6_t>RSI6_{t-1}\land MACD_t\ge MACD_{t-1}\)。 | 下一有效日仍 \(MA20_t>MA60_t\land C_t>B_{l,t}\land RSI6_t\ge RSI6_{t-1}\land MACD_t\ge MACD_{t-1}\land C_t\ge P_0\)。 | 未清仓且 \(C_t\le B_{l,t}\) **或** \(RSI6_t<RSI6_{t-1}\) **或** \(MACD_t<MACD_{t-1}\)。 | \(C_t\le S_0\)、前日有效移动止损被跌破，或 \(C_t<B_{l,t}\land RSI6_t<RSI6_{t-1}\)（回撤失效）。 |

| 布林放量突破 | \(MA20_t>MA60_t\land C_{t-1}\le B_{u,t-1}\land C_t>B_{u,t}\land V_t\ge qV_{base}>0\land50\le RSI6_t\le r_c\)。 | 下一有效日 \(C_t>B_{u,t}\land C_t\ge P_0\land MACD_t\ge MACD_{t-1}\land V_t\ge V_{base}\)。 | 未清仓且 \(C_t\le B_{u,t}\) **或** \(V_t<V_{base}\) **或** \(MACD_t<MACD_{t-1}\)。 | \(C_t\le S_0\)、前日有效移动止损被跌破，或 \(C_t<B_{m,t}\land MACD_t<0\)（突破失效）。 |

| MACD-RSI 动量反转 | \(D_{t-1}\le E_{t-1}\land D_t>E_t\land D_t<0\land H_t>H_{t-1}\land R_{t-1}<r_o\le R_t\le r_c\land C_t\ge0.97MA20_t\)。 | \(D_{t-1}\le0<D_t\)（DIF 首次上穿 0 轴）。 | 未清仓且 \((D_t-E_t)<(D_{t-1}-E_{t-1})\) **或** \(R_t<R_{t-1}\)。 | \(C_t\le S_0\)、前日有效移动止损被跌破，或 \(D_t<E_t\land H_t<0\)（动量反转失败）。 |

| MA5 预上穿 MA20 提前布局 | \(F_t\le M_t\land F_t>F_{t-1}\land M_t>S_t\land C_t>F_t\land0<X_{cross}-C_t\le dC_t\land45\le RSI6_t\le70\land MACD_t\ge0\)。 | 成交后的第 1–3 个完整有效交易日，首次 \(F_t>M_t\)（实际金叉）。 | 尚未兑现时 \(F_t\le F_{t-1}\) **或** \(MACD_t<MACD_{t-1}\)，目标降为 \(0.25Q_{risk}\)；若后续转强但仍未金叉，最多恢复至 \(0.50Q_{risk}\)。 | \(C_t\le S_0\)、前日有效移动止损被跌破、\(F_t<M_t\land F_{t-1}\ge M_{t-1}\)，或第 3 个有效交易日仍 \(F_t\le M_t\)（`EXPECTATION_TIMEOUT`）。 |

| 成交量骤增确认 | \(V_{base}>0\land V_t\ge qV_{base}\land C_t\ge C_{t-1}(1+g)\land MA20_t>MA60_t\land C_t>MA20_t\land50\le RSI6_t\le75\land MACD_t>0\)。 | 下一有效日 \(C_t>MA20_t\land C_t\ge P_0\land MACD_t\ge MACD_{t-1}\)。 | 未清仓且 \(C_t\le MA20_t\) **或** \(V_t<V_{base}\) **或** \(MACD_t<MACD_{t-1}\)。 | \(C_t\le S_0\)、前日有效移动止损被跌破，或 \(C_t<MA20_t\land MACD_t<0\)（放量确认失效）。 |

| 圆弧底 75A | 中部锚点相对左端至少回落 `min_left_decline_pct`、不高于两侧锚点；右中部相对中部至少恢复 `min_recovery_pct`；当前收盘突破 \(N=max(close[-41],close[-11])\) 至少 `neckline_breakout_pct`，且放量、\(MA20_t>MA20_{t-1}\)、\(MA20_t>MA60_t\)、RSI6 在 50–75、MACD 为正且不弱于昨日。 | **仅成交后下一完整有效日**：\(C_t>arc\_neckline\_price\)、成交量不低于前五日基线、MACD 不弱于昨日且尚未 `profit_target_reached`；该日失败写 `confirmation_missed`，后续不得加仓。 | 未清仓且 \(C_t=arc\_neckline\_price\) **或** \(C_t\le MA20_t\) **或** \(V_t<V_{base}\) **或** \(MACD_t<MACD_{t-1}\)。 | \(C_t\le S_0\)、前日有效移动止损被跌破、\(C_t<arc\_neckline\_price\)，或 \(MA20_t<MA60_t\land MACD_t<0\)。 |

其中 \(F=MA5\)、\(M=MA20\)、\(S=MA60\)、\(B_l/B_m/B_u\) 分别为布林下/中/上轨、\(D/E/H\) 分别为 DIF/DEA/MACD、\(R=RSI6\)，\(V_{base}=max(V_{t-1},\ldots,V_{t-5})\)。圆弧底 75A 的 \(N=max(close[-41],close[-11])\) 是 BUY 时冻结为 `arc_neckline_price` 的颈线，后续生命周期判断不得重算。`profit_take_price` 首次触达是所有策略共同的获利减仓条件：仅在尚未触发清仓时，把目标降至 \(0.50Q_{risk}\)，且实际减仓成交后写入 `profit_trim_completed`，后续重复触价不再减仓；若当前实际仓位本来低于 50%，不得为满足该比例反向加仓。

同一交易日按以下顺序只计算一个最终目标：① 成本止损或移动止损清仓，② 模板结构失效或 MA5 超时清仓，③ 首次触达 `profit_take_price` 的获利减仓，④ 表中减仓条件，⑤ 表中加仓条件。多个减仓条件同时成立时取最低目标；目标为 0 后不再评估后续规则。条件所需行情/因子、真实成交或冻结风险容量缺失时，记录 `LIFECYCLE_DATA_UNAVAILABLE` 并保持现有仓位，不猜测或补造交易。

| 策略/能力 | V1 可创建 | 核心数据 | 限制原因 |

|-----------|-----------|----------|----------|

| 基本面价值/成长 | 否 | PE/PB、营收、净利润、ROE | 单票上下文无数据合同。 |

| 横截面多因子/行业轮动 | 否 | 全市场/行业收益与排名 | 当前 context 只有单票。 |

| 日内均线、网格、T+0 | 否 | 分钟/Tick、撮合、手续费 | 当前仅日线；C11 只负责 T+1 可卖数量和最早执行日，不提供日内撮合或 T+0。 |

| ATR/KDJ/海龟突破 | 否 | ATR/KDJ 或历史滚动高低 | 因子表未入库，且当前 AST 不支持变量窗口扫描；禁止本地自算绕过技术指标约定。 |

| 盈利阶段移动止损 | 是（C6 验收后） | 已确认成交、初始订单止损、每日 high/close、持仓状态 | 浮盈达到冻结的 \(R\) 阈值后进入保本/跟踪；前一交易日已生效止损被当日收盘跌破时，目标仓位归零。 |

| 首次获利减仓 | 是（C8 验收后） | 实际成交价、初始止损、模板 `reward_multiple`、已成交仓位 | 收盘首次达到冻结 `profit_take_price` 时，目标仓位降至 50%，剩余仓位继续移动止损；成交确认后不重复。 |

| 目标仓位分批建仓/加仓/减仓 | 是（C8 验收后） | 已确认成交、生命周期状态、模板阶段条件 | 按上述阶段合同将实际仓位调整为冻结目标仓位；平台须对意图去重，不能重复执行同一减仓建议。 |

新模板必须同时满足：字段在执行期 context 中真实可用；初始、确认、弱化、获利减仓与失效清仓各有确定性 fixture；渲染源码通过 AST；并声明每个阶段的目标仓位，且不重复实现 PositionPlanner 的账户风控。否则先扩展数据合同/执行本方案，再立新模板方案，不允许脚本访问 DB 或自行请求 Tushare。

七套策略不同时晋级，按复杂度和可解释性分三波：

1. **第一波（基础趋势/回撤/量能）**：`ma_trend_cross_v1`、`trend_pullback_v1`、`volume_surge_confirm_v1`。先验证数据链、可成交性、成本与生命周期最短闭环。
2. **第二波（更强路径依赖）**：`boll_volume_breakout_v1`、`macd_rsi_reversal_v1`、`ma5_pre_cross_v1`。前两者需证明与第一波不是高度重复信号；预上穿必须先完成三日预期状态机。
3. **第三波（实验形态）**：`arc_bottom_75a_v1`。固定五锚点只是项目近似定义，必须在影子样本中单独报告误触发、行业集中和相对第一波的增量价值，不默认启用。

每个模板的 `score` 只用于该模板内部排序，不解释为成功概率，也不直接跨模板比较。若未来合并多策略候选，必须先基于冻结的 forward 样本做分策略校准，再生成统一排序分；不得直接比较当前人工分档的 65、70、90。

策略晋级固定为四个阶段：`DESIGN`（仅公式/fixture）→ `SHADOW`（只记录假设订单和后续表现）→ `MANUAL_ADVISORY`（向用户展示、仍需人工确认）→ `BROKER_EXECUTION`（本方案范围外）。进入 `SHADOW` 前冻结观察期、最小信号数、成本模型和评价指标；进入 `MANUAL_ADVISORY` 至少要跨越预先定义的观察期与样本门槛，并通过 C9–C14。任何阶段都不得用胜率单项作为晋级依据。

影子评估至少保存：信号日/可执行日、假设成交价、费用与滑点、持有期、已实现及浮动收益、MAE/MFE、退出原因、信号到可成交转化率、换手、行业集中、组合开放风险贡献、最大回撤和市场状态标签。评估只使用信号产生后才到达的数据；不得回填历史信号制造样本，也不得在同一观察样本上反复调参后仍声称是前向验证。

#### 4.14.2 三方依赖能力评估

不新增依赖。代理端点的真实能力以 C1 POC 结果为准；全市场日线/因子采集必须遵守项目的“全市场禁区间查询、单日和分批降级、日线升序归一、技术指标不自算”约定。

#### 4.14.3 风险与验证方式

| 验证层 | 场景 | 可执行断言 |

|------|------|------------|

| 因子 POC（人工部署门禁） | 代理端点采集全市场股票技术/复权因子 | 同时记录 250 日历史质量、按模板实际窗口的可执行覆盖、暖机不足、截断、限流和复权版本；未达 C1 门槛不得启用对应模板。 |

| 快照契约 | `market.factor_daily` 行 → `StrategyContext` | 断言各模板 required 字段、日期、数组长度、null 对齐和执行输入 hash；修改市场表后扫描 rerun 读取新数据；已处理持仓日不得二次推进。 |

| 每模板单测 | BUY、结构失效 SELL_ALL、NO_SIGNAL、输入不足、参数边界 | 断言固定七键、reason code、分数范围、qfq 信号价格严格关系与无多余字段；成本/移动止损单独在生命周期测试；MA5 预上穿额外断言 20 日窗口临界价公式，成交量骤增额外断言五日基线、放量下跌与涨幅阈值，圆弧底 75A 额外断言 40 日固定锚点、颈线冻结和放量突破。 |

| 价格规范化 | 低价股、风险仅 1–3 tick、布林中轨贴近入场、浮点边界 | `Decimal` 规范后仍满足严格价格关系与最小风险收益；否则 BUY 保留、订单拒绝。 |

| 安全回归 | 默认/边界参数渲染的七套脚本 | 真实 AST validator 和 runner 通过；源码不含 import、循环、变量下标、属性、f-string 或未授权字段。 |

| 组合集成 | 技术形态 BUY，但现金不足或行业不可用 | 信号可审计；PositionPlanner 拒绝订单；策略不被改写为 HOLD。 |

| 移动止损状态机 | 确认成交后从保护、保本、跟踪到卖出 | 只使用实际成交价和前一日已生效止损；最高价/止损单调不减；同一持仓同一交易日重跑只产生一条卖出建议；缺失成交或日线时 fail-closed 不启用。 |

| 预期兑现时限 | `ma5_pre_cross_v1` 成交后的第 1–3 个有效交易日 | 窗口内首次 MA5>MA20 标记 `FULFILLED`；第 3 日仍未上穿仅一次 `EXPECTATION_TIMEOUT`；成交日/无日线/非交易日不计数；退出优先级无双卖单。 |

| 前端人工验收 | 策略创建与报告 | 可查看公式、字段、参数范围、风险提示和 reason code 中文映射；明确“日线信号、人工确认、非收益承诺”。 |

| 数据水位/对齐 | 日线与因子同日、错日、全空、停牌和采集中途 | 只有共同水位且 `(ts_code,trade_date)` 全部对齐才调用 Sandbox；错位不会静默变成全 null；报告区分 `WARMUP_INCOMPLETE`、`STALE_DATA`、`INDICATOR_UNAVAILABLE`。 |

| 复权与公司行为 | 除权除息日前后 | qfq 信号连续、原始订单价格可成交；结构价映射后严格关系成立；不产生机械假金叉、假突破或假止损。 |

| 可交易性与成本 | 涨跌停、ST、停牌、T+1、最低佣金、印花税、滑点和参与率 | 原始信号保留；订单被正确延后、拒绝或缩量；成本后盈亏比和现金不被高估。 |

| 组合开放风险 | 多票同方向、同一行业、相关标的同时止损 | 单笔、市值和开放风险三套上限均成立；熔断后仅风险降低订单可通过。 |

| Forward shadow | 分波策略在冻结观察口径下运行 | 报告成本后期望、回撤、MAE/MFE、可成交率和分市场状态结果；未达到预设门槛保持实验状态。 |

自测只运行 fake fixture 的新增目录；不运行 `real_llm` 或 `real_toolkit` fixture 的测试。因子代理 POC 是部署前人工门禁，不作为自动 pytest。

#### 4.14.4 文件变更清单

见 §4.6.4。任务分解时必须先形成“基础执行能力（C1–C8）”验收任务，再形成“模板注册与七模板”任务；两类任务不得并行假定对方已完成。

### 4.15 盈利阶段移动止损

#### 4.15.1 模块设计

移动止损只属于**确认成交后的持仓生命周期**，不属于模板 `strategy(context)`。人工或券商回写成交后，`PositionLifecycleManager` 建立与持仓一一对应的止损状态；任何仅有策略 `signal_entry`、建议订单或 `average_cost` 的记录都不得初始化该状态。

初始风险以实际成交价 \(P_0\) 和成交时接受订单的规范化初始止损 \(S_0\) 计算：

\[

R=P_0-S_0>0

\]

状态固定包含：`position_id`、`fill_id`、`fill_price=P_0`、`initial_stop=S_0`、`high_water_mark`、`active_stop_price`、`phase`、冻结 `trailing_config`、`last_processed_trade_date` 与 `exit_signal_id`。同一持仓仅允许一个未关闭状态，`exit_signal_id` 非空后不再产生第二条移动止损卖出建议。`QuantExecutionService` 每日先运行该管理器；若它为某持仓给出目标仓位 0，则该票在同一 `effective_trade_date` 不再计算模板的其他减仓/加仓分支，杜绝同日冲突订单。

每日按持仓处理日审计日线处理，先使用**前一交易日已生效**的 \(S_{t-1}\) 判定今日收盘：

\[

C_t\le S_{t-1}\Rightarrow SELL\_ALL / TRAILING\_STOP\_LOSS

\]

这样不会用当天先出现的最高价反推当天收盘卖出，避免日线 OHLC 顺序未知导致的前视偏差。只有未触发卖出时才更新：

\[

H_t=max(H_{t-1},High_t)

\]

配置以初始风险 \(R\) 表达，包含三个须显式配置的冻结字段：\(b=breakeven\_activation\_r\)、\(a=trailing\_activation\_r\) 与 \(d=trailing\_drawdown\_pct\)，并强制 \(0<b\le a\)、\(0<d<1\)。状态流转如下：

| 阶段 | 进入条件 | 当日收盘未触发卖出后的下一日有效止损 |

|------|----------|--------------------------------------|

| `PROTECT` | \(H_t<P_0+bR\) | \(S_t=S_0\) |

| `BREAKEVEN` | \(P_0+bR\le H_t<P_0+aR\) | \(S_t=max(S_{t-1},P_0)\) |

| `TRAILING` | \(H_t\ge P_0+aR\) | \(S_t=max(S_{t-1},H_t(1-d))\) |

因此有效止损总满足 \(S_t\ge S_{t-1}\)，永远不会因回撤下调。`take_profit` 在入场时用于风险收益校验；首笔实际成交后重算为冻结 `profit_take_price`，首次到价把目标仓位降至 50%，不是全清触发价。由于当前没有可验证回测结果，\(b,a,d\) 不写经验默认值、不在 UI 标记为推荐；它们必须在模板版本/成交时冻结，历史持仓不受后续配置修改影响。

**例子**：若实际成交 \(P_0=10.00\)、初始止损 \(S_0=9.50\)，则 \(R=0.50\)。若用户显式配置为 `b=1R`、`a=2R`、`d=8%`，最高价先到 10.50 时止损提升为 10.00；最高价后续到 12.00 时止损提升为 11.04。次日收盘若不高于 11.04，则只生成一次 `TRAILING_STOP_LOSS` 卖出建议；若收盘为 11.20，则更新后仍持有，且止损绝不回落。

#### 4.15.2 三方依赖能力评估

不新增第三方库。需要本方案新增已确认成交、持仓止损状态与日线读取接口；日线 high/close 必须来自与持仓处理日一致的 `market.instrument_daily` 冻结读模型。策略 Sandbox、实时行情与 LLM 均不参与计算。若系统暂未接券商，人工成交确认必须同时写入真实成交价、数量、成交日及关联建议订单 ID，禁止只修改 `average_cost` 触发移动止损。

#### 4.15.3 风险与验证方式

- 用 `high` 先创新高、随后回撤的跨日 fixture，断言止损只上调，且当日新高形成的止损仅自下一交易日开始生效。

- 分别验证 `PROTECT → BREAKEVEN → TRAILING`、阶段临界点、止损触发、结构失效与移动止损同日时的固定优先级：移动止损触发时原因为 `TRAILING_STOP_LOSS` 且模板卖出分支不得执行；未触发时模板仍可产生结构失效 `SELL_ALL`。

- 并发/重跑测试以 `(position_id, effective_trade_date)` 作为幂等键；重复执行不得降低 `high_water_mark/active_stop_price`，不得创建第二条建议卖出或覆盖已关闭状态。

- 缺失 fill、初始止损、持仓数量、日线 high/close 或配置交叉约束不成立时，记录 `TRAILING_STOP_UNAVAILABLE`，不得猜测价格、不得执行卖出。

#### 4.15.4 文件变更清单

| 类别 | 路径 | 改动说明 |

|------|------|----------|

| 基础前置（新建/修改） | 本方案创建的持仓、成交、建议订单迁移/ORM/仓储 | 保存实际成交事实及 `position_trailing_stops` 状态；唯一约束保证每持仓仅一个活跃止损状态与每持仓/交易日仅一条目标仓位意图。 |

| 基础前置（新建） | `backend/modules/quant_strategy/application/position_lifecycle_manager.py` | 以冻结成交和日线输入实现阶段状态机、单调止损、目标仓位意图幂等与失败降级。 |

| 跨链路（修改） | `QuantExecutionService`、持仓审计 cursor、`ReportDTO` 与量化报告面板 | 每日先处理已确认持仓止损，再执行模板扫描；报告展示阶段、当前有效止损、最新高点、配置快照和目标仓位原因，不展示源码。 |

| 测试（新建） | `backend/tests/unit/quant_strategy/test_position_lifecycle_manager.py` | 覆盖状态流转、跨日生效、重跑/并发、缺失数据和唯一性。 |

### 4.16 MA5 预上穿预期兑现时限

#### 4.16.1 模块设计

“预上穿”买入的不是当日已确认的金叉，而是“短期内 MA5 会上穿 MA20”的可证伪假设。因此 `ma5_pre_cross_v1` 的建议订单被**实际成交确认**后，`PositionLifecycleManager` 必须在同一事务建立 `position_lifecycle_states` 与 `position_expectations`：前者保存 `position_id`、`template_id`、`risk_capacity_shares`、`target_exposure_pct=0.50`、`lifecycle_phase=PROBE`、`profit_take_price`、`profit_trim_completed`、`state_version` 与最后成交订单；后者保存 `fill_id`、冻结 `confirmation_window_trading_days=3`、`fill_trade_date`、`observed_trading_days`、`status=PENDING/FULFILLED/TIMED_OUT`、`fulfilled_trade_date`、`exit_signal_id` 与 `last_processed_trade_date`。一个持仓仅能有一条活跃预期；`FULFILLED` 与 `TIMED_OUT` 终态不可回退。

成交日不计入窗口。处理器从 `market.instrument_daily` 读取成交日之后按 `trade_date` 升序的实际日线；每存在一根可与同日 MA5/MA20 对齐的完整 bar 才消耗一天。第 1、2、3 根完整 bar 分别为观察日 1、2、3，周末、节假日和停牌/缺失日线不消耗窗口。不得用 `AI.dataflows.utils.trading_calendar.TradingCalendar` 推算到期日：该组件的三级 fail-open 降级可能把非交易日误当可用日，不满足此处的 fail-closed 退出语义。

设成交日之后第 \(n\) 个完整观察日的均线为 \(F_n=MA5_n\)、\(M_n=MA20_n\)。在任何可观察日，先执行 C6 的价格/移动止损检查；未退出才判断：

\[

F_n>M_n\Rightarrow status=FULFILLED

\]

兑现同时提出从 50% 到 100% 的加仓差额；仅该差额实际成交后才将 `lifecycle_phase` 推进为 `CONFIRMED`。一旦兑现，永久解除该持仓的三日时限。若第 3 日仍不满足上式，则：

\[

F_3\le M_3\Rightarrow SELL\_ALL / EXPECTATION\_TIMEOUT

\]

该退出由“建仓假设未兑现”触发，不与收益或亏损金额绑定。未兑现且 `MA5≤前日MA5` 或 `MACD` 较前一日走弱时，目标从 50% 降至 25%；若次日修复但未金叉，最多恢复至 50%。每日优先级复用 §4.11：清仓 → 获利减仓 → 弱化减仓 → 确认加仓；前项若生成目标 0，同日不再计算后项。`exit_signal_id`、`state_version` 与 `(position_id, effective_trade_date,target_shares,reason_code)` 唯一约束共同保证重跑/并发下只有一条差额建议。

若尚未取得真实成交、没有关联初始订单止损、某个应观察日缺少日线或 MA5/MA20，状态保持 `PENDING`，记录 `EXPECTATION_DATA_UNAVAILABLE`，且不以自然日猜测超时。价格止损、已生效移动止损和模板结构失效仍继续评估，避免数据问题使持仓失去既有风险保护。

#### 4.16.2 三方依赖能力评估

不新增第三方依赖。事实输入来自确认成交记录、已冻结模板版本和 `market.instrument_daily`/`market.factor_daily` 的本地读模型；逐日对齐以真实写入的 `trade_date` 为准。不能把 `TradingCalendar` 的 Tushare/AKShare/weekday-only 降级路径用于本规则的到期计算，也不请求实时行情或 LLM。

#### 4.16.3 风险与验证方式

- 成交后第 1、2、3 个完整观察日首次 `MA5>MA20` 分别断言 `FULFILLED`、`observed_trading_days` 正确、此后不再生成超时卖出。

- 第 3 日 `MA5=MA20` 或 `MA5<MA20` 分别断言只生成一条 `SELL_ALL / EXPECTATION_TIMEOUT`；同一交易日重跑及并发 worker 不产生第二条。

- 构造周末、节假日、停牌无日线和因子缺行，断言均不消耗三日窗口；缺失日写 `EXPECTATION_DATA_UNAVAILABLE`，不以自然日超时。

- 价格止损/移动止损、兑现、超时和 `MA_DEATH_CROSS` 同日组合 fixture，严格断言既定优先级及无双卖单。

- 建仓来源不是 `ma5_pre_cross_v1`、只有建议订单未成交、`FULFILLED/TIMED_OUT` 终态重跑，均不得新建或重置活跃预期。

#### 4.16.4 文件变更清单

| 类别 | 路径 | 改动说明 |

|------|------|----------|

| 基础前置（新建/修改） | 本方案创建的持仓、成交、建议订单迁移/ORM/仓储 | 增加 `position_expectations`：冻结模板 ID、成交日、三日窗口、观察计数、终态与退出关联；加每持仓一条活跃预期及每持仓/交易日一条退出建议约束。 |

| 基础前置（修改） | `backend/modules/quant_strategy/application/position_lifecycle_manager.py` | 在移动止损后、模板持仓卖出前执行 `ma5_pre_cross_v1` 的兑现、弱化、超时状态机和优先级编排。 |

| 跨链路（修改） | `QuantExecutionService`、持仓审计 cursor、`ReportDTO` 与量化报告面板 | 展示冻结三日时限、已观察日数、状态、兑现/超时日期和原因码；报告清楚区分“预期未兑现退出”与价格止损。 |

| 测试（新建） | `backend/tests/unit/quant_strategy/test_position_lifecycle_manager.py` | 增加兑现、弱化、到期、真实交易日计数、缺失数据、优先级、幂等与终态不可回退的 fixture。 |

### 4.17 全流程持仓生命周期

#### 4.17.1 模块设计

七套规则共享同一个成交后状态机。通用脚本的 `SELL_PARTIAL` 先按实际可卖持仓换算为绝对目标股数，再与平台目标取最低值；清仓优先，未完成订单纳入差额，脚本比例不得直接重复作用于变化后的持仓。首次 BUY 建议被实际成交确认时，服务以实际首笔成交价、该笔接受订单的规范化初始止损和组合冻结风险预算计算 \(Q_{risk}\)，并冻结 `risk_capacity_shares`、`initial_fill_price`、`initial_stop_price`、`profit_take_price`、`template_id`、模板版本/参数、`target_exposure_pct=0.50`、`lifecycle_phase=ENTRY_PENDING` 与 `state_version`。只有首次实际成交数量达到相应整手目标后进入 `INITIALIZED`；若部分成交，保留计划目标并记录实际数量，扣除未完成订单预留后才补足差额，不重建状态。

七模板的确认、弱化与清仓规则以 §4.14 表为唯一来源；圆弧底 75A 以首笔成交时冻结的 `arc_neckline_price` 作为确认、弱化和结构失效的唯一颈线。初始均为 50%，确认条件首次成立时提出加至 100% 的差额；确认前或确认后的弱化条件成立时目标降至 50%，但 MA5 预上穿在未兑现期间降至 25%。同一确认条件只可完成一次，字段 `confirmation_completed` 防止重跑加仓；首次收盘达到 `profit_take_price` 时目标降至 50%，字段 `profit_trim_completed` 防止重放；清仓条件、成本止损和移动止损均把目标设为 0 并终止生命周期。

处理顺序固定为：① 成本止损/移动止损，② 模板完全失效或 MA5 兑现超时，③ 首次获利减仓，④ 模板弱化减仓，⑤ 模板确认加仓。每个阶段先锁定 `position_lifecycle_states`，读取同日已留存行情/因子事实和最新实际成交数量，计算一个最终目标；`PositionPlanner` 以实际持仓和已预留未完成订单共同计算差额（详见 §2.6），不得仅用 `target_shares-actual_shares` 生成 `BUY_ADD`、`SELL_REDUCE` 或 `SELL_EXIT`。建议订单未成交、被拒绝或仅部分成交不推进 `confirmation_completed`/`profit_trim_completed`/终态；人工或券商回写成交在同一事务更新实际仓位、订单状态和 `state_version`，随后才允许下一阶段。若同日多个规则都要求减仓，取最低目标；目标为 0 时不再创建其他订单。

#### 4.17.2 三方依赖能力评估

不新增第三方依赖。需要本地持仓、建议订单与人工/券商成交回写具备事务锁和版本字段；`market.instrument_daily`/`market.factor_daily` 提供实际日线和因子读取。`TradingCalendar` 不参与 MA5 到期判断；状态机本身不请求 Tushare 或 LLM。V1 脚本只读取现有基础 position 摘要，不读取成交、止损、阶段、意图或未完成订单；这些事实只由平台生命周期服务消费。

#### 4.17.3 风险与验证方式

- 分别构造七模板的首仓 50%、确认加至 100%、弱化降至 50%、失效归零；MA5 另覆盖弱化降至 25%、三日未金叉归零。

- 目标首次达到收益价后，断言一次 `PROFIT_TARGET_TRIM` 把仓位降至 50%，价格重复穿越不重复减仓；已低于 50% 的持仓不得因该规则反向加仓。

- 对加仓、减仓和清仓建议构造未成交、部分成交、拒绝、重复 worker 与同日重跑；断言仅按实际差额补单，成交前不推进阶段，且 `(position_id,effective_trade_date,target_shares,reason_code)` 唯一。

- 构造清仓、获利减仓、弱化、确认同日成立，断言固定优先级及最终仅一条建议；缺成交、风险容量、行情或因子时写 `LIFECYCLE_DATA_UNAVAILABLE`，不臆造仓位。

#### 4.17.4 文件变更清单

| 类别 | 路径 | 改动说明 |

|------|------|----------|

| 基础前置（新建/修改） | 本方案创建的持仓、成交、建议订单迁移/ORM/仓储 | 新增 `position_lifecycle_states`、`position_expectations` 与差额订单字段：冻结风险容量/价格、目标比例/数量、确认与获利减仓标志、阶段、版本和订单关联；唯一约束覆盖每持仓活跃状态、每持仓/交易日/目标/原因一条建议。 |

| 基础前置（新建） | `backend/modules/quant_strategy/application/position_lifecycle_manager.py` | 实现七模板生命周期规则、优先级、目标仓位计算、成交回写推进与 fail-closed 审计；持久化圆弧底 75A 的 `arc_neckline_price`。 |

| 基础前置（修改） | `PositionPlanner`、建议订单 DTO/仓储、人工成交确认服务 | 支持 `BUY_ADD`、`SELL_REDUCE`、`SELL_EXIT`；按实际与目标差额、整手、组合约束和 `state_version` 创建/拒绝订单，成交后原子推进状态。 |

| 跨链路（修改） | `QuantExecutionService`、持仓审计 cursor、`ReportDTO` 与量化报告面板 | 每日先管理既有生命周期，再扫描初始信号；展示实际/目标仓位、阶段、冻结风险容量、初始风险、收益目标、已完成确认/减仓、订单与原因码。 |

| 测试（新建） | `backend/tests/unit/quant_strategy/test_position_lifecycle_manager.py` | 覆盖七模板规则、优先级、差额/部分成交、幂等、并发、缺数据与状态不可回退，包含圆弧底 75A 颈线冻结回归。 |


## 五、已确认决策 / 待实施细化

### 已确认决策

1. 决策链路固定为“用户策略脚本是唯一选股逻辑：全市场枚举 → 逐票沙箱执行 → 命中票按 score 排序 → 资金/仓位/行业风控裁剪 → 建议订单（不自动下单）”。AI 层（market/sector/screening/stock）本方案不改造、不依赖，统一接入留待后续任务。

2. 策略为版本化、受限 `strategy(context)`，已发布版本不可原地修改。

3. 仅输出 `BUY/SELL_ALL/SELL_PARTIAL/HOLD` 与建议订单，不自动下单、不改持仓。

4. 组合保存总资产、可用现金、单笔风险、最低盈亏比、总/单票/行业上限。

5. 原基础默认值仍为：风险 1%，最低盈亏比 1:2，总仓位 80%，单票 10%，行业 30%。这些只是产品初始配置，不是经验证的推荐参数；进入人工建议阶段前必须由用户显式确认，并补组合开放风险、费用、滑点和流动性配置。

6. V1 仅支持 CN 股票和 100 股整手；行业使用 SW2021，行业缺失时 BUY fail-closed、SELL 继续。

7. 量化通道与存量 AI 通道互不读取：量化不消费 `candidate_stock_pool`、`risk_gate` 或 LLM `confidence`；同任务同选 AI 层时 AI 层照旧独立运行。报告只输出量化结果。

8. 量化策略执行、signal 持久化和订单规划只有 backend `QuantExecutionService` 一份实现；量化任务由 Worker 直接调用执行，不经 LangGraph 图。

9. 本地单用户 V1 使用 AST 白名单和一次性子进程；全市场扫描以 200 标的批次和至多 8 个在途子进程执行，报告保存量化 BUY、卖出、拒绝和错误，不保存全量 HOLD；多租户/不可信脚本上线前必须升级容器隔离。

10. 用户不回测、只按条件选股：行情与全市场目录**不在提交时冻结**，每次执行（含 retry/rerun）在执行时实时枚举 universe、批量读取行情；仅策略/组合/持仓随任务提交冻结进 `execution_snapshot`。因此不建 universe/市场上下文快照表（`quant_execution_universe_snapshots`/`quant_execution_context_snapshots` 从方案中删除），重跑语义为「同一策略与组合快照 × 当前行情」重算，报告以 `valued_at` 如实标注执行时刻。回测需求出现时再恢复行情冻结。

11. 本方案先走通 frontend + backend 全链路，AI 层暂不接入：量化任务不经 LangGraph、由 Worker 直接执行；风险门控 `risk_gate` 缺省不门控（报告 `warnings` 标注，`BUY_REJECTED_RISK_GATE`/`caution` 在枚举/DTO 预留）；AI 接入（market 层门控传入、命中票深度研究、screening 统一为策略脚本）留待后续方案。

12. 数据执行语义改为“实时读取 + 单次共同水位”：不冻结全市场快照，但每次执行必须先固定 `market_as_of_trade_date`，日线/因子/复权/交易状态严格同日对齐；单票旧数据不得冒充目标日数据。

13. 技术信号统一使用前复权口径，订单/成交/现金/涨跌停/止损统一使用原始价格；宿主负责口径映射、tick、费用和滑点复核，脚本不得访问宿主交易状态绕过边界。

14. 成本止损、移动止损、获利减仓、兑现时限和阶段目标全部归 `PositionLifecycleManager`；脚本只表达初始形态与技术结构失效。脚本 `SELL_ALL/SELL_PARTIAL` 与平台目标统一为绝对目标股数，同日取最低目标，清仓优先。

15. 用户修改参考源码后，版本的 `template_id` 仅保留来源审计，不能自动继承模板生命周期。生命周期使用独立、不可变的 `lifecycle_policy_version_id` 绑定；未显式选择并通过兼容性校验的自定义策略只运行无阶段加减仓的基础信号模式。

16. 七套策略按三波进入 forward shadow，不同时默认启用；`score` 只在单模板内排序。未通过数据、可交易性、组合风险、性能和影子验证门禁的策略保持实验状态。

17. 策略资格按实际字段窗口和指标暖机判断，不再使用统一 250 根硬门槛；Loader 最多保留 250 根审计窗口，并必须在数据库侧逐票裁剪。

18. 本方案只到人工建议，不自动下单。A 股 T+1、停牌、涨跌停、ST/板块限制、费用、滑点、成交额参与率和可卖数量在建议订单阶段即为强制合同，而不是券商接入后再补。

### 本次合并确认的范围

- 七套策略的参数、公式、评分、开仓/确认/弱化/退出规则已纳入 §4.6–4.14；移动止损、MA5 三日预期和全流程状态机见 §4.15–4.17。
- 持仓生命周期属于统一方案范围。真实成交确认是推进实际持仓的唯一入口；生成信号和建议本身不改持仓。
- 保留自定义代码输入和现行七键协议；七套参考规则的输出子集不得直接替代通用脚本合同。
- 行情扫描继续在执行时读取；逐日持仓审计事实只服务状态机幂等，不恢复全市场行情快照表。
- 以上是文档合并，不表示新增模块已经实现，也不继承旧文稿的 R1–R3 通过结论。

### 待实施细化项

详见 [issues.md](issues.md)。方向已经确定，以下只允许细化物理合同，不能在实现中重新引入相反语义：

1. 移动止损 `b/a/d` 不提供经验默认值；需定义配置 DTO、冻结位置和 UI 禁用态，只有用户显式配置且校验通过才启用。
2. §2.6 的成交、活跃意图、未完成订单、逐日事实、费用版本和生命周期策略版本仍需细化物理表名、FK、唯一约束、撤销/更正和对账 API。
3. 共同数据水位、交易状态、复权版本和费用配置需要明确 ingest_state 名称、刷新顺序、失败恢复及保留周期。
4. C13 的最小观察期、最小信号数和晋级阈值必须在首次 shadow 前由用户冻结；方案不预填未经验证的盈利门槛。
5. C14 的 P95 耗时、峰值内存和数据库返回行数预算必须在部署机器完成一次基准后冻结。
6. 因子 POC、行业 POC、真实 6,000 标的性能、完整栈 E2E 和影子运行均未完成；全部通过前不将统一任务标为已完成。
