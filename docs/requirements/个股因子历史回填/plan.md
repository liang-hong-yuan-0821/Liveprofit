# 个股因子历史回填（factor_daily 一次性回填 Job）

> **状态**：已实施并复核（2026-09-22；默认窗口与指数回填同为 2016-01-01 至今、全量、pacing 0.5s）
> **关联文档**：本方案为一次性数据回填 job，无 README/tasks 骨架；落地文件 = `db/instrument/ingest/backfill.py` 扩展

---

## 一、背景与动机

| 维度 | 现状 | 问题 | 目标 |
|------|------|------|------|
| 策略执行（主要消费方） | `quant_execution_market_data.py` `load_batch` 每票固定取 **250 根** qfq 因子（`ma_qfq_5/20/60`、`boll_*_qfq`、`macd_*_qfq`、`rsi_qfq_6`），历史缺口按 None 对齐、最新一根另有非空门禁 | `market.factor_daily` 个股行起初只有每日增量写的 **2 天**（2026-09-21 基线实测 11,347 行）；`DataReadinessGate` 只看全局 `max(trade_date)`、**不看每票历史覆盖** → 缺值进入模板门禁后形成 `INDICATOR_UNAVAILABLE`/ERROR，基线实测 460 ERROR / 150 BUY | 全市场上市个股的 factor_daily 覆盖 ≥250 根历史窗口，策略扫描数据质量与指数路径同水平 |
| K 线指标（次要消费方） | `get_bars` 个股 180 天窗口按需拉取，`_factor_rows_cover` 修复后残段会触发补拉（每次一只、首看才补） | 每只被看的股票首次都要现场拉上游（几秒延迟）；全市场冷启动时按需拉取总量与一次性回填相同，但体验上"边看边补" | 回填后 180 天窗口天然全覆盖，K 线卡/弹窗零上游延迟 |
| 数据缺口边界 | `market.adj_factor` 2016-01-04 起有历史；`market.trade_status_daily` 策略只读任务有效交易日当日一行（`load_batch:108-112`），当前库仅近 2 天 | 对“最新交易日扫描”，主要缺口是 factor_daily 历史；但任意历史日回放仍会受 adj_factor 起点和 trade_status 当日行限制 | 本方案只解决最新扫描所需的因子历史窗口；历史回放的数据建设另案，不误称其它资源已覆盖所有历史日期 |
| 回填时间窗 | 现有指数回填 CLI 默认 `2016-01-01` 至今；个股上游虽可追溯至 2010-01-04，但不是本次要求的对齐口径 | 若个股默认到 2010，会比指数多回填约 6 年，违背“指数多少天、个股多少天” | 抽出共享常量，指数与个股默认 start 均为 2016-01-01；显式 `--start` 仍可覆盖 |

## 二、架构设计

- 本方案**不新增数据表、字段、API**，不改变每日增量采集；新增一次性回填 CLI 模式，数据流复用既有三件套：

```
CLI: python -m db.instrument.ingest.backfill --stock-factors [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--sleep S] [--retry-failed]
  │
  ├─ 代码清单：market.instrument（instrument_type='stock' AND list_status='L'）≈ 5,567 只
  │
  ├─ 断点判定：instrument_daily LEFT JOIN factor_daily ON (ts_code, trade_date)
  │             （以策略实际消费的本地日线为真值，逐交易日统计 missing_rows；0 才跳过；
  │              无本地日线返回未知态，不误算为完整）
  │
  ├─ 逐票拉取：provider.get_stock_factor_df(ts_code, start, end)
  │             （tushare stk_factor_pro 单次调用；请求 bfq+qfq+rsi 共 25 列，
  │              指标消费 bfq 列、策略消费 qfq 列同帧落库）
  │
  ├─ 入库：bulk_upsert_factor_daily(conn, df, update=True)（既有 DAO，与每日增量同写入口、幂等）
  │
  └─ 失败清单：logs/stock_factor_backfill_failures.json
                （沿用 temp+rename 原子写模式，按 ts_code 存）
```

