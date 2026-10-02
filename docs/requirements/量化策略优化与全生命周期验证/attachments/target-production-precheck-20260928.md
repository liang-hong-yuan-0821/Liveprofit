# 可信目标生产接入预检查（2026-09-28）

仅静态代码核对，非实施验收；先完成联合消费剩余回归。

| 接口 | 已核实事实 | 下一步必须解决 |
|---|---|---|
| PortfolioTargetIntent / FamilyBatchService.project | 批次必须包含全部冻结版本，cash_only也需明确intent；valuation_date必须等于每个intent.evaluation_as_of，decision_date必须为当前CN日 | 生产器提供完整清单与明确无交易结论，不允许省略成员 |
| stock_medium_momentum / stock_short_reversion | 目标函数在非调仓日、基准不达标、数据不足或无候选等情况下均可能返回None；历史bar结尾检查使用decision_date | 先区分数据不足、未调仓及真实无候选；不能把所有None包装成清仓。生产决策日和行情截至日必须明确区分，覆盖跨日及节假日 |
| QuantStrategyVersion | 持久化source_hash、template_id/params/renderer和lifecycle_policy_version_id，未见组合研究候选trial定义的专属绑定 | 明确冻结候选定义、参数、目标算法及持仓政策到生产版本的对应，不从资格family字符串推导参数 |
| ScanManifestService.register | 只接受已有PROJECTED CN_STOCK批次，冻结任务清单及outbox；active owner必须在成员中 | 目标生成/投影/注册由一个事务入口组合，生产接入失败整体回滚；ETF路径不能假借CN_STOCK入口 |
| StrategyAdmissionService | 只支持保守限制写入，正资格预留给验证产出器 | 保持未验证不新增风险，禁止用fixture ADVISORY填充真实数据库来开通生产 |

推荐顺序：完成当前验收 → 固定日期/无目标语义与版本绑定 → 接受可信数据的目标生产及原子注册 → 真实验证资格产出/每日调度。当前预检查不改变既有运行行为、不新增自动调度。
