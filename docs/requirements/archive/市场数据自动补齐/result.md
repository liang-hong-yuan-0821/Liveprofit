# 阶段结果（已完成，2026-10-02 归档）

[技术方案](plan.md) 与 [10 项任务](tasks.md) 已实施完成，2026-10-02 恢复 T9/T10 收尾。目标：市场概览自动发现已到发布时间的行情缺口，后端按数据组补齐、去重与有限重试，页面保留旧数据并在入库后刷新；不新增 PG 表/字段/迁移。

## T9 运行验收证据（2026-10-02）

### 本地启停（T9-1，通过）

- 启动 `liveprofit-market-worker` 入口：broker 配置日志 `host=127.0.0.1 port=6379 db=0 password=已设置`（掩码），监听 `market-data` 队列，Redis `dramatiq:__heartbeats__` 出现 Worker 心跳；API `/health/live`、`/health/ready`（postgres ok / redis ok）通过。
- **实时自动补齐**：启动后 Dispatcher 检测 `US_INDEX_BARS` 目标日 2026-10-01 缺口（数据原止于 9-30）→ 入队 → market-worker 真实采集 → job `SUCCEEDED`（attempt 1，processed 3/3）→ 六组全部 `FRESH`（CN 指数 11/11、CN 因子 11/11、US 3/3、KR 1/1、个股 5557+11 豁免、板块 1031/1031；目标日 9-30/10-01）。
- `./run.sh stop-platform`：按 PID 文件停止三进程，`Get-CimInstance` 确认无存活 `liveprofit-*` 进程（无采集孤儿）；已提交行情保留（10-01 的 4 行指数日线仍在）。
- AI worker 未启动（Redis default 队列有 7 条存量 `analysis_task` 重试，触发真实 LLM 属任务范围外）；run.sh 中 worker 条目与其余三 daemon 共用同一 `start_daemon`（启动存活检查 + 失败返回非零），以代码核验佐证。
- `start_all` 不再无条件运行全量增量（[run.sh](../../../../run.sh) 注释与代码核验）；`ingest-market` 手动入口存在，公共锁由 T5 锁冲突零源调用测试佐证（未运行真实全量采集）。
- 日志密钥扫描：仅 `password=已设置` 掩码，无明文凭据。

### 容器整栈（T9-2，通过）

- API 镜像按 HEAD 构建成功（Dockerfile.api，9.17 GB）；worker 类镜像按 HEAD 重建（`verification-head`）；`docker compose --profile app config --quiet` 通过。
- 容器内验证：`liveprofit-api` 容器 `/health/ready` PG/Redis ok；`GET /api/v1/market-data/refresh-status` 返回六组，与宿主一致：CN 指数 11/11、CN 因子 11/11、US 3/3（10-01）、KR 1/1（10-01）、个股 5557+11 豁免=5568、板块 1031/1031，全部 FRESH，worker_online=true。市场连接解析到同一 PG（容器日志 host=postgres/redis，非 localhost）。
- **验收过程发现并修复的问题**：首次整栈复用 9-24 验证镜像（旧覆盖语义），其 Dispatcher 摘要将 `CN_STOCK_DAILY` 写为 expected 5652 / `CATALOG_INCOMPLETE`（旧「股票∪成分并集」语义 + 容器内 `db.instrument` 模块常量默认回退 127.0.0.1 不可达）。按 HEAD 重建镜像后整栈一致、错误摘要自愈。结论：镜像必须与代码同步重建，T9 容器验收的价值即在此。
- 日志密钥扫描：三个容器仅 `password=已设置` 掩码占位符，无明文凭据。
- 验收容器已 stop+rm，恢复初始状态（仅 PG/Redis infra 运行）。

### 开关与离线（T9-3，测试证据）

- 总开关/auto 开关/离线语义：T1 配置单测、T4 双 eligibility 与离线零投递、T7 契约测试覆盖（auto 耗尽手动仍可用；Redis 不可用两模式不可准入；离线 1 小时零投递；页面 auto/定时关闭时手动仍受约束）。

### 部署顺序与回滚（T9-4，通过）

