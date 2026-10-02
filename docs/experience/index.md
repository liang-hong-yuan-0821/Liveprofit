# 经验记录索引（pitfalls + best-practices）

> 新增/更新经验文件后，同步更新本索引（规则见 [开发流程](../standards/workspace/开发流程.md#规范知识和经验的维护)）。
> 强制规范维护在 [standards](../standards/index.md)，本目录保存已核实的原因、经验及做法。
> **pitfalls** = 踩坑记录（做错了会坏）；**best-practices** = 最佳实践（照着做能对）。

## pitfalls/（bad：踩坑记录）

### backend/

| 文件 | 内容 |
|------|------|
| [windows-pg-async.md](pitfalls/backend/windows-pg-async.md) | Windows psycopg async 循环、PG_HOST 归一化、stdout 全缓冲排查 |
| [alembic-jsonb-settings.md](pitfalls/backend/alembic-jsonb-settings.md) | alembic.ini ASCII、JSONB 归一回写、pydantic alias、Alembic raw SQL、SQLAlchemy UPDATE、迁移原行像的SQL NUMERIC精度与JSONB数字类型分离、原文和不变字段类型敏感比较及历史字段全集、TIMESTAMPTZ行像固定UTC/ISO序列化、立即前驱完整原像重建管理状态 |
| [dramatiq-windows.md](pitfalls/backend/dramatiq-windows.md) | Dramatiq 进程内模型、CLI 传参、同进程多 Broker namespace 发布 |
| [threading-futures.md](pitfalls/backend/threading-futures.md) | Thread._stop 禁用、Future 桥接 asyncio |
| [fastapi-openapi-prometheus.md](pitfalls/backend/fastapi-openapi-prometheus.md) | SSE OpenAPI 注册、prometheus_client 导入路径 |
| [db-test-redis-safety.md](pitfalls/backend/db-test-redis-safety.md) | DB 测试隔离、契约测试注入、Redis 数据安全与恢复、PG 参数上限分批、SQLAlchemy锁后ORM刷新、实时账户快照时间、跨入口锁序与共同资格时点、延期新增订单先协调旧单、同族信号唯一任务来源、stage调用方Session刷新与事务所有权、任务完成回调恢复与业务终态、延期消费品种与owner证明、修改前selector防已flush及原始SQL写入、清空GUC与提前约束后仍核事务级命令封口、固定子行及账户观察锁保持到提交、删源全集/FK锁测试真实落点与AUTOCOMMIT拒绝 |
| [quant-target-asof.md](pitfalls/backend/quant-target-asof.md) | 量化目标决策日/行情截至日分离、ETF跨日选集、指数基准有效期与基金快照隔离、近期复权窗口缺口、可空代码比较、本地输入与来源认证分列、非末尾未来行情拒绝与缺全集不清仓、旧信号单日成交额绕过ADV20 |
| [instrument-trading-rules.md](pitfalls/backend/instrument-trading-rules.md) | 品种交易规则按证券/执行日/来源唯一判定，普通100股与科创200后逐股、ETF回转交易差异、日期级同日发布及下一开市日边界；官方原件抓取事件与原文分离持久化 |
| [portfolio-policy-routing.md](pitfalls/backend/portfolio-policy-routing.md) | 组合政策确认BUY的多源身份与早退旁路、目标试验身份及旧扫描信号0.50建单陷阱、保护SELL与账本纠错边界 |
| [account-sellability.md](pitfalls/backend/account-sellability.md) | T+1及冻结持仓的可卖量不得由持仓总量推断；普通扫描、生命周期和账户退出统一缺证拒单 |
| [order-reconciliation.md](pitfalls/backend/order-reconciliation.md) | 在途与部分成交订单不能被无券商回执的状态或目标改写释放预留；无意图首仓分批完成及归档政策续填须核原单冻结身份 |
| [account-ledger.md](pitfalls/backend/account-ledger.md) | 已发生的成交和资金事实即使使本地余额为负也应保留并标偏离；双时点诊断重放不可把本地登记时刻当历史可见证据 |
| [analysis-readiness-retry.md](pitfalls/backend/analysis-readiness-retry.md) | deadline readiness 错误需同步进入 task lifecycle 白名单；独立量化补采以创建时间有界重试且未认证时只做持仓保护 |

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
| [tushare-endpoints.md](pitfalls/ai/tushare-endpoints.md) | 代理端点、停牌 trade_date/S-R 与日内时段、ST源空时独立停牌观察及ST事件间歇鉴权、BaoStock历史isST与当前名称陷阱、连续停牌缺S及北交所旧代码映射、历史名称截断/区间空洞、ST冲突统一有效视图门禁、正时长附带零时长停牌解析、旧停牌误标反向审计、历史 stk_limit 按当日上市池验全、ETF目录与限价源时段差异、DC板块兜底、日线与因子端点 |
| [store-daily.md](pitfalls/ai/store-daily.md) | 新股因子源缺首日行与历史不足、同码因子源close门禁； 全市场日线写入/事务/回填约定；历史窗口需排除未来上市股，ATR20暖机允许逐日可信停牌；北交所因子双码优先主库现码；增量逐日逐证券核验复权与因子、基金PARTIAL持续入队与有界历史暖机核验 |
| [prompts-checkpoint-rerun.md](pitfalls/ai/prompts-checkpoint-rerun.md) | 提示词注册表、checkpoint 存档、AgentState 白名单、环入口上移、重跑触发源 |
| [testing-llm-exclusion.md](pitfalls/ai/testing-llm-exclusion.md) | 自测排除真实 LLM 测试的漏洞与正确姿势 |
| [strict-event-vector-as-of.md](pitfalls/ai/strict-event-vector-as-of.md) | 历史事件召回必须按 embedding_available_at 门控，避免事后向量穿越 as_of |

### workspace/

| 文件 | 内容 |
|------|------|
| [git-bash-windows.md](pitfalls/workspace/git-bash-windows.md) | Git Bash 下 start /c MSYS 转换坑 |
| [git-status-porcelain-empty-path.md](pitfalls/workspace/git-status-porcelain-empty-path.md) | git status 对不存在路径静默返回空，核验前先确认路径存在 |
| [评审循环踩坑.md](pitfalls/workspace/评审循环踩坑.md) | 评审收敛与fixture能力踩坑、辅助实现反复验收及八文件重复收尾（业务单元聚合/受影响范围复验/证据单点） |
| [docker-image-stale.md](pitfalls/workspace/docker-image-stale.md) | 隔日验证镜像不随代码演进：容器健康但旧语义把错误摘要写进生产 Redis，整栈验收前按 HEAD 重建镜像并用容器内查询核对状态一致 |
| [目录迁移与坏ACL清理.md](pitfalls/workspace/目录迁移与坏ACL清理.md) | 坏 ACL 目录 rename 移出仓库+管理员删除；mv 与 mkdir -p 的嵌套陷阱；pytest cache_dir 与 no:cacheprovider 的 PytestConfigWarning |

## best-practices/（good：最佳实践）

### backend/

| 文件 | 内容 |
|------|------|
| [ingest-targeted-backfill.md](best-practices/backend/ingest-targeted-backfill.md) | 定向回填显式 codes、统一 PG session 锁、断连停止、提交后通知、共享资源 coverage 缓存失效；因子先筛缺口再限额、历史状态仅写缺口证券、定向独立源scope与完整ST名单隔离 |

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
