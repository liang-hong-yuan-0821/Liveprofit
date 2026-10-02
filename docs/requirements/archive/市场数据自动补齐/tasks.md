# 市场数据自动补齐任务清单

> **状态**：已完成（2026-10-02）
> **进度**：10/10 任务完成；最终全任务 Code Review PASS（无 blocker/major，6 项发现全部修复并经 delta 复核）
> **下一步**：较早历史缺口（issues V11）按来源可得性另行核验
> **关联方案**：[plan.md](plan.md)｜[评审记录](attachments/review.md)｜[待验证项](issues.md)

用户于 2026-09-23 确认进入任务分解。清单直接拆自 R2 通过的方案，不另启方案评审。2026-09-23 用户进一步授权按本清单实施，当前按任务逐项开发和验收。测试新文件为计划落点，由对应任务创建，不能将不存在的测试视为已通过。

贯穿约束：**不新增 PostgreSQL 表、字段或迁移**；行情与可信停牌事实复用现有表，任务/预算/冷却使用 Redis。自动补齐仅六组、最近三个已到发布时间的交易日；不扩成基金、qfq、AI 或任意历史全量采集。

## 任务总览

| 编号 | 任务 | 依赖 | 状态 |
|------|------|------|------|
| T1 | 建立运行配置与隔离测试基础 | — | 已完成 |
| T2 | 实现多市场日期策略并验证数据源边界 | T1 | 已完成 |
| T3 | 实现六组覆盖读模型与数据版本 | T1、T2 | 已完成 |
| T4 | 实现 Redis 状态、准入、预算与有界投递 | T1、T2、T3 | 已完成 |
| T5 | 实现公共采集锁、定向采集和目录初始化 | T1 | 已完成 |
| T6 | 接入 Market Worker 与 Dispatcher | T4、T5 | 已完成 |
| T7 | 实现更新 API、行情查询语义及客户端契约 | T2、T3、T4、T5 | 已完成 |
| T8 | 实现前端自动检查、状态展示与缓存刷新 | T7 | 已完成 |
| T9 | 完成本地及容器启停装配 | T6、T7 | 已完成（2026-10-02） |
| T10 | 完成联调验收、代码审查及文档收尾 | T8、T9 | 已完成（2026-10-02） |

T1 后，T2→T3→T4 与 T5 可并行；之后 T6 与 T7 可并行，T8 与 T9 可并行，最后汇入 T10。同一文件有多个任务涉及时按依赖顺序整合；并行开发由单一负责人合并，避免互相覆盖。

## 任务

### T1 建立运行配置与隔离测试基础

- **目标**：落实方案 §2.1、§4.6 的配置和测试装配，后续任务能在明确隔离的 PG/Redis 环境验收。
- **涉及文件**：修改 `backend/bootstrap/settings.py`、`backend/workers/broker.py`、`backend/tests/integration/market_data/conftest.py`、`backend/tests/contract/api/conftest.py`；新增 `backend/tests/integration/market_data/test_refresh_isolation.py`、`backend/tests/contract/api/test_refresh_isolation.py` 及相应配置单测。
- **实施要点**：集中定义 `Settings.market_refresh` 的开关、缓冲、周期、预算和超时默认值；为测试注入业务前缀与 Broker namespace。复用平台解析后的 PG DSN/Redis URL，保留旧 CLI 的 PG_* fallback，子进程必须显式传测试连接。Broker 可选 namespace 默认保持现状。
- **依赖**：无。
- **验收标准**：
  - [x] 配置单测验证总开关、自动开关及集中默认值，并验证显式连接优先于旧环境 fallback；输出不包含连接凭据。
  - [x] 执行 `python -m pytest backend/tests/integration/market_data/test_refresh_isolation.py`：DB12 随机业务前缀/独立 Broker namespace、全新子进程一次 configure、实际连接断言、停进程后精确清理均通过；连续两个 run_id 不串消息。
  - [x] 执行 `python -m pytest backend/tests/contract/api/test_refresh_isolation.py` 并结合 integration 隔离测试：两组 fixture 均清理现有 `market.trade_status_daily`；分别验证“上一例停牌、下一例同代码同日未知”，正序/反序均通过。contract 维持专用 DB11 的现有 flushdb 并串行执行，仅注入 fake publisher；不启动真实 Worker。
  - [x] integration 对非测试 PG 库及 Redis DB0/10/11 拒绝启动；teardown 后无存活子进程、无本次双命名空间残留，不清其他队列。