### 2.1 数据模型设计

无表结构变更。写入字段 = `market.factor_daily` 既有列，写入者新增「一次性回填 job」，与既有写入者（每日增量 `collect_stock_quant_day`、按需拉取 `_fetch_stock_factors_into_table`）共用 `bulk_upsert_factor_daily(update=True)` 幂等入口，无冲突。

## 三、设计概览

### backend

#### 采集与 Worker

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| backfill CLI【修改】 | `main()` 增加 `--stock-factors` 模式与 `--sleep/--retry-failed` 参数；新增个股因子回填主流程 | db/instrument/ingest/backfill.py | `python -m db.instrument.ingest.backfill --stock-factors` 可一键回填全市场个股因子历史 |

#### DAO 与读模型

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| factor_daily DAO【已有，无改动】 | 复用 `bulk_upsert_factor_daily` / `query_range` | db/instrument/dao/factor_daily.py | 无变化 |

#### 服务层

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| 个股 K 线因子缓存覆盖判定【修改】 | 用本次 bars 的实际交易日集合精确校验 factor 行，并要求每个展示日至少存在一个可绘制 bfq 指标值 | backend/modules/market_data/application/service.py | 缺日期或只有 qfq、bfq 全空的缓存都不会再产生“均线系列存在但画不出来”的假完整结果 |

### AI

#### Provider 接口

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| TushareProvider 连接与因子超时【修改】 | token 直传 `pro_api`、失败后 60 秒冷却懒重连、stk_factor_pro 单独 90 秒超时 | AI/dataflows/providers/cn/tushare.py | 不再因 `~/tk.csv` 写权限导致永久断连；全历史大帧减少假超时与重复在途请求 |
| 交易日历 Tushare 初始化【修改】 | token 同样直传 `pro_api` | AI/dataflows/utils/trading_calendar.py | 交易日历查询不再写 `~/tk.csv` |

## 四、详细设计

### 4.0 模块总览

| 维度 | 问题 | 方案概览 |
|------|------|---------|
| CLI 与调度 | backfill.py 现仅支持指数按日回填（`run_backfill` 按交易日循环），无法表达「按股票 × 区间」的个股因子回填 | `--stock-factors` 子模式：独立主流程 `run_stock_factor_backfill`，与指数回填互斥 |
| 单票拉取与入库 | `get_stock_factor_df('603790.SH', ...)` 实测返回 bfq+qfq+rsi 25 列，直接 `bulk_upsert_factor_daily` 可一次写齐两类消费方所需列 | 逐票调用 + 必需列校验 + update=True 入库，失败单票隔离 |
| 断点续跑与失败清单 | 约 5,567 次上游调用、十小时级运行，中断不能从头再来；现有失败清单按「日期」存（`_read_failures`），个股回填需按「代码」存 | 启动时按本地日线逐日反连接因子表形成缺口快照；失败清单新增按 code 的读写（独立文件） |
| 上游连接、限频与进度 | `ts.set_token` 会写 `~/tk.csv`，服务账户实测 PermissionError；大帧在 30 秒下偶发超时；全速连发还可能触发限流或在系统性故障时逐票长时间重试 | token 直传、60 秒冷却懒重连、因子调用 90 秒超时；`--sleep` 默认 0.5s/只，每 100 只打印进度，连续 5 票失败则保存清单并熔断 |

### 4.1 CLI 与调度

#### 4.1.1 模块设计

- 职责：参数解析、代码清单装配、断点快照、主循环编排、汇总输出。
- 接口签名：

```python
def run_stock_factor_backfill(
    conn, start: str, end: str, sleep_seconds: float = 0.5,
    retry_failed: bool = False, provider_factory=None, limit: int | None = None,
) -> dict  # {total, done, skipped, failed, failed_codes}
```

- CLI 参数（`main()` 增补，与既有 `--start/--end/--retry-missing` 并存，`--stock-factors` 与指数回填互斥）：

