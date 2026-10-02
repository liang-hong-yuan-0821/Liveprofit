> **状态**：已完成（方案评审通过，2026-09-24）
> **关联文档**：[市场数据自动补齐](../市场数据自动补齐/plan.md)｜[每日投研自动流程（归档）](../../archive/每日投研自动流程/plan.md)

# 量化前行情就绪编排

## 一、背景与动机

| 维度 | 现状 | 问题 | 目标 |
|------|------|------|------|
| 量化准入 | `backend/modules/daily_research/application/quant_pipeline.py::run_daily_quant` 在策略运行前调用 `DataReadinessGate.resolve`；`backend/modules/quant_strategy/application/data_readiness.py` 检查 `instrument_daily`、`factor_daily.ma_qfq_5`、`adj_factor`、`trade_status_daily` 的最大日期 | 数据日期落后时任务只等待重试；没有请求补齐。例：21:00 `factor_daily` 仍停在前一交易日时会抛 `QUANT_DATA_NOT_READY`，但只依赖现有市场定时任务是否恰好补到了目标日 | 在运行策略前自动请求目标日量化输入补齐；目标日与四类输入达到各自就绪门槛后才运行策略 |
| 市场补齐复用 | `backend/modules/market_data/application/refresh_service.py::ensure` 通过 Redis 去重/预算并投递现有 `market-data` Worker；`db/instrument/ingest/refresh.py::collect_refresh` 在共享 PG advisory lock 下定向采集 | `CN_STOCK_DAILY` 的 `_missing_units` 只读 `instrument_daily` 与可信停牌事实；它不检查 `factor_daily` 的 qfq 列或 `adj_factor`。把日线任务成功当作量化就绪会遗漏其余依赖 | 沿用现有准入、任务状态、Worker、重试和锁；补充量化专用内部资源，其完成判定覆盖量化依赖 |
| 公共行情体验 | `RefreshService.status/tick` 遍历 `Resource`，`backend/api/schemas/market_refresh.py` 将该枚举作为页面可请求资源 | 若把量化全市场因子塞进公开 `CN_STOCK_DAILY`，打开市场页的自动补齐也会拉全市场 qfq/复权数据；页面状态和人工按钮还会暴露耗时较大的量化采集 | 页面仍只见既有六种市场资源；量化专用资源只由量化流程准入，不扩大页面定时刷新和手动刷新范围 |

## 二、架构设计

量化任务（analysis task/outbox）在策略扫描前执行数据预检。预检调用同一个 `RefreshService`，以任务不可变的 `scheduled_at`（手动任务即提交时刻）作为既有 `RefreshPolicy.target` 的时间锚点，解析该时点“最新已到发布时点的 CN 交易日”，为 `CN_STOCK_QUANT_INPUTS` 申请后台任务；同一任务重试时不因运行时间变化漂移目标日。Redis 按资源与日期幂等，原 `market-data` Dramatiq Worker 读取冻结的目标代码/日期和组件缺项后，在写入 PG 的连接上获取现有 advisory lock，复核缺项并更新现有行情表。21:00 定时量化通常解析为当日交易日；手动运行处于发布缓冲期间、周末或节假日时使用提交时点最近已发布的交易日，并在报告保留请求日与实际行情日。日历不可用时不猜日期，任务保持可重试。量化任务收到 QUEUED/IN_PROGRESS/未就绪时抛可重试 `QUANT_INPUTS_NOT_READY`；后续 attempt 重新核验，四类输入分别达到明确门槛后才进入量化策略。`quant_news_refresh` 复用父批次行情快照，不触发新的市场采集。

```text
21:00 / 手动量化 analysis task
  → 以任务 scheduled_at 为锚点，RefreshPolicy 解析当时最新已发布 CN 交易日
  → 量化输入覆盖预检
  → 缺项：RefreshService.ensure_quant_inputs
      → Redis 幂等/预算 → market-data 队列 → 现有 Market Worker
      → 公共 PG advisory lock → 定向补齐四类量化输入到现有表
  → 尚未齐备/日历不可用：量化任务可重试（不运行策略）
  → 四类输入分别达标：原有 REPEATABLE READ 快照 → 所有已发布策略 → 报告

市场页面与 Dispatcher 的公开刷新 → 原六个 Resource（行为不变）
```