- **状态**：`已完成`（2026-09-23）。

### T2 实现多市场日期策略并验证数据源边界

- **目标**：实现方案 §4.1 的市场时间/发布缓冲策略，完成 §4.6 的实施前依赖与只读源验证。
- **涉及文件**：新增 `backend/modules/market_data/application/refresh_policy.py`、`backend/tests/unit/market_data/test_refresh_policy.py`；修改 `backend/modules/market_data/infrastructure/calendar_adapter.py`、`pyproject.toml`、`uv.lock`；在 `attachments/` 记录新增验证证据，并更新 `issues.md`。
- **实施要点**：锁定已验证的 exchange_calendars 4.13.2，输出各市场日期、expected/next_ready_at、session 开闭/休息状态及支持范围；保留原 AI CN 日历接口。发布缓冲仍为项目策略。
- **依赖**：T1。
- **验收标准**：
  - [x] 执行 `python -m pytest backend/tests/unit/market_data/test_refresh_policy.py`：六组缓冲、北京时间零点/盘中/20:00 边界、周末长假、纽约 DST/提前收盘及真实 open/close/break 均通过。
  - [x] 将已保存的日历 POC 样例移入正式依赖环境复测：CN 2026 与项目缓存一致；CN 2027 UNKNOWN 不阻止 US/KR；边界前30天 EXPIRING；当前目标可算但 next 越界时保留目标、next=null，无 weekday fallback。
  - [x] 对现有代理做有限只读采样，记录 US/KR trade_date 标签、最新到数观察时刻，以及停牌源 None/空集/截断语义；不运行全市场采集。证据不足的项在 issues.md 明确保留，并验证 UNKNOWN/PARTIAL 降级，不能标为已证实的 SLA 或正常空集。
  - [x] 明确六组目标集合和最近三交易日窗口，日期策略单测不访问网络；正式 Linux/Windows 打包验证由 T9 完成。
  - [x] 日历依赖初始化瞬时异常不缓存为永久 UNAVAILABLE；同一进程下一次请求可成功恢复（`test_transient_calendar_failure_recovers_without_process_restart`）。
- **状态**：`已完成`（2026-09-23）。

### T3 实现六组覆盖读模型与数据版本

- **目标**：实现方案 §2.1、§4.1 的 PG 只读核验，为状态查询与准入提供统一事实。
- **涉及文件**：新增 `backend/modules/market_data/infrastructure/refresh_repository.py`、`backend/tests/integration/market_data/test_refresh_coverage.py`；补充 T2 的策略测试。
- **实施要点**：读取现有 instrument/daily/factor/sector/trade_status 表，不以单个最大日期冒充全组完整；按方案生成 freshness、目标日/窗口覆盖、缺口和不透明 data_version。与 Redis 状态层通过摘要接口组合。
- **依赖**：T1、T2；本任务可直接种入现有停牌表，不等待 T5 的采集实现。
- **验收标准**：
  - [x] 执行 `python -m pytest backend/tests/integration/market_data/test_refresh_coverage.py`：10/11指数、OHLC空值、行情齐而必需因子缺失、股票 pct_chg 缺失、板块缺行均正确判缺。
  - [x] 断言目标日11/11但窗口32/33为 PARTIAL；各日生命周期过滤后满足 `expected=available+exempt+missing`，有效行情与停牌不双计，未知生命周期不被排除。
  - [x] 空固定指数目录仍按 INDEX_TARGETS 计算目标；股票/DC 空目录返回 CATALOG_UNAVAILABLE，不能0/0完整。已有停牌事实在清本功能 Redis 后仍可重建豁免。
  - [x] 覆盖 latest_observed、最近三日内 complete_through、无数据/未知日历及 HISTORY_GAP 语义；摘要过期只读 PG、零 provider/任务投递调用，版本不是持久事务日志。
  - [x] 股票日线目标精确排除 DC 成分中的 `20xxxx.SZ` / `900xxx.SH` B 股；其它未知生命周期代码仍阻止准入，旧冻结任务也不再请求/重试不支持的 B 股。隔离 PG 测试覆盖目标集合和旧 frozen spec 核验。
