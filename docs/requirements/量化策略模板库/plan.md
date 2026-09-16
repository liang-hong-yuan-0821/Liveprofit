0917 量化策略模板库技术方案
AI 速览
点击生成文字速览、播客
# 量化策略模板库技术方案

> **状态**：待确认（2026-09-16；R1–R3 已通过；已增补两套模板并确认盈利阶段移动止损，等待剩余范围/定位确认）

> **关联文档**：[量化执行基础方案](../选股策略/plan.md)｜[数据库表结构](../../knowledge/backend/数据库表结构.md)｜[Tushare 因子约定](../../memory/pitfalls/ai/tushare-endpoints.md)

---

## 一、背景与动机

| 维度 | 现状 | 问题 | 目标 |

|------|------|------|------|

| 可创建策略范围 | `docs/requirements/选股策略/plan.md` §4.1 只提出受限 `strategy(context)` 的执行合同，没有定义策略品类、入场/离场条件、参数或评分。 | 用户不能从 `action/score/entry_price` 七键合同推导“MA5 上穿 MA20”何时买、何时卖、止损多少；不同脚本会形成不一致口径。 | 定义首批可创建策略模板，以及每个模板确定的市场数据、入场、离场、参考目标价、评分与失败路径。 |

| 因子数据事实 | `market.factor_daily` 的 DDL 已定义 MA、布林带、MACD、RSI 列，`AI/dataflows/providers/cn/tushare.py::STOCK_FACTOR_FIELDS` 也可请求这些字段；但当前仓库未验证“全市场股票 `stk_factor_pro` → `market.factor_daily`”的持续入库、覆盖率与历史完整性。 | “列存在”不等于每只活跃股票的历史因子都可冻结。例如在未完成股票因子采集前，全市场扫描会把依赖 MA60/布林带/MACD 的模板稳定降级为不可用。 | 把全市场因子采集、快照投影、AST 白名单列为模板上线前置门槛；门槛未验收时不得将模板标为可发布。 |

| 执行基础能力 | 量化执行基础方案是待实施设计；当前仓库尚无 `backend/modules/quant_strategy/`、`AI/strategy_sandbox/` 或 `0008_quant_strategy_and_portfolio_risk.py`。 | 模板不能假定已有 `StrategyContext`、`PositionPlanner` 或策略版本表；若按“已有模块”拆任务，会修改不存在的文件。 | 本方案只定义模板内容层和其对基础能力的精确依赖；基础执行方案完成、验收后，模板能力才进入实现。 |

| 策略与组合职责 | 基础方案规划由 `PositionPlanner` 统一处理风险收益比、现金、总/单票/行业上限与市场 `risk_gate`。 | 若模板自行检查现金、行业或全市场排名，同一个技术信号会因账户变化而变义，并与组合层重复。 | 模板只输出单票技术信号、参考价格、形态评分和稳定原因码；组合层单独接受或拒绝建议订单。 |

| 持仓生命周期 | 当前 `StrategyContext.position` 只有 `shares/average_cost/market_value`，没有真实成交价、冻结风险容量、已执行阶段、建仓后最高价或预期兑现状态。 | 仅靠每次扫描的当前仓位无法表达首仓、确认加仓、弱化减仓与到价分批止盈；例如 MA5 预上穿金叉后不能安全地从 50% 提至满仓，或风险预算变化后会错误重算旧持仓的计划规模。 | 模板仅输出当天技术形态和初始风险价；成交后 `PositionLifecycleManager` 冻结风险容量并为全部模板维护目标仓位，按“实际持仓→目标仓位”幂等地产生加仓、减仓和清仓建议；移动止损与 MA5 三日兑现均是该状态机的高优先级分支。 |

## 二、架构设计

本方案是“策略内容层”，不实现 Sandbox、任务快照、订单规划或实盘下单。它依赖量化执行基础方案完成以下**前置合同**；七个模板只有在全部合同通过验收后才可发布。

```text

股票因子采集与入库（基础能力，前置）

  stk_factor_pro / 已验证代理端点

    → market.factor_daily（全市场、按日、日期对齐）

    → task-scoped StrategyContext 快照（冻结）

    → TemplateInputGuard（按模板 required_indicators 检查）

    → Sandbox（受限 strategy(context)）

    → QuantExecutionSignal（七键策略结果 + 审计状态）

    → PositionPlanner（账户风险/行业/risk_gate；非模板职责）

    → 人工/券商确认成交（持仓与成交事实）

    → PositionLifecycleManager（首仓/确认加仓/弱化减仓/获利减仓/清仓、移动止损、预期兑现）

    → PositionPlanner（实际仓位 → 冻结目标仓位的差额订单；人工/券商确认成交才推进阶段）

```

### 2.1 上线前置合同

| 合同 | 必须新增/确认的能力 | 验收标准 | 未满足时行为 |

|------|--------------------|----------|--------------|

| C1 股票因子可用性 | **外部硬依赖，归属量化执行基础任务**：先完成 `stock_factor_ingestion_v1` 子任务，TushareProvider 通过项目代理端点采集股票 `stk_factor_pro`，按交易日批量落入 `market.factor_daily`。新增接口先在 `BaseStockDataProvider` 以结构化 `DataFrame | None` 默认值声明，再由 `TushareProvider` 同签名覆写。 | 对**以 POC 估值日为截止日的 250 日窗口**，分母 = `market.instrument` 中 `instrument_type='stock' AND list_status='L'` 且有不少于 250 根日线的规范 CN 代码；必须 100% 完成所需字段（MA5/20/60、三条布林、三项 MACD、RSI6）的 250 日对齐窗口，无未分类缺口。采集固定 `trade_date` 单日查询；响应行数 ≥6000 **或**唯一代码数小于分母时视为疑似截断，立即按每批最多 100 个代码补拉，直至分母逐码覆盖；每批失败重试耗尽即失败。逐批入库异常必须 rollback，POC 产出 coverage/缺失代码/限流/重试记录。 | POC 未达 100% 即阻断模板发布；不把缺失票静默算作正常 HOLD。 |

| C2 冻结输入 | `StrategyMarketSnapshotRepository` 将 OHLCV 与本节列出的因子投影为同长度数组，写入 task-scoped snapshot；重新执行只读快照，不在线重取数据。 | 每个数组长度相等、日期升序、缺失值保留 `null`，并通过 context hash 校验。 | 该票记录 `INDICATOR_UNAVAILABLE` 或 `DATA_UNAVAILABLE`，不调用 Sandbox。 |

| C3 受限字段路径 | 新增无业务依赖的 `AI/strategy_sandbox/strategy_contract.py` 作为唯一字段清单：声明所有可访问路径、合法常量下标区间和输出七键。Sandbox validator 只单向导入它；`backend/modules/quant_strategy/domain/templates.py` 单向导入该清单并为每模板声明**精确** `required_fields`，Sandbox 不得反向导入 backend 模板。 | 默认模板渲染源码全部通过真实 `validate_strategy_source()`；未知路径、超出负索引范围或动态下标被拒绝；四向契约测试断言“源码实际读取集合 = 模板 `required_fields` ⊆ task snapshot 投影字段 ∩ Sandbox 允许字段”。快照可以保留未被某模板使用的数组，模板不得为凑门控而读取无业务意义的字段。 | 发布期 `STRATEGY_VALIDATION_FAILED`。 |

| C4 策略版本化 | 基础方案创建的 `quant_strategy_versions` 记录模板标识、规范化参数、模板渲染器版本与源码 SHA；已发布版本不可变。`QuantTaskSubmissionService` 在创建任务时把模板摘要写入 canonical `execution_snapshot.strategy`，rerun 只读 task snapshot；报告 DTO 只从 snapshot/signal 投影，禁止回查可变注册表。 | 相同版本重跑得到相同 `template_id`、显示名快照、参数、renderer version、源码 SHA 和信号；发布后修改注册表显示名或渲染器不影响历史报告。 | 不允许创建任务或发布。 |

| C5 组合边界 | `PositionPlanner` 只在模板输出 BUY 后复核现金、仓位、行业、最小风险收益和 `risk_gate`；同一服务中的 `OrderPriceNormalizer` 负责将已通过 Sandbox 原始校验的信号价格规范为订单价格。 | BUY 信号即使被组合拒绝也可分页审计；策略不读取组合额度。信号保留 raw `entry/stop/take`，订单另存规范化后的 `order_entry/order_stop/order_take`；两者均可在报告中区分展示。 | 模板照常产生信号，订单标注拒绝原因。 |

| C6 移动止损 | `PositionLifecycleManager` 维护成交后止损状态：只消费确认成交均价、初始订单止损、持仓数量及按日冻结 high/close，持久化 `high_water_mark`、`active_stop_price`、阶段、配置快照和 `last_processed_trade_date`。 | 同一持仓同日重跑不会降低止损、不会重复产生意图；浮盈达到冻结阈值后进入保本/跟踪，跌破前日有效止损时以最高优先级把目标仓位设为 0。 | 未接入真实成交回写、状态迁移或日线价格时不启用移动止损；保留初始止损和结构失效，不以 `average_cost` 或策略信号价冒充成交事实。 |

| C7 MA5 预上穿兑现时限 | Lifecycle Manager 为已确认成交、来源为 `ma5_pre_cross_v1` 的持仓建立预期状态：冻结 `confirmation_window_trading_days=3`、`fill_trade_date`、观察日数和兑现/退出状态；只按 `market.instrument_daily` 中成交后真实日线顺序计数，不调用 fail-open 的 `TradingCalendar` 推算日期。 | 第 1–3 个有效交易日首次 `MA5>MA20` 标记 `FULFILLED` 并将目标从 50% 提至 100%；未兑现时 MA5/MACD 走弱降至 25%；第 3 日仍未上穿则目标为 0。清仓优先于减仓和加仓。 | 缺确认成交、连续日线或 MA5/MA20 时，不按自然日、周末或缺失因子猜测超时；写 `EXPECTATION_DATA_UNAVAILABLE` 并保留既有止损/结构保护。 |