不新增 PostgreSQL 表或迁移。业务事实继续写入 `market.instrument_daily`、`market.adj_factor`、`market.factor_daily`、`market.trade_status_daily`；内部资源作业的可重建运行状态复用现有 Redis 刷新存储和 Dramatiq 队列。

## 三、设计概览

### backend

#### 数据表与迁移

不新增表。四类量化输入继续使用既有行情事实表；Redis 只保存有 TTL 的刷新作业与覆盖快照。

#### DAO 与读模型

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| 量化股票目录【修改】 | 把量化活动 CN 股票 predicate 抽到 `db/instrument/dao/instrument.py`，供策略执行与刷新覆盖共用 | `db/instrument/dao/instrument.py`、`backend/modules/analysis/infrastructure/quant_execution_market_data.py` | 覆盖分母与策略实际枚举股票一致 |
| 量化资源覆盖读模型【修改】 | 分别计算目标日股票目录下日线、qfq、复权因子、交易状态覆盖；冻结组件级缺项 | `backend/modules/market_data/infrastructure/refresh_repository.py` | 仅当四类输入各自达到门槛才报告资源 FRESH |
| 量化数据写入【修改】 | 验证目标日 adj_factor/qfq 帧并写回已有 DAO；保留 qfq 与状态的现有原子采集边界 | `db/instrument/ingest/frames.py`、`refresh.py`、`stock_factors.py`、`guard.py`、`db/instrument/dao/adj_factor.py` | 能按真实组件缺项执行安全、可重试的定向补齐 |

#### 采集与 Worker

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| 内部资源 `CN_STOCK_QUANT_INPUTS`【新增】 | 在资源策略、作业 spec 与 collector 注册量化输入资源；不加入公开资源集合 | `backend/modules/market_data/application/refresh_policy.py`、`db/instrument/ingest/refresh.py`、`db/instrument/ingest/guard.py` | 现有 Worker 能消费并处理量化专用刷新任务 |
| 刷新生命周期【修改】 | `status`/公开 `ensure` 只处理六个公开资源；`tick` 为内部资源处理活动作业与重试但不自动创建日常全市场量化刷新 | `backend/modules/market_data/application/refresh_service.py` | 量化缺项只由量化请求触发，同时保有现有租约恢复和重试 |

#### 服务层

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| 量化预检接入【修改】 | 量化 pipeline 获取有效市场日期与内部刷新决策；缺项时触发并可重试，达标后继续；新闻派生刷新沿用父快照 | `backend/modules/daily_research/application/quant_pipeline.py`、`backend/workers/analysis_executor.py`、`backend/workers/wiring.py` | 不会在量化输入未达标时提前扫描或生成空候选报告 |

#### DTO 与契约

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| 公开资源枚举【修改】 | 用只含六个公开资源的契约类型约束刷新状态、请求、决策和公开作业 | `backend/api/schemas/market_refresh.py` | 内部量化作业不会出现在公开 API 的资源枚举中 |

### frontend

无改动：内部资源不进入页面 API 契约，市场页面维持既有资源和刷新按钮。

## 四、详细设计

### 4.0 模块总览

| 维度 | 问题 | 方案概览 |
|------|------|---------|
| 内部资源与准入边界 | `Resource` 同时用于 `RefreshService.status` 遍历和 API schema；新增枚举会把内部作业暴露给页面 | 分开内部资源和公开 API 同名六值 `Resource`；量化通过私有准入，调度只恢复内部活动作业、不自动发现内部缺项 |
| 覆盖与目标日采集 | `collect_refresh` 现有 `CN_STOCK_DAILY` 仅验证 `open/high/low/close/pct_chg` 与可信停牌；不验证 qfq 和 `adj_factor` | 为量化资源保存组件级覆盖与冻结 `component_units`，分别补日线、adj_factor、qfq+状态原子采集 |
| 量化任务前置条件 | `run_daily_quant` 在快照中发现水位落后时仅重试，未申请补齐；`quant_news_refresh` 则必须固定沿用父候选行情日 | 普通 scheduled/manual quant 先解析已发布目标日、请求内部资源并在任务重试时复核；新闻派生刷新不申请新行情 |
| API 隔离与验证 | `RefreshRequest.resources` 直接引用内部 Resource enum，且 `status()` 展示所有枚举值 | 对外 schema 显式限定六个公开资源，并覆盖 API 隔离、补齐覆盖、锁冲突/失败、任务准入和新闻刷新回归 |

