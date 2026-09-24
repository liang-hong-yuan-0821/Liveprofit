# 问题

## 设计阶段风险

- [x] 复合资源覆盖查询复用统一 `list_active_cn_stocks` 活跃目录，未知 lifecycle 不会通过 active predicate；停牌豁免只接受有效且可信的 Tushare 状态事实。
- [x] 内部资源参与活动作业租约恢复；Dispatcher 的公开资源自动缺口循环仍只遍历六项公开资源，quant tick 只恢复已有私有作业、不自动新建。
- [x] qfq 与状态的 95% 覆盖阈值沿用 `collect_stock_quant_day`；adj_factor 也按同一量化活动股票目录的 95% 覆盖率判定，并要求每行 adj_factor 为正有限数值。

## R1 findings 修复记录

- [x] R1-01（D1/D8）：更正 qfq 能力现状，新增 stk_factor_pro 100 代码分批降级与完整帧校验；不再把现有 `_batched_pull(kind="factor")` 误称为 qfq 采集。
- [x] R1-02（D1/D2/D7）：将 `AllMarketUniverseBuilder` 的活跃 CN 股票 predicate 收敛到 DAO，共用于量化执行与量化资源覆盖分母。
- [x] R1-03（D2/D6/D7/D10）：规定日线与 qfq、adj_factor、交易状态的组件级阈值及缺项序列化；明确为分别达标，不声称组件交集也达 95%。
- [x] R1-04（D2/D3/D6/D7）：预检按现有日历与 5 小时发布缓冲返回最新已发布交易日；返回值在数据快照创建前固定用于共同水位验证，明确盘中/发布缓冲、周末和日历故障行为。
- [x] R1-05（D1/D2/D6/D7）：保留 qfq 与交易状态的现有同事务采集边界，作为组合组件复核与重试，不再描述为独立提交。
- [x] R1-06（D3/D5/D9）：明确公开 API 原名六值 `Resource` 与内部 `MarketResource` 隔离，覆盖请求、状态、决策、分组、作业 DTO、job 查询和 OpenAPI 回归。
- [x] R1-07（D6/D7）：明确可恢复数据库错误先 rollback；会话/锁丢失立即终止；补充事务复核测试。
- [x] R2-01（D1/D2/D6/D7/D10）：将 qfq 与状态帧的目标日、必需列、目标代码域、唯一键、有限数值/真实布尔值校验列为写入前契约；合法暖机缺值行只按缺项计数并从写入剔除，校验或覆盖任一失败时 qfq/status 两表及 ingest_state 均不写；补充各失效输入与事务原子性测试。
- [x] R1 重写稿 R1-01（D2/D6/D9）：明确 `wait_until` 前预检 pending 抛可重试错误，截止及之后 pipeline 直接 `_complete_quant` 提交零策略 partial；不期待 `_fail_or_retry` 构造报告。
- [x] R1 重写稿 R1-02（D6）：schedule 时间必须存在、ISO 可解析且 aware；异常不回退系统当前时刻。有效 deadline 到期可交付最小 partial，deadline 自身损坏时 fail-closed。
- [x] R1 重写稿 R1-03（D8）：私有量化资源显式只用 `TushareProvider`，不受 AKShare 主源环境开关影响、不使用 AKShare fallback；unsupported/空结果不写入。
- [x] R1 重写稿 R1-04（D4/D6/D7/D10）：把 qfq/status 的逐组件事实缺项与刷新操作单位拆开，整市场原子组在每个目标日仅计一个 `qfq_status` 执行单位；明确其两组件 coverage、Redis 进度及低覆盖 auto/manual 终态行为。
- [x] R1 重写稿 R1-05（D2）：冻结量化股票目录的排序 hash 并在量化快照内、策略扫描前重算比对；目录变化时回滚、重试，不扫描。

## 实施阶段守门项

- [x] 组件级门槛、冻结目标规格和执行操作分别由真实 SQL 覆盖测试、真实 PG 采集和 worker/Redis 单位测试回归验证；执行期水位与冻结 universe 在策略前 fail-closed。
- [x] 内部资源只能由量化准入触发；dispatcher 仅恢复活动 job，不会调度新的内部资源 job。
- [x] R2 delta 通过：受影响维度 D2=10、D4=10、D6=10、D8=10、D9=10；D1=10、D3=8、D5=8 沿用；未发现新 blocker/major/minor。已确认 deadline partial、aware 时间、Tushare 隔离、qfq/status 单一操作单位和 universe digest 闭环。
