# db.instrument 指数定向回填（只回填部分指数）

> 一句话结论：真实库回填想只跑部分指数，进程内 `inc.INDEX_TARGETS.clear()+update(子集)` 后调 `backfill_index_history`（incremental/backfill 共享同一 dict 对象），避免全量 15 码的数百次子调用。

## 手法（2026-09-14 首次验证、2026-09-19 趋势对比面板 T5 第二次复用）

```python
from db.instrument.db import get_connection
from db.instrument.ingest import incremental as inc
from db.instrument.ingest.backfill import _providers_from_env, backfill_index_history

# 共享 dict 对象：backfill 模块 `from incremental import INDEX_TARGETS` 绑定同一对象，
# clear+update 对两侧同时生效（_bootstrap_instruments/_assert_index_targets_valid 都读它）
inc.INDEX_TARGETS.clear()
inc.INDEX_TARGETS.update({"000905.SH": "中证500", "932000.CSI": "中证2000"})

provider = _providers_from_env()[0]()   # CLI 直跑装配（主源, 兜底源）工厂对
with get_connection() as conn:
    result = backfill_index_history(conn, provider, "2000-01-01", "2026-09-19")
```

- 实测（2026-09-19）：2 指数全历史 8369 bars + 8368 factors ≈ 12 分钟（5 年分块 × 请求间隔）。
- 运行前提：`.env` 有 TUSHARE_TOKEN（脚本内 `load_dotenv()`）；国内代理域名直连（`NO_PROXY=127.0.0.1,localhost,ts.gyzcloud.top`，见 workspace 代理经验）。
- 断点续跑重跑时已入库段跳过；**首段恒重拉的已知例外**：该段含指数首行且 pre_close 合法 NULL（如 932000.CSI 基日 2013-12-31、000688.SH 基日）→ 缺列探测命中 → 整段重拉重写（幂等 DO UPDATE，只费少量配额，非 bug）。
- 点测（单请求）与回填（5 年分块）的历史深度可能不同：000905.SH 单请求实测 4791 行/2007-01-04 起，分块回填实际 5275 行/2005-01-04 起——验收断言以回填后查库为准。
