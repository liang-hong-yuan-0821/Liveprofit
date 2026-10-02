# 原基础阶段产出与结论

> 本文件仅记录原 T1–T8 基础阶段的历史验证结果，不代表 2026-09-19 统一方案及评审增量 N0–N9 已实现或通过。当前状态以 README.md、plan.md 和 tasks.md 为准。

## 2026-09-21 增量实施实测

- N0–N6 已完成；N8 已取得真实全栈基线但性能预算与 N7 展示终验仍在进行中，N9 观察期尚未开始。
- N1 真实数据质量 POC：共同水位 2026-09-18、活跃股票 5,565；250 日完整 4,935（88.68%），其余为复权缺失 455、暖机不足 163、旧 bar 12。七模板按实际 2/6/20/41 根窗口覆盖 98.10%–99.50%，未达 100%，全部保持不可晋级；报告同时给出失败代码样本、样本截断、数据库窗口裁剪及限流/重试计数。
- N2 第一波三模板均通过真实 loader + 一次性 runner 集成，真实 POC 沙箱抽样均为 3/3；模板合同随任务快照冻结，后续注册表显示名/渲染器变化不会改变已提交任务执行与报告语义。
- 真实浏览器完成模板策略创建/发布、组合资金配置、仅仓位层任务提交与结果查看。
- 成功任务 `1cedb9fb-0601-4174-b636-042601766f1a`：全市场 5,565 只，数据完备并执行 Sandbox 5,507 只，命中 25 个 `MA_TREND_CROSS` 买点，58 只以 `STALE_DATA`/`INDICATOR_UNAVAILABLE` 审计拒绝。
- 真实申万当前成员覆盖率 93.62%，低于冻结门槛 95%；因此 25 个原始买点仍展示 qfq 入场/止损/止盈，但均标记 `BUY_REJECTED_INDUSTRY_BUCKET`，建议订单为 0，未写入持仓。
- 后端定向回归 101 通过；前端 336 通过，typecheck 与 production build 通过。
- N3 二次真实任务 `d6e91b10-04fd-476f-8601-4e00ff96f166` 仍为 5,565/5,507/25/58；页面已同时展示 qfq 策略三价与含 10bps 滑点、tick 规范化后的 raw 订单候选三价，并由真实交易状态拒绝 `603232.SH`（涨停）与 `688109.SH`（停牌）。其余 23 个买点仍因行业覆盖门禁拒绝，建议订单为 0。
- N3 收口回归：后端 unit 259、integration 44、contract 126、数据库 107、Sandbox 32（另 1 skip）、迁移 4 通过；前端 336、typecheck、production build 通过。真实 LLM Agent 测试需要访问 DeepSeek，在当前网络沙箱中不可运行，与 N3 本地执行链无关。
- N4 新增迁移 0012 与组合风险计算器：活动止损损失额、组合/行业开放风险、单日新增风险、回撤/日损熔断、未完成 BUY/SELL 的现金/数量/风险预留均纳入逐单规划；缺净值事实、缺活动止损或水位陈旧时拒绝新增风险，退出信号仍继续评估。任务提交冻结 Decimal 风险配置及净值水位，历史任务不受后续账户修改影响。
- N4 收口回归：后端主回归 515 项通过（另 1 项 POSIX 限额在 Windows 跳过），数据库/instrument 107 项、前端 336 项、typecheck 与 production build 通过，迁移 `0012 → 0011 → 0012` 实测通过。
- N4 真实浏览器任务 `720b12b6-489a-4ff0-89f3-641443679790`：仅运行仓位层，55.6 秒扫描 5,565 只、数据完备 5,507、命中 25、数据拒绝 58；风险事实日期 2026-09-18 与共同行情水位一致。行业覆盖门禁继续 fail-closed，故 0 建议买单、0 自动写持仓。实跑发现并修复 Worker 对同步容器 session factory 的错误字段引用。
- N5 新增迁移 0013 与八张长期审计表，生命周期策略版本与策略源码显式绑定；ELIGIBLE 信号仅物化一次建议订单，后续任务会冻结活动订单剩余现金/数量/风险预留。成交确认、部分成交、更正和撤销使用 append-only 事件，并在单事务内投影实际资金、持仓、订单 revision 与生命周期 state_version；未成交建议不会改持仓。
- N5 幂等与并发边界已覆盖：成交 idempotency replay、陈旧 revision 拒绝、重复冲销 fail-closed、资金不足整事务无残留、跨日活动意图复用、同日逐日事实 hash 复用/冲突拒绝。迁移 `0013 → 0012 → 0013` 实测通过；integration 53、contract 128、量化执行 43、数据库 107、Sandbox 32（另 1 skip）通过，generated client typecheck 通过。
- N5 完整栈已用新代码重启，`/health/ready` 返回 PostgreSQL/Redis 均正常，OpenAPI 暴露 5 条生命周期/建议订单路径；真实浏览器保留任务 `720b12b6-489a-4ff0-89f3-641443679790`，可查看 5,565/5,507/25/58 结果并切换到“建议订单（需人工确认，未下单）—无数据”。
- N6 新增纯规则 `PositionLifecycleManager` 与事务编排：首次真实 BUY 成交才初始化生命周期并冻结实际成交价、风险容量、初始止损、收益目标、策略/策略版本和 50% 初始目标；七模板按固定优先级给出唯一最终目标，移动止损只使用前一日有效止损，MA5 只消费成交后完整有效 bar，首次止盈后不再买回超过 50%。
- N6 已把目标仲裁接入量化执行：脚本 `SELL_ALL`/`SELL_PARTIAL` 与平台目标取较低绝对股数，扣除活动订单剩余量后只生成一笔净差额建议；圆弧底 BUY 同次 qfq/raw 上下文冻结颈线价。部分成交只有达到目标才推进确认/止盈完成标志；拒绝、缺行情/因子和冲突事实均 fail-closed。
- N6 并发夹具以两个独立数据库会话同时处理同一生命周期/同一交易日，发现并修复锁等待后未重读当日事实的竞态，最终只保留一份逐日事实并复用同一 ID。聚焦测试 27、后端 unit/integration/contract 463、量化执行 45、数据库 107、Sandbox 32（另 1 skip）全部通过。