- **状态**：`已完成`（2026-09-23）。

### T4 实现 Redis 状态、准入、预算与有界投递

- **目标**：实现方案 §2.1、§4.2 的状态机及共享准入服务，供 API 和 Dispatcher 复用。
- **涉及文件**：新增 `backend/modules/market_data/infrastructure/redis_refresh_store.py`、`backend/modules/market_data/application/refresh_service.py`、`backend/tests/integration/market_data/test_refresh_jobs.py`；扩充策略测试。
- **实施要点**：集中 Lua/CAS、任务复用、token/租约、冷却预算、双 eligibility、版本标记与 factor_cooldown；投递采用可注入 publisher，Worker 接线留 T6。
- **依赖**：T1、T2、T3。
- **验收标准**：
  - [x] 执行 `python -m pytest backend/tests/integration/market_data/test_refresh_jobs.py`：并发同资源请求同 job、旧 token 不能续期/覆盖/删除新状态、旧消息丢弃、目标切换/目录变化与终态 TTL 均符合方案。
  - [x] fake clock 验证自动最多3轮、15/60分钟退避、手动至少5分钟且滚动24小时最多6次；同目标推进10天仍不重置自动次数，旧目标切换且终态后才设7天清理 TTL。
  - [x] 自动关闭或耗尽时手动 eligibility 独立；Redis 不可用时两模式不可准入，恢复先核验 PG；已完成数据不因旧任务失败重新拉取。
  - [x] 离线1小时零消息、busy90分钟每 job 至多首投一次、idle丢消息每轮至多3次且5/15分钟补发；多 Dispatcher CAS 不翻倍；DELIVERY_UNCONFIRMED 后自动不得靠新 job 重置阻塞。
  - [x] 锁忙且尚未拉源可撤销尝试记账，但不重置投递次数；30秒仅复查锁，真正重投仍按5/15分钟退避。PG commit 后 Redis 上报失败可在下次核验恢复，真实 Redis 全丢失的次数不可恢复边界保留。
- **状态**：`已完成`（2026-09-23）。

### T5 实现公共采集锁、定向采集和目录初始化

- **目标**：交付方案 §4.2/§4.3 的中立采集执行面，复用现有表并接入旧维护入口。
- **涉及文件**：新增 `db/instrument/ingest/guard.py`、`refresh.py` 和 `tests/db/instrument/test_refresh_ingestion.py`；修改同目录 `incremental.py`、`frames.py`、`sector_daily.py`、`sectors.py`、`stock_factors.py`、`backfill.py`，以及 `db/instrument/dao/trade_status.py`、`factor_daily.py`、`AI/dataflows/providers/cn/tushare.py`、`backend/workers/market_ingest.py`、`AI/eventStudy/scheduler/daily_job.py`；同步相关既有测试和定向回填维护文档。
- **实施要点**：中立包定义 typed spec/progress/guard 协议，不依赖 backend。locked公开入口与 unlocked内部组合共享实际写入连接；实现分组 bars/factors、股票单日分批、DC逐板块、独立可信停牌状态和安全 DAO helper。通过回调接入变更通知，允许 T6 再接 Redis。公开入口逐项登记：collect_incremental、run_backfill、backfill_index_history、run_stock_factor_backfill、collect_sector_daily_incremental、collect_sectors、collect_stock_quant_day、collect_refresh、collect_stock_status_day；交互因子入口由 T7 接入同一 guard。
- **依赖**：T1；源语义按 T2 记录校准，未证实时始终保守降级，完成联调前合并 T2 结果。
- **验收标准**：
  - [x] 执行 `python -m pytest tests/db/instrument/test_refresh_ingestion.py` 及本任务涉及的既有采集/DAO/provider测试：只缺指数不调板块/基金/qfq，1000板块仅缺3项只拉3项；股票≥6000行降级每100代码，错误日期/缺列不算成功。
  - [x] 真实隔离 PG 的锁与写入断言加入 `backend/tests/integration/market_data/test_refresh_jobs.py`：公共入口逐个验证锁冲突零源调用，包括直接 backfill_index_history、stock factor回填、quant day、目录初始化及新刷新入口；断连后下一单元不拉源、不自动重连，rollback容错不吞 IngestSessionLost。
  - [x] 逐项提交下第4项失败不回滚前3项，重试跳过已齐单元；daily_job 的采集 finally 在后续 AI 开始前释放锁，组合调用不重复获取同会话锁。
  - [x] 可信状态独立写已有 trade_status_daily，不依赖 qfq/不推进量化 ingest_state；None/不明截断不写，旧非空limit与qfq不被空值覆盖；bfq helper 只更新观测列。
  - [x] fake provider 验证空库指数自举；`--initialize-catalog` 初始化股票基础与 DC 字典/成员；`--initialize-catalog --stock-directory-only` 只更新股票生命周期目录、跳过已有 DC 成员刷新；两者均受公共锁保护，提交后失效覆盖，不执行基金/qfq/AI。
  - [x] `stock_basic` 显式查询 `L/D/P/G/UN × SSE/SZSE/BSE`，校验全部请求字段、失败全有/全无、null/空白交易所与 6000 行上限；只有字段完整的空帧才作为合法空分区；`UN` 入库归一到既有 CHAR(1) 的 `U`，G/U 生命周期按上市日期归入目标；以真实交易所后缀和生命周期日期覆盖 Provider 单测，并用隔离 PG 覆盖生命周期边界。
  - [x] 搜索全部行情/factor/sector upsert 调用点，对方案范围内写入者记录 guard/通知接入结果；旧CLI在Redis不可用时仍能持PG锁维护，db.instrument无backend反向依赖。