| C8 全流程目标仓位 | 每笔首次实际成交时冻结 `risk_capacity_shares`、初始止损、实际成本、`profit_take_price` 与生命周期版本；七模板定义初始、确认、弱化、获利及失效的目标比例。圆弧底 75A 的 `arc_neckline_price` 由 `QuantExecutionService` 基于产生 BUY 的同一 task snapshot 派生为受控 `lifecycle_seed`，随 signal/order 审计元数据及其 snapshot hash 保存；首笔 fill 的同一事务只复制该 seed 至持仓状态，禁止从当前滚动行情重算。 | 初始全部为 50%；MA5 兑现加至 100%、未兑现弱化至 25%；其余模板弱化至 50%；首次达到目标价全模板减至 50%；清仓为 0。首次触达收益目标即冻结 `profit_target_reached`，此后目标不得高于 50%，即使当日无需卖出；`profit_trim_completed` 只记录实际减仓成交。Planner 按实际与目标的差额、`state_version` 和唯一键生成订单，订单部分成交/拒绝后不错误推进阶段或重复下单。 | 未有真实首笔成交、风险容量、价格、所需因子或圆弧底 seed 时，只审计 `LIFECYCLE_DATA_UNAVAILABLE`，不臆造仓位/成交；不启用生命周期加减仓。 |

### 2.2 固定 `StrategyContext` 扩展合同

基础方案当前示例和 AST 白名单只列 `ma_bfq_5`、`ma_bfq_20`、`rsi_bfq_6`，不足以表达本方案七个模板。基础能力必须把以下字段纳入冻结上下文与允许路径；这是**基础方案的变更**，不是模板脚本自行查询数据。

```json

{

  "meta": {

    "symbol": "600519.SH",

    "effective_trade_date": "2026-09-15",

    "bars_count": 250,

    "price_basis": "raw"

  },

  "ohlcv": {

    "trade_date": ["..."],

    "open": [0], "high": [0], "low": [0],

    "close": [0], "volume": [0], "amount": [0]

  },

  "indicators": {

    "ma_bfq_5": [null], "ma_bfq_20": [null], "ma_bfq_60": [null],

    "boll_mid_bfq": [null], "boll_upper_bfq": [null], "boll_lower_bfq": [null],

    "macd_dif_bfq": [null], "macd_dea_bfq": [null], "macd_bfq": [null],

    "rsi_bfq_6": [null]

  },

  "position": {"shares": 0, "average_cost": null, "market_value": 0}

}

```

- 所有数组严格按交易日升序，`[-1]` 是有效交易日的最新值；因子与 OHLCV 同长度且不前填。

- 基础快照的统一 250 根 bar 门槛继续生效。因此上市不足 250 个交易日、停牌导致窗口不足的股票不参与 V1 模板计算，稳定记录 `DATA_UNAVAILABLE`。

- `TemplateInputGuard` 先根据模板的正式 `required_fields` schema 检查：`path`、精确常量索引集合、`must_be_finite`、`must_be_positive`；该集合必须等于渲染源码的实际读取集合。MA5 预上穿登记其推导临界价所需的 `ohlcv.close[-20:-1]`；圆弧底 75A 精确登记正值 `ohlcv.close[-41,-31,-21,-11,-1]`、正值 `ohlcv.volume[-6:-1]`、`ma_bfq_20[-2,-1]`、`ma_bfq_60[-1]`、`macd_bfq[-2,-1]` 与 `rsi_bfq_6[-1]`；布林突破和成交量骤增模板也登记它们实际使用的 `ohlcv.volume[-6:-1]`（`volume` 必须为正）与对应因子索引。`None`、NaN、Infinity、非正值、数组错位、缺字段或索引不足不进入脚本；作为执行审计状态落盘，正常非持仓 HOLD 不需伪造为策略返回的异常原因。

- 模板脚本不负责错误码判断，故不依赖 `isfinite(None)` 的运行时语义；`isfinite` 若继续暴露，基础 Sandbox 必须保证非数值输入返回 `False` 且不抛异常。

### 2.3 模板版本数据合同

模板不是稳定策略实体的属性，而是**策略版本**的不可变定义。基础迁移创建版本表时增加如下字段（若基础迁移已先合并，则后续增量迁移添加）：

| 字段 | 类型 | 写入者 | 含义与示例 |

|------|------|--------|------------|

| `template_id` | `VARCHAR(64)`，可空 | QuantStrategyService | 预置模板标识；`ma_trend_cross_v1`。空值仅为未来“自定义脚本”预留，V1 不允许发布空值。 |

| `template_params` | `JSONB`，可空 | 服务端模板渲染器 | 规范化参数；例如 `{"stop_pct":"0.06","reward_multiple":"2.50"}`。键固定、数值用字符串避免 JSON 浮点漂移。 |

| `template_renderer_version` | `VARCHAR(32)`，可空 | 服务端模板渲染器 | 渲染规则版本；例如 `template_renderer_v1`。 |

| `source_code` | `TEXT` | 服务端模板渲染器 | 根据 `template_id + template_params + renderer_version` 生成的受限源码；前端不直接提交。 |

| `source_sha256` | `CHAR(64)` | 服务端模板渲染器 | 原始 UTF-8 源码散列；例如 64 位 SHA-256。 |

创建/编辑草稿 API 只接受策略元数据、`template_id` 和 `template_params`；服务端按模板参数 schema 验证、渲染源码、调用 AST validator 后保存。`QuantTaskSubmissionService` 在任务创建时将以下不可变摘要复制进 `execution_snapshot.strategy`：`strategy_id`、`version_id`、`version_no`、`template_id`、`template_display_name`、规范化 `template_params`、`template_renderer_version`、`source_sha256` 与内部执行所需 `source_code`；rerun 只从该快照重建 runner。`ReportDTO` 与 signal cursor 只投影同一快照/信号中的 `template_id`、显示名、参数、renderer version、版本号和 12 位 `source_hash_prefix`，不回查注册表、不投影 `source_code`。因此发布后变更注册表显示名或渲染器，不得影响历史任务报告。V1 不开放自定义源码编辑，因此不存在“用户编辑模板源码但字段路径不可改”的矛盾。

### 2.4 标准结果、目标仓位与交易时点合同

Sandbox 的七键输出仍只表达**当天技术形态**；它不读取真实成交、历史目标仓位或生命周期阶段，因此不直接生成不可幂等的 `SELL_PARTIAL`。`PositionLifecycleManager` 将“冻结模板生命周期规则 + 实际成交持仓 + 当日形态”解释为 `TargetPositionIntent`，并由 `PositionPlanner` 把当前实际仓位与目标仓位的差额转换为建议订单。输入不足等审计错误由 `TemplateInputGuard`/执行器保存，不伪装为包含第八个字段的策略结果。对于必须在成交后复用 BUY 时派生值的模板，执行服务可在七键校验通过后、同一冻结快照内生成受控 `lifecycle_seed` 审计元数据；它不是 Sandbox 自由输出，也不改变七键协议。

| 情形 | Sandbox `action` | `score` | `entry_price` / `stop_loss` / `take_profit` | `sell_ratio` | `reason` |

|------|------------------|---------|------------------------------------------------|--------------|----------|

| 无持仓初始形态 | `BUY` | 模板定义的 0–100 整数 | 三者均为有限正数，且 `stop < entry < take` | `null` | 稳定原因码，如 `MA_TREND_CROSS`。 |

| 价格/结构清仓形态 | `SELL_ALL` | `0` | 全部 `null` | `null` | `STOP_LOSS` 或模板固定失效码。 |

| 正常不动作/生命周期由服务处理 | `HOLD` | `0` | 全部 `null` | `null` | `NO_SIGNAL`。 |

`TargetPositionIntent` 不是 Sandbox 自由输出，字段固定为 `position_id`、`template_id`、`effective_trade_date`、`risk_capacity_shares`、`target_exposure_pct`、`target_shares`、`lifecycle_phase`、`reason_code`、`source_signal_id` 和 `state_version`。`target_exposure_pct` 只能是 `0/0.25/0.50/1.00`，含义是相对该持仓**初始成交时冻结**的风险允许最大仓位 `risk_capacity_shares` 的目标比例；不是账户总资产比例。`target_shares=lot_floor(risk_capacity_shares×target_exposure_pct)`，再由组合上限、可用现金和最小交易单位裁剪。目标与实际相同不产生订单；差额为正生成 `BUY_ADD`，差额为负生成 `SELL_REDUCE`，目标为零生成 `SELL_EXIT`。每次建议订单保存 `state_version` 与 `(position_id,effective_trade_date,target_shares,reason_code)` 幂等键，确认成交后才推进生命周期，故重跑、未成交或部分成交不会每天重复加/减仓。

- `reason` 只允许固定 ASCII 原因码，避免 AST 不支持 f-string/`str()` 时无法安全生成“RSI=56.2”等动态文案。前端把原因码映射为中文说明；触发指标值由报告侧从已审计的结构化字段受控展示，不写入策略自由文本。

- 该日线策略在 `effective_trade_date` 收盘后计算。`entry_price=C_t` 是**风险计算参考价/建议限价基准**，不是声称能以同一根 K 线收盘价完成成交；实际成交、可用现金和平均成本仍由人工确认或后续订单生命周期记录处理。