- 运行手册：[attachments/deploy-rollback-runbook.md](attachments/deploy-rollback-runbook.md)。
- 实测：停止后数据保留、无孤儿进程；本任务零 DDL/迁移；Redis 定向前缀 `liveprofit:market-refresh:*` 57 键与 `dramatiq*` 6 键零重叠，前缀清理不影响 AI 队列。

## T10 测试回归证据（2026-10-02，HEAD=2871525f，新 tests/ 布局与 tests.run 运行器）

隔离核查：集成 fixture 模块级自建/自删 `liveprofit_market_test`（独占锁）；contract 用 `liveprofit_contract_test` + Redis DB11；data.ingest 用 `liveprofit_instrument_test`；均经 `assert_test_connections` 守卫，逐例 TRUNCATE 仅作用于隔离库。运行前已读具体 fixture，`--allow-db` 在核查后使用。

| 范围 | 命令 | 结果 |
|---|---|---|
| market_data 单元 | `python -m tests.run --module backend.market_data --level unit` | 88 passed |
| market_data 集成 | `--level integration --allow-db` | 53 passed |
| market_data 契约 | `--level contract --allow-db` | 35 passed |
| 采集单元 | `--module data.ingest --level unit` | 126 passed |
| 采集集成 | `--module data.ingest --level integration --allow-db` | 80 passed |
| Provider 单元 | `--module data.providers --level unit` | 165 passed |
| 平台配置/Broker 单元 | `--module backend.platform --level unit` | 61 passed |
| 前端市场组件 | `node frontend/test-runner.mjs vitest run tests/frontend/market` | 13 文件 85 passed |
| 前端类型 | `pnpm --dir frontend typecheck` | 通过 |
| 浏览器 E2E | `PLAYWRIGHT_CHANNEL=msedge FRONTEND_BASE_URL=http://localhost:5173 … playwright test tests/e2e/market_refresh/ui/market-refresh.spec.ts` | 2 passed |

R1 的 12 项评审要求证据映射见 tasks.md「评审要求与任务映射」及 T1–T8 已勾选验收项；本表为 HEAD 复验。

## 本轮修复（HEAD 代码与测试资产不一致）

1. `tests/support/python/contract_env.py`：量化任务新增防清表/不可变触发器（`fact_history_no_truncate`、`reject_allocation_mutation` 等）后，契约 fixture 的 TRUNCATE 被拒（35 项 setup ERROR）。修复：清理事务内对 public/market 两 schema 全表 `DISABLE TRIGGER ALL` → TRUNCATE → `ENABLE TRIGGER ALL`（DDL 随事务失败回滚，仅作用于一次性隔离库）。
2. `tests/backend/market_data/contract/api/test_market_refresh_reads.py`：停牌播种补 `suspension_scope='full_day'`，对齐量化生命周期任务升级后的停牌语义（`trade_status_effective` 视图 + 全天空停牌口径）。
3. `tests/e2e/market_refresh/ui/market-refresh.spec.ts`：对齐当前组件语义（`行情拉取状态` aria-label、失败态 `待补齐 0/11` + 重试按钮、offline（Redis 不可用→手动不准入）不渲染重试按钮）。组件行为由 85 项前端测试固化，不反向修改组件。

## 与量化生命周期任务的接口一致性

HEAD 上量化任务已把停牌语义升级为 `market.trade_status_effective`（`suspension_scope='full_day'`、source `tushare`/`tushare+baostock`）并扩展 `suspension_evidence`/`suspension_source_daily` 参与豁免；覆盖读模型随之扩展 `suspension_stamp` 进 data_version 摘要。2026-10-02 实时验证与全量回归均在该语义上通过。较早历史 `HISTORY_GAP` 仍保留（issues V11），不在本任务近 3 日自动补齐范围。

## 最终 Code Review 结论

2026-10-02 全任务独立审查：**PASS**，无 blocker/major。1 项 minor（契约 fixture 重试耗尽静默继续）+ 5 项 polish（TRUNCATE 清单缺无 FK 表、release_busy 残留字段、ensure 取消指针未清、任务文档状态行不一致、根目录 node_modules 未忽略）全部修复，另按审查建议一并修复 claim 取消路径同类残留；delta 复核确认全部正确、无新问题。修复后复验：unit 88 / integration 53 / contract 35 通过。审查完整记录见本任务附件与 [复盘](retrospective.md)。
