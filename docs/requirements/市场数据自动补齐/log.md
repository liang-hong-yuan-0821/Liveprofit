# 工作日志

## 2026-09-24 目标日个股与板块更新恢复

- 按 L/D/P/G/UN × SSE/SZSE/BSE 只读拉取 `stock_basic`，合并结果 5907 条，`001246.SZ`、`301716.SZ`、`920201.BJ` 均不在完整目录。确认 DC 板块成分会提前包含尚未上市的 IPO 代码；修改 `CN_STOCK_DAILY` 目标集合为股票主目录，目录内缺上市状态/日期仍阻断，旧冻结任务中的目录外成分可核验退出。目录同步后目标日从 5571 调整为 5568。
- 上游目标日 `daily` 有 5556 条；按冻结缺口补采 9 月 22/23 日，余 25 个代码日期无行情。官方 `suspend_d` 文档要求 `trade_date`；代理实测旧 `suspend_date` 被忽略返回 5000 条历史且日期 null，新 `trade_date` 返回 15/13 条含可验证日期及 S/R。代理忽略 `suspend_type=S` 筛选，故本地只将 S 标停牌、R 不豁免。写入可信状态后目标日 5556+12=5568/5568，三日窗口 16700/16700，均 FRESH。
- `dc_daily(trade_date=20260923)` 实测 1030 行，独缺 BK0165.DC；按代码、改板块类别和去类别查询也缺。`dc_index` 有快照但无 OHLC。东财原始 K 线返回该板块 9 月 23 日完整记录，9 月 22 日开高低收与库内一致；加入只在定向目标缺有效行时触发的校验兜底，经 PG 采集锁写入后板块目标日 1031/1031 FRESH。
- 前端在覆盖已 FRESH 时不再展示旧的 PARTIAL/FAILED 任务文案，`HISTORY_GAP` 改为中文说明。历史核验仍发现股票 2025 年起 5481 个代码日期缺口、BK1675.DC 6 个较早缺日；东财原始 K 线没有 8 月 3–6 日的该板块 OHLC，不能据涨跌快照编造。8 月 12/27 日的定向历史补采遇东财 ConnectionError，未写入也未改变目标日结果。
- 两轮独立增量 Code Review：R1 发现目录重复代码静默保留、板块目标行数值无效时不触发兜底两项 major；均修复并补回归，R2 delta PASS。相关后端/采集/Provider 88 项与前端状态组件 5 项通过。API、Dispatcher、市场 Worker 重启后，真实 `GET /api/v1/market-data/refresh-status` 返回个股 5568/5568、板块 1031/1031、两组 FRESH，Worker 在线；未重启分析 Worker。

## 2026-09-24 根因修复：全生命周期目录与本机网络拒绝