- `take_profit` 是入场风险收益校验的参考价，也是生命周期“首次获利减仓”的触发口径。首笔 fill 初始化前必须满足 `0 < initial_stop_price < initial_fill_price`；若实际成交价 \(P_0\le S_0\)，写唯一 `INITIAL_STOP_BREACHED_ON_FILL` 的 0% 退出目标，禁止初始化风险容量、确认加仓和收益阶段。其余情况以 \(P_0\)、冻结初始止损 \(S_0\) 和模板 \(k=reward\_multiple\) 重算 `profit_take_price=P_0+k(P_0-S_0)`；收盘首次达到该价即原子写入 `profit_target_reached` 并把目标上限锁定为 50%，即使当前仓位已经不高于 50% 而无需卖单。实际减仓成交才写 `profit_trim_completed`；价格后续回落/重上目标均不重复减仓，且 `profit_target_reached` 后不得通过迟到确认反向加仓。

- Sandbox 先按基础方案校验并持久化策略**原始信号价** `signal_entry/signal_stop/signal_take` 的有限性、严格大小关系和模板七键协议；不得在此阶段改写信号。随后 `PositionPlanner` 内部的 `OrderPriceNormalizer` 以 `Decimal`、最小单位 `0.01` 元、`ROUND_HALF_UP` 生成独立的 `order_entry/order_stop/order_take`：先规范 entry，再向下取整 stop，最后以已规范风险距离向上取整 take。顺序固定为“原始七键校验 → tick 规范化 → 规范化价格严格关系与风险收益复核 → 接受订单或写拒绝码”。信号行始终保留原始价格；订单字段仅保存规范化价格。规范化后风险收益比低于 `max(template.reward_multiple, portfolio.min_risk_reward_ratio)`、或任一严格关系不成立时，BUY 信号仍可审计，`order_status=INVALID_PRICE_RANGE` 且不生成建议订单。

## 三、详细设计

### 3.0 模块总览

| 维度 | 问题 | 方案概览 |

|------|------|---------|

| 3.1 模板注册、参数与输入门控 | 当前 `strategy(context)` 只允许三项因子，例如 MA60、布林带、MACD 路径会被现有 AST 白名单拒绝。 | 用单一模板注册表声明参数、必需字段和渲染器；基础快照、AST 与输入门控复用该声明。 |

| 3.2 均线趋势交叉策略 | 已定义 MA5/20/60 列，但没有“首次金叉 + 中期趋势 + 动量确认”的统一交易逻辑。 | 用 MA5 上穿 MA20、MA20 高于 MA60、RSI/MACD 确认形成日线趋势策略。 |

| 3.3 强势回撤反弹策略 | 已定义布林下轨和 RSI 列，但没有“上升趋势内回撤结束”而非接飞刀的判定。 | 以 MA20>MA60、前日跌至下轨、当日收回下轨和 RSI/MACD 修复作为买入条件。 |

| 3.4 布林放量突破策略 | 有 OHLCV 与布林上轨，尚无“首次突破 + 有效量能基线”的统一定义。 | 以首次上破上轨、前五日最大成交量为基线、RSI 不过热形成突破策略。 |

| 3.5 MACD-RSI 动量反转策略 | 已定义 MACD/RSI 列，但没有“低位金叉且真实脱离超卖”的精确条件。 | 以 0 轴下 MACD 金叉、RSI 从超卖阈值上穿和价格不显著偏离 MA20 形成低优先级反转策略。 |

| 3.6 MA5 预上穿 MA20 提前布局 | 当前均线策略只在金叉已发生后买入，未定义如何以固定历史窗口审计“明日上涨即上穿”的提前布局条件。 | 以 MA5/MA20 明日滚动公式推导交叉临界收盘价；仅当该价高于现价且在可配置的上涨距离内、并满足中期趋势与动量过滤时 BUY。 |

| 3.7 成交量骤增确认策略 | 当前布林突破把量能绑定在上轨突破，无法表达“相对前五日显著放量但不要求触及布林上轨”的趋势确认。 | 用当日成交量相对前五日最大量的倍数、正价格涨幅、MA20/MA60 与 RSI/MACD 过滤形成独立的放量确认策略。 |

| 3.8 圆弧底 75A 策略 | 当前模板没有把“左侧回落—中部筑底—右侧修复—放量突破颈线”转为受限脚本可执行的日线合同。 | 使用 40 日固定锚点近似圆弧形态，以突破时冻结的颈线管理确认、减仓和清仓，避免滚动窗口移动后改变持仓理由。 |

| 3.9 策略目录边界与验证 | 单票快照不含 PE、财报、分钟行情或横截面排名；例如不能求“全市场 PE 最低 10%”。 | 限定首批七模板，列明禁用策略、后续准入门槛与逐模板可执行验收。 |

| 3.10 盈利阶段移动止损 | 无状态模板不能可靠维护成交后的最高价和有效止损；例如重跑不能用今日价格覆盖昨日高点。 | 在实际成交后的持仓风控层，以初始风险 \(R\) 驱动“保护→保本→跟踪”状态机；止损只上调，卖出建议按持仓/交易日幂等。 |

| 3.11 MA5 预上穿预期兑现时限 | 预上穿是“短期将金叉”的前置假设；例如已持仓 3 个交易日仍 `MA5≤MA20` 时，现有 `HOLD` 不会退出。 | 对该模板的确认成交持仓冻结 3 个有效交易日；窗口内金叉即兑现并加仓，到期未金叉以 `EXPECTATION_TIMEOUT` 离场。 |

| 3.12 全流程持仓生命周期 | 当前模板只返回单次 BUY/SELL_ALL，不能表达首仓、确认加仓、弱化减仓、获利减仓与差额订单。 | 用统一 `PositionLifecycleManager` 管理目标仓位和成交确认状态；七模板各自声明确认/弱化/失效条件，Planner 仅执行实际仓位到目标仓位的差额。 |

### 3.1 模板注册、参数与输入门控

#### 3.1.1 模块设计

服务端维护不可由用户修改的 `StrategyTemplateDefinition` 注册表。每个模板定义 `template_id`、展示名、`required_fields`、最少 bar 数、参数 schema、渲染器和固定 reason code；草稿只保存模板和规范化参数。禁止在客户端或任务运行时拼接任意公式。

| `template_id` | 展示名 | 最少 bars | `required_indicators` | 日线定位 |

|------|----------|-----------|-----------------------|----------|

| `ma_trend_cross_v1` | 均线趋势交叉 | 250（基础快照统一门槛） | `ma_bfq_5, ma_bfq_20, ma_bfq_60, rsi_bfq_6, macd_bfq` | 数日到数周的日线波段。 |

| `trend_pullback_v1` | 强势回撤反弹 | 250 | `ma_bfq_20, ma_bfq_60, boll_lower_bfq, rsi_bfq_6, macd_bfq` | 数日到两周的日线波段。 |

| `boll_volume_breakout_v1` | 布林放量突破 | 250（实际规则取最近 6 根） | `ma_bfq_20, ma_bfq_60, boll_mid_bfq, boll_upper_bfq, rsi_bfq_6, macd_bfq` | 数日到两周的日线波段。 |

| `macd_rsi_reversal_v1` | MACD-RSI 动量反转 | 250 | `ma_bfq_20, macd_dif_bfq, macd_dea_bfq, macd_bfq, rsi_bfq_6` | 数日到一周；反转优先级低于趋势/突破策略。 |

| `ma5_pre_cross_v1` | MA5 预上穿 MA20 提前布局 | 250 | `ma_bfq_5, ma_bfq_20, ma_bfq_60, rsi_bfq_6, macd_bfq` | 数日到两周；以明日交叉临界价为预判，不承诺次日一定上穿；确认成交后须在 3 个有效交易日内实际金叉。 |

| `volume_surge_confirm_v1` | 成交量骤增确认 | 250 | `ma_bfq_20, ma_bfq_60, rsi_bfq_6, macd_bfq` | 数日到两周；不要求布林上轨突破。 |

| `arc_bottom_75a_v1` | 圆弧底 75A | 250（实际形态观察最近 40 个有效交易日） | `ma_bfq_20, ma_bfq_60, macd_bfq, rsi_bfq_6` | 数周到两月的日线右侧突破；`75A` 是项目内模板名称，不假定为外部标准公式。 |

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

MA 周期、布林带、MACD 字段和评分分档是模板定义常量，V1 不开放修改；若未来开放周期参数，必须先扩充因子采集、快照合同和字段白名单，不能在脚本内自算。

移动止损**不是 Sandbox 模板参数**：模板只定义初始 `stop_loss` 与 `reward_multiple`。首笔成交确认时，Lifecycle Manager 冻结 `risk_capacity_shares`、实际成本、初始止损、`profit_take_price`，并将版本关联的 `TrailingStopConfig` 冻结入状态；其中 `breakeven_activation_r`、`trailing_activation_r`、`trailing_drawdown_pct` 必须由样本外回测标定后才可作为默认值。`confirmation_window_trading_days=3` 是 `ma5_pre_cross_v1` 的版本常量：成交时复制进预期状态，后续版本变动不影响持仓。

#### 3.1.2 三方依赖能力评估

本模块不新增第三方依赖。模板渲染仅输出基础 Sandbox 已允许的条件、常量索引、数值运算、比较、布尔表达式、`min/max` 和固定字符串字面量；不使用循环、变量下标、属性、f-string、`str()`、动态键或网络调用。全市场因子采集的代理能力必须通过 C1 POC 实测，不能以官方接口文档替代。

#### 3.1.3 风险与验证方式