- **状态**：`已完成`（2026-09-24）。生命周期分区、字段/schema 完整性、空分区、截断及 CHAR(1) 归一通过独立 Code Review；154 项采集/Provider/隔离后端回归通过。生产目录重同步列为 T10 受控验收，不作为猜测生命周期的理由。

### T6 接入 Market Worker 与 Dispatcher

- **目标**：实现方案 §4.2/§4.3 的实际后台运行和恢复，将 T4 准入、T5 采集连成闭环。
- **涉及文件**：新增 `backend/workers/market_refresh.py`；修改 `backend/workers/dispatcher.py` 及必要装配；扩充 `backend/tests/integration/market_data/test_refresh_jobs.py`。
- **依赖**：T4、T5。
- **验收标准**：
  - [x] 隔离真实 Broker + fake provider 集成测试通过：专用单线程仅订阅 market-data，AI Worker保持default；actor只传job_id、max_retries=0；按六组真实覆盖核验终态，子项失败不因退出码0宣告成功。
  - [x] 验证 Dispatcher 启动/每60秒检查、每组最多5分钟覆盖重算、有限重试、指数→股票→板块投递顺序及分析调度异常隔离；关闭页面不影响任务推进。
  - [x] 故障注入覆盖发送前后崩溃、重复/丢失消息、父/子进程死亡、Redis失联/TTL过期、PG连接丢失和commit后通知失败；PG锁仍占用时不启动第二采集，恢复先查已有覆盖。
  - [x] 验证15秒token续期、180秒租约、90分钟子进程上限、95分钟actor上限及terminate10秒/kill-wait20秒回收；测试使用可注入短时钟/超时，不真实等待90分钟。退出无孤儿子进程，日志有统一job/resource/date/attempt，进度限频。
  - [x] 将 Windows `WinError 10013` 归类为 `SOURCE_NETWORK_ACCESS_DENIED`；Tushare 结构化接口保持 `None` 失败契约，由目录/行情 ingest 边界恢复 typed error；即使 API worker 在调用超时之后才抛出该错误也锁存并阻止后续 Provider 请求；刷新 Worker、旧增量和历史回填入口都立即传播错误、不继续换源或重试；状态附可操作错误，Redis 暂停自动准入，修复网络权限后显式手动重试解除；隔离 PG、Redis、Provider 与 legacy 入口回归验证。
