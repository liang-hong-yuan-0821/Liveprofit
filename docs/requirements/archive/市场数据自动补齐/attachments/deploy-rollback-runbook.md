# 部署顺序与回滚运行手册（T9-4 验收证据）

> 验证日期：2026-10-02。本任务无数据库迁移；部署仅涉及进程/容器替换。

## 上线顺序（方案 §4.6.1-7）

1. **新 Worker 与 Dispatcher 先行**：先启动 `liveprofit-market-worker` 与 `liveprofit-dispatcher`（新代码接入公共采集锁与有界投递），旧采集进程（旧 `market_ingest`/旧 Dispatcher）先自然结束再切换——旧版未接公共锁，不能与新 Worker 并行写库。
2. **API 次之**：启动新 API（刷新三端点 + 行情查询语义）。
3. **前端最后**：前端只消费 API，接口兼容后任意时刻切换。
4. 无数据库迁移，无需停机窗口；`start_platform` 已按 api → worker → market-worker → dispatcher 顺序拉起，并对每个 daemon 做启动存活检查（`kill -0` + API 30 秒就绪等待），启动失败返回非零不报告成功。

## 回滚步骤（方案 §4.6.1-7）

1. **停止新准入与 Worker**：`./run.sh stop-platform`（PID 文件 + 按工作区路径整树终止）。已实测：停止后无存活 `liveprofit-*` 进程（无市场采集孤儿子进程），API/Worker 退出不影响已提交行情。
2. **保留已提交行情**：行情与停牌事实均在 PG 既有表，不随进程/Redis 状态删除。已实测：停止后 2026-10-01 的 4 行指数日线（含刚自动补齐的 3 行美指）仍在。
3. **无需 DDL**：本任务零迁移、零表结构变更，回滚不涉及 schema。
4. **定向清理 Redis 状态**：仅按业务前缀 `liveprofit:market-refresh:*` 清理（57 个键，实测与 6 个 `dramatiq*` 队列键零重叠）；**禁止 FLUSHDB / 清理其他前缀**——AI 队列（`dramatiq:*`）与其他业务键不受影响。Redis 状态可重建（PG 是唯一事实源），丢失后重新核验即可恢复准入。
5. **回退旧代码**：再部署旧版本进程；旧采集入口不依赖新 Redis 状态。

## 开关行为（T9-3，测试证据）

- 总开关 `MARKET_REFRESH_ENABLED=false`：停止准入（策略单测 + 契约测试）。
- 仅 `MARKET_REFRESH_AUTO_ENABLED=false`：页面 auto 与 Dispatcher 定时均不入队，手动仍按约束准入（T4 双 eligibility 测试）。
- Redis 不可用：auto/manual 均不可准入；恢复后先核验 PG（T4 集成测试）。
- 工作进程离线：只保留一份已准入任务并显示离线，不持续重建队列（T4 离线一小时零投递测试）。