### 4.1 内部资源与准入边界

#### 4.1.1 模块设计

- 新增内部 `Resource.CN_STOCK_QUANT_INPUTS`，目标市场为 CN。内部目标日由既有 `RefreshPolicy.target(resource, now)` 按交易日历、5 小时发布缓冲和当前时间解析；内部快照只检查该目标日，不继承 public daily resource 的 3 日窗口。公开资源集合仍是现有六项，`RefreshService.status()` 仅列公开资源，公共 `ensure()` 拒绝内部资源。
- 量化运行入口先解析并校验 workflow `scheduled_at` 与 `wait_until`，再读取 scheduled news dependency。两者必须存在、ISO 格式可解析且 `tzinfo`/`utcoffset()` 有效；任一缺失、坏格式或 naive 时间不得回退到当前时间、事件 cutoff 或 `effective_trade_date`。`scheduled_at` 无效时截止前返回可重试的 `QUANT_INPUTS_NOT_READY`；若 `wait_until` 有效且已经到期，则直接交付最小零策略 partial，`market_as_of_trade_date`、`event_as_of` 设为 null 并列明元数据错误。若 `wait_until` 自身不可解析则 fail-closed，不打开行情快照、不扫描策略。
- 为 `RefreshService.snapshot` 增加可选 `at: datetime | None`；普通市场 API/dispatcher 仍以当前时间选日，量化预检传任务冻结的 `scheduled_at`。新增私有 `ensure_quant_inputs(*, at: datetime, mode: Literal["auto", "retry"], trigger: str="QUANT") -> dict`，返回 `{state: "ready"|"pending", effective_trade_date, decision, job_id, coverage, universe_digest}`；FRESH 返回 ready/UP_TO_DATE，否则调用现有 `RedisRefreshStore.ensure` 与 `dispatch`（预算/cooldown 使用当前 clock，目标日仍按 `at` 计算）。21:00 scheduled quant 通常使用当日；manual 处于当天发布缓冲期、周末或节假日时按提交时点使用最近已发布交易日。日历未知时不猜日期，返回 pending。该入口只被量化 Worker 使用。
- 私有量化资源必须固定使用 `TushareProvider`，不跟随 `LIVEPROFIT_DATA_SOURCE=akshare` 的主源选择，也不把 AKShare 的 unsupported 文本或空返回交给帧验证器。该资源不使用 AKShare 兜底；Tushare 未配置、接口异常、空或不完整响应时保持缺项，按既有刷新模式重试/失败，不写成功事实。其他公开资源保持原 provider/fallback 路由。
- `tick()` 对内部资源缓存/核验快照、恢复 RUNNING 租约、触发到期重试；恢复时用 Redis 活动作业冻结的 `target_trade_date` 读取上下文，不用新一天的 policy target 替换；lease recovery 以 `verify_spec.freshness == "FRESH"` 终结成功。自动准入分支仅遍历公开资源，因此不会在量化请求之外自行启动全市场 qfq 采集。
- scheduled quant 用 `mode="auto"`，尊重现有 auto 总开关/预算；manual quant 用 `mode="retry"`，仍受市场刷新总开关、公共预算与 cooldown 控制。手动在当日发布缓冲结束前提交时固定使用提交时点最近已发布日，重试即使跨过 20:00 也不把同一分析任务改成另一行情日；不在 15:30–20:00 对尚未发布的当日数据空耗 1 小时重试窗口。量化 preflight 超过 `wait_until` 仍未就绪时按 `QUANT_INPUTS_NOT_READY` partial 返回；数据快照共同水位不匹配使用 `QUANT_DATA_NOT_READY`，目标日和股票池变化分别保留 `QUANT_INPUT_TARGET_MISMATCH`、`QUANT_UNIVERSE_CHANGED` 原因。报告的最新行情标志按实际 effective date 相对请求日推导，不拿旧日数据冒充目标日结果。
- API schema 独立定义仍名为 `Resource` 的六值公开枚举；内部策略枚举通过 `MarketResource` 别名导入。`RefreshRequest.resources`、`RefreshDecision.resource`、`RefreshJob.resource`、`RefreshGroup.resource` 和公开 status DTO 全引用公开枚举；job 查询在序列化前检查 resource，不公开内部 job（404）。公开 OpenAPI component 名和六个枚举值不变。

