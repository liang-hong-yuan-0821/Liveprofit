# 产出与验证

实现交付了每日新闻/事件报告、每日量化扫描报告、人工触发 API、事件批次工作台和兑现快照。自动时点配置为 Asia/Shanghai 09:00 新闻、21:00 新闻及量化准入；原文每5分钟采集，有待处理新闻时按30分钟桶增量研判。运行复用平台 analysis task、outbox、lease 与版本化 report。若量化批次因新闻积压不完整，完整新闻后来清空积压时 Dispatcher 幂等准入刷新批次，沿用原扫描候选及量化分，更新近90天事件加减分、排序和重点候选深研，不重复执行策略。量化一期不生成组合仓位或建议订单。功能需要 backend API、Dispatcher/worker 持续运行；尚未在生产部署启用。

| 层面 | 方法 | 结果 |
|------|------|------|
| 现状核查 | 主会话 + 事件/量化/agent 三路只读代码审查 | 已定位真实复用点与接线缺口 |
| 原版方案评审 | 三位独立reviewer：R1全量、R2/R3修复核验 | R3 PASS，评分6.4→8.7→9.8；不覆盖后续存储修订 |
| 存储设计修订 | 核对实际表、任务级联删除和连接配置；比较零新表与独立业务模型 | 推荐两张职责单一的业务表＋共性执行能力复用；根 AGENTS.md 改为最佳整体设计原则 |
| 时点与入口修订 | 对照现有每日批处理和方案调度/API，检查独立09:00/21:00批次及人工按钮边界 | 当时先确定整点截止、partial/backlog、人工运行幂等与早晚展示；现已接入运行流程 |
| 量化起跑修订 | 按用户补充将量化自动准入改为每天21:00，新闻与量化各有独立手动入口 | 当时先明确起跑时间、休市复用、行情等待与防重；现已接入调度和独立入口 |
| 事件研究 UI 修订 | 对照现有 `PredictionForm`/`PredictionTab` 的自由文本一次性预测 | 当时改为每日新闻批次预测和按版浏览；现有自由文本表单已退出主流程 |
| 文档自检 | 相对链接、八文件骨架、空白检查；核对公式、枚举、章节与测试映射 | 通过 |
| 新闻双 Agent | mock LLM 测试及新闻流水线单测 | A 复核、0/1/2 轮上限、争议结果覆盖；新闻锁重试有界 |
| 每日投研后端 | `.venv\Scripts\python.exe -m pytest backend/tests/unit/daily_research backend/tests/unit/analysis/test_task_service.py -q` | 42 passed |
| 最终后端/API回归 | `.venv\Scripts\python.exe -m pytest backend/tests/unit/daily_research backend/tests/unit/event_study backend/tests/unit/analysis/test_quant_executor_branch.py backend/tests/contract/api/test_daily_research.py backend/tests/contract/api/test_event_study_review.py -q` | 123 passed；未运行真实 LLM/provider 用例 |
| 补齐后刷新与报告契约 | scheduler / quant snapshot 单测覆盖积压门控、原交易日/新闻来源快照、候选重排复用量化分及日报报告区块键 | 包含在最终定向回归中 |
| 最终扩展后端回归 | 上述每日研究/API/事件研究套件，并加入 `tests/event_study/test_predictor.py` | 164 passed；未运行真实 LLM/provider 用例 |
| PostgreSQL 事件快照与升级 | `.venv\Scripts\python.exe -m pytest tests/event_study/test_predictor.py -q` | 32 passed；覆盖旧 schema 升级、原有 events/assets/event_impacts 保留、原文与assessment版本幂等、事务回滚、历史向量可用时点 |
| 图适配 | `.venv\Scripts\python.exe -m pytest tests/graph/test_graph_topology.py tests/graph/test_derive_risk_gate.py tests/graph/test_market_layer_graph.py -q` | 23 passed |
| API 与构建 | `backend/tests/contract/test_openapi_gate.py`；`.venv\Scripts\python.exe -m compileall -q ...`；前端 typecheck/build | OpenAPI 2 passed；compileall/typecheck/build 通过 |
| 每日研究页面 | `pnpm test -- --run src/modules/daily-research src/modules/event-study` | 9 个测试文件、61 passed |
| API contract | 每日研究与事件审核 API contracts | 已包含在最终后端/API回归中并通过 |
| 前端类型检查 | `pnpm typecheck` | 通过 |
| Python 编译检查 | `.venv\Scripts\python.exe -m compileall -q AI/eventStudy backend/modules/daily_research backend/modules/quant_strategy/application/execution.py` | 通过 |
| 前端全量 | `pnpm test` | 369 passed、1 failed；已有 QuantExecutionPanel 图表测试的 `cache_only` 参数断言与本功能无关，详见 issues.md |
| AI/数据源 | 未使用真实 LLM、新闻源或行情 provider | 按工作区测试规则，不运行 `real_llm`/`real_toolkit` 用例 |

剩余验收集中在目标部署库的旧 schema 在线升级、并发 Dispatcher/租约恢复、外部 provider/LLM 覆盖和实际每日耗时；不影响本机已通过的定向流程回归。组合仓位和订单不属于一期候选排名目标。

最终候选刷新回归复跑：每日研究、事件研究、量化执行分支、每日研究 API、事件审核 API 与 PostgreSQL 事件预测快照共 **164 passed**；daily_research 定向 Ruff、相关模块 `compileall` 通过。前端每日研究/事件研究测试 **61 passed**，`pnpm typecheck` 通过。候选刷新代码审查 R2 PASS、无 findings；刷新失败路径验证保留父批次行情日和候选、标记 partial 且不重复运行策略。

任务资料已归档。当前工作区的 Dispatcher、事件研究、前端路由等共享文件还含其他并行任务的未提交差异，因此暂缓提交，避免误提交无关工作。
