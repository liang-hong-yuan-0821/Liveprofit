# 截止型 readiness 错误码必须进入任务重试白名单

一句话结论：`RetryableAnalysisError` 标记错误可重试，不代表 analysis task 会越过普通最大尝试次数；截止型 readiness 错误还必须加入 `_task_readiness_deadline` 的错误码白名单。

## 表象

量化行情仍在补齐时，`quant_pipeline.py` 已经抛出可重试错误，但任务在默认 `max_retry_attempts=3` 后提前变为 `FAILED`，早于 workflow 的 `wait_until`。

## 根因

`TaskService.fail_or_retry` 只有在普通尝试次数未耗尽，或 `_should_retry_task_readiness(task, now, error.code)` 命中截止型 readiness 条件时才会继续重试。`_task_readiness_deadline` 按 kind 和错误码显式允许有限集合；只在 pipeline 添加新错误码而没有同步此集合，会静默退回普通重试上限。

## 正确姿势

- 新增 deadline-bounded readiness 错误时，同步扩展 `_task_readiness_deadline` 中相应任务类型的白名单。
- 为每个错误码验证 deadline 前可越过最大重试次数、deadline 后不再按 readiness 特例重试；其他普通错误仍遵守原上限。
- 对到期需要产出 partial 的流程，由 pipeline 在 deadline 分支直接保存报告；不要期待 task lifecycle 的异常重试路径生成业务报告。
- 独立量化执行没有 `daily_research.wait_until`；其 `execution_snapshot` 的 `QUANT_INPUTS_NOT_READY` 从任务 `created_at` 起最多重试两小时。没有持仓时在补采未认证前停下；有持仓时执行服务必须显式 `protection_only`，即使 PostgreSQL 共同水位暂时可用，也禁止新开仓。

验证：`backend/tests/unit/daily_research/test_readiness_retry.py` 覆盖量化输入未就绪、目标日不一致和股票池变化三个错误码。