#### 4.1.2 三方依赖能力评估

- Redis 和 Dramatiq 均为“市场数据自动补齐”已接通依赖；复用当前 `market-data` 队列，不新增依赖、队列或 PG 作业表。
- 数据源使用现有 `TushareProvider.get_full_market_daily_df`、`get_full_market_factor_df`（股票 `adj_factor`）、`get_full_market_technical_factor_df` 与 `get_full_market_trade_status_df`。目标日尚未发布时源返回空/不完整，collector 保持缺项，量化等待重试；不把空响应写成成功。

#### 4.1.3 风险与验证方式

- 通过 API contract 验证新增内部枚举不可由公开 refresh 请求提交、状态响应不出现内部资源、内部作业不可通过公开 job 接口读出。
- 用 fake clock / Redis store 验证 21:00 与发布缓冲前手动任务的 effective date；公共 tick 不会自动创建量化资源任务，但会按冻结日期恢复其活动租约与到期重试。

#### 4.1.4 文件变更清单

新建：无。修改：`backend/modules/market_data/application/refresh_policy.py`、`refresh_service.py`、`backend/api/schemas/market_refresh.py`、`backend/api/routers/market_refresh.py`、相关刷新 service/API 测试。schema 内公开 `Resource` 仍保持原六值与 OpenAPI component 名，生成客户端无需变化。

### 4.2 覆盖与目标日采集

#### 4.2.1 模块设计

- 量化目录必须与策略扫描完全相同：`AllMarketUniverseBuilder.list_active_cn_stocks` 使用 `instrument_type='stock' AND list_status='L' AND ts_code ~ '^[0-9]{6}\\.(SH|SZ|BJ)$'`。将该 predicate 抽为 `db.instrument.dao.instrument` 中的共用查询，由量化执行器与刷新覆盖仓库共同调用；不把 dc 成分股额外加入分母。单日四类依赖独立统计：
  - 股票日线：有效 `open/high/low/close/pct_chg`，或该日可信 `source='tushare' AND is_suspended=true`，与既有 `CN_STOCK_DAILY` 口径一致。
  - qfq：`stock_factors.REQUIRED_QFQ` 全列有效覆盖率至少 95%，沿用 `collect_stock_quant_day` 的 `MIN_GLOBAL_COVERAGE`。
  - 复权因子：有效、正值 `market.adj_factor.adj_factor` 覆盖率至少 95%。
  - 交易状态：可信、目标日状态记录覆盖率至少 95%，沿用现有量化采集器的状态帧覆盖门槛。