- 四向契约测试：每个模板的“渲染源码实际访问路径/最早负索引”必须等于 `required_fields`，并同时出现在 C2 快照投影和 C3 `strategy_contract.py` 允许路径中；任一漏配使测试失败。

- 参数测试：每个参数最小/最大值、相邻越界、未知键、缺失键、交叉约束和规范化 JSON 都有 fixture。

- 渲染测试：默认参数与边界参数生成的七套源码逐个经过真实 AST validator 与一次性 runner，返回结果严格符合 §2.4 七键矩阵。

- 快照/报告回归：修改注册表显示名或渲染器后重跑旧任务，断言任务快照、signal cursor 与 ReportDTO 仍展示创建任务时冻结的模板摘要。

- 价格双口径测试：分别断言 raw signal 和 normalized order 价格；覆盖低价股、风险距离不足一 tick、布林中轨贴近 entry、组合最小收益比高于模板倍数与 `INVALID_PRICE_RANGE` 拒绝。

#### 3.1.4 文件变更清单

| 类别 | 路径 | 改动说明 |

|------|------|----------|

| 基础前置（新建） | `AI/strategy_sandbox/strategy_contract.py`、`AI/strategy_sandbox/` | 无业务依赖的字段/索引/七键声明、AST 路径注册、受限 runner 与模板源码实际校验；仅在基础执行任务中创建。 |

| 基础前置（新建） | `backend/modules/quant_strategy/` | 策略版本、模板字段、服务、仓储与执行服务；当前不存在，不能标注为修改。 |

| 基础前置（修改） | `AI/dataflows/providers/base_provider.py`、`AI/dataflows/providers/cn/tushare.py` | 增加受控股票因子 DataFrame 接口及代理端点 POC/采集实现。 |

| 基础前置（修改） | `db/instrument/ingest/`、`db/instrument/dao/factor_daily.py` | 全市场股票因子按日采集、升序对齐、批量入库与失败 rollback。 |

| 模板层（新建） | `backend/modules/quant_strategy/domain/templates.py` | 静态注册表、参数 schema、模板渲染器和输入要求。 |

| 模板层（新建） | `backend/tests/unit/quant_strategy/test_strategy_templates.py` | 参数、渲染、七键协议和七套策略公式测试。 |

| 跨链路（修改） | 基础方案创建的策略版本迁移、ORM、DTO、router、`QuantTaskSubmissionService`、task snapshot、signal cursor、报告 DTO | 增加 `template_id/template_params/template_renderer_version`；创建任务冻结显示名/参数/渲染器版本/源码 SHA，rerun 与报告只读快照；信号和订单分别保存 raw/normalized 价格及订单拒绝码。具体文件以基础任务落地路径为准。 |

| 前端（新建/修改） | 基础方案新增的策略管理页面及其 generated API client 消费层 | 模板选择、参数表单、公式说明、创建/发布和报告摘要；后端 OpenAPI 导出后执行 codegen，禁止手改 `frontend/src/api/generated/`。 |

### 3.2 均线趋势交叉策略

#### 3.2.1 模块设计

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

持仓时按以下顺序返回：

1. 若 \(C_t\le P(1-p)\)，`SELL_ALL / STOP_LOSS`；

2. 否则若 \(F_t<M_t\land F_{t-1}\ge M_{t-1}\)，`SELL_ALL / MA_DEATH_CROSS`；

3. 否则 `HOLD / NO_SIGNAL`。

**例子**：若 `MA5[-2]=10.00≤MA20[-2]=10.05`、`MA5[-1]=10.20>MA20[-1]=10.10>MA60[-1]=9.80`、`close[-1]=10.30`、`RSI6[-1]=56`、`MACD[-1]=0.08`，则产生 BUY；以 `p=0.06,k=2.50` 计算 raw `stop=9.682`、raw `take=11.845`，最终订单层按 §2.4 规范价格。

#### 3.2.2 三方依赖能力评估

依赖 C1–C3 已验收的 MA、RSI、MACD 字段；不请求在线 Tushare，也不在策略中计算均线。

#### 3.2.3 风险与验证方式

- 前日相等、当日刚上穿应 BUY；前日已上穿应 HOLD。

- `M_t≤S_t`、RSI=71、MACD≤0 任一情形 HOLD。

- 成本止损与死亡交叉同时触发时必须稳定输出 `STOP_LOSS`。

#### 3.2.4 文件变更清单

模板逻辑在 `templates.py` 的 `ma_trend_cross_v1` 定义中实现，测试加入 `test_strategy_templates.py`；不单独创建无复用价值的每模板 Python 模块。

### 3.3 强势回撤反弹策略

#### 3.3.1 模块设计

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

持仓时先以 \(C_t\le P(1-p)\) 返回 `STOP_LOSS`；否则 \(C_t<B_{l,t}\land R_t<R_{t-1}\) 返回 `PULLBACK_INVALIDATION`；其余 HOLD。

**例子**：`MA20=12.20>MA60=11.70`，昨日 `close=11.90≤boll_lower=12.00`，当日 `close=12.15>boll_lower=12.05`，RSI6 从 38 升至 44，且 MACD 柱不再减弱，则 BUY；当日仍未收回下轨时 HOLD。

#### 3.3.2 三方依赖能力评估

依赖 C1–C3 的 MA、布林、RSI、MACD 冻结字段。不计算布林标准差、不请求 ATR。

#### 3.3.3 风险与验证方式

- `MA20≤MA60` 时，下轨回收也 HOLD。

- 前日触轨、当日没有收回下轨时 HOLD。

- 下轨失守且 RSI 下降为结构卖出；RSI/MACD 任一缺失由输入门控拦截，不调用脚本。

#### 3.3.4 文件变更清单

模板逻辑与测试收敛在 §3.1.4 的模板注册表和参数化单测中。

### 3.4 布林放量突破策略

#### 3.4.1 模块设计

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

持仓时，先按 \(C_t\le P(1-p)\) 止损；否则 \(C_t<B_{m,t}\land MACD_t<0\) 返回 `BREAKOUT_INVALIDATION`；仅收回上轨以内但仍在中轨上方时 HOLD。

**例子**：前五日最大成交量为 80 万手、当日 130 万手，昨收 20.00 未越过上轨 20.10，当日收 20.50 越过上轨 20.30，且 MA20>MA60、RSI6=63，则 BUY；当日仅 100 万手时因不满足 `1.5×80` 万手而 HOLD；前五日全为零时也 HOLD。

#### 3.4.2 三方依赖能力评估

成交量来自 `market.instrument_daily`，布林/MA/RSI/MACD 来自 C1 的因子入库。日线消费维持升序归一；不得为了成交量均线新增本地技术指标计算。

#### 3.4.3 风险与验证方式

- 前收等于上轨、当日上破可触发；前收已在上轨上方则 HOLD。

- 验证 `Vbase=0`、`Vt=0`、恰好 `q×Vbase`、略低于阈值四种量能边界。

- 验证中轨异常时不会输出非法 BUY 价格。

#### 3.4.4 文件变更清单

模板逻辑与测试收敛在 §3.1.4 的模板注册表和参数化单测中。

### 3.5 MACD-RSI 动量反转策略

#### 3.5.1 模块设计

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

持仓时，\(C_t\le P(1-p)\) 返回 `STOP_LOSS`；否则 \(D_t<E_t\land H_t<0\) 返回 `MACD_REVERSAL_FAILURE`；其余 HOLD。

**例子**：昨日 `DIF=-0.30≤DEA=-0.25`、`RSI6=31<35`，当日 `DIF=-0.18>DEA=-0.22`、MACD 柱从 -0.10 改善到 0.04、RSI6 升至 42 且收盘不低于 MA20 的 97%，则 BUY；若昨日 RSI=50，则即使 MACD 金叉也 HOLD。

#### 3.5.2 三方依赖能力评估

只使用 C1–C3 已验收的 MACD、RSI、MA 和 OHLCV；不使用资金流、新闻、情绪等未进入单票快照的数据。

#### 3.5.3 风险与验证方式

- DIF 必须在 0 轴下金叉；0 轴上金叉不属于本模板。

- RSI 必须满足“昨日低于超卖阈值、当日上穿”；只在区间内上升但未经历超卖时 HOLD。

- 覆盖成本止损、MACD 失败卖出、价格跌离 MA20 超过 3% 和因子缺失门控。

#### 3.5.4 文件变更清单

模板逻辑与测试收敛在 §3.1.4 的模板注册表和参数化单测中。

### 3.6 MA5 预上穿 MA20 提前布局策略

#### 3.6.1 模块设计

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

第二式意味着“若明日收盘至少达到 \(X_{cross}\) 则会金叉”，而不是预测明日价格；`X_cross <= C_t`、今天已经金叉或需要上涨超过上限时均 HOLD。参数为 \(p=stop\_pct\)、\(k=reward\_multiple\)：

\[

entry=C_t,\quad stop=entry(1-p),\quad take=entry+k(entry-stop)

\]

评分起点为 68；若所需上涨距离 \((X_{cross}-C_t)/C_t\le1\%\) 加 8 分、\(RSI6_t\in[50,65]\) 加 5 分、\(MACD_t>MACD_{t-1}\) 加 5 分，范围为 68–86。

**中文操作规则**：

- **开仓**：今天 MA5 还没有超过 MA20，但 MA5 正在上升、MA20 高于 MA60、收盘站在 MA5 上方；按历史 20 日数据推算，明天只需在允许涨幅内上涨就可能形成金叉；同时 RSI 在 45–70、MACD 不为负。实际成交后以风险允许最大仓位的 50% 试仓。