## 验证结果

| 层面 | 命令/方式 | 结果 |
|------|----------|------|
| 后端单测/集成/契约 | `.venv/Scripts/python.exe -m pytest backend/tests/unit backend/tests/integration backend/tests/contract tests/strategy_sandbox tests/agents tests/db/instrument -q` | 通过（exit 0，全绿；CR 修复 + M7 覆盖率口径 + 格式化端点后最终回归） |
| 迁移链 | `alembic upgrade head → downgrade 0007 → upgrade head`（测试库实测） | 通过 |
| 数据级约束 | partial unique 仅一 DRAFT / signal_kind CHECK / cash<=assets CHECK / 默认值迁移 | 通过（数据级实测） |
| cursor 压力 | 6,000 信号 30 页分页 | 通过（无重复无漏项） |
| 前端 | `pnpm run test`（311 例）+ `npx tsc --noEmit` + `pnpm run build` | 通过（311 全绿、tsc 0 错、build 成功） |
| OpenAPI/codegen | `python -m backend.scripts.export_openapi` + `pnpm run generate:api` | 通过（QuantStrategies/QuantSignals 服务生成并消费） |
| 行业 POC | `python -m db.instrument.ingest.industries --poc`（真实 TUSHARE_TOKEN） | **留待用户部署前人工执行** |
| E2E 量化流程 | 完整栈（Worker+行情+策略）人工验收 | **留待用户** |

## 原基础阶段 Code Review

- **结论**：两轮——首轮 subagent 报告 3 BLOCKER / 15 MAJOR / 12 MINOR / 6 项测试盲点；修复后全量回归通过（exit 0）；M7 覆盖率口径经用户拍板 A（活跃股票口径）落地；前端交互优化（编辑器着色/格式化/新建必填代码）与模板库方案差异分析完成
- **关键修复**：
  - B1 行情窗口取成最老 250 根（>250 交易日票会按一年前行情决策）
  - B2 Worker 进度回调签名不兼容（真实量化任务必崩）
  - B3 行业 POC/refresh CLI 100% 崩溃（contextmanager 误用）
  - M2 批内 valuation_price 串票污染订单规划
  - M5 非持仓错误样本未落库（错误分页恒空）
  - M8 失败短事务翻 status 导致整周关闭行业门控
  - M10 量化面板未接 cursor 全量分页（>50 条永不可见）
- **遗留**：原 E2E 量化流程与行业 POC（--poc 真实 token）两项人工验收项留待用户；评审后另新增数据对齐、共同水位、qfq/raw 双口径、可交易性/费用、组合开放风险、生命周期、真实 6,000 标的性能与 forward shadow 门禁，详见 tasks.md N0–N9。

## 交付物

- 迁移：0008（策略表+组合风控列）、0009（signals 表+版本审计列）
- 沙箱：AST 校验器（validator）、协议校验（protocol）、受限子进程执行器（runner）
- 策略领域：backend/modules/quant_strategy/（状态机：创建/草稿/发布/归档 + 乐观锁）
- 提交服务：QuantTaskSubmissionService（同 Session 冻结快照、canonical 幂等、replay/REUSED）
- 执行与订单：QuantExecutionService（实时全市场扫描/8 并发沙箱/取消回收）+ PositionPlanner（资金/盈亏比/行业/整手裁剪）+ Worker 量化分支（不经图）+ 源码泄漏 guard
- 行业底座：market.ingest_state + 采集模块（collect_industries/--poc/--refresh）+ 周刷接线 + 门控谓词
- API：策略路由 7 端点、组合账户原子 PATCH、报告 quant_execution 投影、信号 cursor（attempt 隔离/稳定编码）
- 前端：策略管理页、组合账户设置、任务表单量化参数选择器、量化执行面板（cursor 分页）、/ai/strategies 路由
- 文档：本文件保留原基础 8/8 历史记录；统一 plan 已进入评审修订状态，不再视为冻结或完成。