- 核实 `AI/dataflows/providers/cn/tushare.py::get_stock_basic_df()` 过去没有传 `list_status`。Tushare 官方说明省略参数默认 `L`，支持 `L/D/P/G/UN`，单次最多 6000 行；此前 5568 行同步及定向代码查询因此不能证明全生命周期目录完整。官方事实见 [stock_basic 文档](https://tushare.pro/document/2?doc_id=25)；三个具体缺失代码是否属于 G/UN 尚未验证。
- 目录采集现逐状态、逐 SSE/SZSE/BSE 请求，响应状态/交易所不符、单项失败或命中 6000 上限时拒绝整批返回；无新增表/字段/迁移，`UN` 在 `_basic_to_instrument` 中归一为 `U` 写入既有 CHAR(1)。覆盖模型对 G/U 按上市日期排除上市前交易日；没有上市日期的明确 G/U 不再造成假缺口；上市日之后仍会要求真实日线。
- 将 Windows `WinError 10013` 识别为 `SOURCE_NETWORK_ACCESS_DENIED`，保留该类型穿过 Tushare Provider、板块/日线采集到 Worker；失败后立刻停止当前采集，不把本机 ACL 拒绝伪装成可退避的上游空结果。Redis 写入自动阻塞原因，面板给出修复 Worker 出站网络后手动重试的指引；显式手动重试可清除此临时阻塞。此代码不授予 Windows 或容器网络权限。
- 收尾时按 `BaseStockDataProvider` 契约收紧错误传递：Tushare 结构化接口继续返回 `None`，但在 Provider 内保留本机策略拒绝；目录、指数、个股和板块的 ingest 边界将其恢复成 typed error，确保异常不会被普通空结果吞掉。Provider 和隔离 PG/Redis 回归分别覆盖两端。
- 独立 Review 抓到旧 incremental/backfill 多处宽泛捕获会降级或重试同一本机拒绝；现补齐交易日历、目录、指数、行情、板块和因子边界的 typed rethrow，连接探测失败在入口也先检查 retained error。指数 bars 遇本机拒绝立即退出，不再尝试另一个外部源。
- 独立 Review 建议强化目录测试样本真实性；现改用沪/深/北对应后缀和各状态合理的上市/退市日期，并补测混合有效值与 null 交易所、请求状态错配及 6000 行边界。校验也明确拒绝任何 null/空白交易所，避免仅对非空值集合比对时漏收残缺响应。
- Code Review 另发现“无列空 DataFrame 被当作合法空分区”会破坏目录全量保证；现在必须先有完整请求字段结构，才能接受空分区，分别覆盖错误空帧与合法空帧。Future 超时后底层线程才抛 WSAEACCES 的极端路径也加入 worker 线程异常回调及锁存，之后的 API 调用会在 Provider 层被阻断。
- 验证：`.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=.pytest-tmp-root-codex tests/db/instrument/test_incremental.py tests/db/instrument/test_backfill.py tests/db/instrument/test_sectors.py tests/db/instrument/test_refresh_ingestion.py tests/db/instrument/test_refresh_ingestion_db.py tests/dataflows/providers/test_tushare_store_methods.py tests/dataflows/providers/test_tushare_connection.py tests/dataflows/providers/test_tushare_trade_status.py backend/tests/integration/market_data/test_refresh_coverage.py backend/tests/integration/market_data/test_refresh_jobs.py` → **154 passed**；改动模块 `compileall` 通过；`tests/utils/test_tushare_call_log.py::test_timeout_late_write_attribution` → **1 passed**。API概念树独立查询另有 **1 passed, 19 deselected**。Provider 用例隔离重跑为 **32 passed**。未重新调用生产 stock_basic、未请求/写入 `BK0165.DC`，因为此前一次性源请求授权均已使用/板块请求尚待明确授权。
- 独立 Code Review 对空分区 schema 和迟到的异步网络拒绝两项修复做 delta 复核，结论 **PASS**，无 blocker/major。T5/T6 关闭，整体任务进度更新至 8/10；T9 与 T10 仍未完成，不据此宣称生产行情已补齐。

## 2026-09-23 状态根因修复与实时复查

- 修复 `calendar_adapter` 把一次加载异常连同 UNAVAILABLE 结果永久缓存的问题：只缓存成功日历，失败不进入 LRU；同一进程后续查询可以恢复。韩国日历在旧 API 进程重启后实时恢复为 `FRESH`、目标 2026-09-23、1/1 覆盖。
- 覆盖模型核实 DC 板块成员含 81 个 B 股代码（`20xxxx.SZ` / `900xxx.SH`），Tushare A 股日线源不覆盖它们。目标目录与历史冻结任务核验均排除这两类代码，保留未知 A/BJ 生命周期为 fail-closed；隔离 PG 用例验证新目录及旧任务只剩真实 A 股缺口。
- 修复 Dispatcher 回收失联任务时未保存 `result.completed/total` 的终态显示问题；任务状态现在使用数据库核验的 total/completed/freshness，前端可显示核验进度。
- 为已存在板块目录而仅缺股票生命周期的场景增加 `--initialize-catalog --stock-directory-only`，由同一公共 IngestGuard 执行并在提交后仅失效股票覆盖；完整 `--initialize-catalog` 默认行为不变。
- 按用户授权执行一次 `stock_basic` 同步：收到 5568 行，只更新既有 `market.instrument` 与 `market.stock_info`，跳过 1031 个板块成员请求、价格日线、schema 变更。该同步补齐 `920025.BJ`，剩余 `001246.SZ`、`301716.SZ`、`920201.BJ` 仍无可靠生命周期记录，所以股票刷新仍按规则阻止；进一步外部查询需另行授权。
- 实时 API 当前 CN/US/KR 指数及指数因子均为 FRESH；股票仍因上述3个目录项 `CATALOG_INCOMPLETE`；板块仅缺 `BK0165.DC` 当日行情，保持 `RETRY_WAIT`，计划重试 `2026-09-23T15:45:48Z`。
- 验证：`test_refresh_policy.py` 32项，market coverage + jobs 26项，ingestion guard/DAO 12项，market_ingest CLI 8项通过（共78项）；独立增量 Code Review 两轮收敛 PASS。pytest 仅有仓库 `.pytest_cache` 写权限 warning。

## 2026-09-23 定向目录核验与自动重试观察

- 用户授权后，对 `001246.SZ`、`301716.SZ`、`920201.BJ` 执行一次定向 `stock_basic` 请求。接口返回0条匹配记录，没有写入任何数据库行；因此不推断上市状态，也不绕过目录完整性门控。
- 实时 API 再次确认 CN/US/KR 指数和 CN 指数因子均 `FRESH`，终态核验数分别为22/22、22/22、3/3、2/2；`CN_STOCK_DAILY` 仍因3个目录代码无元数据而 `CATALOG_INCOMPLETE`。
- 板块重试仍处 `RETRY_WAIT`，唯一缺口为 `BK0165.DC`，自动重试时间 `2026-09-23T15:45:48Z`（上海时间23:45:48）；继续等待现有队列，不手工清理冷却状态。

## 2026-09-23 板块重试网络故障

- 到期后 Dispatcher 执行了第3次自动尝试，job 到 `PARTIAL`，覆盖仍为1030/1031，唯一缺口 `BK0165.DC`；自动预算达到 `AUTO_RETRY_LIMIT`。
- 手动冷却结束后，通过 API 对 `CN_SECTOR_DAILY` 提交一次重试。job 在一次单元处理后 `FAILED`，缺口未变；Worker 日志显示 `ts.gyzcloud.top` 出站连接报 Windows `WinError 10013`，说明请求未成功发出，不能据此断言上游无数据，PG 无新行。
- 手动冷却到 `2026-09-23T15:56:40Z`。已向用户请求一次精确授权：只请求 `BK0165.DC` 的 `2026-09-23` `dc_daily` 并在公共采集锁内写入命中行；不请求其他板块或行情。等待回复，不重复提交。
- 修正前端 `CATALOG_INCOMPLETE` 文案：不再误导用户“只需运行目录初始化”，改为说明上游目录未返回全部成分的上市状态/日期，系统不会猜测是否应有日线。新增组件用例；市场目录 80 项 Vitest、两套 TypeScript 配置检查通过。

## 2026-09-24 零点实时状态复核

- 当地日期跨至 2026-09-24 后，市场日历因发布缓冲仍将 CN/US/KR 目标定为 2026-09-23、美国目标 2026-09-22；CN 指数 11/11、CN 因子11/11、US指数3/3、KR指数1/1 均为 FRESH，任务终态数显示正确。
- CN_STOCK_DAILY 当前 expected=5571、available=0，仍因目录中3个代码生命周期信息缺失而不准入；未运行全市场日线采集。
- CN_SECTOR_DAILY 当前1030/1031，仍缺 `BK0165.DC` 2026-09-23 行；自动预算已尽，手动资格恢复为 MISSING_DATA。此前手动 job 因 Worker 出站连接被 Windows 拒绝而失败，数据库未写入。单次定向外部补采请求等待用户授权。

## 2026-09-23 市场状态显示口径修正

- 对照状态面板输出与实现核对：目标日 `11/11` 是行情覆盖数；`RETRY_WAIT 2062/2062` 是已处理的冻结任务单元，不代表覆盖完整；`HISTORY_GAP` 标记自动三交易日窗口以外的历史缺口。韩国交易日历不可用时不猜目标日期；个股生命周期目录不完整时按设计阻止补齐。
- 确认成功终态 `0/22` 是前端展示错误：Worker 可在执行前复核发现数据已被其他入口补齐，跳过采集后以最终数据库核验成功结束，故 `processed` 仍为0而 `result.completed` 为22。现改为终态展示核验计数；活动/重试态明确标“已处理”，`RETRY_WAIT` 的时间明确标“自动重试时间”。
- 增量代码审查发现 `result.total` 缺少运行时计数验证；已增加非负整数与完成数不超过总数校验，并在无效时回退处理计数。另一轮复审 PASS。市场 Vitest 79项、Edge 浏览器 E2E 2项通过，前端 `tsconfig.json` 与 `tsconfig.node.json` 检查通过；Playwright 自带 Chromium 缺失，改用已安装 Edge 执行。
- 当前环境对 `127.0.0.1:3000` 连接被拒绝，因此完成的是代码契约与组件验证，未能从正在运行的本地 API 读取用户所见的实时状态。

## 2026-09-23 存储清理与容器恢复

- 用户授权删除 `D:\BaiduNetdiskDownload` 下 Windows 自动编号副本。按同目录去掉末尾 `(数字)` 后存在原文件的标准，删除148个文件，释放16.816 GB；其他下载文件未动。
- Docker Desktop 恢复运行，PostgreSQL/Redis 等既有容器 healthy。Worker Linux 镜像完整构建、导出、导入成功，179个锁定依赖安装通过；镜像约8.96 GB。`docker compose --profile app config --quiet` 和 `liveprofit-market-worker --help` 通过。
- 使用初始为空的 Redis DB15、独立 key prefix 与 Broker namespace 启动监听 `market-data` 队列的临时 Worker；观察到专属 Worker key 和 Dramatiq Broker heartbeat，未投递任务/采集行情。停止容器并删除唯一残留 Broker heartbeat，DB15 回到空状态。
- 清理两份本任务安装步骤的 BuildKit 缓存，各8.776 GB；保留验证镜像、现有容器和PG/Redis数据卷。Docker 内部缓存已降至约63 MB，但宿主 D 盘仅余1.54 GB，因为 `docker_data.vhdx` 仍37.47 GB。
- 仅检测到 `docker-desktop` WSL 发行版运行。Windows 返回 DiskPart 需要提升权限；未压缩/移动/删除 VHDX，Docker Desktop 保持运行。后续需管理员权限停止 Docker/WSL 并 compact VHDX，之后继续 API/Dispatcher/Worker 整栈和本地启停验收。

## 2026-09-23 实现与联调进展

- Docker Hub 认证超时通过 Google `mirror.gcr.io` 拉取缓存镜像绕过；首次构建曾因 D 盘满在镜像导出时报只读文件系统并导致 Docker API 暂时不可用。删除下载目录内编号副本后 Desktop 恢复；随后完整镜像及隔离 Worker heartbeat 验收通过。宿主盘 VHDX 仍待管理员压缩，详见本节新记录与 issues V8。
- T6 后续补测：独立 market-data Broker 真实消费成功，发布端不再重配置 Dispatcher 的分析 Broker；调度资源顺序、5 分钟重算、发送结果不确定时保留预留、Redis 租约丢失后 PG 核验均通过。T6 标为已完成，当前 8/10；T9/T10 仍待容器及最终收尾。
- T2/T4/T5/T7/T8 验收通过，合计 7/10：市场后端定向回归 148 项、采集/provider 51 项，前端 76 项、typecheck 与 Edge 浏览器 E2E 2 项通过。测试只使用隔离 PG/Redis、假源和可控 API；未进行全市场采集。
- T6 真实 Windows spawn/公共 PG 锁/超时回收以及隔离 Redis Broker 单队列消费已验证。子进程提交 PG 后未发送结果的回归用例通过；继续核对调度与故障覆盖后才能标整项完成。
- 按 AGENTS.md 启动独立只读 Code Review；R1 发现股票 OHLC/停牌跳过口径、子进程退出误报、未知生命周期目录无效重试及 window_sessions 未接线。修复后 R2 delta 审查通过，无新增 finding。
- T9 本地 Python editable 安装、`liveprofit-market-worker --help`、uv 锁文件离线校验、Git Bash 与 PowerShell 脚本语法、compose 配置通过。Docker Hub 认证端点连接超时，Linux 镜像与整栈容器连接暂不能验收，任务保持阻塞，不提前归档或提交。

## 2026-09-23 首批实现验证

- T1配置与隔离设施完成：配置/安全边界24项单测、真实Broker命名空间和停牌隔离11项通过；T3覆盖读模型10项通过，均使用明确测试库。
- 日期策略31项、Redis原子状态10项、新刷新API契约8项通过；T2实际源发布时间仍待采样，T4/T6继续故障恢复验证。
- Worker真实spawn测试发现Windows管道关闭后poll抛BrokenPipeError，会覆盖已收到成功结果；修正关闭分支后3项进程/锁/超时测试通过。
- OpenAPI已重导出并生成客户端，前端按新契约接入中。Linux镜像构建遇DockerHub连接超时，尚未通过，compose/脚本/锁文件校验已通过；不把此环境限制当功能验收完成。

## 2026-09-23 开始实施

- 用户授权根据tasks实施；启动配置/测试隔离、日期覆盖与采集三条实现线，主会话实现Redis准入与后台/API集成。
- 保存现有工作区源文件基线到var隔离目录，保留用户并发改动；不新增PG表/字段/迁移。

## 2026-09-23 任务分解完成

- 用户明确“好，生成 tasks 吧”，确认进入任务分解；按模板将已评审方案拆成 T1–T10，全部待开始、进度0/10。
- 每项列明目标、涉及文件、依赖及可执行验收；测试隔离前置，日历/覆盖/准入与采集可按依赖并行，之后接Worker/API、前端/部署及综合验收。
- 将R1的12项修复逐一映射到实施和验收任务；另由独立代理只读提取覆盖清单协助核对，未启动额外方案评审。
- 同步README、plan状态及result/decisions；本轮仅文档，无业务实现、依赖安装、采集或测试执行。
- 文档自检通过：10个任务、47条待验收项、12条评审映射；依赖无环，任务总览与状态一致，相对链接及Markdown围栏无错误。

## 2026-09-23 正式评审收尾

- R2 新独立代理确认 12 项闭环，PASS，十维均10，均分轨迹6.6→10.0；无遗留问题，按规则两轮结束。
- 主会话完成数值、字段表述、章节编号、12项验收映射、Markdown链接/围栏及JSON示例自检。
- 状态改为待确认；未拆实施任务、未编写业务功能、未执行真实采集。依赖POC的已验证范围与实施期待验证项分别保留。

## 2026-09-23 R1 修订与 R2

- R1 独立全量评审 FAIL，9 major + 3 minor，十维均分 6.6；全部问题与修复映射写入 attachments/review.md。
- 修订冷启动、可信停牌落现有表、全部公开采集入口锁、断连停止、有界投递、交互因子只读刷新、heat_v1 兼容、测试隔离、长假预算、双模式 eligibility、覆盖计数及日历支持边界。
- 日历库在 var 隔离安装并复现 POC：CN 2026 与缓存一致，2027 不支持；US/KR 2026/2027 可构造。未修改运行依赖、未调用真实行情、未读写真实 Redis/PG。
- 已启动另一独立只读代理 market_refresh_r2 进行 delta 核验，未开始业务实现。

## 2026-09-23 正式评审启动

- 用户明确要求评审，并将项目约定改名为 AGENTS.md；已重新读取评审规则。
- 启动独立只读代理 R1，按十维度全量评审；主会话并行核验第三方日历能力。
- AGENTS.md 指定 Claude，但本环境没有该类型代理；已向用户说明采用可用独立代理，仍每轮换新代理、保持 R1 全量/R2 delta 与评分门槛，不冒称 Claude 评审。

## 2026-09-23 存储方案修订

- 用户明确非必要不增表；重新评估，刷新任务是可由真实行情重建的运行状态，无需长期业务审计。
- 删除方案中的 PG 状态表、任务表、字段与迁移设计；复用平台 Redis 和已有 Dramatiq Broker，新增专用消费队列，公共 PG session 锁不依赖表。
- 同步修改准入、预算、队列补发、状态过期、前端变更检测及测试隔离；明确 PG/Redis 不具备跨库原子提交，不宣称 Redis 数据丢失后仍保有全部次数和任务历史。
- 将用户存储原则记入根目录 agent.md；仅修改方案/约定文档，未修改业务代码、运行服务或实际 Redis/PG 数据。修订稿尚未正式评审。

## 2026-09-23

- 用户确认自动补齐方向，要求开始编写技术方案。
- 核查代码：现有市场日历仅 CN；freshness 使用 UTC date；概念 tree 将 from 作为 as_of；采集摘要退出码不能证明数据完整；前端未监听数据更新。
- 核查接入：Dispatcher 是现有调度者；API 不注册定时器；AI Worker 单线程；market_data PG 集成测试已有独立 env fixture。
- 核查外部依赖：阅读 exchange_calendars 官方用法、注册表与韩国日历源码；本地未安装，运行与年份覆盖作为实施前 POC 保留。
- 建立八文件任务骨架，完成技术初稿及主会话一致性自检。无业务代码、依赖、数据库或进程变更。
- 自检修正：统一 state→job 行锁顺序；补齐 market_date/next_ready_at 响应字段；覆盖首次挂载旧缓存及跨发布时间但 revision 未变的刷新场景；明确中立采集包依赖方向。八文件相对链接与 Markdown 围栏检查通过。
