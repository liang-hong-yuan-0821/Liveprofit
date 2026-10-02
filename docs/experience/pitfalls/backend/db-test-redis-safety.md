# DB 测试隔离 / 契约测试注入 / Redis 数据安全 / PG 参数上限

> 一句话结论：DB 集成测试必须核验隔离库和具体 fixture；不可变历史表不能靠逐用例 TRUNCATE 清理，应重建专用测试库；契约测试靠 monkeypatch AI 侧 config 模块全局切库；对真实 Redis/PG 写数据前必须先检查现存 key/行；大窗口批量 upsert 必须分批（PG 单语句参数上限 65535）。

## 删源全集与锁阻断测试须验证真实落点（2026-10-01）

- **表象**：把晚到成员写成INSERT到已FOR UPDATE锁定的组合，会先阻断在组合FK，验证不到来源锁后的全集重查；同一信号创建多订单又先违反已有唯一约束。
- **根因**：并发反例必须满足其他约束才能到目标路径；driver AUTOCOMMIT可报告READ COMMITTED但每条语句立即释锁。
- **正确姿势**：任务多信号分别对应订单。晚到引用用预存无引用旧单更新source；信号FK反例用未锁第三组合新单，外连接有限lock_timeout。selector入口拒已有XID、全局排序锁并刷新，任务/信号锁后重查，最后核XID存在，拒自动提交无锁scope。
- **验证**：`lifecycle_source_selection.py`与随机`test_lifecycle_source_selection.py`，新增31+原选择19通过，独立CR和事务门禁delta PASS。真实删除/输入冻结/生产接线未实施，scope不授权执行。

## 最终核验后至提交仍须保持固定子行锁（2026-10-01）

- **表象**：组合与生命周期锁不能阻止未统一取组合锁的旧写者直接修改止损、观察、意图或账户观察行；最终投影读取通过不证明提交前这些行未变化。
- **根因**：父行锁不等于所有子行锁，命令GUC/当前XID保护只覆盖本事务。
- **正确姿势**：0053按既定组合→版本/政策→生命周期/订单之后，固定成员的止损→观察→意图→同证券账户观察按UUID排序加行锁并保持到caller提交；账户只是观察证据，不授执行权限。后续全入口治理仍依主线推进。
- **验证**：`test_lifecycle_bound_command.py`四类真实已有行由外连接UPDATE，100ms lock_timeout均阻断，caller提交后原内容可独立重放；新增55项及独立R2 PASS。后续独立事务旧写者仍可写，连续性保持未知。

## 清空命令GUC不能解除同事务封口（2026-10-01）

- **表象**：0052初版在GUC清空后只防原目标订单再写；同事务改另一订单可落业务变化却没有对应命令变化行，提前SET CONSTRAINTS也不能证明此后没有额外变化。
- **根因**：GUC仅是可清空的关联上下文；窄命令的事务承诺是恰一订单，不应只按该订单ID检查已发生写入。
- **正确姿势**：所有订单INSERT/UPDATE/DELETE无上下文时均按当前XID检查窄命令，存在即拒未声明变化；使用部分索引且只对命令头安排一次递延全链检查。结果源插入后封口，之后任何上下文追加均拒绝。GUC不能授审计权限，普通角色与表owner边界独立核验。
- **验证**：真实窄命令后提前SET CONSTRAINTS、清空/伪造GUC及三种其他订单写入均拒绝，savepoint回滚违规写入后原命令仍能提交。初次DELETE命中迁移基线FK，改为升级后独立事务创建无引用订单，确认实际封口门禁。新文件25项与独立R2 PASS；主库和生产角色未迁移/授权。

## 修改前选择只查Session脏集合会漏已flush写入（2026-10-01）

