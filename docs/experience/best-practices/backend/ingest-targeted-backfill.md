# db.instrument 指数定向回填（只回填部分指数）

> 一句话结论：定向指数回填使用 `backfill_index_history(..., codes=(...))` 显式参数，公共入口在实际写入连接上持 PG session lock；禁止修改全局 `INDEX_TARGETS`。

## 有界历史因子批次先筛缺口再限额（2026-09-26）

历史因子`--limit N`必须作用在逐证券缺口快照筛出的待补集合上。若先对全市场有序代码表截取前N只，再在循环中跳过已覆盖者，重复运行会永久重访相同前缀，后续证券永远得不到处理。现有`run_stock_factor_backfill`先按`_factor_cover_snapshot`筛掉完整代码，再限额；相同区间重跑下一批会自然前进。验证：首批2025年50只待补股票成功50、失败0，`test_limited_run_advances_past_covered_prefix`及同文件28项定向测试通过。

## 历史状态重采只写缺口证券（2026-09-26）

在2016年已有703,249条完整状态、只余896条时，按日全市场重采会为补少数长期停牌股票反复覆盖同日约3,000条已核验事实，并更新其时间戳。`backfill_status`先在PG按与年审计相同的状态有效性条件筛`missing_status_codes`，再把代码集传给日采集器；提供器仍验证完整上游日源，写入仅限缺口证券，已有可信行不被无谓重写。隔离库验证部分日期只返回缺口代码，2016-01-04实库先补2只长期停牌股票，其余2,806条保留。

## 显式代码集与公共锁（2026-09-23）

单日/短窗口市场补齐使用 `RefreshSpec` + `collect_refresh`；历史回填仍使用下列入口。所有公开采集入口均受同一 PG session advisory lock 保护，逐项 commit 不释放锁；取不到锁先抛 `IngestBusy`，零上游调用。已持有 guard 的组合调用显式传 `guard=guard`，不重复取锁。连接丢失时抛 `IngestSessionLost` 并停止，不能重连继续。

验证：`tests/db/instrument/test_refresh_ingestion_db.py` 在 `liveprofit_instrument_test` + fake provider 下验证公共入口锁冲突、断连停止、逐项提交保留和定向重试。

覆盖读模型和采集跳过判定必须同口径：股票日线需要完整 OHLC+pct_chg，可信停牌需来自已核验的完整状态或独立证据。若只以 close/pct_chg 或任何停牌标志跳过，覆盖查询仍会报缺口，自动任务反复空跑。`backend/tests/integration/market_data/test_refresh_coverage.py` 与 `tests/db/instrument/test_refresh_ingestion.py` 分别验证覆盖和源帧有效性；Worker 子进程没有返回结果时，父进程先重新读 PG 再确定任务终态。

2026-09-26 补充：可信全天停牌事实有三种持久载体：完整`trade_status_daily`、独立`market.suspension_source_daily`及核对过日线的公司/交易所`market.suspension_evidence`。日常覆盖、补采跳过、历史洞检查和年度审计必须同时识别这三者；复合状态其他字段仍逐项核验，不以独立停牌事实填造ST。`test_independent_full_day_evidence_closes_refresh_gap_and_changes_digest`在隔离测试库验证来源证据使缺口关闭且覆盖摘要变化，相关60项测试通过。

## 历史回填手法

```python
from db.instrument.db import get_connection
from db.instrument.ingest.backfill import _providers_from_env, backfill_index_history

provider = _providers_from_env()[0]()   # CLI 直跑装配（主源, 兜底源）工厂对
with get_connection() as conn:
    result = backfill_index_history(
        conn, provider, "2000-01-01", "2026-09-19",
        codes=("000905.SH", "932000.CSI"),
    )
```

- 实测（2026-09-19）：2 指数全历史 8369 bars + 8368 factors ≈ 12 分钟（5 年分块 × 请求间隔）。
- 运行前提：`.env` 有 TUSHARE_TOKEN（脚本内 `load_dotenv()`）；国内代理域名直连（`NO_PROXY=127.0.0.1,localhost,ts.gyzcloud.top`，见 workspace 代理经验）。
- 断点续跑重跑时已入库段跳过；**首段恒重拉的已知例外**：该段含指数首行且 pre_close 合法 NULL（如 932000.CSI 基日 2013-12-31、000688.SH 基日）→ 缺列探测命中 → 整段重拉重写（幂等 DO UPDATE，只费少量配额，非 bug）。
- 点测（单请求）与回填（5 年分块）的历史深度可能不同：000905.SH 单请求实测 4791 行/2007-01-04 起，分块回填实际 5275 行/2005-01-04 起——验收断言以回填后查库为准。

## 独立 CLI 变更通知

低层 CLI 的 Redis 通知在 `db/instrument/ingest/notifications.py` 中立装配，优先 `LIVEPROFIT_REDIS_URL`，其次 `REDIS_CONNECTION_STRING` 或 `REDIS_HOST/PORT/PASSWORD/DB`。提交后按 resource 写 changed 标记并清 coverage；Redis 不可用不回滚已提交 PG 数据。未配置 Redis 时由平台覆盖重算和 5 分钟活跃查询刷新兜底。平台 `market_ingest` 走 `CoreSettings.resolved_market_dsn()`，保证与 Worker/API 相同写库。


## 共享资源的 coverage 缓存失效（2026-09-24）

资源名不等于独立数据表。`CN_STOCK_DAILY` 与内部 `CN_STOCK_QUANT_INPUTS` 都会更新或读取 `instrument_daily`、`trade_status_daily` 事实，因此任一资源提交后都必须使两边的 Redis coverage 快照失效。将依赖关系放在 ingestion commit notification 上，使用同一写事务的 `IngestGuard.changed` 提交后通知；不要只清作业自身资源的缓存。

验证：PostgreSQL 集成测试分别断言量化 qfq/status 组提交与公开日线提交都会通知这两个资源键；`RefreshRepository` 后续重新读取事实并缓存新 coverage。

## 定向独立历史源与完整名单分工（2026-09-26）

独立源批次通过`scope_codes`声明年度证券范围，复用同一不可变批次/观察模型：NULL代表全沪深，显式代码数组代表定向。两种都按独立交易日历及上市退市边界验全；定向批次不会进入全市场ST输入选择，但可提供明确未交易证券日，逐日与Tushare ST/日线/S-R交叉后写混合来源状态。实测2022两只股票349个有效证券日全部抽取，230个缺状态目标日均报未交易；1月4日只读交叉得到两条全天停牌且ST为真，限价未知保留NULL。定向scope非法/不完整拒绝、不得升级全量、混合来源写入均经隔离库验证，R/定时S冲突三组纯Mock通过，独立CR R2 PASS。