- **状态**：`已完成`（2026-09-24）。本机网络拒绝在新旧采集入口均分类为 `SOURCE_NETWORK_ACCESS_DENIED`，错误不会再被吞掉、换源或消耗自动重试预算；超时后异步线程迟到的拒绝也会锁存并阻止后续 Provider 请求。独立 Code Review PASS，154 项定向回归通过。OS 出站权限修复与实际板块采样属于 T10 运行验收。

### T7 实现更新 API、行情查询语义及客户端契约

- **目标**：落实方案 §4.4，提供三个刷新端点，修正概念日期与因子读取行为，并生成匹配的 TypeScript 客户端。
- **涉及文件**：新增 `backend/api/schemas/market_refresh.py`、`backend/api/routers/market_refresh.py`、`backend/tests/contract/api/test_market_refresh.py`；修改 `backend/api/schemas/market.py`、`backend/api/routers/market_data.py`、`backend/modules/market_data/application/service.py`、`backend/main.py`、`backend/openapi/openapi.v1.json`、`frontend/src/api/generated/` 及既有 market_data/factor测试。
- **依赖**：T2、T3、T4、T5；使用 fake publisher 验收，不等待 T6 的真实 Worker。
- **验收标准**：
  - [x] 执行 `python -m pytest backend/tests/contract/api/test_market_refresh.py backend/tests/contract/api/test_market_data.py backend/tests/unit/market_data/test_factor_cover.py`：GET零provider/投递、POST200/202/重复job/混合decision、422/404/Redis503、envelope/problem和双eligibility通过；async路由沿用既有线程池执行阻塞SQL。
  - [x] tree/hot 同步迁移 latest/显式历史 as_of，删除无用途from/to及全部调用桥接；响应标注真实as_of、date_mode、coverage/heat_window_rows，历史缺数据不暗换日期，指数/板块bars的from/to保留。
  - [x] 覆盖全字典boards、top N概念截断前成员关系计数：跨概念重复股票分别计、单概念去重、>100未显示成员仍进分母、hot members=null。保留heat_v1的1/2–10/>10行分支和既有单行降级测试；两模式均过滤无当日行情候选。
  - [x] stocks bars factor_policy默认ensure保兼容，cache_only零源调用；ensure先公共PG锁再Redis15分钟冷却，Redis失联/忙/源失败均不重复拉取；bfq写入保留qfq。100次cache_only与同区间冷却内ensure验证调用上界。
  - [x] 执行 `python -m backend.scripts.export_openapi`、`pnpm --dir frontend generate:api`、`pnpm --dir frontend typecheck`；全文搜索并迁移tuple解包/失败返回/字面URL/生成Service调用点。后续前端体验逻辑归T8；本任务确保接口更改后的现有消费者可编译。
- **状态**：`已完成`（2026-09-23）。

### T8 实现前端自动检查、状态展示与缓存刷新

- **目标**：实现方案 §4.5 和 §4.4 的个股交互补因子，使用户能看见实际数据日期与补齐进度。
- **涉及文件**：新增 `frontend/src/modules/market/pages/refreshQueries.ts`、`MarketRefreshStatus.tsx` 及测试；修改同目录 `MarketOverviewPage.tsx`、`HotConceptsPanel.tsx`、`MarketIndicesPanel.tsx`、`TrendComparisonPanel.tsx`、`queries.ts`，`frontend/src/api/queryKeys.ts` 和 `frontend/src/modules/market/components/KLineDialog.tsx`；新增 `frontend/e2e/market-refresh.spec.ts`。
- **依赖**：T7；本任务通过 generated Service mocks 验收，不依赖已部署 Worker。
- **验收标准**：
  - [x] 执行 `pnpm --dir frontend test -- src/modules/market` 与 `pnpm --dir frontend typecheck`：fake timer验证首次/恢复可见检查、30秒聚焦合并、空闲5分钟/活跃任务10秒轮询、后台暂停、卸载清理、StrictMode去重和网络失败60秒后先GET；状态组件覆盖终态显示核验数、重试态显示已处理数、异常核验计数回退。本轮市场组件及页面测试79项、前端两份 tsconfig 检查通过。
  - [x] 仅auto eligibility允许时提交auto，mutation不自动重试；手动按钮独立使用manual eligibility；Redis离线/冷却/任务过期/部分完成有准确提示且保留已有图表。
  - [x] 验证按资源精准失效、同版本去重、最多10秒一轮；首次状态与已有缓存比较，目标日/freshness变化无版本变化仍刷新；5分钟只refetch活跃行情兜底，不加载未打开历史图。
  - [x] latest默认不传today，历史选择/缩放/画线/展开不因更新重置；指数和趋势在跨市场日期或恢复可见后更新范围，不被首次常量冻结。
  - [x] 所有stock queryFn走cache_only；仅用户打开K线/扩展区间显式ensure并回填相应缓存。模拟100次自动刷新无额外源请求，重复render不触发ensure。
  - [x] 执行 `pnpm --dir frontend e2e -- market-refresh.spec.ts`：可控API演示旧数据→触发→部分→完整，刷新页面复用任务，失败保留图、离线提示及历史选择保持；2026-09-23 在本机 Edge 重跑2项通过。