- **加仓**：成交后的前三个完整有效交易日内，只要第一次出现 MA5 高于 MA20，即确认实际金叉，目标加至 100%。

- **减仓**：尚未确认金叉时，如果 MA5 不再上升，或 MACD 比昨天走弱，表示提前布局的预期变弱；目标降至 25%。后续重新转强但仍未金叉时，最多恢复到 50%，不能提前加满仓。首次达到收益目标价时也要减仓至 50%，但不会为凑到 50% 而反向买入。

- **清仓**：收盘跌破初始止损价、跌破前一交易日已生效的移动止损价、出现 MA5 向下跌破 MA20，或第三个有效交易日结束后 MA5 仍未高于 MA20，任一成立即清仓。最后一种是“预期落空”退出，而不是等待无限期持有。

**全流程目标仓位**：实际首笔成交后冻结风险允许最大仓位 \(Q_{risk}\)（按 §2.4 和组合风险预算计算），并进入 `PROBE`，目标仓位为 \(0.50Q_{risk}\)。成交日不计入窗口；从其后的 `market.instrument_daily` 实际有效交易日依次计为第 1、2、3 日，停牌无日线不计数，不能用自然日代替。

- **加仓 / 预期兑现**：第 1–3 日任一天首次满足 \(F_t>M_t\) 时，标记 `FULFILLED`，目标仓位从 \(0.50Q_{risk}\) 提至 \(Q_{risk}\)，原因码 `PRE_CROSS_CONFIRMED_ADD`。只有加仓差额实际成交后才转入 `CONFIRMED`；若现金/组合上限裁剪加仓，则保留未完成目标并在后续日重新评估，不重复把同一已成交部分再计入。

- **减仓 / 预期弱化**：仍处于 `PROBE`、尚未兑现且 \(F_t\le F_{t-1}\) **或** \(MACD_t<MACD_{t-1}\) 时，目标仓位降至 \(0.25Q_{risk}\)，原因码 `PRE_CROSS_WEAKENING_REDUCE`。弱化发生在第 1 或第 2 个观察日时立即提出差额减仓；若次日重新转强但尚未实际金叉，目标最多恢复到 \(0.50Q_{risk}\)，不允许在预期兑现前增至满仓。

- **清仓 / 预期落空**：第 3 个有效交易日收盘仍 \(F_t\le M_t\) 时，前置假设“短期会金叉”已被证伪，目标仓位为 0，生成 `SELL_EXIT / EXPECTATION_TIMEOUT`；这不是因价格亏损而卖出，而是因建仓理由失效而退出。

- **获利减仓**：任一已成交阶段收盘首次达到冻结 `profit_take_price` 时，目标仓位为 \(0.50Q_{risk}\)，原因码 `PROFIT_TARGET_TRIM`，且 `profit_trim_completed` 后不因再次触价重复减仓；余仓继续走移动止损。

同一交易日的固定优先级为：① `STOP_LOSS` / `TRAILING_STOP_LOSS`（目标 0），② `EXPECTATION_TIMEOUT`（目标 0），③ `MA_DEATH_CROSS`（目标 0），④ `PROFIT_TARGET_TRIM`（目标 50%），⑤ `PRE_CROSS_WEAKENING_REDUCE`（目标 25%），⑥ `PRE_CROSS_CONFIRMED_ADD`（目标 100%）。同层级若目标低于当前仓位则取更低目标；前一项已产生目标 0 时不再计算后项。因子缺失或成交后第 3 个应观察交易日缺失时只记录 `EXPECTATION_DATA_UNAVAILABLE`，不伪造超时卖单，原有止损与结构失效保护仍继续运行。

**例子**：`MA5[-1]=10.00`、`MA20[-1]=10.08`、`close[-5]=9.80`、`close[-20]=9.20` 时，\(X_{cross}=(20×0.08+4×9.80-9.20)/3=10.533\)。若 `close[-1]=10.35`，所需上涨约 1.77%，在默认 2% 上限内；再满足趋势、RSI 与 MACD 条件时 BUY。若随后确认成交，后续三个有效交易日中任一日 `MA5>MA20` 即兑现；若第三日仍 `MA5≤MA20` 且未先触发价格/移动止损，则输出 `EXPECTATION_TIMEOUT`。若现价为 10.20，则需上涨约 3.26%，默认参数下 HOLD。

#### 3.6.2 三方依赖能力评估

只使用 C1–C3 已验证的 MA、RSI、MACD 与冻结 OHLCV；`close[-5]` 和 `close[-20]` 是固定常量索引，AST 无需变量窗口或循环。不得用实时行情、未来日线或本地重算均线补足预测。

#### 3.6.3 风险与验证方式

- 用手工窗口数据断言上述 \(X_{cross}\) 公式，并构造 `X_cross` 恰高于、恰等于、低于当前收盘的三种边界。

- `MA5[-1]>MA20[-1]`、`MA5[-1]≤MA5[-2]`、`MA20[-1]≤MA60[-1]`、临界涨幅超过上限任一情形均 HOLD。

- 对确认成交后的持仓构造三个连续有效交易日：第 1/2/3 日任一首次 `MA5>MA20` 均标记 `FULFILLED`，并生成从 50% 到 100% 的唯一加仓差额；第 3 日仍未上穿则目标为 0，仅产生一条 `EXPECTATION_TIMEOUT`。成交日、周末、节假日和无日线停牌日均不消耗窗口。

- 覆盖未兑现时 `MA5≤前日MA5`、MACD 变弱两类条件均从 50% 降至 25%，转强但未金叉仅恢复至 50%，不得直接加至 100%。

- 覆盖 `STOP_LOSS`/`TRAILING_STOP_LOSS → EXPECTATION_TIMEOUT → MA_DEATH_CROSS → PROFIT_TARGET_TRIM → PRE_CROSS_WEAKENING_REDUCE → PRE_CROSS_CONFIRMED_ADD` 优先级：前项退出后不得继续评估后项；同一持仓同一交易日重跑不重复生成加/减/退出建议。

- 验证 `close[-20]`、MA5/MA20 或成交后第 3 个应观察交易日缺失时，分别由输入门控或持仓风控记录数据不可用，绝不以自然日猜测超时。

#### 3.6.4 文件变更清单

模板逻辑与测试收敛在 §3.1.4 的模板注册表和参数化单测中；兑现时限状态、跨日计数、目标仓位优先级和报告审计由 §3.11/§3.12 的 `PositionLifecycleManager` 实现。

### 3.7 成交量骤增确认策略

#### 3.7.1 模块设计

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

持仓时先按 \(C_t\le P(1-p)\) 返回 `STOP_LOSS`；否则若 \(C_t<MA20_t\land MACD_t<0\) 返回 `VOLUME_SURGE_FAILURE`；其余 HOLD。

**例子**：前五日最大成交量为 80 万手、当日为 170 万手，昨日收盘 10.00、当日收盘 10.20，且 `MA20>MA60`、收盘在 MA20 上方、RSI6=60、MACD=0.06，则默认 `q=2.0,g=1.5%` 下 BUY。若当日 170 万手但收盘为 9.90，或当日收盘仅为 10.10，则分别因价格下跌、涨幅不足而 HOLD。

#### 3.7.2 三方依赖能力评估

成交量来自 `market.instrument_daily`，MA/RSI/MACD 来自 C1 的因子入库；固定 `volume[-6:-1]` 可被受限 AST 展开。该模板不依赖布林带，也不得因“放量”自行请求分钟成交明细。

#### 3.7.3 风险与验证方式

- 覆盖 `Vbase=0`、`Vt=q×Vbase`、略低于阈值、放量但收跌、放量且涨幅恰等于 \(g\) 五类边界。

- `MA20≤MA60`、收盘不高于 MA20、RSI=76、MACD≤0 任一情形 HOLD。

- 覆盖止损优先于趋势失效卖出，及 `volume[-6:-1]` 缺失/非正值被输入门控拒绝。

#### 3.7.4 文件变更清单

模板逻辑与测试收敛在 §3.1.4 的模板注册表和参数化单测中。

### 3.8 圆弧底 75A 策略

#### 3.8.1 模块设计

**定位与命名**：`arc_bottom_75a_v1` 是项目内定义的“圆弧底右侧突破”日线模板；“75A”仅保留为产品名称，不宣称是市场通用公式或收益等级。为适配当前受限 Sandbox，形态不扫描任意低点，而是使用**跨 40 个交易日间隔的 41 根锚点窗口**：每 10 个交易日取一个固定锚点，以“左侧回落—中部低位—右侧修复—突破颈线”形成可复现的近似定义。

设 `close[-41]`、`close[-31]`、`close[-21]`、`close[-11]`、`close[-1]` 分别为窗口的左端、左中部、底部、右中部和当前收盘；颈线基准 `N` 为左端和右中部收盘价中较高者；前五日量能基线 `Vbase` 为昨天起向前五日中的最大成交量。参数 `min_left_decline_pct`、`min_recovery_pct`、`neckline_breakout_pct`、`volume_multiple` 分别控制左侧最小回落、右侧最小修复、有效突破幅度和突破量能。

无持仓 BUY 必须同时满足：中部 `close[-21]` 相对左端至少回落默认 8%，且不高于左中部和右中部；右中部相对中部至少回升默认 6%；当前收盘至少高于颈线默认 1%，当日成交量至少为前五日最大量的 1.5 倍；同时 `MA20[-1] > MA20[-2]`、MA20 高于 MA60、RSI6 位于 50–75、MACD 为正且不弱于昨日。价格以当前收盘作为参考入场价，止损取冻结颈线和按 `stop_pct` 计算的价格止损中较高者，收益目标按 `reward_multiple` 计算。若止损不低于入场价，模板不产生 BUY，并按 §2.4 记录 `INVALID_PRICE_RANGE`。

