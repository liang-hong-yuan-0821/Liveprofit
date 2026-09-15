# 时间线日志

按时间**倒序**追加（最新在上）。每条 = 日期 + 动作 + 结果/备注；一句话能说清就不写长段。

| 日期 | 动作 | 结果/备注 |
|------|------|----------|
| 2026-09-16 | 方案收敛为纯 frontend+backend 链路（用户「本方案先不考虑 ai 层，先 frontend，backend 走通再说 ai」） | AI 层全部退出本方案：量化任务不经 LangGraph、Worker 直接执行 QuantExecutionService；删 QuantExecutionNode/图注入/AgentState/Propagator/checkpoint guard/AISelectionPanel/ai_selection DTO 等设计；risk_gate 缺省不门控（warnings 标注、BUY_REJECTED_RISK_GATE 预留）；层级校验放开 position 单独成任务；决策 1/7/8 改写 + 新增决策 11（AI 接入留待后续方案）；架构图/设计概览/4.2-4.5/README/log 同步 |
| 2026-09-16 | 恢复「设计概览」章节 + 更新 plan 模板（用户「参考我给你发的内容更新模板，要加一个设计概览」） | 模板固定五章：一背景/二架构/三设计概览/四详细设计/五决策（速查表同步，设计概览为多模块方案必需）；plan.md 按新方案内容重建设计概览（三层视图：AI 图编排/Agent/状态/运行时、backend 服务/表/迁移/DTO/API、frontend 页面/交互/查询/面板；行情实时口径、0008 本任务创建、signals 完整列清单指向 4.3 节）；详细设计 3.x→4.x、决策五；tasks.md 引用同步 |
| 2026-09-16 | 止损等价格列全量落图 + 2.1 补 signals 行（用户「把止损这些加入数据表」） | ER 图 signals 实体由压缩 8 行展开为 16 行（entry_price/stop_loss/take_profit/sell_ratio/建议订单列组/error_code 逐列展示）；plan 2.1 数据模型表补 quant_execution_signals 行（指向 3.3.1 完整列清单）；Playwright DOM 复检通过 |
| 2026-09-16 | signals 表完整列清单补进 3.3.1（用户问「七键是否都存库」引出） | 逐列落定：entry/stop/take 仅 BUY 行有值、sell_ratio 仅 SELL_PARTIAL、建议订单列组由 PositionPlanner 回写、error_code 归错误样本行；澄清 ER 图压缩展示造成的「不存库」误解 |
| 2026-09-16 | ER 图按新方案更新（用户「根据新方案更新下就好」） | 删两张快照表实体与其 4 条边，signals→instrument 逻辑引用改走原 spine；14 表 12 边；脚注补「行情执行时实时读取、不落快照表」；Playwright DOM 复检通过（无超框/压实体/出画布，hover 冒烟 hl=2 dim=11） |
| 2026-09-16 | 行情冻结砍除（用户拍板「不回测、只按条件选股」+ 保留组合与订单） | 删 `quant_execution_universe_snapshots`/`quant_execution_context_snapshots` 两张表（决策第 10 条）：提交只冻结策略/组合/持仓进 execution_snapshot，执行时实时枚举 universe + 分批读行情（AllMarketUniverseBuilder/MarketContextBatchLoader）；错误码 SNAPSHOT_CORRUPTED→STRATEGY_SNAPSHOT_INVALID；0009 迁移改名 quant_execution_signals；幂等 envelope 排除行情；重跑语义改为「同一策略与组合快照 × 当前行情」；3.2/3.3/3.4/3.5 与 README/log 同步 |
| 2026-09-16 | 表 ER 图生成（用户要求） | attachments/er-diagram.html：16 表 16 边自包含 SVG（域着色：蓝=存量平台/橙=新增量化/青=market；实线=FK、虚线=JSONB 快照引用与逻辑关联；悬停高亮、深色模式）；调色板过 validate_palette.js 双模式 PASS；Playwright DOM 级布局校验通过（无超框/标签压实体/出画布）；plan.md 2.1 挂链接 |
| 2026-09-16 | plan.md 结构优化（用户「你先优化下吧」） | 删除与详细设计重复的「三、设计概览」（独有细节并入 3.3.1：Agent 事件研究定位、position 依赖 market 的门控语义、checkpoint guard；3.3.4 补 checkpoint.py）；章节对齐模板编号（详细设计 4.x→3.x、决策五→四）；tasks.md/README 交叉引用同步 |
| 2026-09-15 | 数据表设计修订（对照存量表后用户拍板「都先改了」） | 0008 前提修正为「本任务创建」（存量迁移仅到 0007）；命名修正：context_snapshots、`*_hash` 统一、乐观锁列 revision→version；DDL 缺口补齐：universe 双 UNIQUE、signals 加 signal_kind+复合索引+FK、attempt 成功谓词（analysis_reports 行存在）、ingest_state 走 schema.sql、单任务快照体量预估 |
| 2026-09-15 | 新建任务文件夹 + plan.md 起草 | 方案已按后续评审修订（量化执行职责边界收归 backend QuantExecutionService）；状态待确认，等待用户确认后进入任务分解 |