- `CN_STOCK_QUANT_INPUTS` 的 FRESH 语义为四类依赖分别满足各自覆盖门槛，而非合成一个最小/交集百分比：日线要求所有策略股票都有有效目标日日线或可信停牌豁免；qfq、复权因子、交易状态各至少 95%，按 `available + exempt >= ceil(expected * threshold)` 计算，非空目录为分母。执行期 `MarketContextBatchLoader` 对具体股票检查目标日与各必需列，单票缺失仍 fail-closed/标不可用，不使用不完整输入。内部快照分别保存每类的 expected/available/exempt/missing 和门槛。
- 当前 `collect_stock_quant_day` 将 qfq 与交易状态作为同一写事务：两帧各自达到 95% 覆盖才一起写入。此方案保留该原子边界；覆盖与 `component_units` 仍分别记录 `qfq` 和 `trade_status`，任何一项未达阈值时都重新执行 `qfq_status` 采集组，并在两项同时验证通过后原子写入。日线与 adj_factor 各为独立提交组件。
- `target_spec` 冻结完整策略代码集及排序后 SHA-256 `universe_digest`、四组件阈值/coverage 基数、组件事实缺项 `component_missing_units` 和 Redis 可计数的执行操作 `units`。组件事实缺项用 `{component,code,trade_date}`，组件名为 `daily`、`qfq`、`adj_factor`、`trade_status`，保留组件身份供复核与审计。执行操作单位使用 `{operation,code?,trade_date}`：日线每个需补股票一项（100% 门槛，需要逐票完成）；adj_factor 是同一目标日单一分组操作（95% 门槛）；qfq/status 因采集为整市场帧且共用原子事务，只要 qfq、trade_status 任一低于阈值，或日线存在缺口需要确认停牌豁免，就合并为同日唯一一项 `operation="qfq_status"`。若该组只由日线缺口触发，则冻结 `force_refresh=true`，worker 不因 qfq/status 当前总体 coverage 已达标而跳过此次停牌复核；执行后仍依据两组件门槛核验完成。因而同日不会按 qfq/status 代码数重复触发全市场采集，也不会把 qfq 与 status 记作两次采集。
- `verify_spec` 始终以冻结完整代码集重算各组件 coverage；日线操作按该代码的有效日线或可信停牌事实复核；adj_factor 操作仅当 adj_factor 达到 95% 门槛才完成；qfq_status 操作仅当 qfq 与 trade_status 分别达到各自 95% 门槛才完成。对允许留存的 ≤5% 实际缺行，两个日级因子组操作仍算完成；coverage 继续呈现真实 available/missing，不把其描述为逐股完整。`RefreshProgress` 增加 operation/component 身份；日线逐代码报告，adj_factor、qfq_status 每个目标日各最多报告一次。Redis `total/processed/completed` 仅按冻结的唯一执行操作统计，95% 组件成功不会留下未完成进度。整体 `freshness` 按四组件门槛计算，job/lease recovery 以 `freshness == "FRESH"` 判定 SUCCEEDED。
- worker 在 `ingestion_scope` 持有同一 PG advisory lock 后按冻结组件复核。先对 qfq/status 组合与 adj_factor 分别做一次目标日采集；完成 qfq/status 事务后重新读 trusted suspension，再只拉剩余日线缺口。四类数据帧在覆盖计算与写入前均须按目标日验证必需列、代码域与唯一键。qfq `REQUIRED_QFQ` 里的非空值必须为有限数；合法暖机缺值行不计入覆盖且不写入，其他行需十个字段全有效。交易状态的 `is_suspended`/`is_st` 必须是真实 bool，`market_board` 等必需状态字段非空，`up_limit`/`down_limit` 可空但若存在必须为有限正数，两者都存在时 `up_limit >= down_limit`。全市场回包先限缩到冻结量化代码集，目标批次回包带有非目标代码则拒绝。任一 qfq/status 帧校验或 95% 覆盖门槛失败时，两表和相应 ingest_state 均不写；两帧均有效后在同一事务写入并 commit。每个提交组件后重新核验；可恢复 SQL/写入错误必须先 rollback 再检查其他组件，`IngestSessionLost`/`IngestOwnershipLost` 直接终止且不重连。
- 日线/adj_factor 复用现有 6000 行截断和 100 代码 fallback；qfq 技术因子新增专用采集 fallback：全市场 `get_full_market_technical_factor_df(trade_date)` 返回行数达到 6000 时，按最多 100 代码调用 `get_full_market_technical_factor_df(trade_date, ts_codes=batch)`，批次间隔 0.2 秒，批次仍到 6000 行视为截断失败。现有 Tushare 与 BaseProvider 签名确认支持 `ts_codes`；分批结果除前述目标日/唯一键/值域校验外，还必须全部属于本批请求代码。不得复用 `_batched_pull` 的 adj_factor endpoint 处理 qfq。
- adj_factor 帧验证列、目标日、代码集合、唯一键、有限正数值，再通过 `bulk_upsert_factor` 写现有表。源空/截断或验证失败不写入。
- 任务进度仅按 frozen 执行操作单位在 DB 中复核后更新；失败或源截断不记该操作完成，重跑从仍未完成的操作开始。qfq/status 一对若未达到两项各自覆盖门槛则整对不写、同一 qfq_status 操作不完成；达到门槛且两表同事务 commit 后操作完成。`RefreshSpec` 保留带 component 的事实缺项及去重后的执行操作单位；Worker child 与主 Worker 都按组件阈值 freshness 判成功。scheduled/auto job 在刷新预算内失败且仍可重试时按现有状态转 RETRY_WAIT；manual/retry job 若没有任何执行操作完成则按 Redis 既有契约为 FAILED，有部分操作已落库但仍未达标则为 PARTIAL。量化分析任务仍独立按其 `wait_until` 重试并最终提交零策略 partial；下次 attempt 会重新核验并申请/复用补齐，不把刷新 job FAILED 当作量化就绪。操作只更新现有行情事实表，不建表。