评分起点为 70；当日成交量达到前五日最大量 2 倍加 8 分、RSI6 位于 55–68 加 5 分、当前收盘比颈线高至少 3% 加 5 分，范围为 70–88。

**中文操作规则**：

- **开仓**：观察跨 40 个交易日间隔的五个固定锚点，价格先从左侧回落到中部低位，再在右侧逐步回升；当前收盘以至少 1%（默认值）突破左端/右侧基准中较高的颈线，同时放量、MA20 严格高于昨日且高于 MA60、MACD 为正且不走弱、RSI 未过热。信号接受后，`QuantExecutionService` 在同一冻结快照派生 `arc_neckline_price` seed；信号被实际成交后，以风险允许最大仓位的 50% 建立首仓并复制该颈线，后续不随窗口滚动改变。

- **加仓**：**仅首仓后的下一完整有效交易日**检查一次：收盘严格守在冻结颈线上方、成交量不低于前五日量能基线，且 MACD 不比前一日走弱时，只补一次差额，使目标仓位达到 100%。该日未通过即写 `confirmation_missed`，之后不得迟到加仓。

- **减仓**：未触发清仓时，收盘等于冻结颈线、跌到或低于 MA20、当日成交量低于前五日量能基线，或 MACD 比前一日走弱，任一成立即将目标仓位降至 50%。

- **清仓**：收盘跌破初始止损价、跌破前一交易日已生效的移动止损价、**严格跌破**冻结颈线，或 MA20 低于 MA60 且 MACD 为负，任一成立即目标仓位归零。跌破冻结颈线表示右侧突破已失败，不能因滚动窗口改变而放宽退出标准。

**例子**：若 `close[-41]=10.00`、`close[-31]=9.45`、`close[-21]=9.10`、`close[-11]=9.80`，则中部相对左端回落 9%，右侧相对中部回升约 7.7%，颈线为 10.00。若今日收盘为 10.15、当日成交量 150 万手而前五日最大量 90 万手、MA20 高于 MA60、RSI6=61、MACD 为正且不低于昨日，则在默认参数下 BUY；若今日收盘仅 10.05，或量能仅 120 万手，则 HOLD。

#### 3.8.2 三方依赖能力评估

不新增第三方库或数据端点。形态只消费 C1–C3 已验收并冻结的 OHLCV、MA20/MA60、MACD、RSI6；使用固定常量索引和 `max`，不自算技术指标、不查询实时行情、不使用循环或变量下标。`market.instrument_daily` 与 `market.factor_daily` 必须按 `trade_date` 升序对齐，日线降序响应必须先经 `_sort_asc_by_trade_date` 归一后再生成快照。

#### 3.8.3 风险与验证方式

- 构造左侧回落、中部最低、右侧恢复、当前突破的确定性 fixture，分别验证回落幅度、恢复幅度、颈线突破幅度恰好达到和略低于阈值的边界。

- 分别构造中部不低于两侧锚点、`MA20[-1]≤MA20[-2]`、MA20 不高于 MA60、RSI6=76、MACD 为负或走弱、`Vbase=0`、量能刚好/略低于阈值，均应 HOLD。

- 断言 BUY 时受控 `lifecycle_seed.arc_neckline_price` 等于 `max(close[-41], close[-11])`，并与 `source_signal_id`/snapshot hash 随订单审计；首笔 fill 原子复制该 seed。后续窗口数据变化不得改写该值。只在下一完整有效交易日守住颈线且量能/MACD 确认时产生一次 50%→100% 加仓差额；下一日失败、第二日恢复仍不得加仓。

- 覆盖 `close=arc_neckline_price` 的 50% 弱化、`close<arc_neckline_price` 的 0% 清仓、成本止损、移动止损和趋势转空清仓的优先级；缺失任一锚点、量能、因子或 seed 时由输入门控/生命周期写 `LIFECYCLE_DATA_UNAVAILABLE`，不猜测减仓或清仓。

- 覆盖实际首笔成交价低于或等于初始止损时只产生 `INITIAL_STOP_BREACHED_ON_FILL` 的退出目标，以及首仓先达到 `profit_take_price`、随后满足确认条件时仍不得加仓至 100%。

#### 3.8.4 文件变更清单

模板注册、参数 schema、固定索引白名单和单测均收敛在 §3.1.4 的 `templates.py`、`strategy_contract.py` 与 `test_strategy_templates.py`；成交后需在基础方案的 `position_lifecycle_states` 加入冻结 `arc_neckline_price`，并由后续 `PositionLifecycleManager` 按本节规则计算确认、弱化与失效目标仓位。

### 3.9 策略目录边界与验证

#### 3.9.1 模块设计

V1 一个策略版本只能选择一个 `template_id`。用户可以复制同一模板创建不同参数版本，例如“稳健均线交叉（6% 止损）”与“进取均线交叉（8% 止损）”，但不能在 UI 通过 `AND/OR` 嵌套多个模板。原因是单票 BUY 的 score、风险距离和原因必须只有一个可审计来源；跨策略的资金竞争由不同任务的组合规划结果分别展示，而不是在策略源码中隐式合并。

以下合同把每个模板的开仓、加仓、减仓与清仓分开定义，是 `PositionLifecycleManager` 和回测引擎共用的唯一业务口径。设实际首笔成交价为 \(P_0\)、风险允许最大股数为 \(Q_{risk}\)、当前收盘为 \(C_t\)；“下一有效日”均指成交日后的下一根完整日线。开仓信号经组合层接受并**实际成交**后，才建立生命周期状态和 \(Q_{risk}\)，初始目标均为 \(0.50Q_{risk}\)。除 MA5 预上穿的未兑现阶段外，减仓目标均为 \(0.50Q_{risk}\)；加仓只在尚未完成确认加仓时发生一次，清仓目标均为 0。

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

| 基本面价值/成长 | 否 | PE/PB、营收、净利润、ROE | 单票快照无数据合同。 |

| 横截面多因子/行业轮动 | 否 | 全市场/行业收益与排名 | 当前 context 只有单票。 |

| 日内均线、网格、T+0 | 否 | 分钟/Tick、撮合、手续费 | 当前仅日线，且股票 T+1 约束未建模。 |

| ATR/KDJ/海龟突破 | 否 | ATR/KDJ 或历史滚动高低 | 因子表未入库，且当前 AST 不支持变量窗口扫描；禁止本地自算绕过技术指标约定。 |

| 盈利阶段移动止损 | 是（C6 验收后） | 已确认成交、初始订单止损、每日 high/close、持仓状态 | 浮盈达到冻结的 \(R\) 阈值后进入保本/跟踪；前一交易日已生效止损被当日收盘跌破时，目标仓位归零。 |

| 首次获利减仓 | 是（C8 验收后） | 实际成交价、初始止损、模板 `reward_multiple`、已成交仓位 | 收盘首次达到冻结 `profit_take_price` 时，目标仓位降至 50%，剩余仓位继续移动止损；成交确认后不重复。 |

| 目标仓位分批建仓/加仓/减仓 | 是（C8 验收后） | 已确认成交、生命周期状态、模板阶段条件 | 按上述阶段合同将实际仓位调整为冻结目标仓位；禁止固定 `SELL_PARTIAL` 比例重放。 |

新模板必须同时满足：字段在冻结 context 中真实可用；初始、确认、弱化、获利减仓与失效清仓各有确定性 fixture；渲染源码通过 AST；并声明每个阶段的目标仓位，且不重复实现 PositionPlanner 的账户风控。否则先扩展数据合同/执行基础方案，再立新模板方案，不允许脚本访问 DB 或自行请求 Tushare。

#### 3.9.2 三方依赖能力评估

不新增依赖。代理端点的真实能力以 C1 POC 结果为准；全市场日线/因子采集必须遵守项目的“全市场禁区间查询、单日和分批降级、日线升序归一、技术指标不自算”约定。

#### 3.9.3 风险与验证方式

| 验证层 | 场景 | 可执行断言 |

|------|------|------------|

| 因子 POC（人工部署门禁） | 代理端点采集全市场股票技术因子 | 记录字段、单日覆盖、截断、限流和 250 日可用率；未达 C1 门槛不得启用模板。 |

| 快照契约 | `market.factor_daily` 行 → `StrategyContext` | 断言各模板 required 字段、日期、数组长度、null 对齐和 task hash；修改当前市场表后 rerun 仍用同一快照。 |

| 每模板单测 | BUY、成本止损 SELL_ALL、结构失效 SELL_ALL、NO_SIGNAL、输入不足、参数边界 | 断言固定七键、reason code、分数范围、raw 价格严格关系与无多余字段；MA5 预上穿额外断言 20 日窗口临界价公式，成交量骤增额外断言五日基线、放量下跌与涨幅阈值，圆弧底 75A 额外断言 40 日固定锚点、颈线冻结和放量突破。 |

| 价格规范化 | 低价股、风险仅 1–3 tick、布林中轨贴近入场、浮点边界 | `Decimal` 规范后仍满足严格价格关系与最小风险收益；否则 BUY 保留、订单拒绝。 |

| 安全回归 | 默认/边界参数渲染的七套脚本 | 真实 AST validator 和 runner 通过；源码不含 import、循环、变量下标、属性、f-string 或未授权字段。 |

| 组合集成 | 技术形态 BUY，但 `block`、现金不足或行业不可用 | 信号可审计；PositionPlanner 拒绝订单；策略不被改写为 HOLD。 |

