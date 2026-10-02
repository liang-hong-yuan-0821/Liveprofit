# 复盘

任务级复盘；**跨任务可复用的主题沉淀进 docs/experience/**（按「经验沉淀规则」），本文件只留本任务的视角。

## 做对了什么（可复用）

- 先收敛范围再动工：AI 层全部退出本方案（「先 frontend、backend 走通再说 AI」），量化任务不经图、由 Worker 直接执行 QuantExecutionService；执行时实时枚举 universe、不落行情快照，重跑语义（同一策略与组合快照 × 当前行情）从一开始就明确，避免了快照一致性问题。
- 验收标准可执行且每轮真实全栈复验：每个增量完成都跑真实浏览器全市场扫描并留任务 ID（`1cedb9fb…`、`d6e91b10…`、`720b12b6…`），信号、订单、拒绝码在页面上肉眼可核，不止信单测。
- 沙箱安全靠硬约束而非信任：`-I` 一次性子进程、空 builtins、300ms wall-clock、stdout 4KiB 上限、close_fds，测试矩阵逐项验证超时/崩溃/import/print/NaN 全部 fail-closed。
- 迁移不只信 alembic 通过：partial unique 仅一条 DRAFT、signal_kind CHECK、cash<=assets CHECK、存量默认值均做数据级实测，升级→降级→升级链反复验证。
- 评审增量先修 P0 再推进：N0 先把因子日期对齐、数据错误审计、跳空后成本失真订单三个可信性缺陷修掉，才继续 N1+，避免旧基础链路把失真结果标为可执行。

## 踩了什么坑（教训）

- Windows 子进程 stdin/stdout 默认 GBK 与宿主 UTF-8 载荷不兼容（T3）：改走 `.buffer` 显式 UTF-8。同主题已并入 docs/experience/pitfalls/backend/db-test-redis-safety.md。
- 并发锁等待后未重读当日事实（N6）：双会话夹具发现竞态，修复为锁内二次读取先刷新，最终只保留一份逐日事实。
- Worker 将 SyncContainer 的 `session_factory` 误写为 API 专属字段：真实浏览器实跑才暴露，单测没覆盖 Worker 分支——Worker 接线类改动需要冒烟级真实链路验证。
- 行业覆盖率「非空即过」会放行被截断的帧（M7）：改口径为「有行业归属的活跃股票数/活跃股票总数 ≥0.95」。
- POSIX 专属进程组测试在 Windows 跳过，导致 O2 长期「进行中」：平台专属验证项应从一开始单列「待部署验证」门禁，避免完成状态被误解。

## 下次改进

- 数据质量门禁（因子日期对齐、全 null/错日拒绝、数据错误样本落库）应在执行链设计阶段就内置并配专项 fixture，而不是评审后返工。
- 未完成项承接要显式建承接表（本任务 N7→T7、N8→T8、N9→T5/T8、O2 POSIX→T8），归档时不留歧义——本任务已按此办理。
- 真实人工项（行业 POC 需真实 TUSHARE_TOKEN、E2E 完整栈）要提前单列并约定触发时点，不要混在「已完成」状态里。
