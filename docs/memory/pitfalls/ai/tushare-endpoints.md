# Tushare 代理端点与数据接口坑

> 一句话结论：日线消费必须先 `_sort_asc_by_trade_date` 升序归一（端点返回降序）；全市场拉取禁区间查询（静默截断）；因子取自 idx_factor_pro/stk_factor_pro（禁自算）；代理端点能力必须实测。

## 每日停牌和 DC 单板块漏行（2026-09-24）

- **表象**：`CN_STOCK_DAILY` 目标日 5556/5568 后余 12 个无日线代码；旧 `suspend_d(suspend_date=目标日)` 返回 5000 条且 `suspend_date` 全空，不能作为停牌依据。
- **根因与正确姿势**：[Tushare 官方 `suspend_d` 文档](https://tushare.pro/document/2?doc_id=214)使用 `trade_date`，输出 `trade_date/suspend_type`。本项目代理按 `trade_date` 返回该日 13 条（含 S 与 R），但忽略请求中的 `suspend_type=S` 筛选；必须校验响应日期与类型，并只将 S 当停牌，R 为复牌。修复后 12 个无行情代码均有可信 S 事实，目标日完整。
- **板块源缺行**：2026-09-23 的 `dc_daily` 全板块仅 1030/1031，BK0165.DC 按代码、类别、无类别均无当日 OHLC；`dc_index` 虽有该板块涨跌快照但无完整日线，不可合成 OHLC。东财原始 `push2his` K 线有目标日完整行情，前日值与已有 `sector_daily` 一致；定向补采在 Tushare 缺**有效**目标行时校验代码、日期、必需数值后兜底。不能以日期存在而数值无效为由跳过兜底。
- **历史边界**：BK1675.DC 的 2026-08-03 至 08-06 在东财原始 K 线亦无记录；`HISTORY_GAP` 保留，不能从 `dc_index` 涨跌幅伪造日线。验证：目标日真实 PG/API 覆盖 1031/1031；Provider/采集隔离回归与增量 Code Review 通过。

## 板块日线端点能力实测（2026-09-13，板块概念Treemap方案）

- **dc_daily 是窗口型数据源：仅返回最近 33 交易日，更早区间直接 0 行**（实测 start=20260101 请求仅回 33 行、最早 20260729；start=20260601+end=20260715 旧区间回 0 行）——**不可用于历史回填**，只能每日增量积累（历史自采集启动日起增长）。返回列实测：ts_code/trade_date/close/open/high/low/change/pct_change/vol/amount/swing/turnover_rate/category（**无 pre_close**；pct_change → 统一列名 pct_chg；swing/category 丢弃）。
- **ths_daily 全历史单请求可用**：实测 883300.TI 一次请求 3988 行（2010-04-13 起）、最老板块 885311.TI（2007-08-08 上市）4,642 行、首行 = 上市日精确吻合（无截断）；随机 20/20 板块有数据；列 open/high/low/close/pre_close/change/pct_change/vol/turnover_rate（**无 amount**）；行序降序需升序归一。当前项目"先存 dc"（用户 2026-09-14 拍板），ths 暂缓——启用时走 `get_sector_daily_df` 的 source 分支即可。
- **akshare 东财板块历史主机 push2his.eastmoney.com 在 2026-09-13 的环境不可达**：当时直连与本地代理连接重置；2026-09-24 允许出站的进程已从该主机取得 BK0165.DC 完整日线并修复目标日。因此连通性是运行环境事实，不能把旧观测写成永久能力否定；失败时仍保留缺口、有限重试。
- 官方端点（api.tushare.pro）本项目 token 无效（"您的token不对"）——代理端点（ts.gyzcloud.top）能力即事实标准。

## 代理端点（自定义 URL）

- 位置：`TushareProvider._connect()`（`AI/dataflows/providers/cn/tushare.py`）——`ts.set_token(TUSHARE_TOKEN)` + `ts.pro_api()` 之后覆写私有属性指向自定义端点：

  ```python
  self.api = ts.pro_api()
  self.api._DataApi__http_url = "https://ts.gyzcloud.top/api"  # 自定义 Tushare 端点
  ```

- `_DataApi__http_url` 是 name-mangled 私有属性，写法必须保持双下划线形式；换回官方端点删掉该行即可（默认 `http://api.tushare.pro`）。
- Token 通过 `.env` 的 `TUSHARE_TOKEN` 配置（`LIVEPROFIT_DATA_SOURCE=tushare` 时生效）。
- **代理端点能力可能与官方有差异** → 新增数据函数做"三方依赖能力评估"时必须对代理端点**实测**（真实 token 探测），不能只看 tushare 官方文档。

## 日线接口返回降序（2026-08-23）

- **表象**：行业排名拿到 17 天前的数据。
- **根因**：sw_daily / dc_daily / ths_daily / index_daily 实测按 trade_date **降序**（新→旧）返回，直接 `tail(N)` 会取到最旧数据。
- **正确姿势**：所有日线消费点必须先经 `cn/tushare.py` 的 `_sort_asc_by_trade_date(df)` 升序归一，再 tail/iloc；新增日线消费点必须遵守，单测需含"降序输入"回归用例。

## 端点子日志（2026-08-25 引入）

- `wrap_tushare_api(api)`（dataprovider_log.py）在 `_connect`/trading_calendar 建 api 后包装 `query`，端点调用落在当前 DP 调用目录的 `tushare/{seq:03d}_{api_name}/`（req/res/meta.json，viewer 在 dataprovider 展开内嵌套展示）；仅 tushare 数据源 + run 内有 DP 上下文时落盘，`LIVEPROFIT_TUSHARE_LOG=0` 可关闭。
- **坑 1**：tushare DataApi 的 `__getattr__` 对任意未知属性返回 `partial(self.query, name)`（truthy）→ 包装器幂等/探测标志判定必须查实例 `__dict__`，`getattr` 会误判"已包装"导致完全不落盘。
- **坑 2**：ThreadPoolExecutor 不自动传播 contextvars（Py3.12 实测）→ `_run_with_timeout` 已用 `contextvars.copy_context()` + `ctx.run` 显式带入；任何新增"在 worker 线程内读 contextvar"的代码必须同样显式复制，勿假设自动传播。
- 端点调用超时（`_api_call` 返回 None）后 worker 仍会补写日志（上下文已复制），「DP res 显示超时、tushare 展开却有成功记录」并存属预期诊断行为。
- `_connect` 的连通性探测调用（stock_basic limit=1）在 DP 上下文内会被记录，meta 带 `probe=true`（viewer 显示「🔌 连通性探测」角标）。
- 单次结果 records 超 500 行截断（`truncated=true` + `row_count` 元数据）。

## Future 超时后迟到的本机网络拒绝（2026-09-24）

- **表象**：`_api_call()` 已因 `FutureTimeout` 返回 `None`，但请求线程仍在运行；线程稍后才抛 Windows `WinError 10013`。只在调用线程的 `Future.result()` 异常分支识别，会永远漏掉迟到异常，后续采集可能把本机策略拒绝当作普通上游空结果。
- **根因**：`ThreadPoolExecutor.shutdown(wait=False)` 不取消运行中的 I/O；Future 超时只代表等待方停止等待，不代表底层请求已经结束。
- **正确姿势**：在传给 worker 的调用包装层中捕获并保留特殊网络拒绝，再重新抛出给 Future；调用线程超时后返回仍遵守 Provider 契约，但迟到异常会锁存供采集边界与后续请求识别。保留状态需线程安全、幂等，不能把所有异常都升级成本机网络错误。
- **代码与验证**：`AI/dataflows/providers/cn/tushare.py::_run_with_timeout`、`TushareProvider._remember_network_access_error`；`tests/dataflows/providers/test_tushare_store_methods.py::test_network_permission_error_is_retained_if_worker_fails_after_timeout` 用 Event 控制真实后台线程先超时、再抛 WinError 10013，验证 typed error 留存且后续调用不再碰源。

## 全市场拉取禁止区间查询（2026-08-30）

- **表象**：代理端点 `daily(start,end 区间，无 ts_code)` 2 天区间仅回 6000 行（应 ~11000，**静默截断**）、`fund_daily` 区间返回 0 行。
- **正确姿势**：全市场日线/因子拉取必须 `trade_date` 单日查询；单日行数 ≥6000 视为截断，自动降级为分批补拉（每批 100 代码逗号分隔 + trade_date，store/backfill.fetch_day_frames 已实现该降级，新增全市场消费点必须复用）。

## 概念成分接口参数硬约束（实测）

- ths 用 `ths_member(ts_code=概念代码)`（`code=` 参数被代理忽略，恒回全量截断 6000 行）；dc 用 `dc_member(ts_code=板块代码, trade_date=最近交易日)` 组合过滤（仅 ts_code 返回跨 5 日快照、全量拉截断 8000 行）。

## 技术因子端点（2026-09-12 实测）

- 指数因子走 **`idx_factor_pro`**、个股因子走 **`stk_factor_pro`**——**`stk_factor_pro` 不覆盖指数**（实测三指数代码 0 行、全市场单日拉取 5549 行全为个股）；**`stk_factor` 旧端点已降级**（个股因子列全 None，不可用）。
- 官方文档单次上限：idx_factor_pro 8000 行（实测全历史 8715 行未截断，设计仍按上限分页保险）；`TushareProvider.get_index_factor_df` 已按 5 自然年一段分页（≈1220 行/段），`get_stock_factor_df` 单次调用（消费方短区间）。
- idx_factor_pro 的 close 与 index_daily 逐值一致（实测 3888.1106 等）→ 因子与行情按 trade_date 直接对齐；因子行自带全历史窗口（上游用区间前历史计算，区间首根即有值），**无需补窗口**。
- 两个字段常量：`INDEX_FACTOR_FIELDS`（大盘页 10 因子 + close 对齐自检）/ `STOCK_FACTOR_FIELDS`（个股报告 14 因子，多 ma_bfq_250 + rsi_bfq_6/12/24）。

## 技术指标不自算（2026-09-12 决策）

- 大盘页指数 K 线指标（MA/BOLL/MACD）取自 idx_factor_pro 入库数据（`market_index_factors` 表，随 market_ingest 采集，`--skip-bars` 仅采因子用于全历史回填）；AI 个股技术报告（stockstats.py 已重写）取自 stk_factor_pro。
- **禁止新增任何本地指标计算代码**；因子表按需加列迁移扩展（如未来 KDJ 副图）。实现详见 docs/requirements/archive/技术指标数据源切换方案.md。
- 全历史因子回填的 PG 参数上限分批坑见 [backend/db-test-redis-safety.md](../backend/db-test-redis-safety.md)。