| 移动止损状态机 | 确认成交后从保护、保本、跟踪到卖出 | 只使用实际成交价和前一日已生效止损；最高价/止损单调不减；同一持仓同一交易日重跑只产生一条卖出建议；缺失成交或日线时 fail-closed 不启用。 |

| 预期兑现时限 | `ma5_pre_cross_v1` 成交后的第 1–3 个有效交易日 | 窗口内首次 MA5>MA20 标记 `FULFILLED`；第 3 日仍未上穿仅一次 `EXPECTATION_TIMEOUT`；成交日/无日线/非交易日不计数；退出优先级无双卖单。 |

| 前端人工验收 | 策略创建与报告 | 可查看公式、字段、参数范围、风险提示和 reason code 中文映射；明确“日线信号、人工确认、非收益承诺”。 |

自测只运行 fake fixture 的新增目录；不运行 `real_llm` 或 `real_toolkit` fixture 的测试。因子代理 POC 是部署前人工门禁，不作为自动 pytest。

#### 3.9.4 文件变更清单

见 §3.1.4。任务分解时必须先形成“基础执行能力（C1–C8）”验收任务，再形成“模板注册与七模板”任务；两类任务不得并行假定对方已完成。

### 3.10 盈利阶段移动止损

#### 3.10.1 模块设计

移动止损只属于**确认成交后的持仓生命周期**，不属于模板 `strategy(context)`。人工或券商回写成交后，`PositionLifecycleManager` 建立与持仓一一对应的止损状态；任何仅有策略 `signal_entry`、建议订单或 `average_cost` 的记录都不得初始化该状态。

初始风险以实际成交价 \(P_0\) 和成交时接受订单的规范化初始止损 \(S_0\) 计算：

\[

R=P_0-S_0>0

\]

状态固定包含：`position_id`、`fill_id`、`fill_price=P_0`、`initial_stop=S_0`、`high_water_mark`、`active_stop_price`、`phase`、冻结 `trailing_config`、`last_processed_trade_date` 与 `exit_signal_id`。同一持仓仅允许一个未关闭状态，`exit_signal_id` 非空后不再产生第二条移动止损卖出建议。`QuantExecutionService` 每日先运行该管理器；若它为某持仓给出目标仓位 0，则该票在同一 `effective_trade_date` 不再计算模板的其他减仓/加仓分支，杜绝同日冲突订单。

每日按冻结日线处理，先使用**前一交易日已生效**的 \(S_{t-1}\) 判定今日收盘：

\[

C_t\le S_{t-1}\Rightarrow SELL\_ALL / TRAILING\_STOP\_LOSS

\]

这样不会用当天先出现的最高价反推当天收盘卖出，避免日线 OHLC 顺序未知导致的前视偏差。只有未触发卖出时才更新：

\[

H_t=max(H_{t-1},High_t)

\]

配置以初始风险 \(R\) 表达，包含三个待回测标定的冻结字段：\(b=breakeven\_activation\_r\)、\(a=trailing\_activation\_r\) 与 \(d=trailing\_drawdown\_pct\)，并强制 \(0<b\le a\)、\(0<d<1\)。状态流转如下：

| 阶段 | 进入条件 | 当日收盘未触发卖出后的下一日有效止损 |

|------|----------|--------------------------------------|

| `PROTECT` | \(H_t<P_0+bR\) | \(S_t=S_0\) |

| `BREAKEVEN` | \(P_0+bR\le H_t<P_0+aR\) | \(S_t=max(S_{t-1},P_0)\) |

| `TRAILING` | \(H_t\ge P_0+aR\) | \(S_t=max(S_{t-1},H_t(1-d))\) |

因此有效止损总满足 \(S_t\ge S_{t-1}\)，永远不会因回撤下调。`take_profit` 在入场时用于风险收益校验；首笔实际成交后重算为冻结 `profit_take_price`，首次到价把目标仓位降至 50%，不是全清触发价。由于当前没有可验证回测结果，\(b,a,d\) 不写经验默认值、不在 UI 标记为推荐；它们必须在模板版本/成交时冻结，历史持仓不受后续配置修改影响。

**例子**：若实际成交 \(P_0=10.00\)、初始止损 \(S_0=9.50\)，则 \(R=0.50\)。若回测标定配置为 `b=1R`、`a=2R`、`d=8%`，最高价先到 10.50 时止损提升为 10.00；最高价后续到 12.00 时止损提升为 11.04。次日收盘若不高于 11.04，则只生成一次 `TRAILING_STOP_LOSS` 卖出建议；若收盘为 11.20，则更新后仍持有，且止损绝不回落。

#### 3.10.2 三方依赖能力评估

不新增第三方库。需要基础执行方案新增已确认成交、持仓止损状态与日线读取接口；日线 high/close 必须来自与持仓处理日一致的 `market.instrument_daily` 冻结读模型。策略 Sandbox、实时行情与 LLM 均不参与计算。若系统暂未接券商，人工成交确认必须同时写入真实成交价、数量、成交日及关联建议订单 ID，禁止只修改 `average_cost` 触发移动止损。

#### 3.10.3 风险与验证方式

- 用 `high` 先创新高、随后回撤的跨日 fixture，断言止损只上调，且当日新高形成的止损仅自下一交易日开始生效。

- 分别验证 `PROTECT → BREAKEVEN → TRAILING`、阶段临界点、止损触发、结构失效与移动止损同日时的固定优先级：移动止损触发时原因为 `TRAILING_STOP_LOSS` 且模板卖出分支不得执行；未触发时模板仍可产生结构失效 `SELL_ALL`。

- 并发/重跑测试以 `(position_id, effective_trade_date)` 作为幂等键；重复执行不得降低 `high_water_mark/active_stop_price`，不得创建第二条建议卖出或覆盖已关闭状态。

- 缺失 fill、初始止损、持仓数量、日线 high/close 或配置交叉约束不成立时，记录 `TRAILING_STOP_UNAVAILABLE`，不得猜测价格、不得执行卖出。

#### 3.10.4 文件变更清单

| 类别 | 路径 | 改动说明 |

|------|------|----------|

| 基础前置（新建/修改） | 基础方案创建的持仓、成交、建议订单迁移/ORM/仓储 | 保存实际成交事实及 `position_trailing_stops` 状态；唯一约束保证每持仓仅一个活跃止损状态与每持仓/交易日仅一条目标仓位意图。 |

| 基础前置（新建） | `backend/modules/quant_strategy/application/position_lifecycle_manager.py` | 以冻结成交和日线输入实现阶段状态机、单调止损、目标仓位意图幂等与失败降级。 |

| 跨链路（修改） | `QuantExecutionService`、持仓审计 cursor、`ReportDTO` 与量化报告面板 | 每日先处理已确认持仓止损，再执行模板扫描；报告展示阶段、当前有效止损、最新高点、配置快照和目标仓位原因，不展示源码。 |

| 测试（新建） | `backend/tests/unit/quant_strategy/test_position_lifecycle_manager.py` | 覆盖状态流转、跨日生效、重跑/并发、缺失数据和唯一性。 |

### 3.11 MA5 预上穿预期兑现时限

#### 3.11.1 模块设计

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

该退出由“建仓假设未兑现”触发，不与收益或亏损金额绑定。未兑现且 `MA5≤前日MA5` 或 `MACD` 较前一日走弱时，目标从 50% 降至 25%；若次日修复但未金叉，最多恢复至 50%。每日优先级复用 §3.6：清仓 → 获利减仓 → 弱化减仓 → 确认加仓；前项若生成目标 0，同日不再计算后项。`exit_signal_id`、`state_version` 与 `(position_id, effective_trade_date,target_shares,reason_code)` 唯一约束共同保证重跑/并发下只有一条差额建议。

若尚未取得真实成交、没有关联初始订单止损、某个应观察日缺少日线或 MA5/MA20，状态保持 `PENDING`，记录 `EXPECTATION_DATA_UNAVAILABLE`，且不以自然日猜测超时。价格止损、已生效移动止损和模板结构失效仍继续评估，避免数据问题使持仓失去既有风险保护。

#### 3.11.2 三方依赖能力评估

不新增第三方依赖。事实输入来自确认成交记录、已冻结模板版本和 `market.instrument_daily`/`market.factor_daily` 的本地读模型；逐日对齐以真实写入的 `trade_date` 为准。不能把 `TradingCalendar` 的 Tushare/AKShare/weekday-only 降级路径用于本规则的到期计算，也不请求实时行情或 LLM。

#### 3.11.3 风险与验证方式

- 成交后第 1、2、3 个完整观察日首次 `MA5>MA20` 分别断言 `FULFILLED`、`observed_trading_days` 正确、此后不再生成超时卖出。

- 第 3 日 `MA5=MA20` 或 `MA5<MA20` 分别断言只生成一条 `SELL_ALL / EXPECTATION_TIMEOUT`；同一交易日重跑及并发 worker 不产生第二条。

- 构造周末、节假日、停牌无日线和因子缺行，断言均不消耗三日窗口；缺失日写 `EXPECTATION_DATA_UNAVAILABLE`，不以自然日超时。

- 价格止损/移动止损、兑现、超时和 `MA_DEATH_CROSS` 同日组合 fixture，严格断言既定优先级及无双卖单。

- 建仓来源不是 `ma5_pre_cross_v1`、只有建议订单未成交、`FULFILLED/TIMED_OUT` 终态重跑，均不得新建或重置活跃预期。

#### 3.11.4 文件变更清单

| 类别 | 路径 | 改动说明 |

|------|------|----------|

