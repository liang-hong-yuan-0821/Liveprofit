# 经验记录索引（pitfalls + best-practices）

> 新增/更新经验文件后，同步更新本索引（规则见 Liveprofit/CLAUDE.md「经验沉淀规则」）。
> **pitfalls** = 踩坑记录（做错了会坏）；**best-practices** = 最佳实践（照着做能对）。

## pitfalls/（bad：踩坑记录）

### backend/

| 文件 | 内容 |
|------|------|
| [windows-pg-async.md](pitfalls/backend/windows-pg-async.md) | Windows psycopg async 循环、PG_HOST 归一化、stdout 全缓冲排查 |
| [alembic-jsonb-settings.md](pitfalls/backend/alembic-jsonb-settings.md) | alembic.ini ASCII、JSONB 归一回写、pydantic alias、Alembic raw SQL、SQLAlchemy UPDATE |
| [dramatiq-windows.md](pitfalls/backend/dramatiq-windows.md) | Dramatiq 进程内模型、CLI 传参 |
| [threading-futures.md](pitfalls/backend/threading-futures.md) | Thread._stop 禁用、Future 桥接 asyncio |
| [fastapi-openapi-prometheus.md](pitfalls/backend/fastapi-openapi-prometheus.md) | SSE OpenAPI 注册、prometheus_client 导入路径 |
| [db-test-redis-safety.md](pitfalls/backend/db-test-redis-safety.md) | DB 测试隔离、契约测试注入、Redis 数据安全与恢复、PG 参数上限分批 |

### frontend/

| 文件 | 内容 |
|------|------|
| [pnpm-openapi-codegen.md](pitfalls/frontend/pnpm-openapi-codegen.md) | pnpm 布局、openapi 重导出链条、codegen tags 分组与 Literal 两种形态 |
| [react-query-dialog.md](pitfalls/frontend/react-query-dialog.md) | refetchOnMount 门控、RegExp g 标志、弹窗回填 userEditedRef |
| [zrender-shared-eventful.md](pitfalls/frontend/zrender-shared-eventful.md) | zr.on/off 与 ECharts 共用 Handler Eventful：裸 off 误删内部监听且不可恢复，按引用 off + 外来 handler 存活断言 |
| [echarts-datazoom-anchors.md](pitfalls/frontend/echarts-datazoom-anchors.md) | dataZoom 百分比与日期锚互斥：混写锁死窗口致放大失效；jsdom wheel 探针验证交互行为 |

### ai/

| 文件 | 内容 |
|------|------|
| [tushare-endpoints.md](pitfalls/ai/tushare-endpoints.md) | 代理端点、日线降序归一、禁区间查询、概念成分参数、因子端点、技术指标不自算、板块日线端点能力实测（dc_daily 33 日窗口/ths_daily 全历史/push2his 不可达） |
| [store-daily.md](pitfalls/ai/store-daily.md) | 全市场日线本地库（store 包）写入/事务/回填约定 |
| [prompts-checkpoint-rerun.md](pitfalls/ai/prompts-checkpoint-rerun.md) | 提示词注册表、checkpoint 存档、AgentState 白名单、环入口上移、重跑触发源 |
| [testing-llm-exclusion.md](pitfalls/ai/testing-llm-exclusion.md) | 自测排除真实 LLM 测试的漏洞与正确姿势 |

### workspace/

| 文件 | 内容 |
|------|------|
| [git-bash-windows.md](pitfalls/workspace/git-bash-windows.md) | Git Bash 下 start /c MSYS 转换坑 |
| [评审循环踩坑.md](pitfalls/workspace/评审循环踩坑.md) | 评审收敛的 5 段踩坑史（评审维度清单的来源） |

## best-practices/（good：最佳实践）

### frontend/

| 文件 | 内容 |
|------|------|
| [markdown-render.md](best-practices/frontend/markdown-render.md) | MarkdownView 统一渲染约定与内容类型分流 |

### ai/

| 文件 | 内容 |
|------|------|
| [langgraph-topology.md](best-practices/ai/langgraph-topology.md) | 图结构确定性拓扑提取的正确姿势（compiled.builder） |
| [market-t6.md](best-practices/ai/market-t6.md) | 市场层 T6：花括号注入、纯代码节点登记、结构化 State 消费约定 |
| [eventstudy-scheduler.md](best-practices/ai/eventstudy-scheduler.md) | 每日批处理机制与睡眠补跑三层触发设计 |
| [debug-step-mode.md](best-practices/ai/debug-step-mode.md) | 调试步进模式协调机制 |