#### 4.2.2 三方依赖能力评估

- 不增加第三方依赖。复用项目现有 Pandas/Provider/psycopg/Redis/Dramatiq。
- `CN_STOCK_QUANT_INPUTS` 在 `collect_refresh` 的内部资源分支显式构造 `TushareProvider`，不调用 `_providers_from_env()` 的 AKShare 主源；不配置/不使用 AKShare fallback。Tushare `__init__` 仍从现有 `TUSHARE_TOKEN` 装配，缺 token、未连接和接口返回空时按缺项失败关闭。测试设 `LIVEPROFIT_DATA_SOURCE=akshare` 并注入假工厂，验证私有资源只调用 Tushare，而公开资源仍走既有 provider/fallback 对。
- Tushare 的股票日线、`adj_factor`、stk_factor_pro 与 suspend_d 调用已有实现。日线与复权因子复用 `frames.py` 的已有截断处理；stk_factor_pro 单独新增 100 代码 `ts_codes` 降级，因为 `frames._batched_pull(kind="factor")` 实际调用 adj_factor endpoint。测试通过 fake provider 验证 qfq 全市场截断后调用分批技术因子接口、批次/总覆盖不足时不写库，以及目标日和完整键。

#### 4.2.3 风险与验证方式

- 源端合法暖机缺 qfq 行由既有 95% 阈值容纳并从写入帧剔除；覆盖低于阈值、重复键、错目标日、必需列缺失、非目标批次代码、Infinity qfq、非法状态布尔值/限价及 NaN/非正 adj_factor 不通过，且 qfq/status 失败不会部分写入。
- 测试至少验证：四组件指标分别达标才 FRESH（构造两个组件各缺不相交的 5% 时仍按各组件口径通过，不伪称交集 95%）；刷新 catalog 与 `AllMarketUniverseBuilder` 返回集合完全相同；95% 组件在 5% 容许缺项仍终态 SUCCEEDED，qfq/status 合并后的单个日级操作只计一次；qfq/status 低于阈值且无其他操作完成时，scheduled/auto 在预算允许时进 RETRY_WAIT，manual/retry 符合既有 FAILED/PARTIAL 规则，量化 analysis task 仍按自己的 deadline 重试并产出 partial；qfq 与状态帧分别覆盖错日期、缺列、非目标/非法代码、重复键和非法值；任一帧失败或单项覆盖不足时 qfq/status 两表及 ingest_state 均无写入，两项均有效才同事务提交；公开 CN_STOCK_DAILY 与内部 CN_STOCK_QUANT_INPUTS 互相清除共享日线依赖的 Redis coverage 缓存；并发公开 CN_STOCK_DAILY 与量化作业共享 advisory lock 并重核库内结果；组件已提交后另一组件失败，rollback 后复核/重试不重复覆盖有效数据；失去锁连接立即终止。
- 当 `LIVEPROFIT_DATA_SOURCE=akshare` 时，内部资源仍只路由 Tushare；Tushare 未连接/unsupported/空回包必须失败关闭且不写库，公开行情资源仍维持既有 provider 主源与 fallback 行为。

#### 4.2.4 文件变更清单

新建：`db/instrument/dao/quant_inputs.py`（量化目标日四组件事实读取，共用 coverage SQL）。修改：`backend/modules/market_data/application/refresh_policy.py`、`refresh_service.py`、`backend/modules/market_data/infrastructure/refresh_repository.py`、`backend/api/schemas/market_refresh.py`、`backend/api/routers/market_refresh.py`、`backend/modules/daily_research/application/quant_pipeline.py`、`backend/modules/analysis/infrastructure/quant_execution_market_data.py`、`backend/workers/analysis_executor.py`、`backend/workers/wiring.py`、`backend/workers/market_refresh.py`、`db/instrument/dao/instrument.py`、`db/instrument/ingest/refresh.py`、`frames.py`、`stock_factors.py`、`guard.py` 及对应 API contract、repo/ingest/unit/integration/Provider 测试。复用既有 `db/instrument/dao/adj_factor.py` 与 `factor_daily.py`，不改 DDL。Redis store 仍以通用 `units` 长度维护进度，不需改动其生命周期协议。

### 4.3 量化任务前置条件