- **表象**：单订单命令selector初版仅拒`Session.new/dirty/deleted`；调用方修改订单并flush后集合清空，或直接execute UPDATE，可把修改后行当作before。
- **根因**：ORM待flush集合不能证明当前数据库事务未执行过变化；原始SQL不进入脏集合。
- **正确姿势**：本单命令选择器使用尚未写入/行锁的事务，允许普通SELECT预读；先核`pg_current_xact_id_if_assigned()`未分配XID，再由选择器统一按组合→版本/政策→生命周期/订单取锁。已flush、原始写入或先锁均保守拒绝，调用方整事务回滚后重选；不将多个命令拼接在一个已执行事务内重选。其他全组合命令须独立一次冻结全部参与者，不据实际变化反推预期集合。
- **验证**：单命令请求纯10项和随机隔离选择9项共19 passed，含三类事务前置动作、只读预读/ORM刷新、100ms锁阻断、真实SQL锁序及初读后并发改绑；独立R1 major修后R2 PASS。尚未接捕获/封口或执行写者，不授权生产。

## 可空CHECK与不可变版本步保护（2026-10-01）

- **表象**：LOCAL_CAUSAL版本步的前后版本可为NULL，原CHECK只写`before>=1 AND after=before+1`仍放行；只有UPDATE/DELETE拒绝还允许直接插造历史和TRUNCATE。
- **根因**：PostgreSQL CHECK只拒FALSE，NULL结果不会拒绝；行级变更保护不涵盖语句级清表，GUC关联成交ID也不是写入授权。
- **正确姿势**：必须明确两版本IS NOT NULL/恰加1，UNATTRIBUTED恰双NULL；由父行触发捕获，提交时核不可变成交的生命周期与组合身份。早先事件不能拿提交时最终版本判等，同一事务可能有多笔成交；升级坏历史拒绝而不补造，回退保护不删除事实。
- **验证**：0050随机隔离库覆盖全空/单空形状、直接INSERT伪GUC、错归属及回滚、同事务多笔、不可变与升级/降级。数据库所有者能关闭触发器不在普通写入保护边界内；部署ACL须独立核实。

## 不可变审计表会拒绝测试夹具 TRUNCATE（2026-09-29）

- **表象**：投资工作区旧 autouse fixture 对 `portfolios ... CASCADE` 执行 TRUNCATE，新迁移下触发既有不可变分配历史表的拒绝触发器，测试在 setup 阶段报 `allocation decision history is immutable`，未执行用例主体。
- **根因**：CASCADE 清理会波及具有不可变审计约束的子表；新增账户观察历史后，类似清理与持久事实的生命周期设计冲突。
- **正确姿势**：先读测试和 fixture 并确认只指向专用 `liveprofit_workspace_test`；改为每用例重建该测试库并运行迁移，不关闭审计触发器，也不将失败误判为业务代码问题。旧组合回归4项与账户观察4项合计8项在隔离PG通过。

## 幂等 schema 测试也可能写主库（2026-09-24）

- **表象**：`tests/db/instrument/test_db.py::test_init_schema_idempotent` 名称像安全回归，实际未使用 `pg_env`，`init_schema()` 从模块默认 DSN 连接主库，执行整个 `schema.sql`；本次新增的 `market.etf_catalog_observation` 因而已在主库建立，但只读核对为0行。
- **根因**：该文件中其他测试刻意连主库做只读或回滚验证，不能根据目录、测试名或同文件 fixture 推断所有用例都已隔离。`CREATE TABLE IF NOT EXISTS` 仍可能修改主库结构。
- **正确姿势**：运行 DB 写入/DDL 测试前读具体测试和 fixture，核验连接注入点；本例已给该测试加 `pg_env` 参数，将 schema 初始化限定到 `liveprofit_instrument_test`，隔离复跑通过。无测试库时跳过，不回退到主库。

## 实时复核沿用提交快照时间（2026-09-27）

- 表象：量化报告账户字段已经从锁后当前账户读取，snapshot_at仍使用任务提交as_of，无法区分任务输入与真实决策依据。
- 根因：从冻结快照切换到实时复核时，只同步了数值字段，遗漏时间语义。
- 正确姿势：`planning_account.py`在读取时生成snapshot_at，报告取该时间；资格decision_at另由数据库当前时间生成，事件ID/修订一并保存。任务提交快照保持独立。独立审查核验通过。

## module 级共享 DB 的集成测试（2026-09-05）

