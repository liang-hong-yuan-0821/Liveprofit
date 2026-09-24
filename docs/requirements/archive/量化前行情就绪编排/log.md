# 日志

## 2026-09-23

- 根据用户授权与澄清结果，核对“市场数据自动补齐”现有准入、Redis 状态、market-data Worker 和 PG advisory lock。
- 确认现有 `CN_STOCK_DAILY` 只覆盖股票日线/可信停牌；量化还依赖 qfq、`adj_factor` 和交易状态，直接复用现有日线完成状态会漏检。
- 完成内部量化资源方案初稿，待评审。
- R1 全量评审为 FAIL（均分 6.5）；已修订目标日解析、策略股票池一致性、组件独立覆盖语义、qfq 截断分批、qfq/状态原子提交、API 枚举隔离和事务回滚/失锁路径。
- 补充预检必须早于量化 REPEATABLE READ 快照；只有有效交易日就绪且 DataReadiness 共同水位等于该日时才扫描。开始 R2 delta 核验。
- R2 delta 发现新的 major：现有 `collect_stock_quant_day` 与 DAO 清洗未保证 qfq/status 回包目标日、唯一键、代码范围和数值有效性。按评审机制终止该轮，补入两帧独立校验、暖机缺值行排除、任一失败整组不落库与针对性测试，并重启 R1 全量评审。
- R2 结果：FAIL；受影响维度 D1=7、D2=7、D6=7、D7=7、D10=7（D4=8 沿用，其余未涉及维度 ≥10），均分仅作参考；新 major 已落实到方案，现开始重写后的 R1 全量审查。
- 重写稿 R1 全量评审仍 FAIL（D1=10、D2=6、D3=8、D4=6、D5=8、D6=6、D7=6、D8=6、D9=6、D10=6，均分 6.8）。修订五项 major：明确 deadline 前 retry、到期由 pipeline 直接 `_complete_quant` 零策略 partial；校验 schedule/wait 时间，缺失/naive 不漂移；内部私有采集固定路由 Tushare；用一项日级 `qfq_status` 执行操作承载两事实组件、冻结组件缺项与 Redis 进度分离；将预检代码集 digest 放入量化 snapshot 并在任何策略扫描前比较。同步澄清低覆盖时 Redis job 按现有 auto/manual 终态规则，analysis task deadline 独立重试。
- 修订稿进入 R2 delta 核验，逐项确认上述 R1 findings 落地及修订点与周边数据流无矛盾。
- R2 delta PASS：D1=10、D2=10、D3=8、D4=10、D5=8、D6=10、D7=10、D8=10、D9=10、D10=10，均分 9.6；五项 R1 findings 与低覆盖 job 状态约定均闭环，无新 blocker/major/minor。
- 根据用户既有实施授权生成 7 项 tasks；T1 开始统一活跃 CN 股票目录查询，为覆盖 repo 和策略扫描提供同一来源。
- T1 完成：新增 `instrument.list_active_cn_stocks` 共用过滤与排序，`AllMarketUniverseBuilder` 委托 DAO；`backend/tests/unit/analysis/test_quant_stock_universe.py` 2 项通过。进入 T2。
- T2 完成：策略层新增内部 `CN_STOCK_QUANT_INPUTS` 和 aware anchor 私有准入；API schema 保留独立六值 Resource，内部 refresh request 422、job read 404，status 六组不变。刷新 policy/service 与股票目录共 38 项单测通过，API contract 隔离用例通过。进入 T3。
- T3 完成：新增统一事实 DAO，以每个组件独立覆盖门槛生成 coverage 和冻结目标；日线逐股、复权因子按日、qfq/status 按日组合三种操作分开建模。95% 容许缺项的组操作仍按一个操作统计，日线缺口额外强制一次 qfq/status 刷新以确认停牌。repo 单测 6 项、真实 market PG coverage 测试通过。
- T4 完成：内部资源固定使用 Tushare；qfq 截断时用 100 代码批次调用 stk_factor_pro，批次越界/仍截断即失败；日线/adj_factor 批次拒绝非请求代码。目标日、必需列、唯一键、数值、交易状态和限价校验均在落库前执行。qfq/status 两帧及两条 ingest_state 同事务提交；任一无效不写入。采集与单元回归 36 项通过，独立 PostgreSQL 的双表成功提交、输入失败零写入和按日操作进度 3 项通过。
- T5 完成：worker child 按 frozen operation spec 执行，进度元数据含 operation/component；收盘数据先更新状态，随后只补仍缺日线。刷新 SUCCEEDED 与过期 lease 恢复统一读取组件 `freshness == FRESH`；scheduler 只恢复内部活动 job，不自动创建内部 job。quant group Redis lifecycle、公共刷新 worker 与覆盖 SQL 集成通过。
- T6 完成：日常量化 Worker 装配注入 shared RefreshService；普通定时/手动量化先用 `scheduled_at` 进行 aware 日期锚定并申请补齐，只有内部覆盖就绪后才打开行情快照。共同水位必须等于预检目标，且 repeatable-read 内股票池 hash 与 frozen digest 一致才运行策略；截止前 pending 重试，截止时直接提交零策略 partial。`quant_news_refresh` 路径跳过新扫描与补齐。量化预检与执行器回归通过。
- 当前进入 T7：还需运行最终跨链路回归、检查 OpenAPI 与工作树变更、完成 subagent Code Review、更新知识文档并归档。

## 2026-09-24（收尾）

- Code Review 首轮发现 readiness 错误码未进入任务截止重试白名单；修复后确认 `QUANT_INPUTS_NOT_READY`、`QUANT_INPUT_TARGET_MISMATCH`、`QUANT_UNIVERSE_CHANGED` 会在 `wait_until` 前持续重试，不受普通最大尝试次数提前终止。
- 复核并修正 16:00 手动量化使用前一完整交易日时的 `used_latest_complete_market_data`、到期 partial 保存已知新闻依赖、日线/量化刷新共享 coverage 缓存双向失效；修正方案中错误码与 Worker 注入契约描述。
- Follow-up review 指出测试承诺和变更清单两处 minor 缺口。已扩展 pipeline 测试：16:00 报告字段、缺失/错误/naive `scheduled_at` 的截止前重试及到期 partial、缺失/错误/naive `wait_until` 的预检前拒绝；文件清单补入 lifecycle 与 readiness retry 测试。
- 最终 `test_quant_snapshot.py` + `test_readiness_retry.py`：30 passed；Ruff F 检查通过。此前量化/refresh 相关单测 147 passed、采集测试 28 passed、跨链路集成与 API 契约 57 passed；未运行真实 LLM 集成测试。
- 两轮 Code Review findings 均已修复；更新 `docs/knowledge/ai/市场层.md`，补充 ingestion shared coverage 缓存最佳实践和 readiness 截止重试踩坑，并同步 memory 总索引。
- T7 验收完成，任务记录归档。工作树包含多个并行功能的大量改动且共享文件交叉修改，为避免夹带其他修改未创建 commit。