#### 4.3.1 模块设计

- 组合根在 `backend/workers/wiring.py` 用既有 Redis、market DSN、settings 和 `publish_refresh` 构造 `RefreshService`，把私有 `ensure_quant_inputs` 回调注入 `AnalysisExecutor`；不在 quant pipeline 内创建 provider、Worker 或连接锁。
- 普通 quant task 必须先严格解析 aware `scheduled_at` 与 `wait_until`，再解析 scheduled news dependency；随后调用预检回调完成目标日解析与内部资源准入，然后才打开量化行情 `REPEATABLE READ` 只读快照。回调返回 ready 时进入快照；pending 在有效 `wait_until` 前抛 `RetryableAnalysisError(code="QUANT_INPUTS_NOT_READY")`，不运行策略、不占线程轮询。scheduled_at 缺失、坏格式或 naive 时不使用当前时间兜底，也不调用市场预检；有效 deadline 前以 readiness code 走 task retry，到期由 pipeline 保存 `QUANT_METADATA_INVALID` 零策略 partial。`QUANT_INPUTS_NOT_READY`、`QUANT_INPUT_TARGET_MISMATCH`、`QUANT_UNIVERSE_CHANGED` 都纳入 `_task_readiness_deadline` 的 readiness 白名单，在 wait_until 前可越过普通 max-retry 次数；其他错误仍遵守普通重试上限。
- 若预检 pending 且已到/超过 `wait_until`，由 `run_daily_quant` 直接构造 `partial/QUANT_INPUTS_NOT_READY` 零策略报告并调用现有 `_complete_quant` 提交；该分支在打开行情快照、执行 `DataReadinessGate` 或调用策略前返回。此时不可把预检异常交给 `AnalysisExecutor._fail_or_retry` 后再期待其生成报告，因为该异常路径只会 fail/retry。报告保留请求交易日、事件 cutoff 和新闻依赖状态；有效目标日未知时将市场日置空并写明不可就绪原因。
- ready 后打开量化行情快照，在运行任何策略前以同一 `REPEATABLE READ` 连接查询 `AllMarketUniverseBuilder.list_active_cn_stocks` 并计算排序代码集 SHA-256，与预检返回的 `universe_digest` 比较。若两次查询间股票新增/退市等使代码集改变，立即 rollback 并按 `QUANT_UNIVERSE_CHANGED` 重试，不跑策略；新 attempt 重新冻结最新目录并确保其覆盖。目录一致后将已核对股票集复用作报告候选过滤，避免稍后第三次读取。
- 目录一致后，`DataReadinessGate` 检查四类水位，`market_as_of_trade_date` 必须等于 `effective_trade_date` 才能扫描。刷新状态虽为 ready、但共同最大水位仍落后时，截止前重试；到截止时间由 pipeline 走现有零策略 partial 报告提交路径，不以旧日数据冒充目标日完成。刷新 worker 的 Redis retry 与 dispatcher 恢复继续独立运行。
- `quant_news_refresh` 必须沿用父报告的 `market_as_of_trade_date` 与候选量化分，不调用新资源、不因新水位变动改变旧批次市场快照。
- `AnalysisExecutor` 注入并传递 `quant_preflight: Callable[..., dict[str, Any]]`；回调严格接收 workflow 中冻结且 aware 的 `scheduled_at`，market refresh 的预算与 cooldown 仍使用当前 clock。`run_daily_quant(..., quant_preflight=...)` 在打开行情快照/调用 `DataReadinessGate` 前调用回调，接收 `{state, decision, effective_trade_date, universe_digest}`。普通 quant 将有效日期作为实际行情 `requested`，同时保留任务原 `scheduled_trade_date` 供报告记录；刷新状态 ready、代码集 hash 一致后，`DataReadinessGate` 检查四类水位，`market_as_of_trade_date` 必须等于 `effective_trade_date` 才能扫描。这样发布缓冲前手动启动、周末和节假日会先解析为最近已发布日；日历或 `scheduled_at` 不可用时不猜日期且不扫描。
- `quant_news_refresh` 在任何新行情预检前识别并绕过回调，使用父量化报告固定的 `market_as_of_trade_date`；只在该固定日期检查父批次快照可读性，不申请新行情、不因新水位变动改变候选集。

#### 4.3.2 三方依赖能力评估