- 必须 autouse fixture 逐用例 TRUNCATE + flushdb 隔离（否则前用例遗留 Outbox/任务被后用例 claim，如 dispatch 数量断言翻倍）。
- pytest 输出经管道时用 `-o faulthandler_timeout` 或写文件排查挂起。

## 契约测试复用 AI 侧全局 config 的注入点（2026-09-08，事件研究审核平台集成引入）

- AI 侧 `AI.eventStudy.collectors.config` 的 `pg_dsn()`/`redis_uri()` 在**调用时读模块全局**（env 仅导入时捕获进模块常量），故契约测试可：

  ```python
  monkeypatch.setattr(es_config, "PG_CONNECTION_STRING", 测试串)  # REDIS_CONNECTION_STRING 同理
  es_config._redis_client = None  # 懒加载客户端重建指向 db 11
  ```

  把 AI 侧连接切到契约测试库；配合 AI 侧函数级 import，补丁在调用时生效。
- 先例：`backend/tests/contract/api/test_event_study_review.py` 的 `_review_test_env` fixture（含 events/assets/event_impacts 最小列集 DDL + 逐用例 TRUNCATE）。

## E2E/脚本向真实 Redis 写草稿前必须先检查现存 key（2026-09-08，曾覆盖 3 条真实草稿）

- **根因**：爬虫 `events:draft_seq` 已分配大量号段，`SET events:pending:<id>` 无条件覆盖会毁掉真实待审草稿（当日靠 dump.rdb 快照 + 临时容器恢复）。
- **正确姿势**：先 `SCAN events:pending:*` + 读 `events:draft_seq`，用**远高于 seq 的 draft_id**（如 9001+）并事后 `DELETE` 清理；向真实 PG 写行同理先确认无同标题行、事后按明确条件 DELETE。

## Redis 数据误覆盖恢复手法（2026-09-08 验证有效）

```bash
docker cp liveprofit-redis:/data/dump.rdb <本地目录>
docker run --rm -v <本地目录>:/data -p 6390:6379 redis:7-alpine redis-server --appendonly no --save ""
docker exec redis-cli -p 6390 --raw GET <key>
```

- 两个坑：Git Bash 的 `/tmp` 路径 Docker Desktop 挂载无效（必须 Windows 形式 `C:/Users/...`）；Windows GBK locale 下 Python subprocess 读中文输出必须显式 `encoding="utf-8"`。

## PG 单语句参数上限 65535（2026-09-12）

- **表象**：全历史因子回填（8715 行 × 14 列 ≈ 12 万参数）单批 `pg_insert().values([...])` 报 `number of parameters must be between 0 and 65535`。
- **正确姿势**：`MarketFactorRepository.upsert` 已按 `_UPSERT_BATCH=2000` 分批（2000×14=28000 参数留余量）；**任何新增"全历史/大窗口批量 upsert"的 Repository 必须同样分批**（单批行数 ≤ 65535/列数 打七折），集成测试需含超单批上限的回归用例（test_factor_ingestion_large_batch_beyond_param_limit）。

## SQLAlchemy行锁不会刷新旧对象（2026-09-27）

- **表象**：同一会话先读持仓，另一个事务成交提交后再取得行锁，仍可能用旧数量覆盖最新持仓；资金锁本身不能消除identity map中的旧对象。
- **根因**：`SELECT ... FOR UPDATE`锁数据库行，不自动覆盖会话已有ORM实例的属性。
- **正确姿势**：组合锁先于订单/持仓锁，锁后查询加`execution_options(populate_existing=True)`；写入前读取最新账户及预留。修改点为`quant_strategy/application/lifecycle_service.py`、`position_lifecycle_manager.py`和`planning_account.py`。
- **验证**：隔离PG双会话先预读position/lifecycle/intent、交错两笔成交后，数量300、生命周期版本累加2、意图revision3及现金18000全部成立；并发两生命周期竞争2500现金仅一份买单通过。


## 跨入口锁顺序与共同决策时点（2026-09-27）

