# 最终产出与结论

<a id="checkpoint-3ar2-portfolio-unbound-db-20261002"></a>

## 2026-10-02 3ar-2组合未绑定BUY批量DB捕获/封口（实现检查点）

**范围**：新增0054、`application/lifecycle_portfolio_command.py`及集中目录`test_lifecycle_portfolio_command.py`，模型同步事务检查部分索引。沿用原始`PORTFOLIO_DRAWDOWN`规范请求（schema/kind/组合/估值日/请求键），DB独立选择`portfolio-unbound-drawdown:v1`。仅支持组合没有任何生命周期行、全部订单无lifecycle/intent绑定且已有最新PAUSE的情况；空候选拒绝。未接生产API，未建立新暂停、退出目标或来源认证，未支持多生命周期步骤。

**持久语义**：组合→全部订单排序锁保持到caller结束，最新风险事件须为PAUSE且事实日不晚于估值日。DB选入PROPOSED/EXECUTING/PARTIALLY_FILLED/RECONCILIATION_REQUIRED中尚未足额的全部BUY，冻结原revision；未成交PROPOSED→SUPERSEDED，其余→RECONCILIATION_REQUIRED，已待对账保存NO_STATE_CHANGE。共享暂停精确原像恰一，每订单结果恰一；真实UPDATE触发器捕获每次OLD/NEW，按命令全局change_seq排列，不只比较最终状态。逐单核原文、物理类型、revision链、允许字段、摘要和实际效果，命令全集核漏结果/额外结果及变化成员归属；入头时独立重算候选全集，不接受客户端清单。UTC/ISO固定序列化。

**事务与权限**：stage无自动提交/回滚，拒脏Session、已flush/rawSQL分配XID及driver AUTOCOMMIT；入口限定未修改READ COMMITTED事务。立即检查及递延提交检查并存，清空GUC或提前SET CONSTRAINTS后仍拒同事务额外订单、受管状态、账户/风险事件/退出目标写入；跨事务复用context拒绝。SECURITY DEFINER固定search_path，PUBLIC执行撤销，GUC只用于关联，普通角色仅授受控入口执行权即可完成业务；即使误授来源INSERT也不能伪造审计。stage返回`execution_authorized=False/continuous_history_known=False`，没有账户或来源认证。

**DB隔离与执行许可**：先读具体新用例、复用的旧未绑定命令support函数、operation_db及平台迁移fixture。前者明确替换URL为随机`liveprofit_lc_history_0051_test_<uuid>`，校当前库名再升级/写入，finally删除专名库；平台回归显式使用`liveprofit_platform_test`，升级/降级只在该测试库。admin连接只创建/删除上述专名库与只读探测。只使用`--allow-db`，无真实LLM/toolkit、外部数据源或主库DDL/业务写入。

**最终有效检查**：36个不同组合命令用例 + 4个平台迁移用例，共40项，无失败/跳过；定向重跑不累计。

```powershell
.venv/Scripts/python.exe -m pytest tests/backend/quant_strategy/integration/test_lifecycle_portfolio_command.py --allow-db -q --tb=short --show-capture=no
# 基础/反例32 passed in 66.19s；随后新增四项按以下范围检查
.venv/Scripts/python.exe -m pytest tests/backend/quant_strategy/integration/test_lifecycle_portfolio_command.py --allow-db -q --tb=short --show-capture=no -k 'no_lifecycle_portfolio or new_key or saved_context or utc_images'
# 4 passed, 32 deselected in 8.17s
.venv/Scripts/python.exe -m pytest tests/backend/quant_strategy/integration/test_lifecycle_portfolio_command.py --allow-db -q --tb=short --show-capture=no -k downgrade
# 降级原像/再升级重放加强后1 passed, 35 deselected in 2.34s，不累计
.venv/Scripts/python.exe -m pytest tests/backend/migrations/integration/test_migrations.py --allow-db -q --tb=short --show-capture=no
# 4 passed in 6.74s；平台head建表/列/约束及base降级再升级
.venv/Scripts/python.exe -m ruff check backend/migrations/versions/0054_portfolio_unbound_drawdown_capture.py backend/modules/quant_strategy/application/lifecycle_portfolio_command.py tests/backend/quant_strategy/integration/test_lifecycle_portfolio_command.py --output-format concise
# All checks passed
.venv/Scripts/python.exe -m tests.index
.venv/Scripts/python.exe -m tests.index --check
```

**关键证据**：四种待完成BUY逐单结果/SELL不消费、caller提交观察隔离及全回滚、同键异估值冲突、两真实线程同键只写一次、真实A→B→A三次中间变化与跨订单全局序号；漏第二结果和异常quantity触发器使所有订单及命令回滚。伪造空/遗漏清单并重算摘要仍拒绝；NULL变化数、外订单结果归属及损坏原像即使重算摘要也拒绝。已写事务、坏原请求、无/已恢复/未来暂停、足额/终态BUY排除、无本组合生命周期但有外intent绑定拒绝。后继新键独立全集与NO_STATE_CHANGE，旧请求重放保留旧四结果；两时区重放原件稳定。同/跨事务封口和普通随机NOLOGIN角色证明边界，测试角色finally清理。旧0052/0053入口在0054下真实APPLIED均通过，0052 A→B→A/普通角色全检查在0054下复用support复验。0054降至0053及再升时命令/来源/变化逐行JSON原文完全一致，旧批结果再重放一致。

**失败与修正**：初轮触发guard在复合IF中访问其他表不存在的NEW.command_kind，改按表分支后读取；随后operations分支也提前独立拒绝，不依赖未定义字段错误。补反例时全表零命令断言误计0051合法基线，改只核PORTFOLIO_DRAWDOWN；RESUME fixture原INSERT列不符合实际schema，改显式列和独立审核占位事实（仅隔离反例，无真实恢复认证）。最终受影响范围已复验通过，未关闭保护触发器制造结果。

**独立复核**：按代码验收使用可用独立agent（环境无type:claude工具），首轮及增量静态复核均无待修代码finding；首轮所提事务前置、风险证据、排除边界、角色及重算摘要反例建议已补实际隔离证据。review只读，未替代DB验证。

**仍未通过**：3ar-2整项不验收，多生命周期DB步骤、阻塞步骤、跨步骤完整链和其他全组合命令仍待实施。建单/首填输入、删源消费、全部业务写者、容量/延迟预算及历史重建依3ar-3至6推进；0051至0054未部署主库，生产角色/来源账户认证和连续可得性未知。后续独立事务旧写者仍可改变当前投影，不据局部封口授连续认证。只记录实现检查点，不新增模块验收任务、不归档或提交整个未完成工作区。

<a id="checkpoint-3ar2-portfolio-scope-20261002"></a>

## 2026-10-02 3ar-2全组合回撤参与者选择（实现检查点）

**范围**：新增`lifecycle_portfolio_selection.py`及集中目录的`test_lifecycle_portfolio_selection.py`，不改变既有迁移、生产回撤或订单入口。原请求只含schema、kind、组合、估值日和请求键；活动集合、revision、账户值及预分配operation ID不参与请求身份。组合锁后独立生成每生命周期一步、每未绑定待完成BUY一个允许结果清单；无活动生命周期但有未绑定BUY仍有独立成员，空组合及仅非受管持仓明确`has_history_participants=False`，不能据空scope写LIVE命令。结果只为scope，不保存命令、不授执行或来源认证。

**全集与稳定性**：全部组合生命周期/订单（包括被排除的旧终态行）、固定止损/观察/意图、账户持仓按组合→全局策略/政策→生命周期/订单→子行排序锁到caller提交/回滚，防既有旧写者重新激活。选入全部未闭仓生命周期及闭仓但尚有活动BUY的生命周期；未绑定BUY含PROPOSED、EXECUTING、PARTIALLY_FILLED、RECONCILIATION_REQUIRED且未足额完成。已绑定活动SELL保留为固定管理成员；不存在的/跨组合的/证券不一致的订单、意图或持仓归属拒绝，反向外组合订单引用也拒绝，不临时乱序取第二组合锁。锁后ORM刷新、库存前后重查、READ COMMITTED及XID前后门禁防已flush/rawSQL/先锁和driver AUTOCOMMIT。原行像直接用SQL原文，UTC/ISO序列化，不用Decimal解释重造原件。

**执行许可核查**：先读随机`operation_db`及固定`env`fixture与具体断言。随机库由`liveprofit_lc_history_0051_test_<uuid>`显式URL配置，升级仅随机库；关联回撤使用`liveprofit_quant_strategy_test`且模块串行重建/迁移/清理。admin连接只执行专名测试库CREATE/DROP及只读探测，不对主库执行DDL/业务写入。读取根测试真实依赖与动态fixture门禁，只使用`--allow-db`，未调用真实LLM/toolkit或外部数据源。

**最终有效命令及结果**（不同范围合计64项，重跑不累计）：

```powershell
.venv/Scripts/python.exe -m pytest tests/backend/quant_strategy/integration/test_lifecycle_portfolio_selection.py --allow-db -q --tb=short
# 36 passed in 62.38s；随机隔离库，包含普通NOLOGIN角色及清理
.venv/Scripts/python.exe -m pytest tests/backend/quant_strategy/integration/test_lifecycle_command_selection.py tests/backend/quant_strategy/unit/test_lifecycle_command_request.py tests/backend/quant_strategy/integration/test_portfolio_drawdown_actions.py --allow-db -q --tb=short
# 28 passed in 23.71s；现有原请求、单订单选择与回撤/结算/恢复链路
.venv/Scripts/python.exe -m ruff check backend/modules/quant_strategy/application/lifecycle_portfolio_selection.py tests/backend/quant_strategy/integration/test_lifecycle_portfolio_selection.py --output-format concise
# All checks passed
.venv/Scripts/python.exe -m tests.index
.venv/Scripts/python.exe -m tests.index --check
```

**测试边界**：真实第二连接UPDATE验证七类已有行及被排除订单锁保持，父FK阻断新未绑定订单；真实owner变化、无历史引用订单删除和无受管引用持仓删除触及库存重查整事务重试。原角色只有SELECT与取锁所需UPDATE(id)，成功选择后数据库拒审计INSERT；随机角色最终计数0。请求重复规范字节一致，不冒充持久幂等；业务重放/不可变封口沿用既有窄命令证据，未据此验收全组合writer。

**检查中修正**：本机初始PG未监听且Docker未启动，定位到fixture连接等待后终止本轮等待进程，启动已安装Docker及其既有PG/Redis服务，再执行隔离检查。锁反例原现金变化先违反cash_bounds，改为有效version变化；旧未绑定单和生命周期归属修改先违反历史保全FK，分别改为升级后无历史引用订单及新外组合生命周期错误引用，不关闭触发器制造通过。

**独立复核**：按代码验收要求使用可用独立agent（未提供type:claude工具）做本轮增量静态复核；无实现逻辑finding，提出反向外组合引用及闭仓排除两项minor测试建议，补齐后delta无待修finding。review不代替DB结果，不另开辅助模块验收任务。