```
--stock-factors         开启个股因子历史回填（与 --skip-concepts 等指数参数互斥）
--start / --end         YYYY-MM-DD；指数与个股模式共享默认 start=2016-01-01、end=今天；
                        显式传值可覆盖默认窗口
--sleep                 每票 pacing 秒数，默认 0.5
--retry-failed          只重跑失败清单中的代码（忽略断点）
--limit N               仅处理排序后的前 N 只，用于正式小样本验收；默认不限制（全量）
```

- 主流程：加载代码清单 → `_factor_cover_snapshot(conn, start, end)`（本地日线逐日反连接因子表）→ 提交读事务 → 逐票「缺口为 0 跳过 / 拉取 / 入库 / 失败记录」→ 汇总打印。没有本地日线的上市股票返回未知态并尝试拉取，不能把空集合误判为完整。Provider 固定使用 Tushare（AKShare 不提供 stk_factor_pro），初始化未连接时 fail-fast，不逐票空跑。

#### 4.1.2 三方依赖能力评估

- 无新三方依赖；`argparse/logging/pandas` 均为既有栈。

#### 4.1.3 风险与验证方式

- 风险：`--stock-factors` 与指数回填参数误组合 → 启动时参数互斥校验直接报错退出。
- 验证：单测——参数互斥、日期格式/start≤end、sleep/limit 边界、未连接 fail-fast、`--limit` 小样本、汇总字段完整。

### 4.2 单票拉取与入库

#### 4.2.1 模块设计

- 职责：单票「拉取 → 校验 → 入库」，隔离单票失败。

```python
def _backfill_one_stock(conn, provider, ts_code: str, start: str, end: str) -> bool:
    df = provider.get_stock_factor_df(ts_code, start, end)   # 2026-09-21 实测：默认 fields 返回 25 列
    if df is None or df.empty:
        return False                                          # 上游无数据/失败 → 记失败清单
    missing = [c for c in REQUIRED_QFQ + REQUIRED_BFQ if c not in df.columns]
    if missing:
        logger.warning("个股 %s 因子列缺失 %s（跳过）", ts_code, missing)
        return False
    df = df.copy()
    df["ts_code"] = ts_code
    df["updated_at"] = pd.Timestamp.now(tz="UTC")
    bulk_upsert_factor_daily(conn, df, update=True)
    conn.commit()                                             # get_connection 不自管 commit
    return True
```

- 列契约（实测 2026-09-21）：通过 `STOCK_FACTOR_FIELDS` 明确请求 `trade_date, ma_bfq_5/10/20/60/250, boll_mid/upper/lower_bfq, macd_dif/dea/bfq, rsi_bfq_6/12/24, ma_qfq_5/20/60, boll_mid/upper/lower_qfq, macd_dif/dea/qfq, rsi_qfq_6`。入库后指标路径读 bfq 列、策略路径读 qfq 列，同一帧满足两类消费方。
- `close` 列：stk_factor_pro 无 close，宽表可空（既有语义，DAO 清洗置 None）。

#### 4.2.2 三方依赖能力评估

- `get_stock_factor_df`：默认窗口（2016-01-01 起）单票约 2,600 行，即使显式追溯至上游下限 2010-01-04 也约 4,000 行，均**低于 stk_factor_pro 单次 8,000 行上限，无需分页**；大帧使用 90 秒专用超时（`TUSHARE_FACTOR_TIMEOUT` 可配置）。
- `bulk_upsert_factor_daily(conn, df, update=True)`：既有 DAO，与每日增量/按需拉取同一写入口，幂等覆盖无冲突。

#### 4.2.3 风险与验证方式

- 风险：上游返回列集变化（镜像端点行为）→ 必需列校验 fail-closed 跳过并记失败清单，不回写脏行。
- 验证：单测——fake provider 返回帧入库断言（bfq+qfq 列均落库）、列缺失跳过、None/空帧记失败。

### 4.3 断点续跑与失败清单

#### 4.3.1 模块设计

- 职责：中断后重跑不重复拉取；失败代码可单独补拉。