- 表象：量化任务提交先锁策略版本再锁组合，新增实时资格规划先锁组合再锁版本，存在并发环状等待。
- 正确姿势：`quant_task_submission.py`统一组合→版本；`family_batch.py`多版本按UUID排序共享锁，隔离PG捕获真实SQL确认顺序。
- 表象：批次成员资格用同一decision_at，非成员持仓owner家族却无时间截止，可读入决策后才写的事件。
- 正确姿势：所有资格来源含owner身份都加`recorded_at < decision_at`。真实生命周期绑定未来事件的隔离回归确认owner仍未知、持仓占用保留。历史批次重放只返回审计投影，不能当实时订单授权。


## 延期新增订单仍要先协调旧单（2026-09-27）

- 表象：生命周期新目标高于已持仓但低于旧买单完成后的数量，直接保存等待批次状态，会留下超目标旧买单。
- 正确姿势：`position_lifecycle_manager._materialize_delta`先协调旧意图和可撤旧单，再推迟新增BUY；隔离PG验证旧1500股买单被SUPERSEDED且未新建单。终态消费和审计/订单必须在同事务提交，消费投影禁止重放授权。
- 批次目标附带原价入场区间时，消费者必须传到共享planner，在含滑点/tick的order_cost_price处检查；收盘10、上界10不能放行10.01的成本。相关区间内外与滑点越界隔离PG用例通过。


## 同族信号不能拼接不同任务政策（2026-09-27）

- 表象：按strategy_version_id保存execution_policy时，同版本两个任务会覆盖前一政策，最终检查漏掉滑点/费用差异。
- 正确姿势：共同建单从完整家族task/attempt清单自动取数，每族唯一来源；cash_only家族也必须完成。固定任务顺序共享锁，完成状态/当前attempt/日期/组合/版本一起核验。独立review及混来源、cash_only未完成等隔离PG回归通过。
- 首仓比例做整手裁剪后为0时，应明确拒绝并同步摘要计数，不能保留ELIGIBLE却没有订单；订单必须保留source_signal，首次fill才能恢复冻结生命周期。


## 提交服务从自建Session改为调用方Session（2026-09-27）

- 表象：增加stage原子编排入口后，调用方预读的版本/持仓/订单留在identity map，锁后查询仍可能冻结旧数量与预留，甚至沿用已撤销的发布状态。
- 正确姿势：提取事务内stage时，除组合外，所有可变快照来源均显式populate_existing；调用方负责整体commit/rollback，幂等replay也不能中途commit。quant_task_submission的版本/持仓/待成交查询已统一刷新。
- 验证：双Session预读→另一事务更新→stage，冻结最新150股持仓、50股待成交；策略改ARCHIVED后拒绝。整合374项及独立R2 PASS。


## 任务完成后的业务回调必须可恢复（2026-09-27）

- 风险表象：扫描任务先提交SUCCEEDED，进程在共同建单回调前退出，单靠worker回调会永久漏消费；把回调失败写回扫描失败又会导致重复扫描。
- 正确姿势：业务清单与独立终态保存在PG，worker独立事务触发；现有dispatcher启动/恢复周期补查无终态且可收敛的批次。组合锁下以终态主键幂等，receipt/订单/终态共同提交；基础设施异常整体回滚，输入校验错误用savepoint撤销部分写入后保存阻断。不要用Redis冷却充当消费事实。
- 验证：test_batch_completion覆盖漏回调恢复、事务回滚、并发只建一单、取消后拒绝直接消费、当前attempt及跨日终态；396项整合与独立R1 PASS。


## 延期消费必须保留原扫描的品种与owner证明（2026-09-27）

- 表象：延期路径只保存行情与政策，却丢弃stock_symbols分类；消费者按批次CN_STOCK标签重建上下文，会把未经确认的品种当股票。
- 正确姿势：延期日事实保存实际扫描的asset_scope，消费逐项核对冻结owner任务、政策hash、资产类别和当前成功attempt持仓信号；跨族按资格排名、族内先加仓后首仓，落单后刷新账户预留。source_task_id不拼attempt以维持同任务重试日事实hash，当前attempt证明在消费时核验。
- 验证落点：test_joint_orders的unknown_scope/旧attempt/外任务/政策行情冲突拒绝、跨族现金竞争、回滚/并发/重放，以及受控行情的真实worker调用链；test_execution_cancellation验证owner独立窗口与分类持久化。
