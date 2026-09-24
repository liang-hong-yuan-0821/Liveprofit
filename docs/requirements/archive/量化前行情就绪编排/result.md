# 结果

# 结果

量化策略运行前会通过内部 `CN_STOCK_QUANT_INPUTS` 资源，复用市场补齐现有准入、Redis 作业状态、market-data Worker、Tushare 采集和 PostgreSQL advisory lock。日线、qfq、复权因子、交易状态分别按各自门槛核验；补齐任务冻结股票目录和组件缺项，只有四类输入就绪且量化快照中的日期与股票目录 hash 仍一致时才运行策略。公开行情页面仍只暴露原六种资源，未新增 PostgreSQL 表。

量化预检 pending 时，任务在 workflow 截止时间前按 readiness 专用错误码重试，截止后由 pipeline 保存零策略 partial。无效或缺失的调度时间不会退回当前时刻或猜测行情日；`quant_news_refresh` 继续复用父量化扫描，不触发行情补齐。手动运行的最新行情标志按实际预检日期写入报告。

验证结果：

- 量化与市场刷新单元测试：147 passed。
- DB ingestion 测试：28 passed。
- market refresh 集成与 API contract 测试：57 passed。
- 最终 pipeline 边界与 readiness lifecycle 定向测试：30 passed。
- Ruff F 检查、Python 编译检查及 `git diff --check` 通过；未执行依赖真实 LLM 的集成测试。

代码审查两轮内完成，发现的 readiness 截止重试、实际行情日标志、partial 新闻依赖、共享 coverage 缓存失效以及测试/方案对齐问题均已修正。