- **状态**：`已完成`（2026-09-23）。

### T9 完成本地及容器启停装配

- **目标**：实现方案 §4.6 的完整运行入口和连接一致性，确保新流程能在本地与容器实际启动和停止。
- **涉及文件**：修改 `backend/cli.py`、`pyproject.toml`、`uv.lock`、`run.sh`、`docker-compose.yml`、`docker/Dockerfile.worker`（依赖需要时）；更新运行说明及测试/验收记录。
- **依赖**：T6、T7。
- **验收标准**：
  - [x] 隔离环境运行新增 `liveprofit-market-worker` 入口，验证队列和心跳；本地start_platform/stop包含新PID、日志与子进程回收，启动失败不报告成功；start_all不再无条件运行全量增量，手动ingest-market仍可用且受公共锁。（2026-10-02 实测：api/market-worker/dispatcher 启停干净、无孤儿、数据保留；AI worker 因存量分析重试消息未启动，其脚本条目以代码核验 + 同一 start_daemon 佐证，证据见 log.md 与 result.md）
  - [x] Windows依赖安装及Linux worker镜像构建/导入通过；`docker compose --profile app config --quiet` 校验服务配置。在隔离容器中验证API/Dispatcher/market-worker实际连同一PG库，状态/Broker同Redis URL/DB，市场连接不回落localhost，不打印密钥。（2026-10-02 容器内实测：API/Dispatcher/market-worker 以 HEAD 镜像运行，同一 PG/Redis（host=postgres/redis，非 localhost），六组覆盖 FRESH 与宿主一致；旧 9-24 镜像曾产生错误摘要，重建后消除；日志仅掩码占位符）
  - [x] 总开关关闭停止准入；仅auto关闭时页面auto/定时均不入队而手动仍受约束可用；离线只保留一份当前任务，不持续增消息。（T1 配置单测 + T4 双 eligibility/离线零投递 + T7 契约测试证据）
  - [x] 演练部署顺序与回滚：旧采集自然结束后切换；停止新准入/Worker保留已提交行情，无需DDL，定向清理不影响AI队列；stop后无市场采集孤儿进程。（运行手册 attachments/deploy-rollback-runbook.md；停止后数据保留、无孤儿、Redis 前缀零重叠已实测）
- **状态**：`已完成`（2026-10-02）。本地启停验收与容器整栈验收（HEAD 镜像）均通过；验收容器已清理。

### T10 完成联调验收、代码审查及文档收尾