| 基础前置（新建/修改） | 基础方案创建的持仓、成交、建议订单迁移/ORM/仓储 | 增加 `position_expectations`：冻结模板 ID、成交日、三日窗口、观察计数、终态与退出关联；加每持仓一条活跃预期及每持仓/交易日一条退出建议约束。 |

| 基础前置（修改） | `backend/modules/quant_strategy/application/position_lifecycle_manager.py` | 在移动止损后、模板持仓卖出前执行 `ma5_pre_cross_v1` 的兑现、弱化、超时状态机和优先级编排。 |

| 跨链路（修改） | `QuantExecutionService`、持仓审计 cursor、`ReportDTO` 与量化报告面板 | 展示冻结三日时限、已观察日数、状态、兑现/超时日期和原因码；报告清楚区分“预期未兑现退出”与价格止损。 |

| 测试（新建） | `backend/tests/unit/quant_strategy/test_position_lifecycle_manager.py` | 增加兑现、弱化、到期、真实交易日计数、缺失数据、优先级、幂等与终态不可回退的 fixture。 |

### 3.12 全流程持仓生命周期

#### 3.12.1 模块设计

所有模板共享同一个成交后状态机，避免在 Sandbox 中按固定比例输出 `SELL_PARTIAL`。首次 BUY 建议被实际成交确认时，服务以该笔接受订单的规范化 `entry/stop` 和组合冻结风险预算计算 \(Q_{risk}\)，并冻结 `risk_capacity_shares`、`initial_fill_price`、`initial_stop_price`、`profit_take_price`、`template_id`、模板版本/参数、`target_exposure_pct=0.50`、`lifecycle_phase=ENTRY_PENDING` 与 `state_version`。只有首次实际成交数量达到相应整手目标后进入 `INITIALIZED`；若部分成交，实际仓位即为临时目标，后续仍用差额补足而不是重建状态。

七模板的确认、弱化与清仓规则以 §3.9 表为唯一来源；圆弧底 75A 以首笔成交时冻结的 `arc_neckline_price` 作为确认、弱化和结构失效的唯一颈线。初始均为 50%，确认条件首次成立时提出加至 100% 的差额；确认前或确认后的弱化条件成立时目标降至 50%，但 MA5 预上穿在未兑现期间降至 25%。同一确认条件只可完成一次，字段 `confirmation_completed` 防止重跑加仓；首次收盘达到 `profit_take_price` 时目标降至 50%，字段 `profit_trim_completed` 防止重放；清仓条件、成本止损和移动止损均把目标设为 0 并终止生命周期。

处理顺序固定为：① 成本止损/移动止损，② 模板完全失效或 MA5 兑现超时，③ 首次获利减仓，④ 模板弱化减仓，⑤ 模板确认加仓。每个阶段先锁定 `position_lifecycle_states`，读取同日冻结行情/因子和最新实际成交数量，计算一个最终目标；`PositionPlanner` 仅用 `target_shares-actual_shares` 生成 `BUY_ADD`、`SELL_REDUCE` 或 `SELL_EXIT`。建议订单未成交、被拒绝或仅部分成交不推进 `confirmation_completed`/`profit_trim_completed`/终态；人工或券商回写成交在同一事务更新实际仓位、订单状态和 `state_version`，随后才允许下一阶段。若同日多个规则都要求减仓，取最低目标；目标为 0 时不再创建其他订单。

#### 3.12.2 三方依赖能力评估

不新增第三方依赖。需要本地持仓、建议订单与人工/券商成交回写具备事务锁和版本字段；`market.instrument_daily`/`market.factor_daily` 已提供实际日线和因子读取。`TradingCalendar` 不参与 MA5 到期判断；Sandbox、Tushare、LLM 均不在持仓状态机调用路径。

#### 3.12.3 风险与验证方式

- 分别构造七模板的首仓 50%、确认加至 100%、弱化降至 50%、失效归零；MA5 另覆盖弱化降至 25%、三日未金叉归零。

- 目标首次达到收益价后，断言一次 `PROFIT_TARGET_TRIM` 把仓位降至 50%，价格重复穿越不重复减仓；已低于 50% 的持仓不得因该规则反向加仓。

- 对加仓、减仓和清仓建议构造未成交、部分成交、拒绝、重复 worker 与同日重跑；断言仅按实际差额补单，成交前不推进阶段，且 `(position_id,effective_trade_date,target_shares,reason_code)` 唯一。

- 构造清仓、获利减仓、弱化、确认同日成立，断言固定优先级及最终仅一条建议；缺成交、风险容量、行情或因子时写 `LIFECYCLE_DATA_UNAVAILABLE`，不臆造仓位。

#### 3.12.4 文件变更清单

| 类别 | 路径 | 改动说明 |

|------|------|----------|

| 基础前置（新建/修改） | 基础方案创建的持仓、成交、建议订单迁移/ORM/仓储 | 新增 `position_lifecycle_states`、`position_expectations` 与差额订单字段：冻结风险容量/价格、目标比例/数量、确认与获利减仓标志、阶段、版本和订单关联；唯一约束覆盖每持仓活跃状态、每持仓/交易日/目标/原因一条建议。 |

| 基础前置（新建） | `backend/modules/quant_strategy/application/position_lifecycle_manager.py` | 实现七模板生命周期规则、优先级、目标仓位计算、成交回写推进与 fail-closed 审计；持久化圆弧底 75A 的 `arc_neckline_price`。 |

| 基础前置（修改） | `PositionPlanner`、建议订单 DTO/仓储、人工成交确认服务 | 支持 `BUY_ADD`、`SELL_REDUCE`、`SELL_EXIT`；按实际与目标差额、整手、组合约束和 `state_version` 创建/拒绝订单，成交后原子推进状态。 |

| 跨链路（修改） | `QuantExecutionService`、持仓审计 cursor、`ReportDTO` 与量化报告面板 | 每日先管理既有生命周期，再扫描初始信号；展示实际/目标仓位、阶段、冻结风险容量、初始风险、收益目标、已完成确认/减仓、订单与原因码。 |

| 测试（新建） | `backend/tests/unit/quant_strategy/test_position_lifecycle_manager.py` | 覆盖七模板规则、优先级、差额/部分成交、幂等、并发、缺数据与状态不可回退，包含圆弧底 75A 颈线冻结回归。 |

## 四、已确认决策 / 待确认问题

### 已确认决策

1. 本方案专门定义“可创建的量化策略和持仓生命周期”，不替代 Sandbox、快照、人工成交确认或实盘券商下单设计。

2. V1 只消费已通过 C1–C3 验收的日线 OHLCV、MA、布林带、MACD 与 RSI；不在策略中自算技术指标。

3. Sandbox 仍只开放 `BUY / SELL_ALL / HOLD`，不以无状态 `SELL_PARTIAL` 直接卖固定比例；成交后由 `PositionLifecycleManager` 计算 0/25/50/100% 的冻结目标仓位，Planner 只执行实际仓位到目标仓位的唯一差额订单。

4. 所有模板首仓为风险允许最大仓位的 50%；确认条件首次成立加至 100%，模板弱化降至 50%（MA5 预上穿未兑现期间降至 25%），首次达到实际成交风险收益目标降至 50%，止损/结构失效/兑现超时清仓。

5. 策略基于日线收盘后运行，属于人工确认的日线波段信号，不宣称同一根日线收盘可成交或任何收益表现；移动止损使用前一交易日已生效止损判断当日收盘，新抬升的止损从下一交易日生效。

6. `ma5_pre_cross_v1` 的实际成交持仓以成交后第 3 个完整有效交易日为预期兑现截止点；窗口内首次 `MA5>MA20` 加至 100%，未兑现时 MA5/MACD 走弱降至 25%，第 3 日仍未上穿以 `EXPECTATION_TIMEOUT` 清仓。超时只基于实际日线/因子序列计数，不按自然日或 fail-open 日历推算。

### 待确认问题

1. **首批上线是否确认只提供本方案七个预置模板，不提供自定义受限脚本？**

   - **背景**：当前基础方案仍设计 `StrategyEditorDialog`、草稿源码 DTO 与用户可编辑 `strategy(context)`；本方案的模板模式则要求前端只提交 `template_id/template_params`，服务端渲染源码。两者不能在同一 V1 接口中同时作为未区分的草稿模式存在。

   - **目标**：选“仅模板”则将基础方案的 V1 草稿源码 API/UI 改为模板选择、参数表单和只读源码/公式预览，并在 §2.3 的模板快照合同下实现；选“模板 + 自定义脚本”则必须新增 `version_kind=TEMPLATE/CUSTOM`、两套互斥请求 DTO、独立编辑器、源码审计标识与完整回归矩阵。

   - **推荐**：**仅模板先行**。七套首批策略均可解释、可测试，先验证 C1–C8；自定义脚本另立增量需求。

2. **是否确认先以“日线波段、非收益承诺”定位七个模板默认参数，并将移动止损的 \(b/a/d\) 参数留待样本外回测标定？**

   - **背景**：5%/6% 初始风险上限、2.0–2.5 倍目标及 RSI 阈值是可解释初始值，尚未经过本项目回测；移动止损的保本触发 \(b\)、跟踪触发 \(a\) 与回撤比例 \(d\) 更不能仅凭经验指定。当前没有分钟行情、滑点、手续费或样本外评估引擎。

   - **目标**：选“日线基础模板”则 UI 明示需自行评估，移动止损仅在配置被显式创建/冻结后启用；选“回测后推荐”则需先新增复权、交易日历、成本模型与回测评估方案，统一标定初始止损、收益目标与 \(b/a/d\)。

   - **推荐**：**日线基础模板、非收益承诺**。先交付可复现信号与人工确认订单，回测能力独立设计。