- 不增加 AI/LLM 调用，也不依赖新的第三方服务。Dramatiq refresh actor 和 analysis task 分属现有队列；analysis Worker 提交一次准入后释放执行线程，由 task lease/retry 保证幂等重入。

#### 4.3.3 风险与验证方式

- 测试预检收到原任务冻结的 `scheduled_at`；21:00 当日、盘前、15:30–20:00（16:00 pipeline 报告按实际 effective date 标记）、周末/节假日和日历不可用均有 fixture，并验证跨 20:00 重试不漂移已选择的交易日；scheduled_at 缺失、坏格式、naive 均 fail-closed、不退回当前时间，并覆盖 deadline 前 retry 与到期 partial；wait_until 缺失、坏格式、naive 均在 pipeline 层验证为 fail-closed，且不打开行情快照或调用预检。测试在创建 `REPEATABLE READ` 快照之前调用预检；刷新 pending 在 deadline 前走 retry（含错误码白名单越过普通 max retry），deadline 到/过后不打开行情快照、不调用策略并通过 `_complete_quant` 保存含新闻依赖状态的零策略 partial。ready 的 `effective_trade_date` 实际进入 DataReadinessGate 与策略快照，DataReadiness 共同水位落后时不调用策略。
- 测试预检返回的 universe digest 与刷新目标一致；预检后目录增删股票时，同一量化快照在策略调用前检测 hash 不符、回滚并重试，目录稳定且水位相等才运行策略，并复用该股票集作候选过滤。
- 普通 scheduled/manual 缺日线、qfq、adj_factor 或状态任一项均会申请刷新并在首次 attempt 不调用策略；覆盖达标且目标日共同水位相等后第二次 attempt 才运行策略。
- 测试刷新准入重复调用复用 job ID；刷新禁用/Redis异常不绕过准入；等待到期后仍输出现有 partial 形态。
- 测试 `quant_news_refresh` 不发起刷新并沿用父任务行情日、候选基分。

#### 4.3.4 文件变更清单

新建：无。修改：`backend/modules/daily_research/application/quant_pipeline.py`、`backend/modules/analysis/application/task_lifecycle.py`、`backend/workers/analysis_executor.py`、`backend/workers/wiring.py`、`backend/tests/unit/daily_research/test_readiness_retry.py` 和每日研究/worker 单测。

### 4.4 API 隔离与验证

#### 4.4.1 模块设计

- 刷新 API 在 `backend/api/schemas/market_refresh.py` 定义原名六值 `Resource`；策略 enum 以 `MarketResource` 导入，status/request/decision/group/job DTO 都使用 schema 类型。公开 job 查询对内置资源执行 404 防护。
- OpenAPI 回归断言 component `Resource` 仍恰含原六个枚举值；无需前端 UI 或 generated client 变化。

#### 4.4.2 三方依赖能力评估

- 复用现有 FastAPI/Pydantic/OpenAPI 代码生成链路，无新增依赖。

#### 4.4.3 风险与验证方式

- 运行每日研究量化单测、市场补齐 policy/repository/service/API contract、qfq fallback 与采集锁测试；运行隔离市场 refresh integration 测试。按 `AGENTS.md` 逐文件 Code Review。
- `git diff --check`、OpenAPI 生成后比较公开 schema，以及 `python -m pytest` 的对应隔离测试。

#### 4.4.4 文件变更清单

新建：新增针对内部资源覆盖、采集和准入的测试文件（实现拆解时定名）。修改：`backend/api/schemas/market_refresh.py`、`backend/api/routers/market_refresh.py`、相应 contract/integration/unit tests；OpenAPI 与 frontend/generated 不应变化。

## 五、已确认决策 / 待确认问题

### 已确认决策

- 用户确认：量化策略启动前应先保证量化依赖齐备。目标数据包括股票日线、qfq 技术因子、复权因子、交易状态。
- 沿用现有市场自动补齐准入、队列、Worker、Redis 幂等状态和 PG advisory lock；不新增行情事实表、任务表或队列。
- 内部量化输入补齐不扩展市场页面公开资源，也不让市场页自动触发全市场 qfq 拉取。

### 待确认问题

- 无。本方案是在用户“先拉取今天的数据，再量化”实施授权范围内，对新增内部资源与现有市场刷新任务机制作出的具体实现推荐。