- **目标**：执行方案 §4.6 验收和 AGENTS.md 的实现后流程，将已实现事实沉淀并完成任务归档。
- **涉及文件**：前述单测/集成/契约/e2e测试及 `attachments/` 验收证据；更新项目 `README.md`、`docs/knowledge/backend/API契约.md`、`docs/knowledge/frontend/前端平台.md`、受影响的维护文档，以及本任务八文件。
- **依赖**：T8、T9（传递依赖 T1–T7）。
- **验收标准**：
  - [x] 执行方案 §4.6.3 的后端单元/采集/market集成/API契约、前端类型检查/market测试/e2e命令，逐项核对下方12条评审要求的实测证据；隔离fixture执行，不使用真实LLM。（2026-10-02 于 HEAD 经新 tests/ 布局复验，证据见 result.md；12 条 R1 映射见下表与 T1–T8 勾选项）
  - [x] 隔离库构造“最新9/21、目标9/22”，观察仅必要组入队、逐步显示真实入库数据；多页不重复任务、关页继续、睡眠恢复补缺。受控真实源验收另行记录调用范围/耗时/实际到数，不为演示删除生产数据。（组件证据合成：e2e 2 项覆盖页面 stale→queued→partial→complete/关页继续/选择保持；integration test_refresh_jobs 覆盖隔离库+真实 Broker 的组入队与终态；生产观察见 9-24 目标日 5568/5568 与 2026-10-02 US 10-01 自动补齐 SUCCEEDED）
  - [x] 按根 AGENTS.md 启动实现后的独立code review，修复发现并完成相关回归；这是代码审查，不重复启动已通过的方案评审。不能将方案PASS替代代码或功能验收。（2026-10-02 全任务独立审查 PASS：无 blocker/major；1 minor + 5 polish 全部修复并经 delta 复核；修复后 unit 88 / integration 53 / contract 35 复验通过）
  - [x] 验证本任务无PG建表/字段/迁移，无不相关改动被覆盖；确认issues.md的实施项已有证据或明确剩余限制，日历支持范围和源缓冲不作超出证据的承诺。（任务差异 dbfdc3f9..HEAD 中无本任务 DDL/迁移：atr_qfq/atr_bfq 列与迁移 0011–0019 均属并行量化任务；issues V1–V11 均有证据或明确剩余限制）
  - [x] 实现且审查通过后将真实架构变化合入knowledge，纠正旧“08:30兜底已接通”等描述；填写result/retrospective、同步10/10与README已完成，整体移至archive并修相对链接；按AGENTS仅显式暂存本任务路径再提交，不使用git add -A。（knowledge 的 API 契约与前端平台文档已在实施中同步刷新端点与状态栏事实；根 README 三处过时表述修正；result/retrospective 完成；文件夹已移至 archive 并校正链接）
- **状态**：`已完成`（2026-10-02）。HEAD 全量回归通过；最终 Code Review PASS；任务已归档。

## 评审要求与任务映射

此表保证既有修复落入实现验收，不新增评审轮次；详细断言以各任务及方案 §4.6.3 为准。

| 评审要求 | 主责任务 | 联合验收 |
|----------|----------|----------|
| R1-01 冷启动和目录初始化 | T3、T5 | T6、T10 |
| R1-02 停牌事实写入/读取、旧字段保护 | T3、T5 | T7、T10 |
| R1-03 全入口公共锁、断连停止与释放 | T5 | T6、T7、T10 |
| R1-04 有界投递和多Dispatcher CAS | T4 | T6、T10 |
| R1-05 自动只读、交互因子冷却与qfq保护 | T5、T7、T8 | T10 |
| R1-06 heat_v1三段降级兼容 | T7 | T8、T10 |
| R1-07 两组fixture停牌清理 | T1 | T3、T5、T7 |
| R1-08 Broker进程/namespace/连接隔离 | T1 | T4、T6、T10 |
| R1-09 长假当前目标计数不失效 | T4 | T10 |
| R1-10 自动/手动双eligibility | T4、T7 | T8、T10 |
| R1-11 目标日/窗口/成员计数 | T3、T7 | T8、T10 |
| R1-12 日历支持范围与越界降级 | T2 | T3、T9、T10 |

## 执行与维护规则

- 命令从仓库根目录、项目Python环境执行；当前Windows可将 `python` 替换为 `.venv/Scripts/python.exe`。新增测试建好后才执行。每个任务完成需勾选全部验收项并记录命令/结果或人工观察证据；环境缺失时写 `阻塞：具体原因`，不默认通过。
- 任务状态依次为待开始、进行中、已完成；完成关键步骤时同步任务总览、README进度和log。T10的收尾门槛满足前不把整个需求标为完成。
- 已评审的参数和语义以plan.md为准，避免在任务清单另起一套设计。清单随任务文件夹归档保留；当前已获用户实施授权；只有验收证据齐全的任务才标完成。