```python
# 断点快照：以 instrument_daily 为真值，按 (ts_code, trade_date) 反连接 factor_daily
def _factor_cover_snapshot(conn, start: str, end: str) -> dict[str, int]  # missing_rows

def _snapshot_covers(missing_rows: int | None) -> bool  # 仅 missing_rows == 0

# 失败清单（按代码，独立文件 logs/stock_factor_backfill_failures.json，temp+rename 原子写）
def _read_failed_codes() -> list[str]
def _write_failed_codes(codes: list[str]) -> None
```

- 断点语义：本地日线窗口内每个 `(ts_code, trade_date)` 都有 factor 行才算完成；无本地日线不是“零缺口”，仍进入拉取。首跑中断后重跑（不带 `--retry-failed`），完整票直接跳过；失败票可走 `--retry-failed`。
- 新股从 `max(start, list_date)` 起统计；区间内部缺一天也会触发整票幂等补拉。该判定比 min/max 严格，避免“首尾存在、中间有洞”被误判。
- 限量试跑只更新本轮代码；失败清单采用“旧失败 − 本轮处理代码 + 本轮新失败”合并，避免 `--limit 10` 误清除全量作业此前记录的其它失败代码。

#### 4.3.2 三方依赖能力评估

- 本模块不依赖外部库/API。

#### 4.3.3 风险与验证方式

- 风险：中途中断发生在单票写入期间。缓解：`bulk_upsert_factor_daily` 整票在单事务内写入并随后 commit，要么全写要么 rollback；即使外部原因形成日期缺洞，下次精确反连接仍会识别并补拉。若首跑用了自定义窗口，`--retry-failed` 必须沿用同一 `--start/--end`；结束日志会打印带精确窗口的补拉命令。
- 验证：单测——0/非 0/缺快照、SQL 参数、内部缺洞、失败清单读写、重跑跳过已覆盖票。

### 4.4 上游连接、限频与进度

#### 4.4.1 模块设计

- 职责：Provider 可恢复连接、因子大帧超时、pacing、进度输出、日志落盘。

- `TushareProvider` 使用 `ts.pro_api(token)`，不调用会写家目录的 `ts.set_token`；首次连接失败后，业务读取 `connected` 每 60 秒最多重试一次。
- `get_stock_factor_df` 使用独立 `TUSHARE_FACTOR_TIMEOUT`（默认 90 秒）；其它 Tushare 端点保持既有 30 秒默认。

```python
# 主循环内：
for i, code in enumerate(codes, 1):
    ...
    if sleep_seconds > 0:
        time.sleep(sleep_seconds)
    if i % 100 == 0:
        logger.info("进度 %d/%d，失败 %d", i, len(codes), failed_count)
```

- 默认 `--sleep 0.5`。真实 10 只样本中老股全历史帧约 2MB，30 秒超时曾出现 2 次、重试成功；调整为 90 秒专用超时后，全量保守预计 **10–24 小时**，不是原估的 2.5–4 小时。
- 单票最多重试 3 次（最后一次失败后不再额外 sleep）；连续 5 票失败视为数据源整体故障或限流，先原子保存已失败代码再中止，避免把全市场逐票耗尽超时。已成功提交的数据保留，恢复后常规重跑由精确断点继续。
- 2026-09-22 实库按默认窗口精确反连接：5,567 只上市股票、已有本地日线的目标约 10,725,799 行、尚缺 10,686,173 行；11 只完整，另有 2 只无本地日线并按未知态尝试拉取。按当前表行宽估算新增主表+索引约 **3–4GB**，另需预留 WAL/临时空间；运行前应确认 PostgreSQL 所在磁盘至少有 8GB 可用。
- 日志：复用 `logs/stock_backfill.log`（`PROGRESS_LOG_PATH` 既有指向），stdout 同步输出。

#### 4.4.2 三方依赖能力评估

- stk_factor_pro 频率与积分：tushare 高积分端点，约 **5,567 次调用**（已完整票会跳过），积分余量由用户评估；先用 `--limit 10` 小样本试跑。

#### 4.4.3 风险与验证方式

