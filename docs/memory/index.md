# 经验记录索引（pitfalls + best-practices）

> 新增/更新经验文件后，同步更新本索引（规则见 Liveprofit/CLAUDE.md「经验沉淀规则」）。
> **pitfalls** = 踩坑记录（做错了会坏）；**best-practices** = 最佳实践（照着做能对）。

## pitfalls/（bad：踩坑记录）

### backend/

| 文件 | 内容 |
|------|------|
| [windows-pg-async.md](pitfalls/backend/windows-pg-async.md) | Windows psycopg async 循环、PG_HOST 归一化、stdout 全缓冲排查 |
| [alembic-jsonb-settings.md](pitfalls/backend/alembic-jsonb-settings.md) | alembic.ini ASCII、JSONB 归一回写、pydantic alias、Alembic raw SQL、SQLAlchemy UPDATE |
| [dramatiq-windows.md](pitfalls/backend/dramatiq-windows.md) | Dramatiq 进程内模型、CLI 传参、同进程多 Broker namespace 发布 |
| [threading-futures.md](pitfalls/backend/threading-futures.md) | Thread._stop 禁用、Future 桥接 asyncio |
| [fastapi-openapi-prometheus.md](pitfalls/backend/fastapi-openapi-prometheus.md) | SSE OpenAPI 注册、prometheus_client 导入路径 |
| [db-test-redis-safety.md](pitfalls/backend/db-test-redis-safety.md) | DB 测试隔离、契约测试注入、Redis 数据安全与恢复、PG 参数上限分批 |
| [analysis-readiness-retry.md](pitfalls/backend/analysis-readiness-retry.md) | deadline readiness 错误需同步进入 task lifecycle 白名单，否则默认 max retry 会让任务提前失败 |

### frontend/

| 文件 | 内容 |
|------|------|
| [pnpm-openapi-codegen.md](pitfalls/frontend/pnpm-openapi-codegen.md) | pnpm 布局、openapi 重导出链条、codegen tags 分组与 Literal 两种形态 |
| [react-query-dialog.md](pitfalls/frontend/react-query-dialog.md) | refetchOnMount 门控、RegExp g 标志、弹窗回填 userEditedRef |
| [zrender-shared-eventful.md](pitfalls/frontend/zrender-shared-eventful.md) | zr.on/off 与 ECharts 共用 Handler Eventful：裸 off 误删内部监听且不可恢复，按引用 off + 外来 handler 存活断言 |
| [echarts-datazoom-anchors.md](pitfalls/frontend/echarts-datazoom-anchors.md) | dataZoom 百分比与日期锚互斥：混写锁死窗口致放大失效；jsdom wheel 探针验证交互行为 |
| [date-arithmetic-clamping.md](pitfalls/frontend/date-arithmetic-clamping.md) | 近 N 月区间：setMonth 溢出滚动、负号方向漏负出未来日期；addMonthsClamped 夹取写法 |
| [grid-minmax-collapse.md](pitfalls/frontend/grid-minmax-collapse.md) | grid-cols 任意值固定列超宽时 minmax(0,Xfr) 弹性列塌缩为 0px 整列不可见；标题类弹性列须正下界 + overflow-x-auto |

### ai/

| 文件 | 内容 |
|------|------|
| [tushare-endpoints.md](pitfalls/ai/tushare-endpoints.md) | 代理端点、停牌 trade_date/S-R 语义、DC 单板块漏行与东财原始 K 线定向兜底、日线降序归一、禁区间查询、概念成分与因子端点；push2his 连通性随环境变化 |
| [store-daily.md](pitfalls/ai/store-daily.md) | 全市场日线本地库（store 包）写入/事务/回填约定 |
| [prompts-checkpoint-rerun.md](pitfalls/ai/prompts-checkpoint-rerun.md) | 提示词注册表、checkpoint 存档、AgentState 白名单、环入口上移、重跑触发源 |
| [testing-llm-exclusion.md](pitfalls/ai/testing-llm-exclusion.md) | 自测排除真实 LLM 测试的漏洞与正确姿势 |
| [strict-event-vector-as-of.md](pitfalls/ai/strict-event-vector-as-of.md) | 历史事件召回必须按 embedding_available_at 门控，避免事后向量穿越 as_of |

### workspace/

| 文件 | 内容 |
|------|------|
| [git-bash-windows.md](pitfalls/workspace/git-bash-windows.md) | Git Bash 下 start /c MSYS 转换坑 |
| [git-status-porcelain-empty-path.md](pitfalls/workspace/git-status-porcelain-empty-path.md) | git status 对不存在路径静默返回空，核验前先确认路径存在 |
| [评审循环踩坑.md](pitfalls/workspace/评审循环踩坑.md) | 评审收敛的 5 段踩坑史（评审维度清单的来源） |

## best-practices/（good：最佳实践）

### backend/

| 文件 | 内容 |
|------|------|
| [ingest-targeted-backfill.md](best-practices/backend/ingest-targeted-backfill.md) | 定向回填显式 codes、统一 PG session 锁、断连停止、提交后通知及共享资源 coverage 缓存失效 |

### frontend/

| 文件 | 内容 |
|------|------|
| [markdown-render.md](best-practices/frontend/markdown-render.md) | MarkdownView 统一渲染约定与内容类型分流 |

### ai/

| 文件 | 内容 |
|------|------|
| [langgraph-topology.md](best-practices/ai/langgraph-topology.md) | 图结构确定性拓扑提取的正确姿势（compiled.builder） |
| [market-t6.md](best-practices/ai/market-t6.md) | 市场层 T6：花括号注入、纯代码节点登记、结构化 State 消费约定 |
| [eventstudy-scheduler.md](best-practices/ai/eventstudy-scheduler.md) | 每日批处理与睡眠补跑、迟到新闻驱动的量化候选幂等刷新 |
