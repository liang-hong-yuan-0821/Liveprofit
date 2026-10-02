# 组合政策执行门禁的提前返回旁路

未接入真实生命周期的组合政策必须在任何确认BUY早退或复用前核清所有身份来源；单看任务快照会漏掉真实版本。

## 表象（2026-09-28）

初版只在`LifecycleOrderService._initialize_lifecycle_from_first_fill`的旧模板分派前检查`portfolio_trial`。独立审查连续找到活动生命周期复用、无来源信号BUY、订单绑定已关闭/异品种生命周期，以及信号/任务快照只有其中一个带策略版本时的旁路；这些路径均可在`_apply_delta`已改变仓位后提前返回，随后`confirm_fill`提交。`PositionLifecycleManager.process_day`已有日事实重放也会在政策检查前返回。

## 根因

真实身份散落在订单`lifecycle_id`、同品种活动生命周期、`QuantExecutionSignal.strategy_version_id`和任务冻结`strategy.version_id`。它们各自允许为空，但空值不能证明其他来源没有组合政策。把政策检查埋在旧模板分支末端，无法覆盖公共入口和早退路径。

## 正确姿势与验证

确认BUY先核活动/订单绑定生命周期，再独立核信号和任务快照的策略版本及政策；两边有版本时要求一致，任一路径指向`portfolio_trial`即拒绝。读取版本在组合锁后取共享行锁并刷新ORM；异常由调用方事务回滚。逐日持仓包括同日重放均检查当前政策。隔离PG以active/closed/异品种、无信号、仅signal版本、仅快照版本、错配版本构造边界并断言订单、成交事件、仓位、现金回滚。保护SELL不调用旧模板评价器，保持账本路径；其生命周期结清以及correct/void后的状态重算仍需另行验收。

代码：`backend/modules/quant_strategy/application/lifecycle_service.py`、`position_lifecycle_manager.py`。验收：`test_lifecycle_service.py` 49 passed，重整后独立code review PASS；初版R1/R2均未通过的历史记录保留在任务log。

## 目标身份与旧建单路径（2026-09-28）

- **表象**：`PortfolioTrialResult`持有试验ID/hash，内部`PortfolioTargetIntent`原先没有；`FamilyBatchService`可接收无身份目标。`EntryBatchService`只消费扫描BUY信号，`order_materialization.py`对旧生命周期默认按0.50裁剪首仓，无法代表组合目标腿的权重。
- **根因**：研究结果、冻结批次与旧扫描信号建单是三套不同事实载体；只核版本ID或政策ID，不能证明某笔目标属于预注册试验。缺绑定时仅核版本政策还会漏掉目标自己冒称组合政策的情况。
- **正确姿势**：明确目标携带trial ID/hash；批次同时核目标自身、不可变绑定、版本与政策。无绑定时版本政策和目标政策任一属于组合政策都拒绝；旧消费者在写单前重审并阻断组合目标。独立正目标生产与专用目标到订单事实链建立后才允许真实成交。定向纯/隔离PG 118项、旧消费者13项及独立审查PASS。
