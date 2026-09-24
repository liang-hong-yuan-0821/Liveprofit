# db.instrument 指数定向回填（只回填部分指数）

> 一句话结论：定向指数回填使用 `backfill_index_history(..., codes=(...))` 显式参数，公共入口在实际写入连接上持 PG session lock；禁止修改全局 `INDEX_TARGETS`。

## 显式代码集与公共锁（2026-09-23）

单日/短窗口市场补齐使用 `RefreshSpec` + `collect_refresh`；历史回填仍使用下列入口。所有公开采集入口均受同一 PG session advisory lock 保护，逐项 commit 不释放锁；取不到锁先抛 `IngestBusy`，零上游调用。已持有 guard 的组合调用显式传 `guard=guard`，不重复取锁。连接丢失时抛 `IngestSessionLost` 并停止，不能重连继续。

验证：`tests/db/instrument/test_refresh_ingestion_db.py` 在 `liveprofit_instrument_test` + fake provider 下验证公共入口锁冲突、断连停止、逐项提交保留和定向重试。

覆盖读模型和采集跳过判定必须同口径：股票日线需要完整 OHLC+pct_chg，可信停牌只接受已核验的 `source='tushare'`。若只以 close/pct_chg 或任何停牌标志跳过，覆盖查询仍会报缺口，自动任务反复空跑。`backend/tests/integration/market_data/test_refresh_coverage.py` 与 `tests/db/instrument/test_refresh_ingestion.py` 分别验证覆盖和源帧有效性；Worker 子进程没有返回结果时，父进程先重新读 PG 再确定任务终态。

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