- 风险：上游限频掐断大量请求 → 失败清单超长。缓解：失败清单可增量补拉（`--retry-failed` 同样受 pacing 约束）。
- 验证：先执行 `--stock-factors --limit 10` 正式小样本，再去掉 `--limit` 全量。

### 4.5 验证与回滚

#### 4.5.1 模块设计

- 回填后验证（人工 + 脚本）：
  1. 覆盖抽查：随机 10 票，`factor_daily` 行数 ≥250 且 `max(trade_date)` = 最新交易日；
  2. 策略口径：`load_batch` 抽样或重跑 data quality POC——`INDICATOR_UNAVAILABLE` 归零、ERROR 信号不再批量出现；
  3. K 线口径：`get_bars` 对回填票任意 180 天窗口返回完整指标（`_factor_rows_cover` 判定覆盖，不再触发按需拉取）。
- 回滚：**禁止按日期直接 DELETE factor_daily**——该表同时存指数因子、既有增量与按需缓存，且无 `ingest_run_id`，无法仅凭日期可靠区分本次回填行。代码可直接回退；已写入数据是同源权威因子，可安全保留。若确需数据回滚，只能从作业前数据库备份恢复，或另案新增来源批次审计字段后执行精确删除。

#### 4.5.2 三方依赖能力评估

- 本模块不依赖外部库/API。

#### 4.5.3 风险与验证方式

- 风险：回填数据与每日增量竞争写同一行 → `update=True` 幂等、新写覆盖旧写，无脏读风险。
- 验证：单测——fake provider + 真库小样本端到端；契约测试冻结既有 get_bars/按需拉取行为不变。

### 4.6 文件变更清单

- **新建文件**：

| 路径 | 说明 |
|------|------|
| tests/db/instrument/test_stock_factor_backfill.py | 断点覆盖判定、失败清单读写、单票拉取入库（fake provider）单测、CLI 互斥 |
| tests/dataflows/providers/test_tushare_connection.py | token 直传与失败后懒重连回归测试 |
| backend/tests/unit/market_data/test_factor_cover.py | K 线因子覆盖判定及内部缺洞测试 |
| docs/requirements/个股因子历史回填/plan.md | 本方案 |

- **修改文件**：

| 路径 | 改动说明 |
|------|---------|
| db/instrument/ingest/backfill.py | 新增 `run_stock_factor_backfill`、`_factor_cover_snapshot`、`_backfill_one_stock`、按代码失败清单读写；`main()` 增 `--stock-factors/--sleep/--retry-failed` 参数与互斥校验 |
| backend/modules/market_data/application/service.py | K 线请求按实际 bar 日期精确判定因子覆盖，内部缺洞触发按需补拉 |
| AI/dataflows/providers/cn/tushare.py | token 不落盘、懒重连、个股因子 90 秒超时 |
| AI/dataflows/utils/trading_calendar.py | token 直传，不写 `~/tk.csv` |

- **删除文件**：无。

## 五、已确认决策 / 待确认问题

- 已确认决策（2026-09-22 用户拍板）：
  1. **回填窗口严格对齐现有指数 CLI**：指数与个股均默认 `start=2016-01-01`、`end=今天`，由同一 `BACKFILL_START_DEFAULT` 常量驱动，避免后续漂移；上游个股可到 2010-01-04 仅作为显式 `--start` 的能力边界，不作为默认值；
  2. **全量**：`instrument_type='stock' AND list_status='L'`，2026-09-22 实测 5,567 只；新股历史不足是事实（策略侧 WARMUP_INCOMPLETE 语义保留）；
  3. **pacing 0.5s/只**（默认），实施后先小样本试跑再全量；
  4. 回填范围 = factor_daily 个股历史；adj_factor 与 trade_status 不在本次范围内，历史回放仍受它们各自覆盖边界约束；
  5. 不新增表/字段/API，纯一次性采集 job；每日增量不变，按需拉取入口保留，仅修正其缓存覆盖判定；
  6. qfq 与 bfq 同帧落库（镜像端点实测一次调用返回全 25 列，无需两次拉取）。