**未通过/未执行项**：3ar-2整项仍未验收。DB尚未独立消费本selector契约；其他全组合命令、多步骤/A→B→A全组合封口、缺参与者/漏步骤故障矩阵未实施。锁定所有存量行的容量与延迟必须纳入3ar-6实测，尚未以数值预算验收；主库未迁移/接线，全部写者及来源认证依[tasks统一门禁](tasks.md#acceptance-gates)。不归档、不提交含其他并发改动的工作区。

## 2026-09-30 T4活动持仓逐日政策重放3b（纯软件增量PASS）

纯函数从首笔成交初态起，要求逐日事实与声明的独立开市日历一一对应，显式携带日事实修订、实际持仓和已完成意图的来源绑定；复用既有`evaluate_lifecycle_day`推演目标、期望计数与移动止损。停牌日保持旧目标和止损且不增加有效观察日；缺交易日、行情、ATR初始状态、意图完成绑定、公司行动换基或清仓后零持仓终态均`UNKNOWN`。输入来源只是声明，结果标`PROVISIONAL`，不接数据库或生产交易。

验收：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_lifecycle_policy_replay.py -q -x --tb=short -p no:cacheprovider`，10 passed，纯单测无DB/真实LLM；覆盖停牌后的下一有效确认日、完成意图绑定、止损、缺日历/行情、ATR缺seed、畸形日期与布尔、公司行动/零仓、正向TREND_3ATR+MA5期望/高水位、拒中途期望快照。独立Code Review发现ATR seed缺失、畸形日期/布尔/事实崩溃、公司行动/零仓范围、管理政策正向验收及中途seed计数等问题，逐项修复后delta PASS，无剩余blocker/major/minor。本子项未产持久化投影差异报告，T4第3步尚未整体验收。

## 2026-09-30 T4离线数量/成本会计重放3a（纯软件增量PASS）

纯函数接收已由上游解析撤销/更正后的有效成交集合、显式基线和拆股事件，按中国实际执行时刻计算持仓数量、含费成本、平均成本和已实现盈亏。缺费用、执行时刻重叠、基线来源、超持仓卖出及零碎股权均返回`UNKNOWN`，不生成部分余额；红利/配股/现金替代须另建事件契约，不当拆股计算。固定Decimal精度/舍入/trap防外部上下文改变结果；极端有限数值也返回未知。来源引用是调用方声明，纯计算结果不认证券商报告、公司行动或成本，也不授生产投影/订单权限。

验收：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_lifecycle_accounting_replay.py -q -x --tb=short -p no:cacheprovider`，6 passed，纯单测无DB/真实LLM。包括首笔撤销/更正后有效集合、乱序到达、拆股/零碎权利、费用未知、同刻排序歧义、超持仓SELL、错误执行日期、外部Decimal上下文与极端有限值。独立Code Review初轮两项major（极端Decimal溢出及外部上下文改变成本/分拆判定）和一项minor（更正链解析契约）均已修复，delta PASS。尚缺逐日策略状态/日历/公司行动全类型、持久化事实映射和差异报告；T4第3步整体未验收。

## 2026-09-30 T4日事实输入更正提案2b（增量PASS）

0045新建不可变输入提案表，记录所属日事实/基修订、请求键、规范输入字节/摘要、修订原因及声明来源引用/摘要。组合→生命周期→日事实顺序加锁，同基修订唯一、同键同内容幂等、漂移拒绝；写入不改变current、生命周期、意图或订单。DB直写校验基修订仍为最新、内容确有语义变化、UTF8字节与JSONB相符且SHA256一致；服务/只读清单按Python规范编码回读，非规范直写标未知。来源引用与摘要仅为声明，不证明上游身份或历史可得性。0045降级先锁提案表再锁父表，并将所有提案导出JSONL及SHA256校验。

验收：`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_lifecycle_service.py::test_legacy_revision_rejects_persisted_initial_fill_anchor_before_projection backend/tests/integration/quant_strategy/test_lifecycle_service.py::test_active_intent_cross_day_reuse_and_daily_fact_freeze backend/tests/integration/quant_strategy/test_daily_fact_revision_migration.py backend/tests/integration/test_migrations.py::test_upgrade_creates_exactly_platform_tables -q -x --tb=short -p no:cacheprovider`，4 passed。隔离`liveprofit_quant_strategy_test`、`liveprofit_daily_fact_revision_test`、`liveprofit_platform_test`，无主库写入/真实LLM。迁移用例覆盖伪SHA、等价非规范字节伪变化、在途提案插入时降级等待并导出2条及SHA校验；日事实用例覆盖幂等/漂移/同基冲突和current不变。独立Code Review初轮指出伪摘要直写与降级锁序，修复后发现等价JSON字节可绕“有变化”判断，按JSONB语义修复并补反例后delta PASS，无剩余blocker/major/minor。

未通过/后续门禁：来源真实性、证券实际时点、成本与公司行动、完整状态重放及原子current切换均未验；T4整体与生产成交入口保持关闭。

## 2026-09-30 T4日事实本地修订写史2a（增量PASS）

范围：0044新增不可变完整快照链，保留`position_daily_facts`唯一current行和稳定ID；DB触发器捕获新增及实质更新，校验前驱连续/身份/父行内容并拒直接伪链。迁移前旧行标`MIGRATED`，不宣称恢复迁移前历史；只读事实清单核链、最新快照和current一致性。规划JSON两处嵌套原地修改改为整体赋值。降级先自动导出修订JSONL并校验行数、SHA256及文件回读，无绝对导出路径时拒绝删除历史。

验收命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_lifecycle_service.py::test_legacy_revision_rejects_persisted_initial_fill_anchor_before_projection backend/tests/integration/quant_strategy/test_lifecycle_service.py::test_active_intent_cross_day_reuse_and_daily_fact_freeze backend/tests/integration/test_migrations.py::test_upgrade_creates_exactly_platform_tables -q -x --tb=short -p no:cacheprovider`，3 passed；另`test_daily_fact_revision_migration.py` 1 passed，其中并发写入期间降级先等锁、最终导出3条修订且SHA核验通过。仅操作隔离`liveprofit_quant_strategy_test`、`liveprofit_platform_test`、`liveprofit_daily_fact_revision_test`，无主库写入/真实LLM。定向`test_typed_family_fill_daily_management_and_partial_exit` 3 passed；同次运行的`test_batch_completion.py::test_automatic_entries_rollback_recover_and_terminal_replay`在`order is None`失败，原因待系统性核查，单独登记，不计为本增量通过。

独立Code Review初轮指出伪链直写、无可达导出降级、验收用例缺口及源输入纠正入口，2a范围内前三项修复并经delta复核PASS；二次delta指出导出和写入竞态，先锁父表及修订表并补等待在途写入的反例后复核PASS，无剩余blocker/major/minor。源输入纠正入口明确留给2b，不将2a认作T4第二步整体完成。

未通过/后续门禁：原始输入更正提案及原因/来源、完整状态重放、受影响订单终态、交易日历和券商账户认证均未完成；2a只能证明本库未来current写史，不放行生命周期更正、研究历史可得性或生产成交投影。

## 最新增量验收：T3完整清单收集与普通首仓建单（2026-09-27）

**增量PASS，整体仍0/8。范围为给定完整扫描清单后的自动收集与首仓建单；清单注册、worker自动触发尚未接通。**

- EntryBatchService要求完整scan_attempts覆盖全部家族（含cash_only），每族唯一任务尝试；按task UUID共享锁并核验SUCCEEDED/当前attempt/日期/组合/冻结版本，再从DB取齐唯一目标信号。漏成员、源任务未完成、旧attempt或跨账户均拒绝。
- 组合锁内fresh资格/预算仲裁，族间按净期望下界与稳定策略ID，族内按score；账户持仓交生命周期管理，首仓由共享planner执行金额上限、原价区间、成本/现金/风险约束。当前仅CN_STOCK，ETF规则未接时不能套用股票规则。
- 抽取order_materialization供原execution与批次复用，保留source_signal_id与冻结首仓比例；首次真实fill仍初始化正确策略版本生命周期。首仓比例裁成0手时明确BUY_REJECTED_INITIAL_LOT，并从普通执行摘要扣除空订单。
- 0020新增quant_allocation_executions，一批一终态不可变消费收据，保存完整清单输入hash、实时仲裁FK、逐信号结果/订单ID；与订单在同一事务提交。重复/并发/撤单重试不重建，回滚不留半成品。
- **348 passed in 14.19s**，含12项新增首仓隔离PG及此前量化单元/生命周期/资格/共同批次/提交回归。独立R1发现同版本不同任务政策覆盖major，改为唯一task/attempt的清单自动收集并补反例；R2 PASS。未运行真实LLM，主库未部署0020。

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_entry_batch.py backend/tests/unit/quant_strategy tests/agents/position/test_position_planner.py backend/tests/integration/quant_strategy/test_lifecycle_service.py backend/tests/integration/quant_strategy/test_family_batch.py backend/tests/integration/quant_strategy/test_admission_service.py backend/tests/integration/quant_strategy/test_task_submission.py -q -x
```

后续需注册冻结清单、让worker在全部任务成功后触发，并统一调度首仓与延期加仓；不以本次消费API验收代替全自动生产链路。


## 前一增量验收：T3延期加仓与批次订单消费（2026-09-27）

**增量PASS：生命周期新增BUY可以等待共同批次后建单，整体仍0/8。完整扫描清单/首仓共同建单尚未接。**

- `process_day(defer_buy=True)`先协调旧意图/订单（过量可撤订单SUPERSEDED），再保存AWAITING_BATCH与完整规则决策，不抢先新增BUY；保护SELL及时走原执行约束。
- `execution.defer_new_risk`提供共同扫描模式接点，普通BUY被AWAITING_FAMILY_BATCH阻止；默认单策略路径保持原行为。自动调度调用方尚未传入此模式。
- `LifecycleBatchService.materialize`在组合/生命周期/日事实锁内校验冻结版本、日期、原价和状态，重新用完整成员做当前资格及预算仲裁；禁止复用已有消费投影。新订单经共享planner消费max_add_notional与原价入场区间（含滑点/tick后的成本检查）。
- 成功或拒绝均保存BATCH_COMPLETED；同日同批重放返回原order_id，撤单不重建、另一批次不能覆盖；消费审计/日规划/订单由调用事务原子提交。
- **336 passed in 12.94s**，无跳过；独立R2 PASS。新增11项隔离PG覆盖延期、预算、资格暂停/状态变更/目标已满足、回滚、并发消费、即时止损、旧单缩量与价格区间。R1两项major（旧单协调、区间漏消费）全部修复；未调用真实LLM，未部署主库。

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_lifecycle_service.py backend/tests/unit/quant_strategy tests/agents/position/test_position_planner.py backend/tests/integration/quant_strategy/test_family_batch.py backend/tests/integration/quant_strategy/test_admission_service.py backend/tests/integration/quant_strategy/test_task_submission.py -q -x
```

没有新增迁移，复用生命周期日规划字段；新消费服务已有真实PG订单验收，但完整生产扫描编排与首仓共同消费仍需接通，不称共同批次全链完成。完整扫描失败/取消/超时及owner独立行情窗口仍属于后续门禁。


## 前一增量验收：T3共同批次事务与审计（2026-09-27）

**增量PASS；整体仍0/8。完成持久化仲裁投影，尚未接生产扫描汇总或自动建单。**

- 新增`FamilyBatchService`及0019两表：`quant_allocation_batches`保存请求身份、账户/估值/归属/分配快照；`quant_allocation_members`以FK绑定版本和实际资格事件，保存完整目标。DB唯一键防重复，不可变触发器禁止改写历史。
- 完整冻结成员、显式cash_only、同一估值日期及当前CN决策日；任一家族缺资格则整批BLOCKED，不将预算分给剩余家族。现有持仓、未成交BUY剩余本金占容量，未知owner不能被自动认领，同票按验证下界排序。
- 任务提交锁序改为组合→版本，与生产规划一致，修复潜在反向等待。共同批次在组合锁内按UUID序共享锁成员版本，caller持有事务提交边界；幂等重放是历史结果，不等于新的执行授权。
- **325 passed in 8.75s**（无跳过），含共同批次、任务提交、资格/生命周期隔离PG及现有量化单元/规划器回归。独立R1发现非成员owner读取未来资格的major，补同一时点过滤及真实生命周期反例；R2 PASS。主库未部署0019，无真实LLM调用。

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_family_batch.py backend/tests/integration/quant_strategy/test_task_submission.py backend/tests/unit/quant_strategy tests/agents/position/test_position_planner.py backend/tests/integration/quant_strategy/test_lifecycle_service.py backend/tests/integration/quant_strategy/test_admission_service.py -q -x
```

下一主线：扫描完成汇总、生命周期加仓共同规划和最终订单原子落库。当前批次投影不创建订单、不预留资金；不能让后续消费者直接重放历史投影绕过实时资格检查。0019仅隔离`liveprofit_quant_strategy_test`验证。收益资格仍未向真实策略授予。


## 前一增量验收：T3生产资格门禁（2026-09-27）

**PASS；普通开仓/生命周期加仓资格复核及账户档位全链完成，整体仍0/8。**

- 0018增加可空`portfolios.risk_profile`，旧账户未选择；PATCH省略保留、null清空，支持保守/均衡/进取范围。三档数值风控尚待统一实现。
- 组合锁内按数据库当前时间读取资格，版本共享锁与暂停写入互斥；旧任务不能恢复历史许可。拒绝码、资格事件ID/修订/检查时点保存至报告或日规划，SELL保护保留。
- UI、API、任务快照、实时账户规划和报告贯通。独立review PASS，修复审查发现的实时账户报告时间错配及旧注释。
- 后端 **308 passed in 9.15s**；前端 **7 passed**；typecheck通过。DB仅使用`liveprofit_quant_strategy_test`和`liveprofit_workspace_test`，无真实LLM调用。

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy tests/agents/position/test_position_planner.py backend/tests/integration/quant_strategy/test_lifecycle_service.py backend/tests/integration/quant_strategy/test_admission_service.py backend/tests/integration/investment_workspace/test_watchlists_portfolios.py backend/tests/integration/quant_strategy/test_task_submission.py::test_happy_path_freezes_snapshot_with_source -q -x
# frontend目录
pnpm run typecheck
pnpm exec vitest run src/modules/watchlist/portfolios/PortfolioDrafts.test.tsx
```

主库未部署0018，正资格仅存在隔离测试fixture；选择档位不能替代T5真实验证授予。下一主线为多家族共同决策批次，存量建议订单后续执行资格复核另列待办。


## 前一增量验收：T3资格历史前置（2026-09-27）

**PASS，独立资格事件表及保守写入/时点读取；生产准入门禁、真实证据产出器与共同批次尚未接，整体仍0/8。**

- 迁移0017新增`strategy_admission_events`。资格独立于PUBLISHED，绑定冻结版本、资产范围、风险档位；范围内修订/幂等键唯一，数据库禁止UPDATE/DELETE。
- 普通写入仅允许EXPERIMENTAL/SUSPENDED/RETIRED，不提供正资格授予入口；版本锁串行追加，重复内容复用、冲突请求拒绝，退役不能重开，家族跨范围保持一致。
- 同范围读取严格按记录时间早于决策时点取最新修订；ADVISORY检查证据时间和有效期，过期/暂停不回退旧许可。正资格数据库字段检查仅防缺项，不替代真实历史/影子统计验证。
- **298 passed in 5.34s**，无跳过；含11项资格和22项生命周期隔离PG测试，另含已有量化单元/规划器回归。R1发现ORM/Alembic元数据遗漏minor，补齐约束/索引/显式导入并增加一致性测试，R2 PASS。

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_admission_service.py backend/tests/integration/quant_strategy/test_lifecycle_service.py backend/tests/unit/quant_strategy tests/agents/position/test_position_planner.py -q -ra
```

本次主库未执行0017，只在`liveprofit_quant_strategy_test`迁移验收；数据库中正资格仅为隔离测试夹具，未给任何实际策略授予使用资格。未调用真实LLM，改动空白检查通过。下一主线：生产入口按明确资产/档位读取资格并汇齐多家族共同决策；验证产出器需接T5真实证据后才可推进正状态。

## 前一增量验收：T3-ALLOC-01b 生产归属门禁（2026-09-27）

**PASS，仅生产归属门禁子项；共同决策批次与真实资格事实仍未接，整体仍0/8。**

- 组合锁内读取活动生命周期冻结版本、非零实际持仓及活跃未成交BUY来源；旧任务快照与信号版本交叉核对。无来源、版本冲突、证券不一致或非法版本均拒绝新增风险，拒绝原因保存在现有信号/生命周期规划结果。
- 普通扫描对已受生命周期管理的持仓不能重复加仓；生命周期必须使用自身冻结版本。最终规划锁内重新读取managed符号，覆盖扫描期间归属变化。其他扫描策略的脚本输出不会改写冻结owner目标。
- 无归属导入持仓不自动接管，已关闭生命周期不能认领余仓；未成交首仓来源保留至撤销/终止，活动生命周期即使归档策略也继续保留归属。该门禁不授予家族资格，不按扫描顺序认证优胜策略。
- **287 passed in 10.05s**，无跳过；含22项隔离PG生命周期用例，实际执行；独立Code Review PASS、无阻断findings。审查建议的双来源版本冲突已补回归，另含证券不一致和非法版本。改动空白检查通过。

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy tests/agents/position/test_position_planner.py backend/tests/integration/quant_strategy/test_lifecycle_service.py -q -ra
```

本次复用既有生命周期与订单持久事实，未新增迁移、未改主库、未调用真实LLM。下一主线：真实资格持久化及多家族共同批次。冻结owner独立历史窗口、同日跨扫描事实修订、实际成交偏离对账仍待实施；保护规则继续按原生命周期运行，不声称已解决这些后续事项。

## 前一增量验收：T3-ALLOC-01a（2026-09-27）

**PASS，仅纯仲裁内核与planner金额容量接口；生产共同决策批次未接入，整体仍0/8。**

- 获准家族等分资金预算，按分向下取整、余数留现金；冲突和候选不足不抬高其他权重。持仓与待成交买入同时占用家族/账户额度，拟卖出不提前释放资金。
- 冻结owner包含策略ID与版本；旧版本、已暂停/未获准家族的现有归属仍保留。未知导入持仓不自动接管，未成交首仓也占用归属。保护性最低目标覆盖新增风险，缺家族输入保留持仓，显式cash-only才产生退出目标。
- 冲突排序只使用决策前已完成验证的净期望下界，同分按稳定策略ID；研究冷启动显式采用预注册顺序，拒绝混用验证证据。排名输入来自可信宿主，本函数不授予策略资格。
- `plan_buy_target(max_notional=...)`把仲裁新增额度与现金、风险等容量取最小值，按规范化买价裁剪股数。2000元额度、10元原价、10.01元执行价时最多100股，不能用原价200股突破额度。
- **259 passed in 1.80s**，无跳过；独立Code Review PASS。初次新测试括号错误导致收集失败，已修复并完整复跑。改动空白检查通过；本次不涉及DB或真实LLM调用。

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy tests/agents/position/test_position_planner.py -q -k "not integration"
```

**下一主线01b**：真实资格事实和生产owner收集、共同批次仲裁及持久审计。当前生产生命周期和普通扫描尚未调用本仲裁内核，不能宣称多家族生产预算已经生效；不会用扫描先后代替收益验证排序。现存跨策略脚本影响持仓、冻结版本数据窗口、首仓待成交归属约束一并纳入01b；其他数据问题继续集中留在issues。

## 前一增量验收：T3-BUY-01b（2026-09-27）

**PASS。生命周期BUY生产调用已接共用规划；整体仍0/8，T3未整体验收。**

- 生命周期及普通信号规划在组合锁内读取最新资金、持仓与跨来源活跃订单；成交更新先取同一组合锁。锁后查询刷新已有ORM对象，防止旧对象覆盖已提交变更。
- 生命周期按目标差额及外部同票买单余量限量，落库含费用现金预留、行业桶、规范化价格与下一交易日。缺共同日估值、风险事实、上下文或有效RR时保留意图并拒绝买单。
- 日事实新增可空`planning_result`，记录账户上下文、规划通过/拒绝结果及订单ID；不修改输入哈希。同日重试使用原结果，不能借重试释放拒绝门禁。
- 验收 **252 passed in 4.23s**，无跳过；其中生命周期隔离PG16项实际执行。覆盖并发现金竞争、部分成交跨来源预留、拒绝重放、真实成交与锁后旧对象刷新。R1发现ORM刷新、共同日估值两项major，修复并添加回归，R2 PASS。收尾同时核验实时账户快照可直接JSON序列化，最终复跑252项通过；改动文件空白检查通过。

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy tests/agents/position/test_position_planner.py backend/tests/integration/quant_strategy/test_lifecycle_service.py backend/tests/integration/quant_strategy/test_template_loader_runner.py -q -ra
```

迁移0016仅在`liveprofit_quant_strategy_test`自动建库验收，尚未对主库执行；部署本版本前需升级迁移。未调用真实LLM或提交交易。下一主线为家族归属/预算仲裁，再推进证券规则和三档风险；实际费用/NAV账本、规则政策准入及研究认证仍是后续门禁。本次保守保留部分成交买单的整笔预计费用缓冲，不将未成交卖单收入用于买入。

## 前一增量验收：T3-BUY-01a（2026-09-27）

**PASS，仅共用规划前置增量。整体仍0/8，生命周期BUY事务接线尚未完成。**

- `plan_buy_target` 将 raw 目标买入交给既有 `PositionPlanner`；数量不超目标差额，按最终股数计算滑点和费用，保留真实交易日历、市场状态、现金/行业/开放风险及账户RR门禁。
- 未成交BUY的剩余毛额计入总仓位、单票、行业额度；不将未成交订单当作已持有资产重算NAV。调用方显式提供空预留集合时不恢复旧快照预留。
- 缺失或非有限的订单预留、负数量、现金预留不足以覆盖剩余毛额均阻止新增风险；适配器不猜测行情，不为无固定止盈政策编造目标价。
- 验收：定向54 passed；相关量化回归 **230 passed in 1.71s**，无跳过。独立只读review PASS，无findings，并复跑定向54项。

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_buy_target_planner.py backend/tests/unit/quant_strategy/test_portfolio_risk.py tests/agents/position/test_position_planner.py -q -k "not integration"
.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy tests/agents/position/test_position_planner.py -q -k "not integration"
```

本次用例均为内存夹具，未执行DB写入或真实LLM调用。测试数字为分别运行的覆盖范围，不能相加。

**下一主线01b**：接生命周期事务与宿主，验证当前账户事实、跨来源订单预留、锁顺序、拒绝原因留痕、同日幂等和部分成交。上述适配器尚无生产生命周期调用方，不能称BUY旁路已消除；家族预算/owner、证券规则及规则政策准入仍保留原门禁。

## 前一阶段综合验收：2026-09-27

**结论：已实现增量通过阶段验收；T2、T3均未整体验收，整体仍0/8。**

| 范围 | 结论 | 证据与边界 |
|---|---|---|
| 28单票/48组合候选入口、冻结身份与参数 | 阶段PASS | 76逐项调用、真实sandbox、篡改拒绝、周/月调仓及参数差异回归 |
| 旧模板冻结政策到首填/持仓/部分成交 | 阶段PASS | liveprofit_quant_strategy_test隔离PG用例实际执行，非跳过 |
| 四新族持仓纯规则 | 阶段PASS | 独立核验缺基准/ATR峰120→105退出，保护分别108/114，缺证据不推进有效日 |
| CN股票共享SELL gate | 阶段PASS | 普通/生命周期卖单共用、跨来源部分成交预留、同批600+400、真实假期和缺证据拒绝 |
| Windows sandbox运行 | 已验证 | POSIX进程组用例1项跳过；Linux资源回收仍待T8环境验收 |
| 四新族生产持仓消费/成交/冷却/换基/共同回放 | 未完成 | 纯规则入口不能替代T4持久化消费 |
| 生命周期BUY统一规划、家族预算/owner、证券规则、三档风险 | 未完成 | BUY仍有直接创建建议旁路；空止盈准入继续拒绝 |
| 收益认证、留出集及影子观察 | 未完成 | 未用本轮软件回归代替T1/T5/T8认证 |

实际执行命令：

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy backend/tests/unit/quant_research/test_trial_registry.py backend/tests/unit/quant_research/test_trial_executor.py backend/tests/unit/quant_research/test_trial_holdings.py tests/strategy_sandbox tests/agents/position/test_position_planner.py backend/tests/integration/quant_strategy/test_lifecycle_service.py backend/tests/integration/quant_strategy/test_template_loader_runner.py -q -ra
```

结果：**410 passed, 1 skipped in 7.51s**。跳过项：`tests/strategy_sandbox/test_runner.py:81`（POSIX process groups only）。测试未调用真实LLM；DB DDL/写入fixture明确指向liveprofit_quant_strategy_test。独立阶段审查无新增阻断缺陷，峰值修复及当前SELL链路PASS。

后续主线：T3-BUY-01统一新增风险规划 → 家族预算/owner与证券规则 → T4账本消费；数据治理按issues根因组保留，不恢复零散补采。


> **当前阶段**：实现中，整体验收0/8；T1数据更新和部分T2代码已实施。本文件记录阶段事实，未完成整体业务验收与长期观察。

## 验证结果

| 层面 | 命令/方式 | 结果 |
|---|---|---|
| 模板与目录 | 阅读requirements八份模板、骨架说明；以rg核对既有实现及测试路径 | 八文件已创建；此项不代表业务验证 |
| 文档静态核验 | `.venv/Scripts/python.exe -X utf8 -c` 执行只读断言，检查目录、章节、模块、链接、任务状态、代码围栏和空白 | PASS：8文件、5章、6模块及24子节、8项待开始任务、37个本地链接有效，无已勾选业务验收或残留模板占位符 |
| 文档交叉核验 | 独立代理只读核验模板与文件间状态；主会话核对验收命令及旧任务预算事实 | 已修正风险档名称、证券规则模型清单、请求次数归属和集成测试过滤；原N8尚未冻结预算保持待验收。未启动新一轮方案全量评审 |
| 会话草稿评审 | 历史R1全量审查、R2修复核验，转录见issues | R1 FAIL、7.0；R2 PASS、10.0。未对落盘文档另开全量审查 |
| 后端与前端验证 | 分阶段定向单测、隔离库测试 | 数据更新主回归133项通过；后续状态/基金61项、增量26项、纯夹具loader集成4项通过（相互有重复，不能相加为总测试数）；前端与全链路E2E尚未验收 |
| 历史收益研究 | 76组预注册、滚动样本外及最终留出 | 未执行；无已验证策略收益结论 |
| 长期模拟观察 | 交易型60日/30笔，配置型120日/6次调仓及风险门槛 | 未开始；不得以等待结束或软件上线替代通过证据 |

## Code Review

- **结论**：已进行上游数据更新阶段两轮只读Code Review，发现及修复记录见issues。后续基金历史请求预算/缓存、沪深与北交所源覆盖边界修复已定向验证；这不等于整个任务Review通过。
- **遗留**：T1仍有历史状态分歧、ETF历史规则及公司行为验证；T2部分实现，T3–T8未完成。详见[tasks.md](tasks.md)。

## 交付物

- 本任务八文件骨架及[plan.md](plan.md)方案、[tasks.md](tasks.md)八项任务分解。
- [issues.md](issues.md)保存会话审查来源、F1–F10修复对照及待验证缺口。
- [README.md](README.md)保存权威状态及原N7–N9承接映射。

## 后续结论记录方式

- **软件验收**：逐项记录真实执行命令、环境、结果和剩余项，不把拟新增测试或未执行命令记作通过。
- **历史研究**：记录数据集与研究版本、全部候选、筛选过程及通过/失败/证据不足；无候选通过也是有效研究结论。
- **模拟观察**：逐版本记录开始日期、已完成交易日/交易或调仓数、净收益及风险表现；证据不足保持观察，不提前归档整个任务。

## 上游数据更新阶段（2026-09-26）

| 范围 | 已核结果 | 证据 |
|---|---|---|
| 最近A股交易日 | 最后交易日9月24日；9月22–24日基金日线补2147/2133/2133行，配对复权缺口0；9月25–27日休市 | README及log |
| 2026基金技术因子 | 175日补348,480行；34项MA250源空值经完整上游历史匹配认证；按必需指标重跑待补日期0，核验请求36/64 | [全期复核](attachments/fund_factor_2026_pending_verification.json) |
| 2019股票 | 890,251个有效证券日：未解释日线缺0、复权缺0、状态缺0；10,360技术因子空值为此前已核暖机 | [年审](attachments/market_history_audit_2019_after_baostock.json) |
| 2020股票 | 21条额外状态已补；未解释日线缺0、复权缺0、状态未认证2，均为ST源分歧 | [年审](attachments/market_history_audit_2020_symbol_quarantine.json) |
| 历史源分歧 | 2020两条、2021一条双方值不可变保存，统一有效视图排除；原始状态保留 | `stock_st_source_conflict` / `trade_status_effective` |
| 自动更新防复发 | 基金整日空/无效复权失败；近期质量按完整检查窗口核对；PARTIAL指标持续入队；按日/按证券共用暖机规则；有界上游请求；冲突隔离贯穿研究与执行 | 对应代码及定向测试 |

2018状态补采已完成，状态未认证余1条。2021累计补9,892条，仅余1笔ST分歧；2022补230条且年审状态缺0。2026股票因子补952,580行，6,845余项暖机核验无超窗异常，首日定向重跑失败0；全年9条状态上游重查仍未认证。没有主动继续搜索停牌原因。缺失的状态证据不伪造成价格或非ST事实，ETF规则未完成时不认证对应收益研究。

## 2026-09-26 本轮收尾检查点

[统一报告](attachments/上游数据更新检查点-20260926.md)列出2016–2026每年数据口径和机器证据。2021只余1条ST分歧，2022状态缺0；2026股票因子955,008项组合空缺降至6,845项，暖机越界检查0、未知缺日线0；9条状态重查仍未知。首日因子缺行分类修复的7日重跑失败0。代码增量47项Provider、15项因子纯Mock通过，独立review均PASS。未进行整体任务归档或收益认证。

## 2026-09-26 主线恢复

用户决定先推进主线，余项统一进入issues根因台账，当前停止扩展零散历史补采。已核对T2候选、管理政策、服务绑定及沙箱消费链，识别MAIN-01为下一整体实施单元。定向基线128 passed、1 skipped（POSIX进程组在Windows跳过）；本次没有新增策略运行实现，不将基线测试记成T2端到端完成。

## 2026-09-26 T2 管理规则增量结果

新增family_management冻结政策与纯状态转换，接入evaluate_lifecycle_day显式可选参数。趋势3ATR、1R一次加仓请求、MACD MA20/10有效日退出及预交叉期待期得到回归覆盖。四个定向纯单元测试文件49 passed（0.22s），无数据库写入或真实LLM。线上绑定/成交/事实注入尚未接通，T2未整体验收。

## 2026-09-26 冻结政策消费链路增量

已接通新政策绑定、快照摘要与族匹配、宿主输出、真实首填初始化、ATR日事实、管理状态持久化与成交完成标记。趋势50%首仓/1R单加/最高收盘3ATR；MACD100%首仓/MA20或10有效日退出。无固定2R填充，旧政策路径保留。R1五项major修复后独立R2 PASS；最终84项定向测试通过（含隔离PG及原政策回归）。T3空止盈BUY仍明确被账户RR门槛拒绝；76候选完整执行适配、四新族生命周期、统一规划器和账本重放尚未完成，未宣称T2或整项完成。

## 2026-09-26 候选执行适配增量

实现PreparedTrial及两个执行入口，76个定义摘要唯一；28模板经真实sandbox，48组合目标均由注册参数调用真实家族函数。输出保留候选ID/定义摘要，管理配置保留H/G/L。ETF双动量周/月参数得到实际调度，单票只收空仓入场。短期恢复补个股MA120条件。6文件108 passed，独立R2 PASS；无DB写入或LLM。研究快照/回放、新族生命周期及T3统一规划尚未接通，整体未完成。

## 2026-09-26 新族冻结持仓规则增量

已实现四新族政策、真实fill初始保护、每日减仓规则，48候选按H/G/L映射；短期d=clamp(2σ√H,3%,10%)、ETF3ATR、中期10%保护均覆盖。数据不足不推进完整观察日，但保留可信收盘高点及独立保护退出；停牌不演进，候选摘要/日期/价格基准不匹配拒绝。187项定向测试通过。R2发现的峰值回归已修、自检通过，尚未取得修后独立PASS。T3/T4账本/成交/冷却/换基仍未接通，未启用新族真实交易。

## 2026-09-26 T3共享减仓与日历增量

普通与生命周期SELL共用数量/预留/市场约束；跨来源部分成交只计剩余预留，同批已接受卖出同步占量。下一执行日来自真实交易所日历，2026-09-24对应9月28日；缺日历/行情明确拒绝新建议并保留退出意图。5文件61 passed，包含12隔离PG用例，独立R2 PASS。T3其余BUY/ETF规则/家族仲裁和T4账本仍未完成。


## 2026-09-27 T3清单注册与延期快照增量

新增不可变quant_allocation_scans：完整家族成员各绑定唯一analysis_task；复用现有task/outbox，在组合→UUID排序版本锁内原子注册，重放保留任务及重试attempt。提交服务新增stage（不提交/回滚/关闭调用方Session），FAMILY_BATCH模式进入canonical快照，worker重试从快照恢复延期；普通旧快照保持原语义及散列。

验收374 passed（13.35s，无skip），覆盖并发注册、跨连接可见性、全批回滚、幂等模式冲突、不可变关系、worker重试及旧Session缓存刷新。独立R1一项major已修，R2 PASS。新增0021仅在liveprofit_quant_strategy_test迁移；当前无自动全批完成触发，无真实ADVISORY产出，不能将本增量等同T3完成。


## 2026-09-27 T3自动首仓与批次收敛增量

worker结束后独立事务触发，dispatcher启动及既有租约恢复周期补查漏回调；自动汇齐注册成员当前成功attempt后复用EntryBatchService。新增0022终态事实与订单/receipt原子提交；重试中等待，失败/取消请求/取消以及CN决策日过期永久阻断。冻结扫描行业上下文并校验同票跨成员一致；输入错误通过savepoint撤销部分修改，再存阻断结论。旧BLOCKED批次不能绕过到首仓或生命周期买入。

验收396 passed（342.17s，无skip）；18项新增隔离PG、worker/dispatcher接线及既有执行链回归覆盖。独立R1 PASS，无findings。0022只在liveprofit_quant_strategy_test执行；未运行真实LLM测试。自动消费当前限普通首仓，有活跃生命周期或待批次日事实整批阻断，联合加仓及owner行情下一项完成；真实资格产出/自动目标生产与T3整体仍未完成。


### 2026-09-27 联合消费收尾检查点

实现与独立R2及scope补充审查PASS：家族排名→族内owner加仓→首仓，刷新账户预留；完整owner日事实/当前attempt/政策/行情/实际品种证明，worker自动联合收敛。定向47项PG、19项执行单测及worker集成单测此前通过（这些统计有重叠，不累加为总数）。最终代码整合重跑239 passed后，既有arc_bottom_75a_v1真实沙箱2秒EXECUTION_TIMEOUT，余下未执行；该批次不能记为全通过。

首轮PG执行中连接断开，服务日志证实后台退出码2并自动恢复，退出根因未定。随后仅复核模板文件及未运行部分，宿主机可用物理内存5092 KiB、虚拟内存余量1587640 KiB，测试停滞，已仅终止本次pytest进程。未改变生产超时标准，未停止业务进程。完整回归尚待环境恢复；本增量暂不勾选验收完成，01b及T3继续进行中。

下一次先恢复此验收，再继续可信目标生产/注册入口、真实资格产出及系统性余项。未部署主库，未运行真实LLM。


## 2026-09-28 联合消费增量最终验收

上次环境中断后，物理可用内存恢复约15 GB。先复跑真实沙箱超时单项1 passed（1.09s），再按隔离`liveprofit_quant_strategy_test` fixture完整运行量化联合消费、生命周期、家族、资格、提交、worker/dispatcher及规划回归：414 passed（18.59s，`-p no:cacheprovider`），无失败/跳过。R2及资产类别delta独立review PASS。此结论替代2026-09-27的未完成测试检查点，不抹去当时PG异常历史。仅测试库执行0016–0022迁移；未改主库，未运行真实LLM。增量验收通过，不等于生产目标、真实资格或T3整体完成。下一主线：可信目标生产/原子注册。


## 2026-09-28 数据截至日防未来增量验收

入口遍历组合基准、股票/ETF候选全部bar日期及ETF事实日期；不允许未来行情。股票/ETF目标构造及ETF月代表选择显式接收`evaluation_as_of`，调仓日和冷却仍按`decision_date`。测试：设置工作区TEMP/TMP后运行`pytest backend/tests/unit/quant_research backend/tests/unit/quant_strategy/test_stock_medium_momentum.py backend/tests/unit/quant_strategy/test_stock_short_reversion.py backend/tests/unit/quant_strategy/test_etf_dual_momentum.py backend/tests/unit/quant_strategy/test_etf_defensive_allocation.py backend/tests/unit/quant_strategy/test_portfolio_targets.py backend/tests/unit/quant_strategy/test_family_allocation.py -q -x --tb=short -p no:cacheprovider`，232 passed / 3.67s。默认系统Temp目录权限拒绝导致首次运行20 passed + 1 setup error；重定向工作区临时目录后全部通过，临时目录已删除。独立review R1发现ETF代表跨日、非末尾未来日两项major；修复并加9月24日→9月28日及非末尾未来行测试，R2 PASS。纯领域增量未运行DB/LLM，未改生产库。后续生产入口必须证明候选全集与冻结trial/version绑定；`None`仍不能视为清仓。


## 2026-09-28 冻结试验/版本绑定增量验收

新增0023`quant_target_trial_bindings`一版本一条不可变关系，记录预注册组合试验ID/定义hash/完整规格与管理参数、资产范围、发布scanner源码hash及绑定时政策版本ID。注册在版本行锁内核对源码真实hash并重算预注册定义；同内容重放保留旧行，异内容拒绝，未发布/单票/错误scope不能首绑。它不创建ADVISORY、目标、任务或订单，也不证明scanner与组合试验策略等价。

验收：隔离`liveprofit_quant_strategy_test`相关量化/研究回归145 passed（8.11s，命令包含test_target_trial_binding、test_scan_manifest、test_family_batch、test_admission_service、test_task_submission、test_trial_registry、test_trial_executor，均`-q -x --tb=short -p no:cacheprovider`）；独立`liveprofit_platform_test`迁移/降级4 passed（2.43s）。Code Review PASS，无findings。新增0023只在隔离测试库迁移，主库未执行。生产接入尚需候选全集证明、当前版本/绑定再验证、生命周期政策一致性及真实正资格。

## 2026-09-29 ETF月代表诊断持久化增量验收

新增0024独立不可变研究表，绑定快照manifest SHA、预注册试验定义hash与双日期；从已发布快照重算诊断并保存显式交易日历/分类输入。同键同内容重放复用，漂移拒绝。定向隔离PG/迁移及纯测11 passed，独立Code Review PASS；只迁移隔离测试库，未运行真实LLM。未通过门禁：历史来源可得时点、ETF独立目录/分类/停牌/规则、账户证书、生产目标与专用订单；T3整体仍未完成。

## 2026-09-29 本地事实修订轨迹增量验收

market四张核心事实表以触发器记录INSERT/实质UPDATE/DELETE到追加表，原地改证券日主键或清表受阻；as-of读取区分未知、存在与删除。隔离instrument库7项、market_data夹具冒烟1项通过，独立Code Review R1两项major/一项minor收敛后R2 PASS。只在隔离库初始化schema，未写主库或运行真实LLM。`observed_at`是写入事件时间且可早于事务提交，历史存量无回溯记录；本增量不提供独立上游发布证书，不开放收益认证、资格或组合目标。

## 2026-09-29 T3 缺执行行情流动性门禁增量验收

普通规划器不再把缺失的执行期行情转换为虚构的大额成交量；BUY 返回 `BUY_REJECTED_LIQUIDITY`。命令：`pytest backend/tests/unit/quant_strategy/test_buy_target_planner.py -q -x --tb=short -p no:cacheprovider`，37 passed，无失败/跳过。仅纯单测，无 DB/真实 LLM；T 日 ADV20 开盘容量、ETF/股票历史交易规则和 T3 整体仍未验收。

## 2026-09-29 T3 执行期 ADV20 容量输入增量验收

`MarketContextBatchLoader`以独立CN开市日历核最近20日完整日线金额，返回千元单位精确十进制ADV20或未知。所有BUY，包括旧持久化信号和直接目标规划，缺明确正数ADV20均拒绝；无执行行情不再虚构金额。定向纯测47 passed、mock loader 1 passed；隔离`liveprofit_quant_strategy_test`中entry batch 14、joint orders 16、lifecycle service 49、batch completion 18 passed。独立R1 major（缺键单日回退）修复后R2确认，畸形值minor已补拒绝和回归。未运行真实LLM、未写主库。剩余门禁：开盘模拟1%参与率、ETF/股票历史交易规则、来源时点认证及T3整体。

## 2026-09-29 T3 风险档实际BUY预算增量验收

保守/均衡/进取三个档位的七项风险上限已冻结并由共享规划器在实时账户数值上核对；无档、超档拒绝新增风险，更保守数值可用。回撤达到档位预算一半即停止新增BUY。10/30/100万元数值情景、账户超档隔离PG拒单、联合加仓与生命周期回归合计155 passed；独立Code Review R1两项major修复后R2 PASS。测试仅使用隔离`liveprofit_quant_strategy_test`，未运行真实LLM或写主库。账户创建/PATCH仍可保存超档值，全额回撤退出/暂停资格、ETF单标的上限与T3整体验收未完成。

## 2026-09-29 T3 账户风险档编辑一致性增量验收

创建明确选档时，未传的七项预算用档位上限填充，显式更保守值保留，超档值拒绝；PATCH省略档位沿用已存档，显式清空才解除绑定。NaN/无穷在后端返回账户校验错误，界面提交前提示具体超限字段。命令：隔离`liveprofit_workspace_test`/`liveprofit_quant_strategy_test`运行`pytest backend/tests/integration/investment_workspace/test_watchlists_portfolios.py backend/tests/integration/quant_strategy/test_task_submission.py backend/tests/unit/quant_strategy/test_risk_profiles.py backend/tests/unit/quant_strategy/test_buy_target_planner.py -q -x --tb=short -p no:cacheprovider`，73 passed；`pnpm test -- src/modules/watchlist/portfolios/PortfolioDrafts.test.tsx`，7 passed；`pnpm typecheck`通过。独立Code Review PASS，无findings。无真实LLM或主库写入。旧账户不自动迁移，BUY仍会拒绝超档账户；全额回撤退出/暂停资格、ETF单标的上限及T3整体仍未完成。

## 2026-09-29 T3/T5 次日开盘纯模拟约束增量验收

新增纯函数按显式T日ADV20截至日和证据引用、含滑点次日开盘价及1%成交金额容量，整手取下界；滑点后超入场区间、停牌和一字涨停均不成交。股票10bps、ETF5bps基线与双倍滑点可比，改变次日全天成交额不改变模拟成交。命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_research/test_opening_execution.py -q -x --tb=short -p no:cacheprovider`，17 passed，无失败/跳过；独立R1两项major和一项minor修复后R2 PASS。无DB、真实LLM或主库写入。证据引用只作调用方标识，尚未校验原件/发布时间、真实下一开市日及证券规则来源；未接历史回放或生产执行，T3/T5整体未验收。

## 2026-09-29 T3 品种日期规则契约及开盘模拟接线增量验收

新增逐证券日期规则契约，覆盖有效期、来源发布日期/引用、价格tick、最低/递增/最高买入量及T+0/T+1。开盘模拟按证券和执行日唯一解析规则，使用对应tick与数量步长；普通100股整手及科创最低200股后逐股递增均有数值反例。独立CN日历核T为开市日、执行日为下一开市日；同T日发布的日期级规则保守拒绝。命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_instrument_rules.py backend/tests/unit/quant_research/test_opening_execution.py -q -x --tb=short -p no:cacheprovider`，33 passed；`git diff --check`无错误。独立Code Review对下一开市日及休市T边界收敛后PASS，日期粒度来源delta复核PASS。无DB/真实LLM/主库写入。规则源引用仍是调用方声明，尚无原件认证、持久化或生产规划器消费；本增量不构成历史回放收益与T3/T5整体验收。

## 2026-09-29 T3 规则原件与逐证券解释持久化增量验收

0025在隔离测试库建原件及解释两张独立不可变表。Repository保存原文字节与SHA、逐证券规则字段与规范hash；同身份相同内容重放幂等，漂移拒绝，不同来源同日冲突保留且诊断解析保持未知，读取重算原件和解释hash。命令：工作区TMP/TEMP下`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_instrument_rule_repository.py backend/tests/unit/quant_strategy/test_instrument_rules.py backend/tests/unit/quant_research/test_opening_execution.py -q -x --tb=short -p no:cacheprovider`，隔离PG 4项加纯测33项共37 passed，无跳过；`git diff --check`无错误。独立Code Review无blocker/major，int4最小量预检和可无损尾随零tick两项minor修复后delta PASS。中间一次计数失败由模块级测试共享证券引起，已改独立证券并重跑。未运行真实LLM或迁移主库。原件由调用方提交、发布日期仍属声明；尚无官方原件认证、完整历史逐证券规则、真实planner消费或生产授权，T3整体未通过。

## 2026-09-29 T3 显式规则规划计算增量验收

范围：共享BUY规划器在调用方显式传入规则集合时，按证券/真实下一开市日核规则有效性，用证券tick映射价格，用最低/步长/最高量裁剪T日ADV20容量、风险/现金/仓位/行业/家族预算及含费用缩量。缺键、错证券、同T日才发布及行情日与估值日不一致均拒绝该显式路径。命令：设置工作区TMP/TEMP后运行`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_buy_target_planner.py backend/tests/unit/quant_strategy/test_execution_constraints.py backend/tests/unit/quant_strategy/test_instrument_rules.py backend/tests/unit/quant_strategy/test_portfolio_risk.py -q -x --tb=short -p no:cacheprovider`，69 passed/0 failed/0 skipped；`git diff --check`无错误。独立Code Review在显式规则计算范围PASS，非1股步长的真实费用缩量及估值日拒绝delta亦PASS。未运行DB/真实LLM，不改主库。

扩展检查`tests/agents/position/test_position_planner.py`时首例失败：旧PORTFOLIO fixture无`risk_profile`且七项预算仍为旧0.5上限，共同规划器按既有T3风险档门禁返回`BUY_REJECTED_PROFILE_BUDGET`，与该旧用例`ELIGIBLE`断言冲突；命令在67 passed后于该首例停下。该测试需系统随迁风险档和ADV20行情fixture，见issues，不冒充完整回归通过。三个真实BUY入口仍未传入认证规则，0025来源日期/URI仍为声明，ETF T+0/T+1可卖量账本及历史逐证券规则覆盖未验；本增量不等于真实执行或T3整体验收。

## 2026-09-29 T3 交易所原件安全采集增量验收

范围：采集器只接官方交易所HTTPS域，逐跳与最终URI核验，限制5次跳转、8 MiB原文、媒体类型、压缩编码、Content-Length及截断异常；保留字节/SHA及本机抓取时刻。`capture_exchange_source`同事务将原文写入0025来源表，并以0026不可变事件保存请求/最终URI、hash、抓取时刻及DB记录时刻，诊断读取核对两表身份；`claimed_published_on`仍是未认证声明。命令：工作区TMP/TEMP下运行`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_official_rule_artifact.py backend/tests/integration/quant_strategy/test_instrument_rule_repository.py -q -x --tb=short -p no:cacheprovider`，16项离线+5项隔离PG，共21 passed/0 failed/0 skipped；只对`liveprofit_quant_strategy_test`执行迁移和写入。独立Code Review R1发现采集事实丢失major及截断异常minor，修后delta PASS。

真实点测：[深交所2026交易规则PDF](https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf)返回282,084字节、SHA256 `9b66f8b0db70f84a25ef1ccb4ee2351001724e408117552d75f6d8993483c586`；[发布通知](https://investor.szse.cn/lawrules/rule/trade/t20260424_620190.html)返回19,302字节、SHA256 `0dcb0971ac96123f369178ce8f5397d948190d41281175694664de1d1dd4dce5`。本机直连[上交所规则页](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml)在TLS握手收到`URLError/WinError 10054`；这是采集通道失败，不证明站点内容或历史规则缺失。两次成功抓取仅在进程内计算hash，未写主库。没有来源发布日期/解释审核与逐证券历史覆盖证书，不得转生产BUY或历史收益认证。
## 2026-09-29 T3 ETF单证券预算计算增量验收

范围：三档ETF单标的上限分别20/25/30%，显式逐证券规则标为ETF时，已有持仓与未完成同票BUY共同扣减；股票继续使用原股票上限。命令：工作区TMP/TEMP下`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_buy_target_planner.py backend/tests/unit/quant_strategy/test_risk_profiles.py -q -x --tb=short -p no:cacheprovider`，62 passed/0 failed/0 skipped。初写股票对照期望995股误写994股，按价格与整手重新核算后修正，最终命令重跑通过。独立Code Review对显式规则计算PASS，补持仓+待成交共占用反例后delta PASS。未运行DB、真实LLM或主库写入。真实入口规则尚未认证或接线，不能把传入`asset_type=etf`当生产授权；T3整体未通过。
## 2026-09-29 T3全回撤需求计算增量验收

范围：有效净值事实下，全回撤预算边界（含等于）设置`full_drawdown_exit_required`，半预算继续阻断新增BUY；缺净值不伪报退出，零或无效预算拒绝风险事实。planner摘要和执行报告输出该需求。命令：工作区TMP/TEMP下`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_portfolio_risk.py backend/tests/unit/quant_strategy/test_buy_target_planner.py -q -x --tb=short -p no:cacheprovider`，60 passed/0 failed/0 skipped。独立Code Review狭义计算增量PASS。未运行DB/真实LLM/主库写入。未通过项：尚无组合级退出建议、受阻持续意图及资格暂停；仅有布尔标志不满足T3 §4.3.1，全回撤动作和T3整体仍未验收。
## 2026-09-29 T3旧规划器夹具随迁验收

范围：将旧`tests/agents/position/test_position_planner.py`的账户档位、七项预算、T日ADV20及风险/现金数值反例随迁到现行契约；生产风险门禁未放宽。命令：工作区TMP/TEMP下`.venv\Scripts\python.exe -m pytest tests/agents/position/test_position_planner.py backend/tests/unit/quant_strategy/test_portfolio_risk.py backend/tests/unit/quant_strategy/test_buy_target_planner.py backend/tests/unit/quant_strategy/test_execution_constraints.py backend/tests/unit/quant_strategy/test_reduction_planner.py -q -x --tb=short -p no:cacheprovider`，101 passed/0 failed/0 skipped；独立delta Code Review PASS。另一次错误扩展运行纳入`tests/agents/position/test_execution.py`，在101项后首个旧可买断言因当前资格门禁失败；运行前未按AGENTS先读DB fixture，已核实该用例仅重建`liveprofit_quant_exec_test`，主库无DDL。该扩展运行不计作通过；旧执行集成夹具仍需随迁。没有运行真实LLM。
## 2026-09-29 T3入场上界预留增量验收

范围：显式入场区间时，含滑点建议价必须落在区间内，再以区间上界以内最高可成交tick核RR及全部预算、现金、费用和T日ADV20容量；最终`order_cost_price`/建议单限价及预留使用该价格。无区间订单沿用原价。命令：工作区TMP/TEMP下`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_buy_target_planner.py backend/tests/unit/quant_strategy/test_execution_constraints.py tests/agents/position/test_position_planner.py -q -x --tb=short -p no:cacheprovider`，85 passed/0 failed/0 skipped；`git diff --check`无错误。独立Code Review PASS，无blocker/major。`estimated_slippage`为基线估计，不表示区间最坏价差；最坏资金和风险由`order_cost_price`承载。未运行DB/真实LLM/主库写入；真实规则来源与三入口仍未认证，T3整体未验收。
## 2026-09-29 T3首仓数量规则与订单落库日期增量验收

范围：显式`instrument_rules`路径在BUY订单落库前复核证券身份、官方规则声明日期、生效期、独立CN决策开市日和真正下一开市日；生命周期首仓比例按证券最低/递增/最高买入量格点向下取整，零量拒绝，保留原持仓/费用预留语义。命令：工作区TMP/TEMP下`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_order_materialization_quantity.py backend/tests/unit/quant_strategy/test_buy_target_planner.py -q -x --tb=short -p no:cacheprovider`，66 passed/0 failed/0 skipped。独立Code Review R1指出落库端未核下一开市日（major），补同日/跳日/休市反例后delta PASS。未运行DB/真实LLM/主库写入。来源仍由调用方声明、三个真实BUY入口尚无认证规则；默认无规则路径仍用旧100股裁剪，故T3整体未验收。
## 2026-09-29 T3真实入口缺规则拒绝式隔离增量验收

范围：扫描、联合批次和生命周期追加的真实新BUY调用统一显式要求规则；目前尚无认证证书，空规则集令新建议单拒绝，旧普通整手退路不再用于这些入口。两处信号订单物化同样检查规则；保护SELL不因该新风险门禁阻断。命令：`compileall`上述四个应用文件PASS；隔离`liveprofit_quant_strategy_test`执行`test_entry_batch.py::test_missing_certified_instrument_rules_cannot_create_joint_buy`为1 passed，生命周期保护SELL两类参数用例共5 passed；独立Code Review在“新建议单fail closed”范围PASS。未运行真实LLM或迁移主库。

未通过项：`test_entry_batch.py`全文件11 passed、5 failed，五项旧测试预期无认证规则仍生成BUY，当前门禁有意拒绝，旧正路径须待证书与认证fixture接线后复跑；不是完整回归通过。旧活动BUY建议仍可能在列表中显示，需当前可执行性标注/重新核准。`confirm_fill`是已发生成交入账，依方案不能因缺证拒绝，但超范围/资格偏离事实及风险重估尚待T4。真实正向买单、T3整体均未验收。

## 2026-09-29 T3账户全回撤持久动作阶段验证（未验收）

范围：0027不可变组合PAUSE事件和逐实际持仓零目标；组合锁内同日净值触发与重复幂等，次日回升仍暂停，规划账户把PAUSE传给统一规划器阻新BUY。严格纠错接口对同日事实漂移报错，三个真实规划入口保留已有暂停、补新实际持仓零目标并允许保护SELL。目标owner由DB触发器核同组合。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_portfolio_drawdown_actions.py backend/tests/integration/quant_strategy/test_lifecycle_service.py::test_corrected_same_day_drawdown_pause_preserves_protective_sell backend/tests/unit/quant_strategy/test_buy_target_planner.py -q`，隔离库及纯测共61 passed/0 failed/0 skipped；仅测试库执行迁移和写入，无真实LLM、无主库写入。首次尝试`uv run`因环境无`uv`未执行测试，改项目`.venv`后通过。

独立CR R1指出同日事实回升/无效绕过漂移、服务未接真实入口；R2指出漂移会中断保护SELL；R3指出漂移期间新增真实持仓未补零目标。以上均已修复并以定向反例通过，但两轮复核后尚无最终独立PASS。本阶段软件验证通过，验收仍挂起。未通过项：持久零目标尚未生成跨日SELL建议，真实可卖量/结算、人工RESUME、旧活动BUY建议核准、规则证书与完整正向回归尚未闭合；T3整体未验收。下一门禁：账户SELL建议及恢复资格、独立代码复审、真实证券规则认证与全入口端到端。

## 2026-09-29 T3账户零目标退出投影增量验收

范围：在组合锁内对全部零目标逐持仓投影，只有外部显式给出结算可卖量且同日CN行情完整时才给READY数量/价格；缺可卖量、旧行情、T+1未可卖、已有SELL预留、非CN品种均保留具体待执行状态。行情与预留按`(market,symbol)`匹配，目标身份由0027 DB触发器核验。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_portfolio_drawdown_actions.py backend/tests/integration/quant_strategy/test_lifecycle_service.py::test_corrected_same_day_drawdown_pause_preserves_protective_sell backend/tests/unit/quant_strategy/test_buy_target_planner.py -q`，隔离PG与纯测64 passed/0 failed/0 skipped。初次加入已有预留断言期望`SELL_NO_EXECUTABLE_DELTA`，实际旧约束先报`SELL_REJECTED_T1`；增设独立`SELL_ALREADY_RESERVED`状态后通过。独立CR R1发现同代码跨市场串票major，修复后R2 PASS。未运行真实LLM、未写主库。

未通过项：该投影只读执行建议且可能随新增持仓追加持久零目标，不落真实SuggestedOrder；外部可卖量尚无T4结算账本证书，生产调用不得把持仓总量当可卖量。跨日自动重试、人工RESUME、规则证书和真实正向BUY尚未验收；T3整体未通过。后续门禁为账本可卖量接线与每日退出建单。

## 2026-09-29 T3内部恢复事件机制增量验收

范围：当前PAUSE ID及组合锁下，要求更晚且同日账户风险事实、低于半档回撤、全部持仓数量恰为0、无未完成BUY建议、非空审核人声明与原因，追加不可变RESUME事件；同请求重放幂等，最新状态投影可解除暂停。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_portfolio_drawdown_actions.py -q`，隔离PG 8 passed/0 failed/0 skipped。独立CR R1指出负数持仓绕过零持仓门禁与审核人身份不可信；改数量!=0、加负数反例后R2在“内部事件机制”范围PASS。未运行真实LLM或写主库。

未通过项：`reviewed_by`只是调用方文本声明，项目未提供可信身份/权限上下文，当前无生产恢复API或调用方。本增量不构成人工审核授权验收；未来入口必须绑定可信主体及权限，生产恢复保持关闭。T3整体仍未通过，继续证券规则认证与真实退出链路。

## 2026-09-29 T3恢复审核签名软件门禁增量验收

范围：`resume_after_review`在组合锁内核当前风险事实后，逐审核人从`LIVEPROFIT_RISK_REVIEW_KEYS`读取规范base64、至少32字节且互异的HMAC密钥；签名绑定组合、PAUSE ID、日期、规范化净值事实hash、审核人及原因。0027 RESUME事件保存审核人和签名，未配置/伪造/事实变更/重复密钥拒绝，同请求重放重新验签。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_portfolio_drawdown_actions.py -q`，隔离PG 8 passed/0 failed/0 skipped。首次把事实hash加入签名后测试因DB numeric尾随零与内存Decimal表示不同而失败，规范化事实摘要后重跑通过；独立CR指出共享密钥冒名，改配置互异并补反例后R2 PASS。无真实LLM、无主库写入。

未通过项：密钥随机生成、发放/保管/轮换/吊销、真实审核签发工具和生产入口尚无；软件验签不等于人员身份已认证，生产RESUME关闭。T3整体验收继续。

## 2026-09-29 T3前向规则证书即时诊断增量验收

范围：0028不可变证书关联规则观察、交易所规则抓取、独立逐证券身份抓取；审核签名绑定证书ID、双原件hash、逐证券身份类别/定位及解释。读时核官方域/字节hash/签名、已提交可见、唯一有效规则，并只允许当前中国日期的即时新决策，返回证书ID和读后授权时刻；跨午夜拒绝。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_instrument_rule_repository.py backend/tests/integration/quant_strategy/test_instrument_rule_certificates.py backend/tests/unit/quant_strategy/test_instrument_rules.py -q`，规则既有/新增共17 passed；补跨市场/午夜反例后新增证书文件单跑3 passed。仅迁移/写入隔离`liveprofit_quant_strategy_test`，无真实LLM/主库写入。独立CR R1两项major（历史T前提交可见性、身份解释）按即时诊断边界修复，R3狭义PASS；跨午夜minor已补并重跑。

未通过项：真实三个BUY入口与订单落库未消费证书；当前签名解释仍依赖真实审核人核官方身份原文，审核密钥发放/签发/吊销尚无；即时授权不构成冻结T收盘或历史研究可得证据。生产BUY继续因无证拒绝，T3整体未验收。下一门禁为新决策证书绑定与真实入口端到端。

## 2026-09-29 T3证书真实BUY入口增量验收

范围：扫描、联合批次及生命周期加仓在组合锁内读取0028签名证书并按证券类别与网格重新规划；物化前复核同证。0029/0030保存建议单证书ID、规则授权时刻和当前新订单决策时刻，跨中国午夜拒单。股票资格不接受ETF类别，生命周期delta不再预先按100股裁剪。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_entry_batch.py backend/tests/integration/quant_strategy/test_instrument_rule_certificates.py backend/tests/unit/quant_strategy/test_order_materialization_quantity.py -q --tb=short`，隔离`liveprofit_quant_strategy_test`及纯测共32 passed/0 failed；此前保护SELL定向17 passed。独立Code Review R1/R2重大项均修复，R3限定本增量PASS；计数minor已补BUY限定。未运行真实LLM、未迁移主库。

未通过项/后续门禁：真实官方逐证券身份和规则解释、生产密钥签发/吊销、旧活动BUY再认证、扫描/生命周期正向全链路及账户全回撤SELL/审核恢复尚未验收；T日冻结事实不因此前向即时证书获得历史可得性。T3整体未通过，继续主线。

## 2026-09-29 T3扫描、联合与生命周期正向回归增量验收

范围：将旧固定日期无证正向夹具按当前日、签名双原件证书、真实资格与独立20日ADV20随迁；保留无资格/无证拒BUY与保护SELL反例。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_joint_orders.py -q --tb=short`为16 passed；同命令目标`backend/tests/integration/quant_strategy/test_lifecycle_service.py`为50 passed；`tests/agents/position/test_execution.py`为10 passed；`backend/tests/unit/quant_strategy/test_execution_constraints.py backend/tests/unit/quant_strategy/test_portfolio_risk.py backend/tests/unit/quant_strategy/test_buy_target_planner.py backend/tests/unit/quant_strategy/test_reduction_planner.py`为81 passed。四组均0 failed；仅两个隔离PG库`liveprofit_quant_strategy_test`与`liveprofit_quant_exec_test`被fixture迁移/写入，无主库写入、真实LLM调用。首次扩大回归17项旧正BUY期望失败；随迁后两文件合计66 passed。模拟旧时钟时尝试UPDATE不可变测试证书被触发器拒绝，方案删除，最终使用真实当前日证书。

后续门禁：账户级零目标按可信结算可卖量生成跨日SELL、活动旧BUY再认证/失效、生产官方逐证券证书及审核密钥流程、人工恢复身份。测试通过不等于这些生产事实已取得；T3整体未验收。

## 2026-09-29 T3全回撤旧BUY隔离增量验收

范围：持久PAUSE在组合锁下使未成交PROPOSED BUY失效，将在途与部分成交BUY置待对账；保护SELL维持原状态。隔离态允许真实部分成交入账且继续隔离，不能转回执行或用SUPERSEDED绕过恢复门禁，明确人工撤单/拒单终态。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_portfolio_drawdown_actions.py -q --tb=short`，仅`liveprofit_quant_strategy_test`隔离库9 passed/0 failed；无主库/真实LLM。独立CR R1/R2重大项修复后R3 PASS。

后续门禁：券商撤单/拒单回执目前未入库，人工终态不等于已认证券商状态；终结后迟报成交与超范围现金对账属于T4。账户级SELL仍需可信可卖量来源和跨日建单，T3整体未验收。

## 2026-09-29 T3旧BUY状态及SELL可卖量一致性增量验收

范围：通用订单状态接口禁止BUY直接转`EXECUTING`，SELL状态与实际成交入账保留；普通扫描与生命周期SELL缺逐持仓可卖量时保留意图，非法、负值、超当前持仓的值不得物化订单，行情日期缺口独立标记。命令：`.venv\Scripts\python.exe -m pytest tests/agents/position/test_execution.py -q --tb=short`为10 passed；`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_lifecycle_service.py backend/tests/integration/quant_strategy/test_portfolio_drawdown_actions.py backend/tests/unit/quant_strategy/test_reduction_planner.py -q --tb=short`为71 passed；补生命周期边界后`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_lifecycle_service.py tests/agents/position/test_execution.py -q --tb=short`为66 passed；最终minor调整后生命周期56 passed。所有写入测试先核fixture只使用`liveprofit_quant_strategy_test`和`liveprofit_quant_exec_test`隔离库；未写主库或调用真实LLM。首次新约束回归有1项旧fixture把800股实际持仓写作1000股可卖而失败，随迁后0 failed。独立CR状态入口PASS；SELL增量R1 major修复后R2 PASS，minor行情日期状态已修。

未通过项与后续门禁：当前账户模型无券商/结算可卖量事实，保护SELL意图可持久化但生产建单不可用；账户零目标跨日订单、官方逐证券证书与审核身份、真实账户回放未验。该阶段验收不代表T3整体验收，继续主线。

## 2026-09-29 T3旧活动BUY迁移阶段验收

范围：0031迁移将无证未成交PROPOSED BUY置SUPERSEDED，在途及部分成交BUY置RECONCILIATION_REQUIRED，保留SELL和终态BUY。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_legacy_buy_quarantine_migration.py -q --tb=short`，隔离`liveprofit_quant_strategy_test`真实降至0030/升至0031，1 passed/0 failed；第一次因测试Alembic URL被隐藏密码而认证失败，已修复。主库只读查询显示迁移版本0026、活动建议单0，运行中仍有API/worker/dispatcher进程；未修改主库。

未通过项/后续门禁：独立CR对单线程离线迁移语义认可，但在线部署范围FAIL：旧worker可在迁移期间或之后写入新的无证BUY。必须先停止旧BUY写入并排空、再迁移、核活动无证BUY为0后启新版，或增加持久层写入门禁并随迁所有旧测试/消费方。此增量不关闭T3活动旧单及整体验收。

## 2026-09-29 T3订单物化函数证书强制增量验收

范围：`materialize_signal_orders`内部把签名证书作为所有新增BUY的必要条件，重新读取同一证书并核证券类别、执行日及决策日；只传原始数值规则、缺证书或漂移均不建单，保护SELL仍可经过物化。命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_order_materialization_quantity.py backend/tests/integration/quant_strategy/test_entry_batch.py -q --tb=short`，29 passed/0 failed；隔离库仍为`liveprofit_quant_strategy_test`，无主库写入或真实LLM。独立Code Review PASS，无blocker/major/minor。

未通过项/后续门禁：已有相同`source_signal_id`的旧订单会跳过新建，应由0031及停写部署核查处理；真实官方逐证券证书、生产审核密钥与账户可卖量仍缺。T3整体未验收。

## 2026-09-29 T3在途状态与意图一致性复核（未通过独立验收）

范围：通用状态接口仅允许未成交PROPOSED直接撤销、拒绝或过期；生命周期在替换活动意图前锁订单，部分成交及券商在途若目标归位、反向、超量、绑定错位、目标数量或原因变化则转待对账并保留预留，同生命周期未报出PROPOSED失效；迟到真实成交仍入账。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_lifecycle_service.py backend/tests/integration/quant_strategy/test_portfolio_drawdown_actions.py -q --tb=short`最终69 passed/0 failed，最后原因变化反例后单文件生命周期60 passed。数据库写入仅限`liveprofit_quant_strategy_test`；未调用真实LLM或写主库。

未通过项：独立Code Review R1/R2/R3依次发现内部PARTIAL自动过期、同向在途旧intent被替换、同数量不同原因旧intent被替换。最终一项修复及迟报成交反例已通过测试，但既定两轮复审上限内未获独立PASS，故本增量不标完成。可信券商撤单/拒单回执及RECON终结路径属于T4，T3统一预留整体验收仍未通过。

## 2026-09-29 T3在途订单隔离补充复核（未通过独立验收）

范围：在先前补丁上复核所有订单、意图及成交更正入口。新增防护：RECON订单迟报成交即使全额完成仍保持RECON；撤销/缩量更正依据订单及绑定意图恢复隔离；无绑定在途单不能接入新意图；直接意图创建入口在组合锁内核活动订单。旧首仓部分成交夹具改为先完成旧单再加仓，外部无绑定部分成交改为待对账。定向命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_lifecycle_service.py -q --tb=short -p no:cacheprovider`，67 passed；相关回撤回归命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_lifecycle_service.py backend/tests/integration/quant_strategy/test_portfolio_drawdown_actions.py -q --tb=short -p no:cacheprovider`，76 passed/0 failed/0 skipped。此前旧正向期望及测试断言失败已修。已读fixture，DDL/写入仅限`liveprofit_quant_strategy_test`，未调用真实LLM或写主库。

独立Code Review R1发现迟报成交意图回退、空绑定在途单被新意图接管和直接意图入口替换旧单三项major；修复后R2又发现全额迟报后的撤销/更正解除隔离、无活动意图时直接入口新建两项major，均已修复并以新反例通过。按既定最多两轮复审，末轮修复尚无独立PASS，本增量及T3统一预留整体验收继续未通过。后续门禁：系统审查最终状态流、可信券商终态/结算可卖量、账户退出跨日建单和0031停写部署核验。用户正核实华泰账户API，本轮未接外部账户事实。

## 2026-09-29 T4账户快照对账纯诊断增量验收

范围：新增`application/reconciliation.py`，对显式组合ID、交易日及中国日期捕获时刻的账户观察做本地现金和逐市场证券持仓差异诊断。只有声明完整的快照才把缺席证券列为差异；可卖量缺失、负数、畸形或超持仓只记问题，不转交易授权。畸形/重复行和跨组合快照均拒绝干净结论。命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/investment_workspace/test_reconciliation.py -q --tb=short -p no:cacheprovider`，8 passed/0 failed/0 skipped；独立Code Review再次运行8 passed并给PASS、无blocker/major/minor。纯函数无DB、真实LLM、主库写入。

后续门禁：`local_values_match`仅是两份数值一致，不认证来源、完整性声明或结算可卖量；持久账户快照、差异处理、费用与公司行动账本、旧写入口统一及真实SELL消费仍未实现。T4整体未验收，T3可信账户事实门禁不因此解除。

## 2026-09-29 T4账户观察持久化增量验收（PASS）

范围：0032以单行不可变账户观察保存组合、交易日、捕获时刻、来源声明、规范现金与逐市场证券持仓JSONB和内容摘要。同组合/来源类型/来源引用重放同义数据返回原记录，内容漂移拒绝；不改变本地组合现金或持仓。观察历史存在时，旧组合删除入口返回明确领域错误。投资工作区集成夹具原 `TRUNCATE ... CASCADE` 会触发既有不可变审计保护，改为每用例只重建 `liveprofit_workspace_test`；原组合生命周期用例重新验证。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_account_observations.py backend/tests/integration/investment_workspace/test_watchlists_portfolios.py -q --tb=short -p no:cacheprovider`，8 passed/0 failed/0 skipped；仅隔离PG被迁移/写入，无主库或真实LLM。初版独立CR指出持仓明细可在提交后追加和旧空组合删除500两项major；改为同一不可变行并补删除拒绝反例后，独立R2 Code Review PASS，无新blocker/major。

未通过项/后续门禁：`source_type`、`source_ref`和完整覆盖由调用方声明，规范快照及hash不证明券商原件、账户身份或结算可卖量；账户差异处理、流水账本、费用、公司行为、旧写入口收敛与真实SELL消费仍未实现。T4整体未验收。

## 2026-09-29 T4账户余额纯重放增量验收（PASS）

范围：新增`application/account_ledger.py`纯算术内核，按组合和双时钟截至重放显式基线、外部资金流、含费用成交、送转/现金分红及有原因更正；返回现金、数量、外部流合计及生效事件ID。更正只替代被引用事件，按记录时刻构链，拒分叉、缺前驱、跨组合及负现金/持仓。命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/investment_workspace/test_account_ledger.py -q --tb=short -p no:cacheprovider`，6 passed/0 failed/0 skipped；纯测无DB、真实LLM或主库写入。独立R1审查发现输入顺序依赖与现金分红被拒两项major、零变动伪事件一项minor，修复并补反例后R2 PASS。

未通过项/后续门禁：这是纯函数，还没有持久事件、成本基准、净值单位化、旧组合现金/持仓投影或真实成交同事务接线。更正的来源和授权亦未认证，纯重放输出不授予生产交易资格。T4整体未验收。

## 2026-09-30 T4账户基线与外部资金流水持久化增量验收（PASS）

范围：0033建立不可变唯一基线及追加流水表；基线关联0032同组合完整观察并冻结摘要，资金流水按组合锁、来源类型/引用与规范内容hash幂等，跨组合基线/更正关系由联合FK保护，单条更正边唯一；写前在纯重放内核检验余额非负。`replay_current`逐条复算包含现金、持仓、价格和费用的摘要，回放当前诊断余额；仅开放外部资金流水入口，不回写`Portfolio.available_cash`。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_account_ledger_store.py -q --tb=short -p no:cacheprovider`，隔离PG 3 passed；与账户观察、旧组合、纯账本及对账核合跑25 passed/0 failed/0 skipped。初次0033后旧观察TRUNCATE测试因新增FK而断言失败，已改为核任何DB拒绝并重跑4 passed；仅`liveprofit_workspace_test`被迁移/写入，未触及主库或真实LLM。独立Code Review及后续回读delta复核均PASS，无blocker/major；`git diff --check`范围内无空白错误。

未通过项/后续门禁：`recorded_at`来自本地`clock_timestamp`，可早于事务提交，不能证明历史研究时的可见性或上游发布时间；来源类型为调用方声明，未认证券商账户或原件。成交、费用、公司行为、撤销更正、投影与旧编辑API同事务接线、成本及单位化净值仍待实现，账本数值不可供生产交易授权。T4整体未验收。

## 2026-09-30 T4显式费用交易流水诊断增量验收（PASS）

范围：`append_trade`持久化市场证券、实际有符号数量、价格、显式费用与现金变化，按来源键幂等，回读摘要覆盖交易全部数值。未认证的报告事实即使使本地现金或持仓为负也照实保存；`replay_current`列`NEGATIVE_CASH`/`NEGATIVE_HOLDING`，不修改生产`Portfolio`，纯重放默认严格模式仍可用于输入校验。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_account_ledger_store.py -q --tb=short -p no:cacheprovider`，5 passed；与账户观察、纯重放/对账、旧组合用例合跑27 passed/0 failed/0 skipped，均为纯测或仅写`liveprofit_workspace_test`，未触主库/真实LLM。首次交易测试把卖出有效时刻设为当时尚未到达的次日早晨而失败，改在已到达时刻后通过。独立CR R1指出严格重放会丢超范围已发生事实，扩展诊断模式并将超额取款/买入/卖出标偏离后R2 PASS；建议增加的超额买入反例已补并定向5 passed。

未通过项/后续门禁：来源仍是自声明，`append_trade`未绑定真实`OrderFillEvent`、券商成交单或费用原件；这些诊断记录不等于生产成交账本。实际成交/费用同事务写入、建议偏离与新增风险阻断、公司行为、成本及单位化净值、旧编辑入口收敛和可信结算可卖量仍未完成。T4整体未验收。

## 2026-09-30 T4旧直接编辑门禁增量验收（PASS）

范围：建立账本基线后，旧组合更新入口在组合`FOR UPDATE`行锁内拒绝直接改变现金/总资产，旧持仓upsert/remove拒绝直接覆盖，读与风险参数更新继续可用；新`PORTFOLIO_LEDGER_REQUIRED`映射HTTP 409。基线服务同样先锁组合，避免并发旧编辑绕过。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_account_ledger_store.py -q --tb=short -p no:cacheprovider`，隔离PG 7 passed/0 failed，含双会话基线未提交时100ms锁超时、基线提交后领域拒绝；此前与旧组合文件合跑10 passed，新增并发后单文件7 passed。独立Code Review PASS，无blocker/major；此前指出的并发测试缺口已补。仅`liveprofit_workspace_test`被写入，无主库或真实LLM。

未通过项/后续门禁：拒绝旧编辑尚无带原因的账本调整入口，`net_asset_value`及峰值/日初风险事实仍可由旧接口改变，后续需归属估值/风险事实链；生命周期`_apply_delta`仍直接写生产现金/持仓且没有显式费用或T4账本同事务绑定。当前仅关闭已建基线组合的旧现金/总资产与持仓直接覆盖旁路，不构成账户端到端验收。

本轮最后账户组定向合跑：`.venv\Scripts\python.exe -m pytest backend/tests/unit/investment_workspace/test_account_ledger.py backend/tests/unit/investment_workspace/test_reconciliation.py backend/tests/integration/investment_workspace/test_account_observations.py backend/tests/integration/investment_workspace/test_account_ledger_store.py backend/tests/integration/investment_workspace/test_watchlists_portfolios.py -q --tb=short -p no:cacheprovider`，29 passed/0 failed/0 skipped；仅已有隔离`liveprofit_workspace_test`被逐用例重建。范围内`git diff --check`无空白错误。

## 2026-09-30 T4未认证成交报告持久化增量验收（PASS）

范围：0034新建独立不可变`account_fill_reports`，同组合订单身份由复合外键保护；报告自身保存市场、代码、买卖方向、数量、价格、可空费用、交易日、采集时刻及自声明来源/证据定位和内容摘要。同来源引用同义重放幂等、漂移拒绝；超订单数量、费用未知和同组合订单标的/方向不符的报告照实留存，既不改变建议单`filled_quantity`也不改账户现金/持仓。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_fill_reports.py -q --tb=short -p no:cacheprovider`，隔离PG 3 passed；与账户诊断/账本/旧组合五文件合跑32 passed/0 failed/0 skipped。独立CR R1指出初版未保存报告标的/方向，修复后R2进一步指出错方向报告不能因关联订单不匹配而丢弃；改为保存并保留对账后R2 PASS。一次测试失败是用例在断言前回滚已写报告，修正测试顺序后3 passed。仅`liveprofit_workspace_test`被写入，无主库/真实LLM。

未通过项/后续门禁：`source_type`、证据URI/hash和报告人仍为调用方声明，未核券商原件或账户身份；无报告接受/拒绝/更正判定、与`OrderFillEvent`一对一绑定、显式费用生产现金投影或风险偏离阻断。0034仅证明报告事实可保留，不表示成交已被核验、券商API可用或T4整体完成。

## 2026-09-30 T4报告本地诊断判定增量验收（PASS）

范围：0035新增不可变`account_fill_report_assessments`，复合外键绑定同组合报告；组合锁后对关联订单行加锁，按当时订单修订冻结费用未知、未关联订单、错标的/方向、超剩余量、早于最早执行日和本地终态问题。每次判定恒含`SOURCE_UNVERIFIED`；相同判定引用返回原快照，新引用读取新订单状态，不变更报告、订单、现金或持仓。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_fill_reports.py -q -x --tb=short -p no:cacheprovider`，隔离PG 5 passed/0 failed/0 skipped；fixture只重建`liveprofit_workspace_test`，无主库或真实LLM。独立Code Review初读指出重复引用在订单修订后可能返回旧结论，经明确冻结快照幂等契约并更新测试后复核PASS，无blocker/major/minor。

未通过项/后续门禁：此判定仅是本地诊断，不验券商原件、账户身份或真实费用来源；无报告更正/撤销关系、外部认证决定、`OrderFillEvent`唯一绑定、账本/现金/持仓同事务投影或未处置偏离阻断。T4整体未验收。

## 2026-09-30 T4报告更正/撤销声明增量验收（PASS）

范围：0036以不可变单出边/单入边保存同组合报告`CORRECT`→替代报告或`VOID`，冻结理由、采集时刻、自声明来源与内容摘要；组合行锁内核来源重放、替代报告身份、时间及链循环，更正/撤销声明不改变任何已接受成交、订单或账户余额。本地判定在原报告已有声明后列`REPORT_RESOLVED_BY_DECLARATION`，仍恒列`SOURCE_UNVERIFIED`。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_fill_reports.py -q -x --tb=short -p no:cacheprovider`，隔离PG 7 passed/0 failed/0 skipped；fixture仅重建`liveprofit_workspace_test`，无主库/真实LLM。独立R1指出数据库来源枚举及自指约束两项minor；迁移与ORM同步加入CHECK后R2 delta PASS，无剩余blocker/major/minor。

未通过项/后续门禁：更正/撤销仍是未经认证的声明，不等于券商撤单或已入账成交冲销。多节点环由服务的组合锁与遍历约束，直接绕过服务写SQL尚无数据库级环验证，生产写权限需收口。真实来源/审核身份、报告至`OrderFillEvent`唯一绑定、费用与订单/账户/账本原子投影、偏离阻断仍未验收，T4整体未通过。

## 2026-09-30 T4已建基线旧成交入口门禁增量验收（PASS）

范围：`LifecycleOrderService`旧`confirm_fill/correct_fill/void_fill`在组合→订单锁后发现0033账本基线即拒绝新变更，避免无显式费用与账本事件的现金/持仓单边投影；更正/撤销由先锁成交改为先锁组合与订单再锁成交。无基线的旧成交行为保留，已发生事实仍可走独立0034报告留存。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_fill_reports.py -q -x --tb=short -p no:cacheprovider`，8 passed；`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_lifecycle_service.py -q -x --tb=short -p no:cacheprovider`，67 passed；分别只重建`liveprofit_workspace_test`与`liveprofit_quant_strategy_test`，无主库/真实LLM。独立Code Review PASS，无blocker/major/minor。

未通过项/后续门禁：新的可信核验成交/费用同事务路径尚未实现，已建基线组合不能用旧入口更新生产投影；报告与诊断账本仍可存已发生事实但不授交易授权。双会话基线/旧成交竞争案例可在T4整体验收补充，现有共享组合锁证明串行顺序。T4整体未验收。

## 2026-09-30 T4未认证账户事实新增BUY门禁增量验收（PASS）

范围：组合锁内任一未认证成交报告或0033账本基线使`planning_account`标记账户需对账；共享规划器、家族分配和直接订单物化均拒新增BUY，保护SELL意图仍按原路径保留。独立R1审查发现已有PROPOSED BUY可绕过新建单门禁经旧`confirm_fill`入账（major）；补组合锁内成交门禁，报告VOID声明仍拒，旧更正/撤销也不在账户未知时改写风险。未建基线的保护SELL实际确认保留。命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_buy_target_planner.py backend/tests/unit/quant_strategy/test_order_materialization_quantity.py -q -x --tb=short -p no:cacheprovider`，68 passed；`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_family_batch.py backend/tests/integration/quant_strategy/test_entry_batch.py -q -x --tb=short -p no:cacheprovider`，28 passed，另家族报告阻断新例单跑1 passed；`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_fill_reports.py backend/tests/integration/quant_strategy/test_lifecycle_service.py -q -x --tb=short -p no:cacheprovider`，78 passed/0 failed。DB测试fixture仅重建`liveprofit_workspace_test`与`liveprofit_quant_strategy_test`，无主库/真实LLM；独立R2 delta Code Review PASS。

未通过项/后续门禁：已建基线账户的旧保护SELL确认仍被费用/账本一致性门禁拒绝，已发生事实只能先留报告，须由新含费同事务成交路径接入；未认证报告即使有VOID声明也不能当券商认证撤销。已有券商在途订单不会因本地阻断自动终结，仍保留预留待对账；生产账户认证和T4整体未验收。

## 2026-09-30 T4报告/成交/账本一对一绑定结构增量验收（PASS）

范围：0037给原始报告增加可空实际`executed_at`，与`captured_at`及CN交易日分别约束；旧无执行时刻报告沿用原内容摘要。独立不可变`account_fill_postings`以同组合复合外键及三端唯一约束连接报告、`OrderFillEvent`和TRADE账本，插入触发器核订单/标的/方向、数量、价格、交易日、执行时刻、显式费用与现金公式；拒原报告已声明更正、原成交已撤销或原流水已被替代。仅隔离结构测试手工构造投影，尚无生产绑定入口。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_fill_postings.py -q -x --tb=short -p no:cacheprovider`，4 passed/0 failed；`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_fill_reports.py -q -x --tb=short -p no:cacheprovider`，11 passed/0 failed；只重建`liveprofit_workspace_test`，未写主库/真实LLM。独立R1发现JSON null数量的SQL三值比较和已撤销成交/已替代流水可事后绑定两项major；修触发器并补三类反例后R2 PASS。范围内`git diff --check`无空白错误。

未通过项/后续门禁：绑定行自身不证明券商来源、审核身份或生产账户投影；当前没有真实接受服务写入该表。非CN报告、缺实际时刻、费用未知或超订单的原件继续保留但不能绑旧订单成交；更正链后续如何逐事件投影和历史绑定是否仍有效需随事务服务验收。T4整体未验收。

## 2026-09-30 T4公司行动诊断流水增量验收（PASS）

范围：`AccountLedgerStore.append_corporate_action`以显式现金变化和逐证券数量变化追加不可变公司行动，规范数值、拒重复证券及零变动，并复用组合锁、同源幂等与更正重放。分拆及现金分红不计外部入金，不更新生产组合投影。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_account_ledger_store.py -q -x --tb=short -p no:cacheprovider`，8 passed/0 failed；fixture仅重建隔离`liveprofit_workspace_test`，未写主库/真实LLM。独立Code Review PASS，无blocker/major/minor。

未通过项/后续门禁：来源标签仍为调用方声明，尚缺发行人原件认证、成本基准调整、与生产账户投影同事务及结算可卖量。T4整体未验收。

## 2026-09-30 T4带原因诊断调整增量验收（PASS）

范围：`append_adjustment`要求非空原因和非零现金/逐证券持仓变化，规范数值并复用组合锁、同源摘要、更正链及允许负余额的诊断重放。调整不计入外部入金，也不改生产`Portfolio`。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_account_ledger_store.py -q -x --tb=short -p no:cacheprovider`，9 passed/0 failed；fixture仅重建隔离`liveprofit_workspace_test`，未写主库/真实LLM。独立Code Review PASS，无blocker/major/minor；本轮未运行额外测试。`git diff --check`已对本轮跟踪文件核验，无空白错误。

未通过项/后续门禁：来源、理由和金额仍由调用方声明；调整仅为诊断事实，不是可信结算余额、可卖量或生产授权。实际成交含费同事务路径、人工身份、成本和对账处置仍待T4整体验收。

## 2026-09-30 T4绑定后更正诊断增量验收（PASS）

范围：`diagnose_posting`在组合锁下读取报告绑定及后续报告撤销/更正声明、旧成交反转、账本替代，逐项输出当前问题，始终保留`SOURCE_UNVERIFIED`。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_fill_postings.py -q -x --tb=short -p no:cacheprovider`，5 passed/0 failed；fixture仅重建隔离`liveprofit_workspace_test`，未写主库/真实LLM。独立Code Review PASS，无blocker/major/minor。

未通过项/后续门禁：这只是当前诊断，未执行报告更正后的生产冲销、费用/持仓重放或可信来源核验，也不解除新增风险BUY门禁。T4整体未验收。

## 2026-09-30 T4人工复核签名事实增量验收（PASS）

范围：0038不可变表及`AccountFillReviewService`保存持钥人员对报告原件引用的复核声明；规范HMAC绑定组合、报告/原件摘要、审核人、理由与引用，要求原件摘要/URI，读取时重验配置、签名、报告内容和当前撤销状态。数据库插入触发器核内容绑定，不将签名记录存在本身视为可信。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_fill_reviews.py -q -x --tb=short -p no:cacheprovider`，最终2 passed/0 failed；仅重建隔离`liveprofit_workspace_test`，未写主库/真实LLM。首次修复后测试因断言仍预期旧“内容漂移”错误而1 failed，调整为当前先验签的错误契约并复跑通过。独立R1指出幂等返回跳过撤权验签（major）与DB内容绑定/负向用例（minor）；修复后R2 PASS，无残留blocker/major/minor。

未通过项/后续门禁：原件字节和券商账户归属尚未独立认证；直接SQL仍可插入摘要匹配但假签名的行，任何后续消费必须调用`verify_signed_claim`，不能查询行存在就放行。密钥轮换使旧声明的当前验签失败，需在生产启用前定义密钥版本/轮换与人员授权。仍无成交、费用、账本及生产投影同事务服务，T4整体未验收。

## 2026-09-30 T4人工原件字节保全增量验收（PASS）

范围：0039不可变`account_fill_evidence_artifacts`按组合保存最长8 MiB的导入原文、媒体类型、来源引用、大小和服务计算SHA；同组合来源/内容双唯一。`require_content`读回重算字节摘要，人工复核写入和当前验签均须找到同组合原件。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_fill_evidence.py backend/tests/integration/investment_workspace/test_fill_reviews.py -q -x --tb=short -p no:cacheprovider`，3 passed/0 failed；fixture仅重建隔离`liveprofit_workspace_test`，未写主库/真实LLM。独立Code Review PASS，无blocker/major/minor。

未通过项/后续门禁：服务只能证明本库持有与摘要一致的字节，不能证明文件由券商签发、属于组合真实账户或历史时点已可得。直接SQL伪造行须由生产写权限收口及读取时摘要/签名重验拦截。真实成交、费用、账本、生产投影同事务与可卖量仍待T4整体验收。

## 2026-09-30 T4成交事务stage拆分增量验收（PASS）

范围：`LifecycleOrderService.confirm_fill`把订单已锁后的成交、组合现金/持仓、生命周期更新提取为无提交的内部`_stage_confirm_fill_locked`；`_apply_delta`支持显式费用扣现金。旧入口仍核旧账本/报告门禁并保留提交、IntegrityError回滚重放与返回契约。新增隔离双会话测试证明未提交变动不可见，回滚后无订单成交/现金/事件残留。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_lifecycle_service.py -q -x --tb=short -p no:cacheprovider`，68 passed/0 failed；仅重建隔离`liveprofit_quant_strategy_test`，未写主库/真实LLM。首次新用例因旧夹具总资产10000、卖出后现金10099触发DB现金上界，调整测试总资产为20000后单例及全文件通过。独立Code Review PASS，无blocker/major/minor。

未通过项/后续门禁：stage没有来源、账户归属和调用权限认证，也不负责事务提交；不能直接暴露给生产。用户已选择在券商账户归属未核实前关闭生产现金/持仓写入口。后续编排须在组合→订单锁下重核数量/价格、签名及原件，并使报告、含费账本、成交、绑定与投影同事务提交；超范围事实仍保留诊断报告。T4整体未验收。

## 2026-09-30 T4原子编排隔离软件增量验收（PASS）

范围：`AccountFillPostingStage._stage_reviewed_report`在组合锁内重验库内原件字节、当前人工签名和报告，订单锁内核证券/方向/交易日/revision/剩余量，再核账本重放与生产现金/持仓一致；同一Session追加含费TRADE账本、旧成交事件、生产投影和0037一对一绑定。调用方统一提交或回滚。硬门禁仅允许带pytest标记的进程连接`liveprofit_workspace_test`，不注册生产路由。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_fill_posting_stage.py -q -x --tb=short -p no:cacheprovider`，最终5 passed/0 failed；只重建隔离`liveprofit_workspace_test`，未写主库/真实LLM。独立R1发现仅内部命名/无路由仍可由生产DB Session直调改现金持仓（blocker）；补进程与DB双条件门禁、生产式直调无写入反例及账本落后注入故障回滚反例后R2 PASS。`current_database()`门禁查询加`no_autoflush`防调用方待写状态先行flush；又补独立原件的费用未知/超订单报告留存反例，第5例与独立delta复核均PASS。

未通过项/后续门禁：仅为隔离软件验收，测试密钥/文件不证明券商签发或账户归属。超建议、缺费用、基线/投影差异与已发生超本地余额事实仍须保持报告与偏离诊断，不能强塞入受订单数量和现金约束的生产投影。真实来源、账户身份、保护SELL结算可卖量、已绑定报告更正冲销、成本/估值与生产启用授权均未验收；T4整体未通过。

## 2026-09-30 T4诊断账本TRADE撤销增量验收（PASS）

范围：0040允许不可变`VOID`流水只替代同组合、同`effective_at`的`TRADE`；DB约束和触发器拒非成交前驱、错时点、非零金额/持仓/费用。账本store组合锁内追加并按来源幂等；重放按`recorded_at`在撤销前保留原成交，在撤销后保留原成交和费用历史、当前余额中剔除其影响。命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/investment_workspace/test_account_ledger.py backend/tests/integration/investment_workspace/test_account_ledger_store.py -q -x --tb=short -p no:cacheprovider`，18 passed/0 failed；集成夹具仅重建`liveprofit_workspace_test`，无主库写入/真实LLM。最初系统Python无pytest，改用项目`.venv`后通过；独立Code Review初审1项minor要求直写DB拒绝反例，补非TRADE前驱、错时点、非零现金/费用四例后delta PASS。

未通过项/后续门禁：本增量仅改变诊断重放，尚未把报告撤销/更正与旧成交、生产现金/持仓和账本放入同一事务；券商账户归属、原件真实性及结算可卖量未认证，生产入口仍关闭。T4及整体任务未验收。

## 2026-09-30 T4撤销声明签名复核增量验收（PASS）

范围：0041独立不可变审核行以域隔离HMAC绑定0036撤销/更正声明的规范摘要、库内原件摘要、审核人/引用/原因；写入与当前回读均重验原件字节、声明内容及当前密钥。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_fill_resolution_reviews.py backend/tests/integration/investment_workspace/test_fill_reviews.py backend/tests/integration/investment_workspace/test_fill_reports.py -q -x --tb=short -p no:cacheprovider`，16 passed/0 failed；仅重建隔离`liveprofit_workspace_test`，无主库写入/真实LLM。独立R1发现若不重算0036声明摘要，SQL伪造的摘要可被持钥人签名而实际字段未受绑定（major）；统一`resolution_digest`供写入/复核共用，补直接SQL反例后delta Code Review PASS。

未通过项/后续门禁：签名只证明当前配置密钥持有人审核了声明与已存字节，不证明券商签发、账户归属或声明真实；尚未连到生产现金/持仓冲销，隔离软件编排待验。T4及整体任务未通过。

## 2026-09-30 T4已绑定成交隔离撤销增量验收（PASS）

范围：0042不可变一对一撤销绑定将签名VOID声明、原0037入账、订单VOID和账本VOID联在同一事务；stage仅在pytest进程且连接`liveprofit_workspace_test`时运行，组合→订单锁内重验原报告与撤销声明的当前签名/库内原件、原成交/TRADE与报告字段、晚记录账本运动以及账本/生产现金持仓一致。原入账保存撤销前数量和平均成本，反向含费成交后恢复BUY成本并再次核账本与生产投影。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_fill_posting_stage.py -q -x --tb=short -p no:cacheprovider`，最终10 passed/0 failed，仅重建隔离`liveprofit_workspace_test`，无主库写入/真实LLM。新增测试覆盖提交/回滚、原审核密钥失效、后记录早生效运动、BUY成本四位舍入、注入投影失败、生产式直调及DB错日绑定。独立Code Review R1三项major（原签名未重验、晚记录运动漏拦、BUY成本回推舍入）和一项minor（DB事件身份欠核）均修复，两次delta PASS。测试首次10项时伪造第二个反转事件先被已有唯一约束阻止，改为隔离事务中临时改事件日期再核0042触发器，最终通过且回滚临时改动。

未通过项/后续门禁：仅支持原TRADE为最新有效且无后来记录账本运动、基线/投影完全一致时的安全撤销。已发生但超本地余额、后续运动或来源未认证的报告/声明继续保留作偏离诊断，不强行投影。已绑定更正、真实券商/账户认证、可卖量、成本估值全链路和生产启用均未验收；T4及整体任务未通过。

## 2026-09-30 T4已绑定成交隔离更正增量验收（PASS）

范围：0043不可变更正绑定连接CORRECT声明、原0037入账、旧VOID订单/账本及替代0037入账；同一隔离Session先回退原含费成交，再按已复核的替代报告重验订单和账户后入账，caller统一提交/回滚。生产进程和非专用库直调仍被硬拒绝。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_fill_posting_stage.py -q -x --tb=short -p no:cacheprovider`，13 passed/0 failed，仅重建`liveprofit_workspace_test`，无主库写入/真实LLM。测试覆盖更正提交/回滚、费用未知不留半笔、生产式直调拒绝及生命周期关联门禁。独立R1发现首笔BUY更正会遗留原生命周期风险锚点（major）；在任何账本/投影修改前拒生命周期关联的VOID/CORRECT，并补反例后delta PASS。0043触发器也约束替代成交与原成交同订单，已解决替代报告的幂等读取重验历史签名。

未通过项/后续门禁：只支持最新、无后续账本运动、账户投影一致且未关联持仓生命周期的成交更正。生命周期风险锚点重建、超建议/超现金偏离、真实账户归属、结算可卖量、成本估值和生产启用仍未验收；T4及整体任务未通过。

## 2026-09-30 T4账户估值与外部资金流单位化纯诊断增量验收（PASS）

范围：`account_valuation.py`用同一时点现金、完整持仓和逐证券正价格求账户价值；负现金/持仓、畸形持仓数量/现金/价格、缺持仓价格或非正价值返回未知。单位化逐笔取流前同刻估值并核累计外部流及完整有效流事件ID清单，同刻连续流核上一笔流后的现金、持仓和价格；缺清单、漏流或不连续均不输出收益。`LedgerBalance.external_flow_event_ids`由同一当前有效账本事件集筛出，旧更正流被替代、等额入出金仍保留两笔身份。命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/investment_workspace/test_account_ledger.py backend/tests/unit/investment_workspace/test_account_valuation.py -q -x --tb=short -p no:cacheprovider`，最终15 passed/0 failed，纯单测，无DB写入/真实LLM；`git diff --check`本次代码范围通过。独立Code Review R1两项major（净流量互相抵消时漏流、同刻重复流前余额）修复后delta PASS；一项minor行序误判已修；账本清单补充delta PASS。后续边界delta发现畸形持仓数量及无效流时间会异常退出，修复并补反例后再delta PASS。

未通过项/后续门禁：完整事件ID清单、价格及流前余额由调用方提供，本内核不认证来源、历史可得时点或与真实券商账户一致；无可审计成本基线、公司行动成本分配和生产估值接线。收益不得用于T5资格或真实风险准入；T4及整体任务未通过。

## 2026-09-30 T4真实持久化生命周期锚点门禁增量验收（PASS）

范围：新增隔离PG反例，真实插入首笔成交`initial_fill_id`的生命周期状态且订单`lifecycle_id`保持空，验证0043更正在任何新账本/现金/订单写入前拒绝。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_fill_posting_stage.py -q -x --tb=short -p no:cacheprovider`，14 passed/0 failed；已读测试及fixture，fixture只重建`liveprofit_workspace_test`，无主库写入/真实LLM。此项是既有门禁的真实持久化验收，不新增生产功能。

未通过项/后续门禁：拒绝门禁不能替代完整生命周期逐事件重放；首笔撤销或更正仍无法安全更新风险容量、止损、目标和后续日状态。生产成交入口继续关闭，T4及整体任务未通过。

## 2026-09-30 T3在途订单状态流系统审查与修复增量验收（PASS）

范围：独立全入口审查确认三项major：旧成交确认/更正/撤销只凭全局幂等键返回无关事件；成交修订归零把已接触券商订单变回`PROPOSED`并可无可信终态取消；旧`COMPLETED`意图有后继活动意图时，迟报修订重开旧意图，唯一约束使真实修订回滚。修复后初查及并发冲突重读均核操作、目标及参数，修订原单保留`RECONCILIATION_REQUIRED`，后继活动意图和关联订单同事务隔离，旧历史终态不重开。T4隔离撤销/更正stage也保留RECON。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_lifecycle_service.py -q -x --tb=short -p no:cacheprovider`，71 passed；`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_fill_posting_stage.py -q -x --tb=short -p no:cacheprovider`，14 passed。两fixture分别重建`liveprofit_quant_strategy_test`与`liveprofit_workspace_test`，未写主库/调用真实LLM。独立系统审查R1三major，修复后delta PASS，无残余blocker/major/minor。测试首次构造SELL后现金超夹具总资产、手工FK插入先子后父，分别修测试总资产和flush顺序，最终全通过。

未通过项/后续门禁：软件状态流增量PASS不等于可信券商终态和结算可卖量认证；旧成交入口仍不支持账本基线后的含费原子入账。账户退出跨日订单、0031停写部署、真实来源/账户归属和T3全入口端到端回放仍未验，T3/T4整体保持未通过。

## 2026-09-30 T3/T4旧入口首笔成交锚点门禁增量验收（PASS）

范围：旧`correct_fill/void_fill`在组合→订单→原成交锁后、任何投影变更前核`PositionLifecycleState.initial_fill_id`。被生命周期冻结的首笔成交拒局部修订，避免初始止损、风险容量和后续目标引用旧值；同一请求仍可由T4不可变报告保存待完整重放。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_lifecycle_service.py -q -x --tb=short -p no:cacheprovider`，72 passed/0 failed；fixture仅重建`liveprofit_quant_strategy_test`，无主库写入/真实LLM。真实持久化锚点反例断言两旧入口拒绝且现金、持仓、累计成交和事件数未变；独立delta Code Review PASS，无blocker/major/minor。

未通过项/后续门禁：拒绝只是保护投影一致性，完整首笔/后续日事实逐事件重放、真实券商来源、结算可卖量与生产启用仍未验收。T3/T4整体保持未通过。

## 2026-09-30 T4账本双时点重放内核增量验收（PASS）

范围：`replay_account_balance`新增可选`recorded_as_of`，分别截止事件生效时间和本地登记时间。后记但原日生效的更正/撤销可重算今天已知的历史现金及持仓；改期更正从旧生效日剔除错记流水。省略参数维持旧同截止语义。`AccountLedgerStore.replay_at`只读持久账本，并拒基线生效/本地登记前的截止。命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/investment_workspace/test_account_ledger.py -q -x --tb=short -p no:cacheprovider`，9 passed/0 failed，纯单测；`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_account_ledger_store.py::test_cash_flow_correction_replays_and_reports_negative_balance -q -x --tb=short -p no:cacheprovider`，1 passed/0 failed，已读fixture且仅重建`liveprofit_workspace_test`，无主库写入/真实LLM。反例核更正登记前后同一历史日余额1100/1080、迟到VOID移除原含费成交、改期更正及无时区/基线未登记截止拒绝。独立Code Review PASS，无blocker/major/minor。

未通过项/后续门禁：`recorded_at`是本地事件时间，不代表提交可见时间或券商上游发布时间；价格/成本和生命周期日事实未接完整重放，不提供研究as-of或生产账户认证。T4整体仍未验收。

## 2026-09-30 T4同刻资金流顺序门禁增量验收（PASS）

范围：单位化对账现在按账本有效资金流ID的完整顺序逐笔匹配，不再只比集合；同一生效时刻两笔流即使现金连续、总额相符，调用方调换顺序也返回`FLOW_INVENTORY_MISMATCH`与未知净值。命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/investment_workspace/test_account_valuation.py -q -x --tb=short -p no:cacheprovider`，9 passed/0 failed，纯测试、无DB/真实LLM。独立delta Code Review PASS，无blocker/major/minor。

未通过项/后续门禁：顺序来自本地账本有效事件排序，仍须认证上游实际资金流及同刻执行顺序；缺独立价格和流前余额证据时不得输出生产收益，T4整体未验收。

## 2026-09-30 T4生命周期重放只读事实清单软件增量验收（PASS）

范围：新增`inventory_lifecycle_replay`，在干净Session中先锁组合后核生命周期，汇集明确归属订单成交和同证券但归属不明的独立清单；逐笔检查报告绑定、方向/数量/价格、含费账本和真实执行时刻，报告声明/入账VOID/CORRECT/账本替代均标修订未知。逐日事实核版本、输入摘要及`last_processed_trade_date`缺日；同刻成交和非零既有持仓成本保留未知。脏Session在SQL前拒绝，避免只读调用触发autoflush。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_lifecycle_service.py::test_legacy_revision_rejects_persisted_initial_fill_anchor_before_projection -q -x --tb=short -p no:cacheprovider`与`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_fill_posting_stage.py::test_persisted_initial_fill_anchor_blocks_correction_before_writes -q -x --tb=short -p no:cacheprovider`，分别1 passed/0 failed；已读fixture，分别只重建`liveprofit_quant_strategy_test`和`liveprofit_workspace_test`，无主库写入/真实LLM。独立Code Review先指出修订、脏Session、归属及逐日版本缺口；逐项修复后delta PASS，无剩余blocker/major。

未通过项/后续门禁：清单仅诊断本地事实，`FILL_SOURCE_UNCERTIFIED`、日事实修订历史/独立日历和成本来源仍为未知；未接券商账户归属或真实同日执行顺序，未构建或切换生命周期投影。生产成交入口关闭，T4整体未验收。

## 2026-09-30 T4当前投影只读差异增量验收（PASS）

范围：`load_current_lifecycle_projection`在干净Session按组合→生命周期→持仓→止损→期望锁定并刷新现有投影；`compare_lifecycle_projection`对3a数量/均价及3b首笔价格、止损、风险容量、目标股数/暴露、止盈/确认、期望和移动止损共14字段逐项输出MATCH/DIFFERENT/UNKNOWN。当前应存在的行缺失报UNKNOWN；任何匹配仅为PROVISIONAL。命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_lifecycle_projection_diff.py backend/tests/integration/quant_strategy/test_lifecycle_service.py::test_active_intent_cross_day_reuse_and_daily_fact_freeze -q -x --tb=short -p no:cacheprovider`，4 passed/0 failed；集成fixture仅重建`liveprofit_quant_strategy_test`，无主库写入/真实LLM。独立Code Review delta PASS，无剩余blocker/major。

未通过项/后续门禁：生命周期phase、减仓完成/关闭状态、已实现盈亏、现金和逐日版本尚未比较；3a/3b尚未接持久事实与独立来源，结果不证明完整生命周期一致，也不授权生产成交。T4整体未验收。

## 2026-09-30 T4未修订持久成交本地映射增量验收（PASS）

范围：`load_local_fill_replay_inputs`在组合锁下复用只读事实清单，仅把归属明确、报告/费用/账本一致、无修订冲突的持久成交整理为`ReplayFill`。任何绑定/身份/时序/修订问题使整组UNKNOWN，不输出部分候选；`LOCAL_CANDIDATE`仍携带来源与初始成本未知，不作券商认证。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_fill_posting_stage.py::test_local_fill_replay_mapping_remains_source_uncertified backend/tests/integration/investment_workspace/test_fill_posting_stage.py::test_persisted_initial_fill_anchor_blocks_correction_before_writes -q -x --tb=short -p no:cacheprovider`，2 passed/0 failed；fixture仅重建`liveprofit_workspace_test`，无主库写入/真实LLM。独立Code Review发现预读订单ORM缓存minor，库存及二次读取改`populate_existing`，同Session预读后另一事务改证券反例通过，delta PASS，无剩余blocker/major/minor。

未通过项/后续门禁：当前不解析有效更正/撤销链，不联接逐日实际持仓和完成意图，不证明券商来源/真实顺序/成本基线；公司行动成本换基、完整投影字段及生产投影切换均未验收。T4整体未通过。

## 2026-09-30 T4日事实当前修订本地候选增量验收（PASS）

范围：`load_local_daily_fact_inputs`在组合→生命周期→全体日事实行锁下复核当前修订链与待处理提案；要求显式日历逐日与本地current完全一致，返回最新revision ID、价格口径、截至时刻及输入副本。任何缺日、修订异常或待决提案整组UNKNOWN，不输出部分日事实。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_lifecycle_service.py::test_active_intent_cross_day_reuse_and_daily_fact_freeze -q -x --tb=short -p no:cacheprovider`，1 passed/0 failed；fixture仅重建`liveprofit_quant_strategy_test`，无主库写入/真实LLM。独立Code Review R1发现两次READ COMMITTED读取间直写current/提案的竞态；清单读取加全体current行锁和ORM刷新，隔离双Session直接更新lock_timeout反例通过，delta PASS，无剩余blocker/major/minor。

未通过项/后续门禁：`LOCAL_CANDIDATE`只在调用方当前事务保持行锁时有效，提交后不能当快照证书。声明日历、日事实输入的上游发布时间、逐日实际持仓、完成意图及公司行动覆盖仍未认证或联接；不形成完整`DailyPolicyFrame`，不授权生产投影。T4整体未验收。

## 2026-09-30 T4逐事件股数轨迹纯软件增量验收（PASS）

范围：成功会计重放保存其已核的基线时点/数量与每笔成交或分拆后的股数、含费成本和盈亏；`project_daily_quantities`仅从该结果读取基线，将事件后数量投到调用方声明的有序交易日。缺会计事实、畸形链、乱序日历返回UNKNOWN，不用当前持仓倒推历史。命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_lifecycle_accounting_replay.py backend/tests/unit/quant_strategy/test_lifecycle_projection_diff.py -q -x --tb=short -p no:cacheprovider`，11 passed/0 failed；纯测无DB/真实LLM。独立Code Review R1发现调用方另传基线可错算首笔事件前日（major）及畸形对象异常（minor）；移除独立基线参数、加非零基线与畸形输入反例后delta PASS。

未通过项/后续门禁：股数仍来自声明有效事件集合与声明日历，只为PROVISIONAL；3d尚不解析修订链，3e未和3f/3b联接，完成意图时点及公司行动成本仍缺事件级证据。T4整体未验收。

## 2026-09-30 T4生命周期phase逐字段差异增量验收（PASS）

范围：逐日政策投影携带非停牌日`decision.phase`，停牌日保持前一次可知阶段；首个日历日停牌而无种子phase时保持未知。当前生命周期phase纳入具名差异，不会在目标/止损一致但阶段错位时误报`PROVISIONAL_MATCH`。命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_lifecycle_policy_replay.py backend/tests/unit/quant_strategy/test_lifecycle_projection_diff.py backend/tests/integration/quant_strategy/test_lifecycle_service.py::test_active_intent_cross_day_reuse_and_daily_fact_freeze -q -x --tb=short -p no:cacheprovider`，14 passed/0 failed；集成fixture只重建`liveprofit_quant_strategy_test`，无主库写入/真实LLM。独立Code Review PASS，无findings。

未通过项/后续门禁：完成减仓、关闭时点、已实现盈亏、现金和每日版本仍未进入全字段比较；phase本身来自声明政策重放输入，不能证实来源或授权生产。T4整体未验收。

## 2026-09-30 T4意图完成时点纯软件增量验收（PASS）

范围：`ReplayFill`及每笔会计事件状态保留订单意图身份；`derive_intent_completions`仅在对应意图成交后持仓准确到达显式目标时输出成交ID、实际执行时刻与完成日。错方向、越目标、缺完成、事件链畸形和同日多项完成均整组UNKNOWN；后一场景不压成3b的一日一项字段。命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_lifecycle_intent_replay.py backend/tests/unit/quant_strategy/test_lifecycle_accounting_replay.py backend/tests/integration/investment_workspace/test_fill_posting_stage.py::test_local_fill_replay_mapping_remains_source_uncertified -q -x --tb=short -p no:cacheprovider`，12 passed/0 failed；集成fixture只重建`liveprofit_workspace_test`，无主库写入/真实LLM。独立Code Review R1发现同日完成按UUID排序丢执行先后（major），保留`effective_at`并加同日先BUY后SELL反例后delta PASS。

未通过项/后续门禁：意图目标/原因仍由调用方声明，当前`PositionIntent`可变状态不能证明历史定义、所有意图及成交全集；返回仅PROVISIONAL且携定义来源未认证问题，不直接填历史政策帧或授权生产。T4整体未验收。

## 2026-09-30 T4意图本地修订史及只读定义候选增量验收（PASS）

范围：0046为`PositionIntent`真实写入追加不可变连续修订，旧意图只标`MIGRATED`基线；直接伪修订和旧史修改拒绝。迁移升级在回填前阻写父表，降级先锁父子并导出带数量/SHA256的JSONL。`load_local_intent_definitions`在组合→生命周期→意图锁下核每条首个LIVE/ACTIVE、连续前驱、目标/原因不变及最新修订等于current；任一异常或旧迁移基线整组UNKNOWN。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_intent_revision_migration.py backend/tests/integration/test_migrations.py::test_upgrade_creates_exactly_platform_tables backend/tests/integration/quant_strategy/test_lifecycle_service.py::test_active_intent_cross_day_reuse_and_daily_fact_freeze -q -x --tb=short -p no:cacheprovider`，3 passed/0 failed；分别只重建`liveprofit_intent_revision_test`、`liveprofit_platform_test`与`liveprofit_quant_strategy_test`，无主库写入/真实LLM。独立Code Review R1发现升级回填并发漏史，补首句阻写锁及等待writer提交反例后delta PASS；loader复核PASS，无剩余blocker/major/minor。

未通过项/后续门禁：本地`recorded_at`只是写事件时间，旧MIGRATED意图过去定义仍未知；当前订单intent归属是否曾重绑、成交全集/券商执行时刻及意图完成事实仍未认证，不把本地定义候选直接喂给生产政策或投影。T4整体未验收。

## 2026-09-30 T4成交时订单意图绑定增量验收（PASS）

范围：0047升级锁定`order_fill_events`后将存量行标MIGRATED/绑定未知，未来插入时由数据库从已锁订单冻结生命周期与意图，并覆盖调用方伪造绑定；成交事件后续UPDATE/DELETE拒绝。正常确认路径先得到最终持仓ID，再一次插入成交事件；3d只将LIVE且生命周期匹配的冻结意图送入诊断重放。降级在排它锁内导出行数、SHA256及读回校验。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_fill_intent_binding_migration.py backend/tests/integration/investment_workspace/test_fill_posting_stage.py backend/tests/integration/quant_strategy/test_lifecycle_service.py::test_fee_aware_fill_stage_stays_in_caller_transaction backend/tests/integration/quant_strategy/test_lifecycle_service.py::test_partial_fill_replay_correction_and_void_are_atomic backend/tests/integration/quant_strategy/test_lifecycle_service.py::test_fill_idempotency_key_rejects_other_order_operation_or_payload backend/tests/integration/quant_strategy/test_lifecycle_service.py::test_late_revision_of_completed_intent_quarantines_successor_without_losing_fill -q -x --tb=short -p no:cacheprovider`，21 passed/0 failed；仅重建`liveprofit_fill_binding_test`、`liveprofit_workspace_test`、`liveprofit_quant_strategy_test`，无主库写入/真实LLM。首次11 passed/1 failed源于旧测试尝试直接UPDATE成交日期，新不可变门禁拒绝；该测试改验不可变后全通过。独立Code Review PASS，无blocker/major/minor。

未通过项/后续门禁：存量MIGRATED行无成交时意图证据；冻结本地订单字段不能认证券商来源、账户归属、执行顺序或有效成交全集。3d仍不解析完整更正/撤销链，3a–3k尚未端到端联接，生产入口保持关闭，T4整体未验收。

## 2026-09-30 T4本地报告与成交集合核对增量验收（PASS）

范围：`inventory_lifecycle_replay`在组合锁下对同证券或候选订单关联的全部本地报告核订单身份、证券/方向与posting；未入账、无归属和身份错误逐报告标UNKNOWN，使3d不输出部分成交候选。成交查询同时纳入冻结的LIVE生命周期ID，订单事后改绑不能让旧成交漏出清单。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_fill_intent_binding_migration.py backend/tests/integration/investment_workspace/test_fill_posting_stage.py::test_local_fill_replay_mapping_remains_source_uncertified backend/tests/integration/quant_strategy/test_lifecycle_service.py::test_legacy_revision_rejects_persisted_initial_fill_anchor_before_projection -q -x --tb=short -p no:cacheprovider`，3 passed/0 failed；仅隔离`liveprofit_fill_binding_test`、`liveprofit_workspace_test`、`liveprofit_quant_strategy_test`，无主库写入/真实LLM。独立R1发现错证券但明确绑定订单的报告漏查（major），扩查询并核身份后delta PASS，无剩余findings。

未通过项/后续门禁：只核本地当前事务可见的报告/成交集合；未入库的券商成交、存量MIGRATED绑定、券商账户归属、有效更正链及真实执行顺序仍未知。全T4链及生产投影未验收。

## 2026-09-30 T4本地意图完成只读桥接增量验收（PASS）

范围：`diagnose_local_intent_completions`在同一事务按组合→生命周期→意图→日事实顺序取得3j定义与3d/3l完整本地成交候选；`combine_local_intent_inputs`仅允许明确首笔ID的无意图BUY作为会计起点，后续成交均须冻结绑定已知意图，再用调用方显式成本基线执行3a/3i。成功结果只标PROVISIONAL并附基线、券商成交全集和定义来源未认证；任一候选未知、畸形身份、未完成意图整组UNKNOWN。命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_lifecycle_local_intent_bridge.py backend/tests/unit/quant_strategy/test_lifecycle_intent_replay.py backend/tests/unit/quant_strategy/test_lifecycle_accounting_replay.py backend/tests/integration/investment_workspace/test_fill_posting_stage.py::test_local_fill_replay_mapping_remains_source_uncertified -q -x --tb=short -p no:cacheprovider`，13 passed/0 failed；纯测与隔离`liveprofit_workspace_test`，无主库写入/真实LLM。独立R1发现首笔无意图现实链路误拒（major），传明确首笔ID并限量豁免；delta发现畸形不可哈希ID异常（minor），前置UUID校验后最终PASS，无遗留finding。

未通过项/后续门禁：基线仍为调用方声明，未核券商账户及成本来源；3d只支持无修订成交，3i同日多完成仍UNKNOWN；本地定义/成交源不能证明报告全量或历史可得性。逐日股数/政策帧、投影差异全字段、公司行动及生产切换未接，T4整体未验收。

## 2026-09-30 T4逐日政策帧拼装纯软件增量验收（PASS）

范围：`build_local_policy_frames`仅接受3e日事实、3f股数轨迹和3m意图完成均为候选的完整同序日期集合；拒绝重复/乱序日期、零或畸形股数、完成日与中国时区执行日不符、同日多完成及异常身份/原因。输出每日日事实深拷贝、真实股数和完成意图理由，保留所有来源问题并标PROVISIONAL。命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_lifecycle_local_policy_frames.py backend/tests/unit/quant_strategy/test_lifecycle_policy_replay.py backend/tests/unit/quant_strategy/test_lifecycle_local_intent_bridge.py -q -x --tb=short -p no:cacheprovider`，12 passed/0 failed；仅纯测，无DB/真实LLM。独立R1发现可将完成提前一天和重复日期（major），修复后delta发现不可哈希原因（minor），最终PASS，无遗留finding。

未通过项/后续门禁：拼装帧仍依据声明日历、未认证本地成交和成本基线；尚未在同一只读诊断调用3b计算状态，亦未核公司行动/券商来源或全字段当前投影。零持仓终态仍UNKNOWN，T4整体未验收。

## 2026-09-30 T4政策重放与限定字段差异纯联接增量验收（PASS）

范围：`compare_local_policy_frames`从同一会计事件状态重算声明日历的每日日终股数，与3n帧逐日相等才调用3b政策重放及3c当前投影限定字段差异；会计、政策或日历外事件未知时不输出匹配，匹配或差异仍标PROVISIONAL并保留来源未认证。命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_lifecycle_local_policy_diff.py backend/tests/unit/quant_strategy/test_lifecycle_local_policy_frames.py backend/tests/unit/quant_strategy/test_lifecycle_projection_diff.py -q -x --tb=short -p no:cacheprovider`，5 passed/0 failed；纯测无DB/真实LLM。独立R1发现仅核末日会放过中间日持仓错误（major），新增真实500→600但帧400→600的反例，逐日重算比较后delta PASS，无遗留finding。

未通过项/后续门禁：当前投影仍由调用方传快照，尚无同事务锁内端到端读取；3c只比较限定15字段，减仓/关闭/盈亏/现金及逐日版本未覆盖。价格、公司行动、成本基线、独立日历、券商账户及有效修订来源均未认证，T4整体未验收。

## 2026-09-30 T4同事务本地诊断入口软件增量验收（PASS，范围受限）

范围：`diagnose_persisted_lifecycle`在调用方同一只读事务内按组合/生命周期→当前持仓/止损/期望→意图→成交报告/日事实读取并连接3m、3f、3n、3o；缺证整组UNKNOWN且不写生产投影，任何临时差异仍携基线、政策种子、独立日历/行动及券商来源未认证。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_lifecycle_service.py::test_persisted_lifecycle_diagnosis_keeps_missing_fill_and_daily_facts_unknown backend/tests/unit/quant_strategy/test_lifecycle_local_policy_diff.py backend/tests/unit/quant_strategy/test_lifecycle_local_intent_bridge.py -q -x --tb=short -p no:cacheprovider`，3 passed/0 failed；隔离`liveprofit_quant_strategy_test`加纯测，无主库写入/真实LLM。首次因测试漏导入`LifecycleStateInput`得到NameError，补导入后通过。独立Code Review PASS，无blocker/major/minor。

补充验收：同一缺证隔离用例中，诊断事务锁住真实持仓行后，另一Session直接SQL UPDATE在100ms锁等待超时，独立复核PASS；证明此行的直接并发改量受锁阻断。另`.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_persisted_lifecycle_diagnosis.py -q -x --tb=short -p no:cacheprovider`，1 passed/0 failed；隔离`liveprofit_quant_strategy_test`的本地完整事实样本经0047冻结首笔无意图/后笔意图、0046意图LIVE修订、0044日事实LIVE修订及0037 posting约束，含费两笔BUY合计10股/成本102，诊断返回PROVISIONAL_DIFFERENCE且保留来源未认证。首次夹具账本源类型错误、ORM未刷新触发器字段各失败一次，修正为`MANUAL_ENTRY`并显式refresh后通过。独立补充复核PASS。

未通过项/后续门禁：完整样本手工插入合法成交与posting并预置投影终态，只证明持久读链可达，不证明人工复核stage到持久重放的写服务端到端；位置锁反例不代表所有投影行/新成交路径都已并发验收。来源时间、真实成本、券商账户、15字段以外及生产切换未验收，T4整体未通过。

## 2026-09-30 T4正常隔离写服务接只读重放增量验收（PASS，范围受限）

范围：将上节正向样本改为两笔各5股、成交价10、手续费1的真实`AccountFillPostingStage`写入；两份不同原件字节分别经报告签名复核，首笔创建生命周期，第二笔冻结既有意图。修正`LifecycleOrderService._apply_delta`买入成本未包含手续费的问题。第二笔后现金898、持仓10、平均成本10.2、生命周期版本2与日事实版本2一致，3p诊断返回`PROVISIONAL_DIFFERENCE`并保留来源未认证问题。撤销stage依已冻结原均价恢复持仓，费用现金逆转。

命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_lifecycle_persisted_diagnosis.py -q -x --tb=short -p no:cacheprovider`，1 passed；`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_fill_posting_stage.py -q -x --tb=short -p no:cacheprovider`，15 passed。两文件的`env` fixture均逐用例重建专库`liveprofit_workspace_test`；无主库写入或真实LLM。首次正向测试因两份相同原件字节配不同引用触发内容去重拒绝，改为不同报告字节；stage回归旧均价断言未计手续费，改为实际持久精度1.0334。独立delta Code Review PASS，无blocker/major/minor；`git diff --check`定向通过。

未通过项/后续门禁：样本原件及基线由测试声明，券商账户归属、报告全集、真实执行时刻、独立日历及公司行动来源均未认证；有效更正/撤销链和完整终态字段也未进入诊断。生产stage仍由pytest专库硬门禁关闭，T4整体未通过。

## 2026-09-30 T4减仓完成字段诊断增量验收（PASS，范围受限）

范围：`PolicyReplay.profit_trim_completed`由逐日帧中有来源的`PROFIT_TARGET_TRIM`意图完成事件推导；仅价格触及只更新`profit_target_reached`。`CurrentLifecycleProjection`在原组合/生命周期锁内读取减仓完成位、冻结止盈价和颈线价，差异器逐字段输出MATCH/DIFFERENT/UNKNOWN。命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_lifecycle_policy_replay.py backend/tests/unit/quant_strategy/test_lifecycle_projection_diff.py backend/tests/unit/quant_strategy/test_lifecycle_local_policy_diff.py -q -x --tb=short -p no:cacheprovider`，15 passed；`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_lifecycle_persisted_diagnosis.py backend/tests/integration/quant_strategy/test_lifecycle_service.py::test_persisted_lifecycle_diagnosis_keeps_missing_fill_and_daily_facts_unknown -q -x --tb=short -p no:cacheprovider`，2 passed，分别为已核专用`liveprofit_workspace_test`/`liveprofit_quant_strategy_test`。首次纯测1项因旧匹配夹具未提供新增字段返回UNKNOWN，补明确false后全通过。尝试比较`last_processed_trade_date`时发现日事实未证明实际状态推进，撤回并列后续门禁。独立Code Review及收尾delta均PASS，无blocker/major/minor；定向`git diff --check`通过。

未通过项/后续门禁：零股终态及`closed_at`、有效更正/撤销、券商报告全集和来源仍未知；本字段的本地差异不构成生产执行许可。T4整体未通过。

## 2026-09-30 T4零仓终态本地标记纯诊断增量验收（PASS，范围受限）

范围：`diagnose_local_terminal_marker`只在会计末笔SELL将持仓归零且其意图身份唯一绑定冻结零目标退出理由、此前未曾归零时，比较本地`phase=CLOSED`和`closed_at`是否有时区值；闭仓时间只作本地标记，不与券商执行时刻混同。确认成交和诊断共用`CLOSING_EXIT_REASONS`。命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_lifecycle_terminal_replay.py backend/tests/unit/quant_strategy/test_lifecycle_intent_replay.py backend/tests/integration/quant_strategy/test_lifecycle_service.py::test_partial_fill_replay_correction_and_void_are_atomic -q -x --tb=short -p no:cacheprovider`，7 passed，隔离PG仅使用已核`liveprofit_quant_strategy_test` fixture，无主库或真实LLM。独立R1发现畸形不可哈希理由抛TypeError（minor），先核字符串并补反例后复跑7 passed，delta PASS；无剩余finding。

未通过项/后续门禁：尚未接同事务持久成交/意图读取；即使本地标记匹配，也不能证明券商报告全集、有效更正/撤销、其他活动订单终态、独立结算可卖量或冷却起点。T4整体未通过。

## 2026-09-30 T4零仓终态同事务本地诊断增量验收（PASS，范围受限）

范围：`diagnose_persisted_terminal_marker`复用组合→生命周期→持仓/止损/期望→意图→订单→报告/成交读取顺序，把0046冻结意图和3d/3l本地成交映射为会计链，要求当前持仓明确0、无活动意图及同证券/同持仓活动订单、显式无公司行动日期后才调用3s；所有既有组合订单先`FOR UPDATE + populate_existing`再按身份/状态筛，阻止取消单并发直写转活动。三笔不同原件的签名复核stage实测BUY5+BUY5→SELL10、本地`CLOSED`标记一致，仍只返`PROVISIONAL_MATCH`及来源未认证问题。命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_lifecycle_terminal_replay.py backend/tests/unit/quant_strategy/test_lifecycle_projection_diff.py backend/tests/integration/investment_workspace/test_lifecycle_persisted_diagnosis.py backend/tests/integration/quant_strategy/test_lifecycle_service.py::test_persisted_lifecycle_diagnosis_keeps_missing_fill_and_daily_facts_unknown -q -x --tb=short -p no:cacheprovider`，8 passed，DB仅用已核专库`liveprofit_workspace_test`和`liveprofit_quant_strategy_test`。正向测试同时核当前持仓直改正数、活动订单/意图、公司行动日期UNKNOWN，以及并发直接SQL修改取消单在100ms锁等待超时。独立R1发现漏核当前持仓和活动订单/意图major，补门禁与反例；R2发现未锁非活动订单导致状态并发major，改锁全组合订单并补竞态反例后delta PASS，无剩余finding。

未通过项/后续门禁：本地标记不认证券商报告全集及取消/拒单终态，`closed_at`只表示本地写时而非真实成交；有效更正/撤销、公司行动、完整逐日政策及冷却事实仍缺。生产成交入口关闭，T4整体未通过。

## 2026-09-30 T4正常整手成交与真实逐日规划本地链验收（PASS，范围受限）

范围：新增独立隔离样本，两张100股BUY建议单各经不同原件字节、签名复核和真实stage确认100股@10/费1；初始风险容量400×50%使日规则目标200股，持仓200及含费均价10.01。报告捕获时刻分别在执行后且早于Sep22 08UTC日规划截止。真实`PositionLifecycleManager.process_day(commit=False)`不新增意图/订单，日事实状态版本2→3，生命周期`last_processed_trade_date=Sep22`；新事务3p诊断为`PROVISIONAL_MATCH`且明确`BASELINE_SOURCE_UNCERTIFIED`。命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_lifecycle_persisted_diagnosis.py -q -x --tb=short -p no:cacheprovider`，2 passed，fixture逐例重建`liveprofit_workspace_test`，无主库/真实LLM。首试误用50股建议单已改为100股，不放宽整手规则；独立Code Review与捕获时间delta均PASS，无遗留finding。

未通过项/后续门禁：手工声明报告原件、基线、日历、政策种子和公司行动覆盖仍不认证券商账户及历史可得性；本增量验证真实日服务链，但3e候选尚未传逐日版本推进证据，不能用日历末日推断最后处理日。有效修订、零仓完整逐日政策及生产入口仍未放行，T4整体未通过。
## 2026-09-30 T4逐日版本与最后处理日只读诊断增量验收（PASS，范围受限）

范围：日事实候选传递`state_version_before/after`，首日版本用截至首日日事实`data_as_of`的本地会计成交数核对；相邻日报版本要求连续，逐日0/1推进要与政策停牌/执行原因一致，末日版本须与同事务锁内当前生命周期版本相等。全部满足后比较`last_processed_trade_date`，来源匹配仍仅为`PROVISIONAL_MATCH`。首日100→101、相邻日2→100、末日后版本漂移、没有实际执行却推进均返回UNKNOWN。真实两笔整手stage成交后的日规划样本保持PROVISIONAL_MATCH。

命令：`.venv\Scripts\python.exe -m pytest -q backend/tests/unit/quant_strategy/test_lifecycle_local_policy_frames.py backend/tests/unit/quant_strategy/test_lifecycle_local_policy_diff.py backend/tests/integration/investment_workspace/test_lifecycle_persisted_diagnosis.py`，4 passed；隔离PG fixture逐例重建`liveprofit_workspace_test`，基线数据库名`liveprofit`已核，无主库写入/真实LLM。最初误用系统Python缺pytest，改用项目`.venv`。独立Code Review先发现跨日版本跳跃major，再发现首日无锚major，逐次修复并补反例后最终delta PASS，无剩余finding；定向`git diff --check`通过。

未通过项/后续门禁：本地执行时刻与`data_as_of`不能证明券商或本地登记/事务提交时序；真实跨日成交引起的版本跳跃在缺持久版本归因时保持UNKNOWN。有效修订链、零仓逐日政策、券商账户归属、报告全集、独立日历/公司行动和生产成交入口仍未验收，T4整体未通过。
## 2026-09-30 T4纯生命周期版本链内核增量验收（PASS，范围受限）

范围：新增纯`reconcile_local_version_chain`，以生命周期创建版1为起点，后续冻结成交各推进1，日事实推进0或1；按版本次序核跨日成交填补间隙、缺失/重复/重叠及最终当前版本。零推进日保留版本检查点；不利用成交执行时间替代本地写入顺序。首笔BUY由调用方单独证明，只创建版1，不进入后续成交步。

命令：`.venv\Scripts\python.exe -m pytest -q backend/tests/unit/quant_strategy/test_lifecycle_version_chain.py`，2 passed；纯测无DB/真实LLM。独立Code Review R1一项minor指出测试把后续1→2误称首笔，改说明和变量名后delta PASS，无剩余finding；定向`git diff --check`通过。

未通过项/后续门禁：尚无持久成交版本前/后不可变证据或调用方接线，不能用本纯契约将3v跨日版本跳跃改判匹配；首笔身份、有效修订链、券商来源/账户归属和历史可见性仍须独立认证，T4整体未通过。
## 2026-09-30 T4未来成交本地因果版本步持久化增量验收（PASS，范围受限）

范围：0048新表为未来已关联生命周期的成交保存不可变版本步，旧成交无回填。`LifecycleOrderService`预分配成交ID并在同一事务设置本地GUC，真实生命周期`state_version+1`的UPDATE触发器才记录该成交ID、前/后版本；成交INSERT核身份，缺对应推进写`UNATTRIBUTED`且无版本界。首笔无关联建仓不生成1→2步。升级/降级先生命周期再成交表锁，降级锁内导出并校验SHA；自定义GUC只证明本地因果顺序，不授服务调用或券商来源认证。

命令：`.venv\Scripts\python.exe -m pytest -q backend/tests/integration/investment_workspace/test_lifecycle_persisted_diagnosis.py --tb=short`，2 passed；此前`.venv\Scripts\python.exe -m pytest -q backend/tests/integration/investment_workspace/test_lifecycle_persisted_diagnosis.py backend/tests/integration/investment_workspace/test_fill_posting_stage.py`，17 passed。fixture逐例重建专库`liveprofit_workspace_test`，基线库`liveprofit`已核，无主库写入/真实LLM。测试验证第二笔stage成交1→2、首笔无步、已版2直接SQL插入只得UNATTRIBUTED；并发降级轮询真实锁等待后writer 500ms内插入、导出包含新增步。第一次并发测试把SQLAlchemy URL转字符串导致密码隐藏、连接失败；改用`render_as_string(hide_password=False)`后通过。独立Code Review初审当前版倒推伪因果major，修后发现迁移锁序反向major，修后指出固定sleep不足以证明竞争minor，全部修复最终delta PASS。定向`git diff --check`通过。

未通过项/后续门禁：`LOCAL_CAUSAL`可由数据库直接操作模仿，只是本地同事务线索；旧成交没有版本步，0042/0043修订尚不支持全链重放；3w/3v暂未消费新表。券商原件/账户归属、成交全集、独立日历与公司行动、历史可见性及生产成交入口保持未知或关闭，T4整体未通过。

补充回归：0048与确认成交服务改动完成后，已核专库`liveprofit_quant_strategy_test`运行`.venv\Scripts\python.exe -m pytest -q backend/tests/integration/quant_strategy/test_lifecycle_service.py --tb=short`，73 passed；确认旧生命周期服务的关联成交/撤销保护入口仍通过，无主库写入或真实LLM。
## 2026-09-30 T4持久版本步接只读重放增量验收（PASS，范围受限）

范围：本地成交候选从0048读非首笔成交的不可变版本步，要求与有效成交全集一一对应、`LOCAL_CAUSAL`且生命周期相符；首笔BUY只作版本1创建身份，缺/错步及UNATTRIBUTED整组UNKNOWN。3p在同一组合锁事务内用3w核成交/日报/当前版连续链，3n只在已核`verified_days`与候选日报完全一致时允许跨日报版本间隙，3o仍核首日截止、政策实际推进、末版和`last_processed_trade_date`。结果保持PROVISIONAL及来源未知。

命令：`.venv\Scripts\python.exe -m pytest -q backend/tests/unit/quant_strategy/test_lifecycle_version_chain.py backend/tests/unit/quant_strategy/test_lifecycle_local_policy_frames.py backend/tests/unit/quant_strategy/test_lifecycle_local_policy_diff.py backend/tests/integration/investment_workspace/test_lifecycle_persisted_diagnosis.py --tb=short`，6 passed；`.venv\Scripts\python.exe -m pytest -q backend/tests/integration/investment_workspace/test_fill_posting_stage.py --tb=short`，15 passed。隔离PG fixture逐例重建`liveprofit_workspace_test`；无主库写入/真实LLM。纯测包含2→3跨日真实版本步填间隙和伪`verified_days`拒绝；真实stage两笔成交/单日日规划样本仍匹配。独立Code Review PASS，无blocker/major/minor；定向`git diff --check`通过。

未通过项/后续门禁：尚未构造真实stage跨日逐日政策完整正向样本，GUC本地因果链不等于券商来源或授权；旧成交、有效更正/撤销、零仓逐日政策与独立日历/公司行动及生产入口仍未验收，T4整体未通过。
## 2026-09-30 T4真实跨日版本归因链样本验收（PASS，范围受限）

范围：隔离测试保留既有单日样本，并新增跨日分支：Sep17账户基线、Sep18首笔100股BUY真实签名stage创建生命周期版本1、Sep21真实`process_day(defer_buy=True)`写日事实1→2及加仓意图、测试按该意图创建100股建议单、Sep22第二笔报告经签名stage写不可变版本步2→3、次日真实规划写3→4；两笔手续费各1使200股平均成本10.01。新Session只读3p以Sep21/22日历重算返回`PROVISIONAL_MATCH`，仍带`VERSION_CHAIN_SOURCE_UNCERTIFIED`。

命令：`.venv\Scripts\python.exe -m pytest -q backend/tests/integration/investment_workspace/test_lifecycle_persisted_diagnosis.py --tb=short`，3 passed；fixture逐例重建专库`liveprofit_workspace_test`，无主库写入/真实LLM。构造中先遇到账本基线晚于首笔成交、第二日重复MA5上穿目标升至400、日服务返回新意图等实际状态，按同一真实流程调整基线、日事实与断言后通过。独立Code Review PASS，无blocker/major/minor；定向`git diff --check`通过。

未通过项/后续门禁：第二张BUY建议单仍由测试按日服务意图手工建，不证明生产目标建单、券商报告全集或账户归属；日历、行情、公司行动与成本基线来源也为声明。有效更正/撤销、零仓逐日政策和生产入口未验收，T4整体未通过。
## 2026-09-30 T4日事实截止与成交版本顺序增量验收（PASS，范围受限）

范围：只读3p在已核本地成交与版本连续链后，要求每个`data_as_of`有时区且落于声明的中国交易日；首笔真实成交须不晚于日报截止；非首笔成交的有效日截至日报日，当且仅当冻结版本推进已在日报前发生，同日成交执行时刻还须不晚于截止。这样日末股数投影不会把日规划之后的迟报倒灌入当天政策输入。违反时整组UNKNOWN，不修改生产投影。

命令：`.venv\Scripts\python.exe -m pytest -q backend/tests/integration/investment_workspace/test_lifecycle_persisted_diagnosis.py --tb=short`，4 passed，隔离fixture逐例重建`liveprofit_workspace_test`，无主库写入/真实LLM。新增Sep21日报1→2后才登记有效日Sep21的成交2→3、Sep22再规划3→4反例，得到`FILL_DAILY_VERSION_ORDER_CONFLICT`；正常跨日正向仍为`PROVISIONAL_MATCH`。独立Code Review PASS，无blocker/major/minor；定向`git diff --check`通过。

未通过项/后续门禁：日事实截止和本地版本步不等于券商上游发布时间或事务提交历史可见性；晚报的正确历史修订链及零仓政策仍未实现，旧成交/券商来源和生产入口保持未知或关闭，T4整体未通过。
## 2026-09-30 T4有效成交修订图纯内核增量验收（PASS，范围受限）

范围：纯`resolve_effective_fill_chain`以已入账报告到成交身份为节点，以已应用的VOID/CORRECT声明为边，要求报告/成交/声明身份唯一、替代报告已经入账、每个节点至多一入一出、无环或未访问节点；VOID链不输出成交，未撤销更正链仅输出末端有效成交ID。任何声明未应用或身份畸形整组UNKNOWN；输出只标PROVISIONAL和来源未认证。

命令：`.venv\Scripts\python.exe -m pytest -q backend/tests/unit/quant_strategy/test_lifecycle_fill_resolution_chain.py`，2 passed；纯测无DB/真实LLM。独立Code Review PASS，无blocker/major/minor；定向`git diff --check`通过。

未通过项/后续门禁：调用方尚未从同一组合锁事务证明本地报告/声明全集、复核签名原件及0042/0043撤销/更正双流真实落地；纯`applied`布尔不认证来源。旧入口首笔生命周期修订拒绝和生产成交入口保持关闭，T4整体未通过。

## 2026-09-30 T4报告签名业务字段摘要复核增量验收（PASS，范围受限）

范围：报告写入和复核消费共用规范摘要；复核记录及当前/历史验签在使用存储摘要前，从报告业务字段重算，继续核原件字节与签名。直接SQL插入字段与自报摘要不一致的报告，即使签名匹配自报摘要，也被拒绝。

命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_fill_reviews.py -q`，3 passed；fixture逐例重建隔离`liveprofit_workspace_test`，无主库写入/真实LLM。独立Code Review PASS，无blocker/major/minor。

未通过项/后续门禁：本地字段、原件与持钥人声明一致不证明券商账户归属或原件真伪；3ab修订图尚未接持久报告全集及0042/0043双流。生产入口保持关闭，T4整体未通过。

## 2026-09-30 T4本地有效成交修订持久适配增量验收（PASS，范围受限）

范围：`inventory_effective_fill_chain`在同一组合锁事务中锁候选订单，按同证券或订单绑定并集读取报告，复核报告/声明签名及原件字节、posting成交和账本、VOID/CORRECT原子反转事实，核同订单全部成交事件与声明图闭合。待入账为UNKNOWN；已原子更正仅输出替代成交，已撤销输出空集合，结果仍为PROVISIONAL。

命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_fill_posting_stage.py -q`，15 passed；最终增量后定向同文件VOID/CORRECT两项2 passed。fixture逐例重建`liveprofit_workspace_test`，无主库写入/真实LLM。独立Code Review发现漏核同订单额外CONFIRM及订单直写并发两项major，补事件全集与`FOR UPDATE`后隔离反例通过；最终delta PASS，无剩余finding。

未通过项/后续门禁：此适配尚未送入3p生命周期版本链、会计及日政策重放；旧3p遇修订仍UNKNOWN，真实首笔生命周期成交修订仍在账本写前拒绝。自报报告与签名不认证券商账户归属、报告外部全集或交易时间可见性，生产入口关闭，T4整体未通过。

## 2026-09-30 T4零仓逐日政策纯重放增量验收（PASS，范围受限）

范围：`replay_lifecycle_policy`仅在先前已算出明确零目标退出、最终非停牌日携唯一带来源且同理由的完成意图、终态后没有日帧时，输出`CLOSED`、零目标和零暴露；终态日不再运行持仓政策，也不推断`closed_at`。同日决策成交、缺/错完成理由、零初始目标或暴露保持UNKNOWN。家族策略后续通用`EXIT_PENDING`保留原冻结退出理由，允许原意图跨日延迟完成。

命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_lifecycle_policy_replay.py -q -x --tb=short -p no:cacheprovider`，16 passed；纯测无DB/真实LLM。独立R1指出泛化`EXIT_PENDING`覆盖原理由major，补三日延迟成交及错理由反例；另一独立审查发现零初始目标/暴露会虚假结清major，补初始目标、暴露、价格/止损/容量校验及反例；最终delta Code Review PASS，无剩余finding。

未通过项/后续门禁：本增量验收时退出完成理由与零股帧均未接入持久链；后续3af已补前者的纯计算，`lifecycle_local_policy_frames`仍拒零股，真实`process_day`零仓不生成终态日事实，3o现按非停牌日检查版本推进。本增量未形成3p持久零仓链或真实退出认证。首笔成交修订和生产成交入口仍关闭，T4整体未通过。

## 2026-10-01 T4冻结退出意图完成纯计算增量验收（PASS，范围受限）

范围：`derive_intent_completions`接受冻结零目标的关闭理由，只在关联SELL逐笔会计数量首次到零时记录完成成交ID及中国交易日；非零关闭目标、错方向、未完成、目标超越、提前或同刻成交、重入后重复完成仍整组UNKNOWN。完成结果仅为PROVISIONAL，不证明券商账户归属或已关闭生命周期。

命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_lifecycle_intent_replay.py -q -x --tb=short -p no:cacheprovider`，17 passed；纯测无DB/真实LLM。独立Code Review PASS，无blocker/major/minor。

未通过项/后续门禁：`lifecycle_local_policy_frames`仍拒关闭理由与零股，真实`process_day`零仓不产日事实；3o版本推进契约尚未承接成交终态，T4整体未通过。

## 2026-10-01 T4本地有效成交链接入3p只读入口增量验收（PASS，范围受限）

范围：`inventory_lifecycle_replay`按组合→生命周期→同证券订单锁序读归属订单，拒初始订单绑定其他生命周期及已存在重复首笔锚点；`load_local_fill_replay_inputs`在旧会计/0048版本链前调用3ad核本地完整报告、签名、原件与VOID/CORRECT双流。仅当有效成交ID集合与生命周期原始成交全集完全相同且初始成交仍有效才继续；有效集合变化返回`FILL_EFFECTIVE_SET_REQUIRES_LIFECYCLE_REPLAY/UNKNOWN`及空成交。

命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_fill_posting_stage.py backend/tests/integration/investment_workspace/test_lifecycle_persisted_diagnosis.py -q -x --tb=short -p no:cacheprovider`，21 passed；两文件的`env`逐用例仅重建`liveprofit_workspace_test`，无主库写入或真实LLM。新反例含失效审核密钥、已完整签名更正后旧锚点、旧生命周期同证券订单/重复首笔锚点；未修订真实stage→3p正向仍PROVISIONAL。独立审查先指出缺完整更正反例minor，补后发现初始订单误归属major，修复并补隔离反例后delta Code Review PASS；无剩余finding。

未通过项/后续门禁：完整有效集合修订尚不能重算生命周期状态与日事实，首笔关联成交撤销/更正仍在账本写前拒绝。初始`initial_fill_id`只有FK无数据库唯一约束；并发不遵循组合锁的直接写入可绕只读检查，此结果不能授权可写投影。券商账户归属、成交全集和执行顺序仍未认证，生产入口关闭，T4整体未通过。

## 2026-10-01 T4初始成交锚点0049持久唯一性增量验收（PASS，范围受限）

范围：0049先锁`position_lifecycle_states`，聚合预检非空首笔成交锚点；重复直接拒升级并回滚，无重复则建立全局`UNIQUE(initial_fill_id)`，允许多个NULL。ORM同步同名约束。降级只移除约束，保留生命周期与成交事实。原3ag重复锚点夹具改为在新约束下断言数据库拒绝且旧行/订单绑定不变。

命令：`.venv\Scripts\python.exe -m pytest -q backend/tests/integration/quant_strategy/test_initial_fill_anchor_uniqueness_migration.py`，1 passed；`.venv\Scripts\python.exe -m pytest -q backend/tests/integration/investment_workspace/test_fill_posting_stage.py`，17 passed；`py_compile`与`git diff --check`通过。迁移测试仅创建/删除随机命名独立测试库，stage fixture逐例仅重建`liveprofit_workspace_test`；无主库写入或真实LLM。独立Code Review PASS，无finding。

未通过项/后续门禁：本地配置库只读预检为0041、生命周期0行/重复0组，不证明其他环境无历史重复；0049尚未部署主库，遇重复须按业务归属人工处置，迁移不清洗旧事实。唯一锚点不认证券商账户、成交顺序或完整生命周期重放，首笔修订与生产入账仍关闭，T4整体未通过。

## 2026-10-01 T4真实单笔平仓终态事件只读诊断增量验收（PASS，范围受限）

范围：`PolicyReplay`显式保留最后真实日的待平仓冻结理由；`diagnose_terminal_policy_event`核冻结退出意图首版定义/决策日日事实版本、零目标及同理由，终态成交为最后唯一SELL并有0048的N→N+1因果步。3p仍只从真实日事实造政策帧，额外终态开市日由调用方声明；与3t同事务终态标记/活动订单意图诊断交叉核对。零仓持仓行保留的旧平均成本不参与当前单位成本比较。结果只读且显式保留日历、基线、签名和券商来源未认证。

命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_lifecycle_terminal_policy_event.py backend/tests/unit/quant_strategy/test_lifecycle_policy_replay.py -q`，22 passed；`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_lifecycle_persisted_diagnosis.py -q -x --tb=short -p no:cacheprovider`，5 passed。隔离`env`逐例仅重建`liveprofit_workspace_test`，无主库写入或真实LLM。真实BUY→次日日事实1→2/零目标止损意图→再下一日签名SELL2→3正向`PROVISIONAL_MATCH`，缺终态开市日和关闭意图后来改`SUPERSEDED`均UNKNOWN。独立Code Review发现零仓均价差异误报及未核关闭意图当前COMPLETED两项正确性问题，修复并补反例后PASS。

测试障碍与处理：完整诊断文件首轮在旧0048→0047降级锁序用例卡住，因为新0049约束降级先取表锁，旧等待条件未命中；停止该轮后在隔离库先降至0048再启动原竞态，并交换线程池/写者上下文退出序，复跑5 passed、独立delta PASS。未通过项/后续门禁：一个终态日内多笔部分SELL尚保守UNKNOWN；同日决策成交、已应用修订改变有效成交集合、可信券商终态与账户归属、独立日历/成本/公司行动未验，首笔修订与生产成交入口保持关闭，T4整体验收未通过。

## 2026-10-01 T4同一终态开市日多笔部分SELL只读诊断增量验收（PASS，范围受限）

范围：3p从最后真实日事实后切出完整成交后缀，前缀单独验会计/0048版本链/逐日政策；纯`diagnose_terminal_policy_event`新增后缀状态与版本步，逐笔要求同一冻结零目标退出意图、SELL、同一终态开市日、严格执行顺序、会计剩余量严格下降且仅末笔首次归零，版本从最后真实日N连续到当前版。唯一完成事件必须绑定最后一笔，3t仍核本地关闭标记及无活动订单/意图；单笔旧接口保留。

命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_lifecycle_terminal_policy_event.py backend/tests/unit/quant_strategy/test_lifecycle_policy_replay.py -q -x --tb=short -p no:cacheprovider`，26 passed；`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_lifecycle_persisted_diagnosis.py -q -x --tb=short -p no:cacheprovider`，6 passed。`env`只逐例重建隔离`liveprofit_workspace_test`，无主库写入/真实LLM。真实签名BUY100→Sep22零目标止损意图/日事实1→2→Sep23同一SELL订单签名报告40股和60股，分别步2→3与3→4；前者持仓60/EXECUTING/3p UNKNOWN，后者持仓0/COMPLETED/CLOSED/3p PROVISIONAL_MATCH，无Sep23日事实。独立Code Review PASS，无finding。

未通过项/后续门禁：只覆盖同一终态开市日、同一关闭意图下分笔卖出；跨多个开市日缺政策日事实、同日决策成交、修订改变有效成交集合仍UNKNOWN。手工建议SELL与报告签名不证明结算可卖量、券商账户归属或报告全集，生产入口与首笔修订门禁保持关闭，T4整体未通过。

## 2026-10-01 T4有效成交集合纯会计候选与生命周期修订写入口增量验收（PASS，范围受限）

范围：`lifecycle_resolved_accounting.py`在纯函数内重算已应用线性修订图，逐报告核`PostedReplayFill`与独立成交事件ID完整一一对应，CORRECT链只取末端，VOID剔除；用3a按执行时点计算数量、成本和盈亏。结果只命名`PROVISIONAL_ACCOUNTING`，有效ID按会计真实时序，被替代ID单列；异常整组UNKNOWN且不输出部分余额。隔离库用真实已签名、冻结生命周期及意图身份的非首笔SELL，核VOID与CORRECT声明虽留存，但两入口在账本写前拒绝并保全已有持久事实。

命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_lifecycle_resolved_accounting.py backend/tests/unit/quant_strategy/test_lifecycle_fill_resolution_chain.py backend/tests/unit/quant_strategy/test_lifecycle_accounting_replay.py -q -x --tb=short -p no:cacheprovider`，15 passed；`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_fill_posting_stage.py::test_noninitial_lifecycle_fill_revision_keeps_persisted_facts -q -x --tb=short -p no:cacheprovider`，2 passed。集成fixture逐例只重建`liveprofit_workspace_test`，无主库写入或真实LLM。独立Code Review指出有效ID原按报告根顺序，改用真实执行顺序并加反序posting反例后PASS，无剩余finding。

未通过项/后续门禁：纯入参本身不能证明报告/声明的本库或券商全集、同ID载荷与原报告经济字段一致；0048原始因果版本步、冻结意图、日日政策及当前投影没有按修订重放。3p仍对有效集合变化给UNKNOWN，首笔及后续生命周期关联成交修订写入口仍关闭。下一步先做已核本地链的只读影响清单，再逐项联接新会计与归属、版本、政策；外部账户归属、结算可卖量和生产成交入口保持门禁，T4整体未通过。

## 2026-10-01 T4修订路径归属与最早影响纯分类增量验收（PASS，范围受限）

范围：`lifecycle_revision_impact.py`在完整调用方输入下重算3ab线性修订图，核每个已入账报告与0047冻结成交身份一一对应、路径根与旧生命周期原始成交全集及唯一初始锚点精确匹配。逐条路径检查替代同订单，后续成交还须同冻结生命周期和意图；分别输出初始/后续CORRECT或VOID、完整报告/声明链、末端有效事件或None，最早影响时点包括提前改期替代报告。VOID路径不和有效事件列表按位置配对；无修订为NO_REVISION，任何畸形全集、迁移绑定、错绑或时间边界整组UNKNOWN且无部分路径。

命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_lifecycle_revision_impact.py backend/tests/unit/quant_strategy/test_lifecycle_fill_resolution_chain.py backend/tests/unit/quant_strategy/test_lifecycle_resolved_accounting.py -q -x --tb=short -p no:cacheprovider`，18 passed，无DB写入或真实LLM。独立Code Review初审发现极端已感知时间转上海时区可OverflowError（minor）；`valid_frozen_time`将其归UNKNOWN并加边界回归，独立delta复核PASS、无剩余finding。

未通过项/后续门禁：原始成交全集、已入账报告与0047冻结字段仍由调用方提供，尚未在同事务从3ad完整持久图组装；最早执行时点只能圈本地候选影响，不能证明券商上游发布时间或历史可见性。未联接日事实、意图、0048版本步或3ak会计，也不向3p/生产投影接线；生命周期成交修订写入口仍拒绝，T4整体未通过。

## 2026-10-01 T4已核本地修订路径持久只读适配增量验收（PASS，范围受限）

范围：`lifecycle_persisted_revision_impact.py`在干净事务中复用3a组合→生命周期→同证券订单锁及3ad本地报告摘要/签名、原件字节、posting与VOID/CORRECT订单和账本双流全集校验。按每条报告路径ID重读报告、posting与0047冻结成交并核完整集合及CORRECT同订单；从已核订单所有CONFIRM扣除已核替代事件独立求旧根，核0049唯一首笔锚后交3al。仅经3ad证明的修订库存提示可保留为诊断问题，错归属、无报告、未入账或声明未应用整组UNKNOWN/空路径。结果仍仅本地`LOCAL_IMPACT`或`NO_REVISION`，不作会计/版本/政策重放。

命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_fill_posting_stage.py::test_local_fill_replay_mapping_remains_source_uncertified backend/tests/integration/investment_workspace/test_fill_posting_stage.py::test_applied_correction_with_stale_lifecycle_anchor_stays_unknown backend/tests/integration/investment_workspace/test_fill_posting_stage.py::test_noninitial_lifecycle_fill_revision_keeps_persisted_facts -q -x --tb=short -p no:cacheprovider`，4 passed。已读目标函数与`conftest.py`，fixture只逐例DROP/CREATE`liveprofit_workspace_test`；无主库写入/真实LLM。`py_compile`与`git diff --check`通过；独立Code Review PASS、无finding。真实签名更正先于生命周期关联的旧首笔锚样本得到`INITIAL_ANCHOR_CORRECT/LOCAL_IMPACT`，未应用的已关联后续VOID/CORRECT与额外/未核报告保持UNKNOWN；只读调用前后持久快照相同。

未通过项/后续门禁：正向是先更正再建旧锚的历史夹具，不能证明已关联生命周期修订写服务可达或生产可用；3p仍对有效集合变化UNKNOWN。只读路径尚未圈定真实日事实、冻结意图或0048旧步骤，3ak新会计未接本库经济字段；券商账户归属、报告外部全集、结算可卖量及生产成交入口均未认证，T4整体未通过。

## 2026-10-01 T4修订影响真实事实范围只读索引增量验收（PASS，范围受限）

范围：`lifecycle_revision_impact_surface.py`在3am本地完整签名与修订图已核后，同事务只读圈最早影响上海日期同日及以后current日报ID与最新0044修订ID、全生命周期意图ID与首LIVE 0046修订ID，以及原始根旧0048因果版本步。三组分别标`PRESENT`或`LOCAL_EMPTY`；坏0044/0046链、待提案、缺/UNATTRIBUTED步骤、从初始版本1到当前的旧数字版本跳号均整组UNKNOWN并清空列表。结果仍带独立交易日历和历史来源可见性未认证。

命令：`.venv\Scripts\python.exe -m pytest -q -x --tb=short -p no:cacheprovider backend/tests/integration/investment_workspace/test_lifecycle_revision_impact_surface.py`，9 passed。已读测试函数及`conftest.py`：`env`逐用例仅重建`liveprofit_workspace_test`，测试内破坏不可变前驱/版本步只在该一次性库构造拒绝反例；无主库写入或真实LLM。真实签名更正先于关联旧首笔锚的历史样本、0044触发器两版、0046首LIVE及真实后续签名SELL所生0048因果步均覆盖；独立Code Review与旧版本链delta复核PASS，无finding。

未通过项/后续门禁：本轮只索引旧本地事实，不建立修订后0048、逐日政策、意图完成或可写账户投影。正向历史夹具仍不能证明已关联生命周期成交修订写服务成功；3p对有效集合变化UNKNOWN，3ak未接本库报告经济字段。独立日历、成本基线、公司行动、券商账户归属与外部成交全集未认证，生产入口保持关闭，T4整体未通过。

## 2026-10-01 T4已核本地报告经济字段到有效成交暂定会计增量验收（PASS，范围受限）

范围：`lifecycle_persisted_resolved_accounting.py`在3am本地完整报告/原件/签名/声明和订单/账本双流核验、组合及订单锁下，重新闭合3ad所有报告路径。逐份报告与posting、CONFIRM和账本核方向、执行时刻、交易日、数量、价格、费用，把含被替代者的完整载荷交3ak，仅有效末端进入暂定数量、成本和已实现盈亏；已VOID且无有效成交时只回显式基线。调用方基线须早于所有已过账报告，包括较早原件；异常整组UNKNOWN且不暴露部分余额/有效ID。

命令：`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_lifecycle_persisted_resolved_accounting.py -q -x --tb=short -p no:cacheprovider`，8 passed。已读测试函数与`conftest.py`，fixture逐例仅重建隔离`liveprofit_workspace_test`；不可变账本错配反例只在该一次性库修改，无主库写入或真实LLM。首轮测试把基线10股/成本100下SELL 5股@10、费1的已实现盈亏误写成0，修为-1后复跑；独立Code Review发现基线位于原报告与较晚更正之间可双计major，补严格早于全部报告及真实签名跨时刻回归，再跑整文件8 passed，delta复核PASS无剩余finding。

未通过项/后续门禁：会计仅证明本库可见且人工签名复核过的报告经济一致性，显式基线的真实来源、券商账户归属、外部成交全集、公司行动和历史可见性均未认证。历史正向在修订后才把旧锚关联生命周期，不能证明已关联生命周期修订写服务可达；原0048因果版本步、冻结意图、逐日政策和当前投影尚未随有效集合重算，3p继续UNKNOWN，生产入账仍关闭，T4整体未通过。

## 2026-10-01 T4修订后重放只读工作清单增量验收（PASS，范围受限）

范围：`lifecycle_revision_replay_manifest.py`把3am变更报告路径按旧成交根映到有效末端或VOID，3an原始根中未变更者自映，旧0048因果步只按旧根附属；与3ao有效成交及会计执行顺序逐ID交叉，携可能受影响日报/意图ID。薄持久入口在同一干净事务顺序核3am、3an、3ao。坏根、坏末端、旧步错归、畸形时间或任一上游UNKNOWN均返回UNKNOWN和全空清单；初始锚VOID、有效事件非BUY、非会计首项、更正需重建种子均明列`REPLAY_BLOCKED`。本增量不产生修订后新0048版本、日事实、意图完成或可写投影。

命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_lifecycle_revision_replay_manifest.py -q -x --tb=short -p no:cacheprovider`，6 passed；`.venv\Scripts\python.exe -m pytest backend/tests/integration/investment_workspace/test_lifecycle_revision_replay_manifest.py -q -x --tb=short -p no:cacheprovider`，3 passed。已读隔离测试与`conftest.py`，`env`逐用例仅重建`liveprofit_workspace_test`，无主库写入或真实LLM；`py_compile`与任务文档`git diff --check`通过。独立Code Review PASS，无finding。真实签名历史更正SELL后才挂旧SELL锚的样本返回`LOCAL_MAPPING+NONBUY_INITIAL`，未修订返回NO_REVISION，待应用更正UNKNOWN。

未通过项/后续门禁：正向只能证明本地工作清单可读，旧SELL锚不能充当首笔BUY政策种子；初始BUY更正/VOID由纯构造覆盖，已关联生命周期真实修订写入口仍在账本前拒绝。旧0048步/0044日报/0046意图均不能当修订后的因果事实，3p仍对有效集合变化UNKNOWN。成本基线、券商账户/成交全集、独立日历、公司行动和历史可见性未认证，生产入口关闭，T4整体未通过。

## 2026-10-01 T4首笔BUY政策种子纯重算增量验收（PASS，范围受限）

范围：`lifecycle_initial_seed_replay.py`以显式创建时冻结输入和3ap初始根映射的有效首笔BUY，按现有初始化公式重算计划容量、风险预算重定价容量、100股下取整、目标/相位、止盈、ATR或旧移动止损初值及MA5期望。字段保留缺键、显式null、有值；MA5模板`confirmed_cross`接受真实整数0/1及布尔。VOID、非BUY、非首位、错映射、成交价不高于止损、无效数值、零目标或缺必要值均UNKNOWN且无部分种子；正向仍标`UNCERTIFIED`并保留后续因果/政策/意图待重放。

命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_lifecycle_initial_seed_replay.py backend/tests/unit/quant_strategy/test_lifecycle_revision_replay_manifest.py -q -x --tb=short -p no:cacheprovider`，26 passed（3aq本文件20项），纯测无DB写入或真实LLM；`py_compile`与任务文档`git diff --check`通过。独立Code Review初审指出MA5原始参数为整数0/1以及畸形manifest可能异常，修复并补反例后最终PASS、无遗留finding。数值样本包括计划1300股/风险重定价2000股取1300、风险预算100元/(10−9.5)=200股取200、改价改量改日与零目标拒绝。

未通过项/后续门禁：测试中的冻结输入由纯夹具声明，现有旧生命周期未有可信创建时原值；3ap/3ao也未把真实首笔报告经济字段作为持久seed入参。3ar专用不可变seed表方案待用户确认，旧行不反填；修订后0048版本、0044逐日政策、0046意图及投影仍未重算。券商账户、成交全集、成本/日历/公司行动和历史可见性未认证，3p及生产门禁不变，T4整体未通过。

## 2026-10-01 T4有效成交与完整日报行情截止纯时序清单增量验收（PASS，范围受限）

范围：`lifecycle_revision_chronology.py`纯组合3ap旧根到有效末端/VOID的映射、3ao有效会计执行序列与3e显式日历下的完整current日事实。逐事件核根/末端/时刻及首笔原锚BUY；逐日报核上海日期、最新修订ID与3an受影响后缀，按行情`data_as_of`列截止前有效成交ID前缀。末日日报后的有效成交保留显式尾部槽；缺完整日报、初锚更正/撤销、日报/影响修订不符、同日成交在或晚于截止、畸形身份/问题列表/嵌套会计均整组`UNKNOWN`且无部分槽位。输出仅`LOCAL_ORDER`身份与时间工作清单，不推导旧0044/0048为新版本，也不生成政策日、意图完成或投影。

命令：`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_lifecycle_revision_chronology.py -q -x --tb=short -p no:cacheprovider`，19 passed；`.venv\Scripts\python.exe -m py_compile backend/modules/quant_strategy/application/lifecycle_revision_chronology.py backend/tests/unit/quant_strategy/test_lifecycle_revision_chronology.py`，通过。纯测无DB写入或真实LLM。独立Code Review初轮一项major：未先核三组issues及嵌套会计类型，畸形构造可抛异常；补9组参数化反例后delta PASS、无遗留finding。任务文档`git diff --check`通过。

未通过项/后续门禁：3e完整日历仍由调用方声明，`LocalDailyFact`不含fact ID，持久接线须同事务核3an日报身份。`data_as_of`是市场输入截止，不能证明政策实际处理或事务提交先后；成交真实执行时刻、外部报告全集、成本基线、公司行动及券商账户来源均未认证。现有历史持久正例的旧锚为SELL，真实已关联生命周期成交修订仍被stage在账本写前拒绝；3ar创建时不可变BUY种子表方案待用户确认。新因果版本、政策/意图、原子投影与3p仍未接，生产入口关闭，T4整体未通过。## 2026-10-01 3ar-1六表与UNKNOWN_PRIOR迁移基线软件验收（PASS）

范围：0051新增命令、生命周期操作、共享来源、共享变化、订单输入和首填绑定六表；同步生命周期复合身份、0044/0046/0048旧流命令/操作关联、ORM注册及平台表断言。锁住生产者后仅保存迁移瞬间旧生命周期和所有未绑定订单（含纯订单组合）的UNKNOWN_PRIOR基线，不反填创建输入或过去操作。物理行像保留精确SQL JSON原文，Decimal规范值另作解释；只读仓储从字段/原文重算摘要与预期全集。六表拒UPDATE/DELETE/TRUNCATE及过渡期所有LIVE INSERT；有历史的降级须完整六表导出、数量/SHA/fsync/readback，旧业务事实保留。

命令与结果：
```powershell
.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_lifecycle_operation_schema.py -q -x --tb=short -p no:cacheprovider -o faulthandler_timeout=60
# 16 passed in 32.14s；逐例随机liveprofit_lc_history_0051_test_<uuid>
.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_initial_entry_completion.py backend/tests/integration/test_migrations.py::test_upgrade_creates_exactly_platform_tables backend/tests/integration/test_migrations.py::test_downgrade_then_upgrade_is_idempotent -q -x --tb=short -p no:cacheprovider -o faulthandler_timeout=60
# 29 passed in 8.29s；liveprofit_quant_strategy_test/liveprofit_platform_test，串行
```

独立Code Review R1两major（未绑定旧单漏基线、JSONB原数字类型改变）和四minor（旧流ORM关系、管理政策/变化行像对象shape、前态版本一致性）均修复；R2 PASS、无遗留。相关ruff、py_compile、文档diff检查通过。中间失败包含DDL约束复制迭代、拆表依赖顺序、SQLNULL/JSONnull、原夹具闭仓标记/订单状态与临时表不能引用永久FK，最终反例均真实可构造。无真实LLM或主库DDL。

未通过项及后续门禁：0051未部署；LIVE命令/捕获/封口和所有业务写者未接入；旧写者仍可变更当前行，基线读取始终continuous_history_known=False，不表示连续还原。未来LIVE降级与角色ACL、首填输入、全组合/删源/历史查询和容量由3ar-2至6继续。T1/T3/T4外部券商账户/结算/报告全集、历史可得性及生产认证均未关闭；不归档整个T1–T8任务。

<a id="acceptance-3ar2-order-scope"></a>

## 2026-10-01 3ar-2单订单原始请求与修改前参与者选择子增量（PASS，整项进行中）

范围：新增lifecycle_command_selection.py。冻结的原请求只含schema/命令kind、订单ID、请求状态、expected_revision和请求键；幂等字节不混当前账户或预分配操作ID。独立选择器在无先写/先锁事务下，允许普通预读，按组合→策略/政策→生命周期/订单取真实锁并populate_existing；核归属/证券/revision后，绑定订单恰有一生命周期步骤，未绑定订单恰有一结果承诺。选择结果仅scope_only，不改变订单、不保存命令、不提交或授执行权限。

命令：
```powershell
.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_lifecycle_command_request.py backend/tests/integration/quant_strategy/test_lifecycle_command_selection.py -q -x --tb=short -p no:cacheprovider -o faulthandler_timeout=60
# 19 passed in 17.75s；纯10、逐例随机隔离9，零跳过
```

独立R1发现只检查Session脏集合会被flush或原始SQL绕过的major；入口先核pg_current_xact_id_if_assigned未分配XID，已有写入/行锁要求整事务回滚重选。补三类反例、真实SQL锁序、初读后并发改绑及预读ORM刷新，R2 PASS无残留。ruff/py_compile通过；最初收集失败因两目录同测试文件名，纯测改唯一文件名后恢复；新测试fixture已核只用随机库，不运行真实LLM。

未通过项及后续门禁：单命令scope不是实际执行或完整操作历史；多命令拼接、全组合/首填/删源selector、命令幂等持久化、DB捕获/封口、实际结果与提交全集、受控角色ACL尚待实现。3ar-2仍进行中，3ar总进度1/6，主库/LIVE/生产及T4整体验收门禁保留。


<a id="acceptance-3ar2-unbound-read"></a>

## 2026-10-01 3ar-2未绑定订单重放读端子增量（PASS，整项进行中）

范围：新增`lifecycle_command_replay.py`，先按组合/原请求键核字节，再核单未绑定清单、命令前驱、唯一结果来源、摘要、冻结schema1物理列和完整变化链；每步revision加1且只改status/revision/updated_at。三种结果均有内容检查，截断历史、空清单、重复/漏结果、scope伪装、结果列漂移、数量改变及A→B→A漏中间像即使重算摘要仍拒绝。共享`_verify_row_image`按规范字节区分整数与布尔，保留SQL NUMERIC原精度和JSONB原文。读端不读当前订单、不写/锁/提交，Session已写或持锁拒绝；结果三个认证字段恒False。

验收命令与实际结果：

- `.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_history_row_image_types.py backend/tests/integration/quant_strategy/test_lifecycle_command_replay.py backend/tests/integration/quant_strategy/test_lifecycle_operation_schema.py backend/tests/unit/quant_strategy/test_lifecycle_command_request.py backend/tests/integration/quant_strategy/test_lifecycle_command_selection.py -q -x --tb=short -p no:cacheprovider -o faulthandler_timeout=60`：63 passed in 107.89s。随后R2补不变字段类型敏感残留，新增完整行1→true反例单独1 passed in 2.67s。
- 最终`.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_history_row_image_types.py backend/tests/integration/quant_strategy/test_lifecycle_command_replay.py -q -x --tb=short -p no:cacheprovider -o faulthandler_timeout=60`：29 passed in 47.63s。加此前未受末次比较修复影响的基线16/选择19，共64个不同用例；不累加重复执行计数。ruff四文件、py_compile及相关tracked文档git diff --check通过。

DB安全：先读测试和复用fixture，DDL/历史构造仅`liveprofit_lc_history_0051_test_<随机UUID>`；fixture再次核数据库名前缀后由owner临时禁用0051拒INSERT触发器，显式处理递延FK并恢复触发器以构造合成历史。此绕过只在隔离夹具，未改迁移、权限、实际写者或主库，没有真实LLM。初轮fixture因待核FK不能ALTER trigger失败，后改先SET CONSTRAINTS；回读时区表示不同导致摘要失败，后改UTC六位微秒，最终无失败/跳过。

独立审查：R1四major（物理列不全、整数/布尔混同、结果结论未入摘要、逐步修改范围/revision）同组修复；R2发现不变字段比较同类残留，补同一规范字节及重算摘要反例，R2续核PASS，无残留findings。经验、规则及知识库同步。

未通过项及后续门禁：此范围仅既存内容核验，不证明合成历史真实发生、数据库触发捕获全集、同事务封口、来源认证或执行权限；生产入口不调用该读端授执行。已绑定命令完整链读端、多入口原请求/参与者选择、LIVE命令持久化、自动逐行捕获、操作封口与提交全集、ACL、容量、首填输入和删源仍待3ar-2至6。主库未迁移，LIVE拒写及生产/关联成交修订门禁保留。3ar-2整项不勾选，任务不归档/提交。


<a id="acceptance-3ar2-unbound"></a>

## 2026-10-01 3ar-2单未绑定状态命令DB协调子增量（PASS，整项进行中）

范围：0052不新增表列或补过去事实，仅安装单未绑定状态命令受控SQL入口、逐行捕获和封口；应用`stage_unbound_order_status_command`不提交/回滚，既有生产API未接。数据库拒已有XID，锁组合→订单后从真实修改前状态冻结唯一清单；不接受客户端清单。真实UPDATE捕获每个OLD/NEW原SQL文本，最后唯一结果封口并一次命令级递延校验。已封口事务在清空GUC或提前SET CONSTRAINTS后仍拒任何额外订单INSERT/UPDATE/DELETE；UTC/ISO固定精确行像环境。同键同内容回放旧结果而不读当前订单补历史，不同内容拒绝。入口/助手默认REVOKE PUBLIC，审计guard校表owner，变化另核嵌套触发；0052降级保留全部事实并恢复0051拒插。

验收命令与实际结果：

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_lifecycle_unbound_command.py backend/tests/integration/test_migrations.py::test_upgrade_creates_exactly_platform_tables backend/tests/integration/test_migrations.py::test_downgrade_then_upgrade_is_idempotent -q -x --tb=short -p no:cacheprovider -o faulthandler_timeout=60
# 27 passed in 52.80s；新增25项+平台2项
.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_history_row_image_types.py backend/tests/unit/quant_strategy/test_lifecycle_command_request.py backend/tests/integration/quant_strategy/test_lifecycle_operation_schema.py backend/tests/integration/quant_strategy/test_lifecycle_command_selection.py backend/tests/integration/quant_strategy/test_lifecycle_command_replay.py backend/tests/integration/quant_strategy/test_initial_entry_completion.py backend/tests/integration/test_migrations.py::test_upgrade_creates_exactly_platform_tables backend/tests/integration/test_migrations.py::test_downgrade_then_upgrade_is_idempotent -q -x --tb=short -p no:cacheprovider -o faulthandler_timeout=60
# 93 passed in 103.34s；含相同平台2项
.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_lifecycle_unbound_command.py::test_stage_captures_real_result_and_caller_owns_commit backend/tests/integration/quant_strategy/test_lifecycle_unbound_command.py::test_incomplete_or_forged_owner_header_cannot_commit -q -x --tb=short -p no:cacheprovider -o faulthandler_timeout=60
# 7 passed in 14.35s；R2后移除无用递延分支及格式修正的定向复核
.venv\Scripts\python.exe -m ruff check backend/migrations/versions/0052_unbound_order_command_capture.py backend/modules/quant_strategy/application/lifecycle_unbound_command.py backend/modules/quant_strategy/infrastructure/lifecycle_operation_models.py backend/modules/quant_strategy/infrastructure/lifecycle_command_replay.py backend/tests/integration/quant_strategy/test_lifecycle_unbound_command.py
# All checks passed
.venv\Scripts\python.exe -m py_compile backend/migrations/versions/0052_unbound_order_command_capture.py backend/modules/quant_strategy/application/lifecycle_unbound_command.py backend/modules/quant_strategy/infrastructure/lifecycle_operation_models.py backend/modules/quant_strategy/infrastructure/lifecycle_command_replay.py backend/tests/integration/quant_strategy/test_lifecycle_unbound_command.py
# 通过
```

计数为118个不同用例（新增25+相关93），不累加重复平台2项或末次7项。覆盖三种结果、三次真实A→B→A状态变化、中文请求键、回滚/外部会话不可见、同键冲突/锁超时后重放、当前订单后来变化不影响历史重放、stale/bound/prior-write拒绝、非法数量/revision全回滚、同/跨事务与普通随机角色权限、缺/伪造结果清单、原请求类型/schema、时区切换以及0052降级/再升级事实保留。

DB安全：执行前读测试函数和fixture。新增测试复用schema fixture，仅随机`liveprofit_lc_history_0051_test_<uuid>`；角色为新UUID NOLOGIN，在已核随机库构造权限反例并DROP OWNED/DROP ROLE清理，不修改现有部署角色。旧回归只在`liveprofit_quant_strategy_test`/`liveprofit_platform_test`等显式隔离库串行；无主库DDL/写入或真实LLM。真实捕获正向不关闭历史触发器；人工故障头也受现有INSERT guard约束。

审查与失败记录：R1一major（清空上下文后漏同事务其他订单变化）与一minor（行像时区漂移）统一修复，R2 PASS无残留。修复后第一次19项执行17 passed/1 failed并提前停止：DELETE反例先命中迁移基线FK，未到拟验封口门禁；改用0052后独立事务创建的无引用订单，加入其他订单INSERT和直接SQL/伪造头反例后最终新文件25项通过。Ruff SIM117格式问题已修；编译及任务范围diff检查通过。经验/规则与知识表结构同步。

未通过项及后续门禁：0051/0052未迁移主库、未授生产角色或接生产API。可信owner可管理触发器，普通随机角色验收不证明部署ACL已核实。此入口仅一个未绑定订单状态命令，已有事务不能拼接；已绑定生命周期操作头、受管状态连续性、全组合/删源selector、全写者、首填输入、历史还原和容量仍待3ar-2至6。后续独立事务旧写者仍不捕获，三个认证字段均False；券商账户/报告全集、结算可卖量与历史可得性保持独立未知。3ar-2整项和T4不勾选，任务不归档/提交；下一主线是已绑定命令的操作头封口与前后状态连续性。

<a id="acceptance-3ar2-bound"></a>

## 2026-10-01 3ar-2已绑定单订单状态命令DB协调增量（PASS，整项进行中）

范围：0053、`application/lifecycle_bound_command.py`、`infrastructure/lifecycle_bound_replay.py`及隔离`test_lifecycle_bound_command.py`；六表不新增列或回填旧事实，旧生产API未接。受控入口在组合→策略/政策→生命周期/订单锁后独立冻结恰一操作，立即前驱须存在且通过完整物理列/原文/管理状态重建；管理状态漂移、新活动成员及无前驱拒绝。前驱固定成员成为终态后仍保留；只推进operation_seq，不伪造state_version递增。固定止损、观察、意图及同证券账户观察锁保留至caller提交，账户观察不当作生命周期余额。

来源恰一修改前ORDER；真实触发捕获每次OLD/NEW，操作头最后插入形成封口，插入即核、命令提交再核完整请求/清单/来源/变化/固定前后状态与真实终态。APPLIED/NO_STATE_CHANGE/BLOCKED均有明确结果；新增BUY执行和在途终态释放仍阻塞。已封口事务清空GUC或提前SET CONSTRAINTS也拒所有额外受管表/订单写入。函数默认撤PUBLIC执行，表owner为可信管理边界；stage不提交/回滚，只读端不取当前投影补历史，三个认证字段恒False。立即前驱核验不是递归完整旧链认证。

验收命令与实际结果：

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_lifecycle_bound_command.py -q -x --tb=short -p no:cacheprovider -o faulthandler_timeout=60
# 最终55 passed in 114.90s (0失败/跳过)
.venv\Scripts\python.exe -m pytest backend/tests/unit/quant_strategy/test_history_row_image_types.py backend/tests/unit/quant_strategy/test_lifecycle_command_request.py backend/tests/integration/quant_strategy/test_lifecycle_operation_schema.py backend/tests/integration/quant_strategy/test_lifecycle_command_selection.py backend/tests/integration/quant_strategy/test_lifecycle_command_replay.py backend/tests/integration/quant_strategy/test_initial_entry_completion.py backend/tests/integration/test_migrations.py::test_upgrade_creates_exactly_platform_tables backend/tests/integration/test_migrations.py::test_downgrade_then_upgrade_is_idempotent -q -x --tb=short -p no:cacheprovider -o faulthandler_timeout=60
# 93 passed in 115.20s
.venv\Scripts\python.exe -m pytest backend/tests/integration/test_migrations.py::test_upgrade_creates_exactly_platform_tables backend/tests/integration/test_migrations.py::test_downgrade_then_upgrade_is_idempotent -q -x --tb=short -p no:cacheprovider -o faulthandler_timeout=60
# R1修复后重复复核2 passed in 5.77s，不累加
.venv\Scripts\python.exe -m ruff check backend/migrations/versions/0053_bound_order_status_capture.py backend/modules/quant_strategy/application/lifecycle_bound_command.py backend/modules/quant_strategy/infrastructure/lifecycle_bound_replay.py backend/tests/integration/quant_strategy/test_lifecycle_bound_command.py
# All checks passed
.venv\Scripts\python.exe -m py_compile backend/migrations/versions/0053_bound_order_status_capture.py backend/modules/quant_strategy/application/lifecycle_bound_command.py backend/modules/quant_strategy/infrastructure/lifecycle_bound_replay.py backend/tests/integration/quant_strategy/test_lifecycle_bound_command.py
# 通过
```

共148个不同用例（55新增+93相关），初轮4/21/33项、0052兼容6项、修复定向17项和重复平台2项不累加。新增覆盖实际三种结果/BUY阻塞、真实A→B→A三次变化、终态成员后继、当前订单后来变化不影响历史重放、请求漂移、caller提交/回滚和外部会话可见性、并发同键超时后重放、前驱和新成员漂移、非法字段/revision全回滚、全部7类受管行更新及空表TRUNCATE、同/跨事务追加、普通随机角色权限、缺操作头提交、三时区以及0053降级/再升级保留事实。另包含13组重新计算摘要的内容反例、六类SQL立即前驱损坏、四类真实固定子行/账户观察锁阻断；0052在0053下四类结果及A→B→A/角色6项真实入口兼容已并入55项。

DB安全：执行前读具体函数/fixture；新增仅随机`liveprofit_lc_history_0051_test_<uuid>`，普通角色为新UUID NOLOGIN并在该库清理。旧回归仅显式隔离测试库串行，无主库DDL/写入、真实LLM或AI测试。六类前驱损坏测试仅由随机库owner暂时关闭审计UPDATE保护以构造故障，在调用真实入口前恢复；不把合成历史当正向捕获。其他正向真实命令不关闭触发器。

审查与未通过项处理：独立R1唯一major指出立即前驱hash未证明订单/止损/观察原件与managed_state关系；SQL与Python同组重建前驱完整schema，迁移基线按当时活动集合过滤，绑定操作按固定集合重建；六类入口和读端损坏回归覆盖后独立R2 PASS，无残留或交互finding。主会话加固定子行及账户观察持锁至提交并以四类外连接100ms超时核验。首轮正常命令拒绝定位为旧closed_at的+08表示与UTC同刻异文，只对closed_at/rule_authorized_at/decision_at管理比较投影规范UTC六位微秒，不修改旧原文/hash；初次新成员fixture省略列名错排、扩展记录helper误用命令头字段均修复，最终55项通过。Ruff、编译、任务范围diff检查通过；知识/experience/强制规则同步。

后续门禁：主库未迁移0051至0053、生产角色未授权、旧生产API未接；此为单状态命令软件验收，全组合/删源selector、多步骤、建单/首填冻结输入、全部写者迁移、逐步完整历史与容量仍按3ar-2至6实施。后续独立事务旧写者仍可改变投影而未留新史，连续性仍未知；券商账户/报告全集、结算可卖量、历史可得性与部署ACL独立未认证。3ar当前1/6，3ar-2整体及T4不勾选，不归档或自动提交。下一主线全组合参与者与删除来源选择器。

<a id="acceptance-3ar2-source-scope"></a>

## 2026-10-01 3ar-2删除来源修改前全集选择增量（PASS，整项进行中）

范围：新增`application/lifecycle_source_selection.py`、随机隔离`test_lifecycle_source_selection.py`。独立请求只含schema/命令种类/task_id/request_key，不以动态revision、引用或预分配operation ID改变幂等字节。按任务全部信号收集订单与意图活引用，包含关闭生命周期、终态订单、仅意图及未绑定订单；每个组合的生命周期步骤去重，未绑定单独列结果承诺。

全部已发现组合按UUID取锁并重查，随后全局策略→政策→生命周期→订单→意图→任务/信号排序锁并刷新；任务和信号FOR UPDATE阻止新信号及新FK引用。来源锁后重新查完整引用身份，发现新增组合/成员、改绑或来源改变整事务重试，不返回部分scope。只允许READ COMMITTED和无先写/先锁的事务；最后核已分配XID，避免driver AUTOCOMMIT逐语句释锁却返回可用scope。可删终态沿用SUCCEEDED/FAILED/CANCELLED；有信号但无引用时返回零组合scope，不写空命令。任务/source及所有目标before保留原SQL文本，caller持锁并负责事务，`scope_only=True`，没有执行或认证授权。

实际命令与结果：

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_lifecycle_source_selection.py backend/tests/integration/quant_strategy/test_lifecycle_command_selection.py backend/tests/unit/quant_strategy/test_lifecycle_command_request.py -q -x --tb=short -p no:cacheprovider -o faulthandler_timeout=60
# 最终50 passed in 69.56s：新增31+原选择19，不含真实LLM
.venv\Scripts\python.exe -m pytest backend/tests/integration/quant_strategy/test_lifecycle_source_selection.py::test_driver_autocommit_cannot_return_scope_with_released_locks -q -x --tb=short -p no:cacheprovider -o faulthandler_timeout=60
# 1 passed in 2.54s，重复复核不累加
.venv\Scripts\python.exe -m ruff check backend/modules/quant_strategy/application/lifecycle_source_selection.py backend/tests/integration/quant_strategy/test_lifecycle_source_selection.py backend/modules/quant_strategy/infrastructure/lifecycle_operation_models.py
# All checks passed（测试with格式修正后）
.venv\Scripts\python.exe -m py_compile backend/modules/quant_strategy/application/lifecycle_source_selection.py backend/tests/integration/quant_strategy/test_lifecycle_source_selection.py backend/modules/quant_strategy/infrastructure/lifecycle_operation_models.py
# 通过
```

覆盖跨两引用组合和未引用第三组合、闭仓/终态/意图独有/未绑定/有信号零引用、每生命周期一步、source/task原像、锁后旧ORM刷新及预分配ID不改请求字节、四类先行变更、真实全局锁序、六类真实锁保持/FK阻断、五种外事务竞态、三类非终态、缺任务/REPEATABLE READ/AUTOCOMMIT和五类请求形状。初轮30 passed（50.84s），显式事务门禁补例后最终50项合跑通过，不重复计数；独立Code Review PASS无finding，主会话补门禁后的只读delta PASS。

安全/失败/未通过项：执行前读具体新增/原回归函数和复用fixture；DB用例只重建随机`liveprofit_lc_history_0051_test_<uuid>`，0053仅该库升级，无主库DDL/写入或AI。初次fixture使用同一信号创建多张订单违反已有唯一约束，改同任务多信号，不改数据库约束。随后自查late INSERT会先被组合FK锁阻断、无法到达最终全集重查，中止旧轮，改无引用旧订单source从NULL更新；信号FK新单反例写未锁第三组合，以排除组合FK干扰，外事务设置250ms上限防测试挂起。最终31项无失败/跳过。该增量没有删任务/信号、修改订单/意图、写命令或提交；原TaskService删除API与生产入口未接。实际受控删源须先完成3ar-3建单输入与消费冻结，再在3ar-4/5同事务清引用/推进revision/封存后删除；全组合业务命令、多步骤封口、全部写者、容量/部署ACL及来源认证仍开放。3ar仍1/6，3ar-2与T4整体不勾选，不归档或自动提交。

## 2026-10-01 验收流程精简（文档检查通过）

- **范围**：AGENTS工作流/Code Review约定、tasks/result模板及骨架职责；当前任务后续验收安排与统一门禁。业务设计、源码、历史测试结论与生产授权保持既有范围。
- **改动**：按业务结果/稳定契约/迁移风险聚合；定向+关联回归收尾，修复delta复验，有效旧证据引用；初轮review无待修finding即结束；完整证据只写result，其他骨架按职责更新。既有3ar-2证据保留并加稳定引用锚点。
- **检查**：核对规则/模板/当前任务没有残留“按文件拆验收”“每检查点全写八文件”“固定两轮代码review”要求；检查本次新增本地文件链接/证据锚点及任务依赖顺序；`git diff --check -- AGENTS.md docs/requirements/templates docs/requirements/量化策略优化与全生命周期验证 docs/experience/index.md docs/experience/pitfalls/workspace/评审循环踩坑.md`。纯文档检查，未运行pytest、DB/DDL或真实LLM；无需代码review。
- **后续门禁**：见[tasks统一门禁](tasks.md#acceptance-gates)；本轮不新增软件验收PASS、不提前勾选3ar-2。减少耗时的效果须以后续业务单元的实际执行验证。

<a id="test-layout-followup-20261002"></a>

## 2026-10-02 集中测试体系后续计划同步（静态检查通过）

- **范围**：复用已完成的[测试目录结构化整理](../archive/测试目录结构化整理/README.md)，仅同步当前任务 plan/tasks 的后续测试路径及维护方式，README/log记录检查点。已执行命令和历史结果保留执行时路径。
- **改动**：测试按领域→业务模块→层级→功能组织；数据集、组合目标、规划器、账本等存量功能扩展已有文件，独立新增能力才拟新增。跨入口/完整流程测试保留独立契约；当前计划引用新运行约定，先定位和读断言/fixture，DB/Redis显式隔离核查与真实依赖人工门禁仍适用。未创建的脚本/目录保持拟新增，未勾选业务标准。
- **索引一致性**：`.venv/Scripts/python.exe -m tests.index --check`，退出0：335个正式测试文件，元数据、源码路径及生成物一致。此检查静态解析，不执行测试。
- **选择入口**：`.venv/Scripts/python.exe -m tests.run --module backend.quant_strategy --related --list`，退出0：量化策略及 analysis、investment_workspace、quant_research、frontend.analysis 共5个模块。此命令只列范围，关联清单须结合真实变更影响判断。
- **存量定位**：`.venv/Scripts/python.exe -m tests.index --source backend/modules/quant_strategy/application/position_planner.py --limit 0`，退出0，定位4个现有功能文件（position_planner、buy_target_planner、reduction_planner、management_runtime）；后续计划复用已有规划器测试。
- **文档关联检查**：逐行检查tasks未勾选项中的38处完整测试路径引用；已存在路径有效，9项尚未交付资产均处于明确拟新增计划中。plan完整测试路径均存在或对应上述拟新增资产；plan与未勾选项旧路径残留0。初版检查暴露一处拟新增措辞不明确，补齐后通过；研究integration目录按拟新增资产处理，不当作已存在。定向`git diff --check`通过。
- **结果边界**：本轮未运行pytest、DB/Redis、浏览器场景、真实LLM/toolkit或外部源，没有新增业务验收PASS。既有目录整理任务记录的存量失败仍按原问题台账处理；当前3ar-2与T1–T8主线状态及[统一后续门禁](tasks.md#acceptance-gates)保持开放。
