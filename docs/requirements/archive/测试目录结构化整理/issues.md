# 已知问题

## 量化执行输入与门禁契约（10 项 Python 失败）

- 四个市场缺口保护场景：test_market_wide_gap_runs_only_holding_protection_and_blocks_buy 的四个参数组合均遇 NoneType.items。
- 管理运行时空目标：test_null_target_planner_rejects_without_exception_or_fabricated_rr 缺 instrument_rule。
- 规划器五场景：未知行业的保护卖出、odd lots 全卖、部分卖 lot、两个卖出来源预留、准入拒 BUY 保留保护退出；当前部分返回缺 shares，或状态变成 SELLABLE_QUANTITY_UNKNOWN 而原断言为 ELIGIBLE。
- **根因组**：存量测试输入与正在开发的执行/可卖量/品种门禁契约不一致，具体业务根因待所属任务系统治理。不能通过放宽断言或补凑字段替代治理。
- **影响／门禁**：相关业务回归未全绿，不放行量化整体或生产执行。迁移前原件同 10 项失败，证据在 result 的回归与基线日志。

## 提示词及旧调度器开关契约（2 项 Python 失败）

- test_default_prompt_keys_cover_topology_llm_nodes：原期待 23，当前注册表实际 26。
- test_start_registers_jobs_and_startup_check_thread：原期待两个旧任务，当前 EVENT_STUDY_LEGACY_DAILY_ENABLED 默认 false，实际不注册。
- **影响／门禁**：由对应功能任务统一确认注册表和旧维护调度器的预期；两个原件基线都重现。当前任务不修改业务开关或提示词数量。

## 前端行情请求契约（1 项失败）

- QuantExecutionPanel K 线场景原断言五个参数，实际新增第六个 cache_only。
- **根因组／影响**：组件的行情消费契约与存量测试不一致；原路径、原件及原 helper 基线同样失败。行情功能所属任务核对缓存行为后更新正式回归，不由目录迁移决定。

## 并发未提交工作与提交边界

- 开始前已有大量业务代码、测试、AGENTS 和文档未提交改动；本次保留其当时内容，未覆盖或还原业务文件。
- 移动的测试含原有修改与未跟踪文件，依赖未提交业务实现，不能安全生成只含本任务的完整 commit。未暂存，等待既有业务变更边界整理；这不改变目录实施和软件验收结果。

全部完整证据以 [result.md](result.md) 为准；13 项失败为 12 Python + 1 frontend，不累计重复运行。
