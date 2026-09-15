# Tushare 代理端点与数据接口坑

> 一句话结论：日线消费必须先 `_sort_asc_by_trade_date` 升序归一（端点返回降序）；全市场拉取禁区间查询（静默截断）；因子取自 idx_factor_pro/stk_factor_pro（禁自算）；代理端点能力必须实测。

## 板块日线端点能力实测（2026-09-13，板块概念Treemap方案）

- **dc_daily 是窗口型数据源：仅返回最近 33 交易日，更早区间直接 0 行**（实测 start=20260101 请求仅回 33 行、最早 20260729；start=20260601+end=20260715 旧区间回 0 行）——**不可用于历史回填**，只能每日增量积累（历史自采集启动日起增长）。返回列实测：ts_code/trade_date/close/open/high/low/change/pct_change/vol/amount/swing/turnover_rate/category（**无 pre_close**；pct_change → 统一列名 pct_chg；swing/category 丢弃）。
- **ths_daily 全历史单请求可用**：实测 883300.TI 一次请求 3988 行（2010-04-13 起）、最老板块 885311.TI（2007-08-08 上市）4,642 行、首行 = 上市日精确吻合（无截断）；随机 20/20 板块有数据；列 open/high/low/close/pre_close/change/pct_change/vol/turnover_rate（**无 amount**）；行序降序需升序归一。当前项目"先存 dc"（用户 2026-09-14 拍板），ths 暂缓——启用时走 `get_sector_daily_df` 的 source 分支即可。
- **akshare 东财板块历史主机 push2his.eastmoney.com 不可达**：直连与本地代理（127.0.0.1:7890）均连接重置（curl 3 次 000）；同域 push2.eastmoney.com 直连正常（kline 主机单独不可达）——板块历史行情不要走东财直连（`stock_board_concept_hist_em` 用该主机）。
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
