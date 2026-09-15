# US/KR 指数上线方案

> **状态**：方案设计（2026-09-14）
> **关联文档**：[证券市场数据库统一方案](../../archive/证券市场数据库统一方案.md)（market schema 与采集链现状）｜[产品需求分析](../../../knowledge/产品需求分析.md)（§7.2 Q-01 大盘资产目录，实现后需同步）｜[后端方案](../../archive/后端方案.md)（§5.1 资产目录与 US/KR 验收门控定稿）｜[数据库表结构](../../../knowledge/backend/数据库表结构.md)

---

## 一、背景与动机

| 维度 | 现状 | 问题 | 目标 |
|------|------|------|------|
| 展示层 | [MarketIndicesPanel.tsx:23-27](Liveprofit/frontend/src/modules/market/pages/MarketIndicesPanel.tsx#L23-L27) 前端写死 12 指数目录，US 3（.INX/.DJI/.IXIC）与 KR 2（KOSPI/KOSDAQ）`availability: 'UNAVAILABLE'`；[:102-116](Liveprofit/frontend/src/modules/market/pages/MarketIndicesPanel.tsx#L102-L116) 直接渲染"暂不可用/该资产尚未通过实测验收"卡片，**连 bars API 都不发请求**（[:96-100](Liveprofit/frontend/src/modules/market/pages/MarketIndicesPanel.tsx#L96-L100) `useMarketBarsQuery(..., available && rangeValid)`） | 用户要求前端展示美国韩国的重要指数，但大盘区块 US/KR 组只有 5 张"暂不可用"卡片，没有任何 K 线 | US 3 + 韩 KS11 前端展示 K 线（来源 tushare）；KOSDAQ 无可用源，从目录移除 |
| 采集链 | [incremental.py:48-58](Liveprofit/db/instrument/ingest/incremental.py#L48-L58) `INDEX_TARGETS` 只有 9 个 CN 指数；[:74-80](Liveprofit/db/instrument/ingest/incremental.py#L74-L80) `_assert_index_targets_cn_only()` 守卫对任何非 `.SH/.SZ/.BJ` 后缀目标直接 `ValueError`（增量入口 L274、回填入口 [backfill.py:103](Liveprofit/db/instrument/ingest/backfill.py#L103) 两处调用） | `market.instrument` / `market.instrument_daily` 里没有 `.INX`/`KS11` 行——就算前端发请求，[service.py:124](Liveprofit/backend/modules/market_data/application/service.py#L124) 也会 404"资产不存在" | INDEX_TARGETS 扩到 13 个（CN 9 + US 3 + KS11），增量/回填自动采集入库 |
| 数据源 | 产品定稿门控（[后端方案.md:2147](Liveprofit/docs/requirements/archive/后端方案.md#L2147)）："US/KR 不承诺本期上线，只有实测验收通过的资产才能 AVAILABLE"——验收从未做过 | US/KR 恒 UNAVAILABLE 的根因 | 实测验收完成（2026-09-14，见下），4 个资产转 AVAILABLE |
| 验收结论 | **实测（2026-09-14，本机 live 测试）**：tushare `pro.index_global(ts_code='SPX'/'DJI'/'IXIC'/'KS11')` 全通——列 ts_code/trade_date/open/close/high/low/pre_close/change/pct_chg/swing/vol（无 amount）；数据到 2026-09-11（US）/ 2026-09-14（KS11）；历史 ≥2000 年（2000~2009 段实测 2515/2472 行）；单次 8.7 年窗口无截断（1900 年超大窗口超时，靠回填 1825 天分块规避）。新浪源可用：`ak.index_us_stock_sina` US 全历史 ~5712 行、`ak.index_global_hist_sina('首尔综合指数')` ~1000 行。**KOSDAQ 三源均不可用**：tushare index_global 清单无 KQ11、东财 `stock_zh_index_daily_em('KQ11')` 本机返回空表、新浪环球表无此品种 | — | 用户拍板（2026-09-14）：上线 US 3 + 韩 KS11 共 4 个；**韩国组只展示 KS11（韩国综合指数），KOSDAQ 从目录移除** |

## 二、架构设计

本方案不改变现有架构。数据链路：`provider.get_index_data_df → db.instrument.ingest（INDEX_TARGETS 13 个）→ market.instrument / market.instrument_daily → MarketDataService.get_bars → GET /api/v1/market-data/indices/{symbol}/bars → useMarketBarsQuery → CandlestickChart`，与 CN 指数完全同构——US/KR 资产复用整条采集与读路径，只扩采集目标清单与 provider 取数分支。

**无数据模型变更**：[schema.sql:16](Liveprofit/db/instrument/schema.sql#L16) `ts_code VARCHAR(16)` 已兼容 `.INX`/`KS11`（统一方案 2026-09-13 拍板删除 market 列：US/KR 用原 symbol 与 CN 代码格式天然不冲突）；instrument_daily 的 pre_close/change/pct_chg/vol/amount 均非 NOT NULL（close/source 必填由采集层保证）。`_bootstrap_instruments` 的 `data_source` 恒 `"tushare"`（[incremental.py:144-157](Liveprofit/db/instrument/ingest/incremental.py#L144-L157)）在主源 = tushare 下变正确，**不改**。

双源兜底：主源 tushare（env 默认）对非 CN 码返回 None 时自动落 AKShare 新浪（[incremental.py:191-195](Liveprofit/db/instrument/ingest/incremental.py#L191-L195)），`source` 列写实际成功源——US/KR 与 CN 同获双源保护，`_providers_from_env` 与主→兜底顺序**不改**。

## 三、详细设计

### 3.0 模块总览

| 维度 | 问题 | 方案概览 |
|------|------|---------|
| tushare 主源 | [tushare.py:1913-1980](Liveprofit/AI/dataflows/providers/cn/tushare.py#L1913-L1980) `get_index_data_df` 只认 CN 码（`.INX` 透传 `api.index_daily` 必失败返回 None），但 `index_global` 端点实测覆盖 SPX/DJI/IXIC/KS11 且上游自带 pre_close/change/pct_chg | 非 CN 码分派到新增 `_get_global_index_df`（映射 `.INX`→SPX、`.DJI`→DJI、`.IXIC`→IXIC、`KS11`→KS11 直通），映射 10 列标准帧（amount 恒 NaN、swing 丢弃） |
| akshare 新浪兜底 | [akshare.py:1944-1986](Liveprofit/AI/dataflows/providers/cn/akshare.py#L1944-L1986) `get_index_data_df` 的 `_index_symbol` 只认 .SH/.SZ 6 位码（`.INX` 转 `inx` 无效）；[:400-404](Liveprofit/AI/dataflows/providers/cn/akshare.py#L400-L404) `_fetch_global_index` 的 kospi 分支走东财 `stock_zh_index_daily_em('KS11')` 本机实测返回**空表** | 新增 `_get_non_cn_index_data_df`（US→`index_us_stock_sina`、KS11→`index_global_hist_sina('首尔综合指数')`，全历史帧先算 pre_close/change/pct_chg 再按区间过滤）；AI 面 kospi 分支同步改新浪 |
| 采集链 | [incremental.py:74-80](Liveprofit/db/instrument/ingest/incremental.py#L74-L80) CN-only 守卫拒绝一切非 CN 目标（含本次要加的 4 个） | INDEX_TARGETS 末尾追加 4 项；守卫改为"CN 格式 或 NON_CN_INDEX_TARGETS 白名单"；非 CN 跳过因子采集（idx_factor_pro 为 tushare CN 端点）；兜底调用补 try/except |
| CLI 门控 | [market_ingest.py:36-38](Liveprofit/backend/workers/market_ingest.py#L36-L38) `--market != "CN"` 直接退出码 2（生产 run.sh L303 与 daily_job 均不带该 flag，参数已无实际作用） | 删除拒绝块，`--market` 保留兼容 |
| 前端目录 | [MarketIndicesPanel.tsx:23-27](Liveprofit/frontend/src/modules/market/pages/MarketIndicesPanel.tsx#L23-L27) US/KR 恒 UNAVAILABLE；KOSDAQ 无可用源 | US 3 + KS11 改 `'AVAILABLE'`；KOSDAQ 条目删除（目录 12→11） |

### 3.1 tushare 主源（get_index_data_df 非 CN 分支）

#### 3.1.1 模块设计

- **职责**：`TushareProvider.get_index_data_df` 对非 CN 码（`.INX`/`.DJI`/`.IXIC`/`KS11`）改走 `api.index_global`，产出与 CN 路径完全一致的 10 列标准帧（`trade_date` YYYY-MM-DD str 升序 / open / high / low / close / pre_close / change / pct_chg / vol / amount）。
- **新增映射常量**（放 GLOBAL_TECH_INDICES 附近）：

  ```python
  # index_global 端点非 CN 指数映射（2026-09-14 US/KR 上线实测验收）：
  # 键 = 平台 symbol（INDEX_TARGETS/前端目录口径），值 = index_global 的 ts_code
  GLOBAL_INDEX_CODE_MAP = {".INX": "SPX", ".DJI": "DJI", ".IXIC": "IXIC", "KS11": "KS11"}
  ```

- **入口分支**（`get_index_data_df` L1913 起、`_normalize_code` 之前）：

  ```python
  if index_code in GLOBAL_INDEX_CODE_MAP:
      return self._get_global_index_df(GLOBAL_INDEX_CODE_MAP[index_code], start_date, end_date)
  ```

- **新增方法** `_get_global_index_df(ts_global_code, start_date, end_date)`：
  - 日期归一 `start_date.replace("-", "")` / `end_date.replace("-", "")`（兼容增量传 YYYYMMDD 与回填传 YYYY-MM-DD 两种入参，同 CN 路径口径）
  - 单次 `self._api_call(self.api.index_global, ts_code=..., start_date=..., end_date=...)`；**无需内部再分页**——调用方窗口已受限：回填 `_iter_date_chunks` 1825 天/段（实测 8.7 年单次 OK），增量 3 个 CN 交易日
  - 异常/空 → 返回 `None`（契约：失败返回 None 不抛，触发采集链换兜底源）
  - 输出：`pd.to_datetime(df["trade_date"])` 升序排序（端点返回降序，沿用"日线消费必须升序归一"不变量）→ 10 列标准帧，`pre_close/change/pct_chg/vol` **直取上游原值不自算**（与 CN index_daily 扩列三列"存上游原值"同口径）；`amount` 恒 NaN（上游无此列，与 CN AKShare 兜底行 amount 恒 NaN 同口径）；`swing` 丢弃
- **CN 路径不动**：`.SH/.SZ/.BJ` 后缀码与纯数字码仍走原 `api.index_daily` 分页逻辑。

#### 3.1.2 三方依赖能力评估

| 项 | 实测结论（2026-09-14 本机，代理端点 ts.gyzcloud.top） |
|----|------|
| 端点覆盖 | `index_global` 清单含 SPX/DJI/IXIC/KS11 全部 4 个目标；**无 KQ11（KOSDAQ）** → KOSDAQ 不采集 |
| 入参签名 | `pro.index_global(ts_code, trade_date=None, start_date=YYYYMMDD, end_date=YYYYMMDD)`——日期必须 YYYYMMDD（归一化处理） |
| 返回字段 | ts_code/trade_date/open/close/high/low/pre_close/change/pct_chg/swing/vol，**无 amount**；vol 对 US 为股数（如 SPX 2026-09-11 vol=472248）、KS11 偶有 NaN |
| 数据深度 | SPX/KS11 2000 年至今（2000~2009 段 2515/2472 行）；US 最新到 2026-09-11、KS11 到 2026-09-14 |
| 单次窗口 | 8.7 年单次调用成功无截断；1900 年起超大窗口 ReadTimeout——靠回填 1825 天分块规避 |
| 排序 | 端点返回降序（trade_date 20260914 → 20260901）——必须升序归一 |

#### 3.1.3 风险与验证方式

- **风险**：tushare 积分/限频导致 index_global 失败 → 返回 None 自动落新浪兜底（source 写 'akshare'），采集不阻断。
- **验证方式**：新增单测 `tests/dataflows/providers/test_tushare_index_global.py`（MagicMock api 断言分派/升序/10 列/amount NaN/异常 None）；真实库回填后 SQL 校验行数与 source。

#### 3.1.4 文件变更清单

- **修改文件**：[AI/dataflows/providers/cn/tushare.py](Liveprofit/AI/dataflows/providers/cn/tushare.py)（GLOBAL_INDEX_CODE_MAP + get_index_data_df 分支 + _get_global_index_df）
- **新建文件**：[tests/dataflows/providers/test_tushare_index_global.py](Liveprofit/tests/dataflows/providers/test_tushare_index_global.py)（4 用例：映射分派 / 标准帧映射 / 日期归一 / 异常空 None）

### 3.2 akshare 新浪兜底（非 CN 分支）

#### 3.2.1 模块设计

- **职责**：`AKShareProvider.get_index_data_df` 对非 CN 码提供新浪源取数，仅在 tushare 主源失败时被采集链"主→兜底"机制触发。
- **入口分支**（L1944-1954 `if not AKSHARE_AVAILABLE` 之后）：

  ```python
  if index_code.startswith(".") or index_code == "KS11":
      return self._get_non_cn_index_data_df(index_code, start_date, end_date)
  ```

- **新增方法** `_get_non_cn_index_data_df(index_code, start_date, end_date)`：
  - `.INX/.DJI/.IXIC` → `ak.index_us_stock_sina(symbol=code)`（列 date/open/high/low/close/volume/amount，amount 恒 0）；`KS11` → `ak.index_global_hist_sina(symbol="首尔综合指数")`（列 date/open/high/low/close/volume，无 amount）；其余返回 None
  - `_call_with_timeout(fetch, timeout=_AKSHARE_TIMEOUT * 2)` 包裹，异常/空 → None
  - date 列 `pd.to_datetime` 后升序；**在全历史帧上先算** `pre_close = close.shift(1)`、`change = close - pre_close`、`pct_chg = (close/pre_close - 1) * 100`（新浪无此三列；先算后过滤可避免分块回填下"每块首行 pre_close 恒 NULL → [backfill.py:139-156](Liveprofit/db/instrument/ingest/backfill.py#L139-L156) 断点跳过探测永远判缺列重跑"）——全历史首行 pre_close 仍 NaN（入库 NULL 属事实，与 CN 指数基日行同口径）
  - 映射 10 列标准帧（vol=volume、amount=None）后按 `[pd.Timestamp(start_date), pd.Timestamp(end_date)]` 过滤（兼容 YYYYMMDD 与 YYYY-MM-DD），过滤后空 → None
- **AI 面工具修复**：[akshare.py:400-404](Liveprofit/AI/dataflows/providers/cn/akshare.py#L400-L404) `_fetch_global_index` 的 `kospi` 分支先试 `ak.index_global_hist_sina(symbol="首尔综合指数")`，失败回落原东财 `stock_zh_index_daily_em(symbol="KS11")`（东财本机实测空表，改新浪为首选）；docstring L367-370 补充新接口说明。

#### 3.2.2 三方依赖能力评估

| 项 | 实测结论（2026-09-14 本机） |
|----|------|
| US | `index_us_stock_sina(symbol='.INX'/'DJI'/'IXIC')` 全历史 ~5712 行，数据到 2026-09-11；不接受日期参数（全历史拉取后本地过滤） |
| KS11 | `index_global_hist_sina(symbol='首尔综合指数')` ~1000 行（接口上限约 4 年），数据到 2026-09-14；**注意 akshare 内部 map 以中文名 '首尔综合指数' 为 key，传 'KOSPI' 会 KeyError** |
| KOSDAQ | 新浪环球表无此品种；东财空表 → 无兜底源（也不采集） |

#### 3.2.3 风险与验证方式

- **风险**：新浪接口限频/网络异常 → 返回 None，采集链跳过该码当日 bars（增量每日重试、回填可断点续跑）。
- **验证方式**：新增单测 `tests/dataflows/test_akshare_index_data_df.py`（fake_ak 模式：区间首行 pre_close = 区间外前一行 close 是"先算后过滤"核心回归；KS11 传 '首尔综合指数'；异常/空 None；CN 分支不受影响；AI 面 `get_global_index("KOSPI")` 走新浪）。

#### 3.2.4 文件变更清单

- **修改文件**：[AI/dataflows/providers/cn/akshare.py](Liveprofit/AI/dataflows/providers/cn/akshare.py)（get_index_data_df 分支 + _get_non_cn_index_data_df + _fetch_global_index kospi 分支）
- **新建文件**：[tests/dataflows/test_akshare_index_data_df.py](Liveprofit/tests/dataflows/test_akshare_index_data_df.py)（6 用例）

### 3.3 采集链（db/instrument/ingest）

#### 3.3.1 模块设计

- **INDEX_TARGETS 末尾追加 4 项**（[incremental.py:48-58](Liveprofit/db/instrument/ingest/incremental.py#L48-L58)；**必须末尾**——`test_backfill_index_history_do_update_and_bootstrap` 断言写入帧首行为 `000001.SH`，依赖 dict 序）：

  ```python
  ".INX": "标普500",
  ".DJI": "道琼斯工业指数",
  ".IXIC": "纳斯达克综合指数",
  "KS11": "韩国综合指数",
  ```

- **新增白名单与判定**（`_INDEX_DAILY_COLS` 后）：

  ```python
  # 实测验收通过的非 CN 采集目标白名单（2026-09-14 US/KR 上线；KOSDAQ 无可用源不入列）
  NON_CN_INDEX_TARGETS = {".INX", ".DJI", ".IXIC", "KS11"}

  def _is_cn_index_code(code: str) -> bool:
      """CN 指数代码格式判定（.SH/.SZ/.BJ 后缀）。"""
      return code.endswith((".SH", ".SZ", ".BJ"))
  ```

- **守卫改名放宽**（L74-80 `_assert_index_targets_cn_only` → `_assert_index_targets_valid`，调用点 incremental L274、backfill L103 同步）：

  ```python
  def _assert_index_targets_valid() -> None:
      """采集目标守卫（原 CN-only，2026-09-14 US/KR 上线放宽）：
      INDEX_TARGETS 仅允许 CN 格式代码或 NON_CN_INDEX_TARGETS 白名单——
      未知非 CN 目标仍 ValueError（保留防御）。"""
      for code in INDEX_TARGETS:
          if _is_cn_index_code(code) or code in NON_CN_INDEX_TARGETS:
              continue
          raise ValueError(f"INDEX_TARGETS 含未认可目标：{code}（仅 CN 格式或 NON_CN_INDEX_TARGETS 白名单）")
  ```

- **兜底调用补 try/except**（[incremental.py:191-195](Liveprofit/db/instrument/ingest/incremental.py#L191-L195)，与 [backfill.py:129-136](Liveprofit/db/instrument/ingest/backfill.py#L129-L136) 对称——新浪网络异常时兜底源抛异常不应吞掉整个步骤 3）：

  ```python
  if bars is None and fallback_provider is not None:
      try:
          bars = _fetch_index_daily(fallback_provider, code, start, end)
      except Exception as e:
          logger.warning("增量: 指数 %s 兜底源拉取异常（跳过 bars）: %s", code, e)
          bars = None
      if bars is not None:
          source_used = _provider_source(fallback_provider)
  ```

- **非 CN 跳过因子**（incremental L214 前插 `if not _is_cn_index_code(code): continue`；backfill 因子段 L168-184 包进 `if _is_cn_index_code(code):`，L185 `conn.commit()` 保持在块外）——`get_index_factor_df` 是 tushare idx_factor_pro 端点，US/KR 无因子源。
- **不改**：`_bootstrap_instruments`（data_source 恒 "tushare"，主源即 tushare 变正确）、`_providers_from_env`、`_fetch_index_daily`（10 列归一与 ts_code 覆写对无后缀 symbol 无格式依赖）。
- **docstring 同步**：incremental L18-22 不变式（前端 11 / 采集 13 / 白名单守卫）；backfill L8 "9 个" → "13 个（CN 9 + US 3 + KS11）"。

#### 3.3.2 三方依赖能力评估

不适用（本模块无新增外部依赖；provider 能力见 3.1.2/3.2.2）。

#### 3.3.3 风险与验证方式

- **风险**：增量窗口按 CN 交易日历（`_last_trade_days` 取最近 3 个 CN 交易日）——CN 长假 >1 周时，窗口起点仍早于假期起点，US 交易日落在窗口内，无漏采；CN 日历对 US/KR freshness 判定的影响见第四章已确认决策 ④。
- **验证方式**：既有 `tests/db/instrument/` 单测改造 + 新增用例（见下）；真实库回填 SQL 校验。

#### 3.3.4 文件变更清单

- **修改文件**：[db/instrument/ingest/incremental.py](Liveprofit/db/instrument/ingest/incremental.py)、[db/instrument/ingest/backfill.py](Liveprofit/db/instrument/ingest/backfill.py)
- **修改测试**：[tests/db/instrument/test_index_ingestion.py](Liveprofit/tests/db/instrument/test_index_ingestion.py)（守卫用例语义反转 + 因子计数断言）、[tests/db/instrument/test_incremental.py](Liveprofit/tests/db/instrument/test_incremental.py)、[tests/db/instrument/test_backfill.py](Liveprofit/tests/db/instrument/test_backfill.py)

### 3.4 CLI 门控（backend/workers/market_ingest.py）

#### 3.4.1 模块设计

- 删除 [market_ingest.py:36-38](Liveprofit/backend/workers/market_ingest.py#L36-L38) `if args.market != "CN": ... return 2` 拒绝块；`--market` 参数保留兼容，help 改为"保留兼容参数（采集范围由 INDEX_TARGETS 决定）"；模块 docstring L8 同步。

#### 3.4.2 三方依赖能力评估

不适用。

#### 3.4.3 风险与验证方式

- 风险：无（生产 run.sh L303 与 daily_job 均不带该 flag）。
- 验证：`tests/db/instrument/test_market_ingest_wrapper.py` L56-59 用例改（任意 --market 值正常进增量模式）。

#### 3.4.4 文件变更清单

- **修改文件**：[backend/workers/market_ingest.py](Liveprofit/backend/workers/market_ingest.py)、[tests/db/instrument/test_market_ingest_wrapper.py](Liveprofit/tests/db/instrument/test_market_ingest_wrapper.py)

### 3.5 前端目录（MarketIndicesPanel.tsx）

#### 3.5.1 模块设计

- [MarketIndicesPanel.tsx:23-26](Liveprofit/frontend/src/modules/market/pages/MarketIndicesPanel.tsx#L23-L26) US 3 项 availability 改 `'AVAILABLE'`；KOSPI 条目 symbol 改 `'KS11'`、availability 改 `'AVAILABLE'`；**KOSDAQ 条目删除**（目录 12 → 11 项）。
- L15-21 头部注释更新：US 3 + KS11 已实测验收 → AVAILABLE；KOSDAQ 无可用源、用户拍板从目录移除；与 INDEX_TARGETS 13 个的同步不变式。
- 无其他改动：`useMarketBarsQuery` 已按 symbol+market 泛化，CandlestickChart 与指标渲染复用 CN 同路径（US/KR 无因子数据 → indicators 全 null 数组降级，[service.py:157](Liveprofit/backend/modules/market_data/application/service.py#L157) 指数路径既有语义）。

#### 3.5.2 三方依赖能力评估

不适用。

#### 3.5.3 风险与验证方式

- 风险：US/KR 卡片 freshness 按 CN 交易日历判定（见第四章决策 ④）。
- 验证：`MarketIndicesPanel.test.tsx` 两个用例更新（请求清单 11 个、无"暂不可用"卡片、STALE 计数 11）。

#### 3.5.4 文件变更清单

- **修改文件**：[frontend/src/modules/market/pages/MarketIndicesPanel.tsx](Liveprofit/frontend/src/modules/market/pages/MarketIndicesPanel.tsx)、[frontend/src/modules/market/pages/MarketIndicesPanel.test.tsx](Liveprofit/frontend/src/modules/market/pages/MarketIndicesPanel.test.tsx)

## 四、已确认决策 / 待确认问题

已确认决策：

1. **tushare `index_global` 作主源**（2026-09-14 用户指正）：原设计以新浪为唯一源，用户指出 tushare index_global 正覆盖 4 目标且上游自带 pre_close/change/pct_chg——实测确认后改为 tushare 主源、新浪降级为兜底。
2. **韩国只展示 KS11**（2026-09-14 用户拍板）：韩国组只保留韩国综合指数一项，symbol 直接用 tushare 原生代码 `KS11`（不再用 KOSPI 作平台 symbol）；KOSDAQ 无可用源、从目录移除（不再展示"暂不可用"卡片）。
3. **新浪仅作兜底源**：与 CN 双源（tushare 主 + akshare 兜）架构对称，`source` 列写实际成功源。
4. **freshness/开闭市判定仍基于 CN 交易日历**（已知限制不修）：[service.py:276-288](Liveprofit/backend/modules/market_data/application/service.py#L276-L288) `_last_trading_day`/`_session_status` 用 CN 日历——周一美股未开盘时 US 卡片 freshness_status=STALE 显示"数据可能延迟"，属预期内；市场级交易日历接入另立任务。

待确认问题：无阻塞项。

## 实现收尾同步清单（产品需求分析.md）

- 产品需求分析 §7.2 Q-01 与大盘资产目录相关表述：US 3 + KS11 由"暂不可用"改为已上线（实现完成后同步）。

## 验证总表

| 层面 | 方式 | 期望结果 |
|------|------|---------|
| 单测（provider） | `pytest tests/dataflows/providers/test_tushare_index_global.py tests/dataflows/test_akshare_index_data_df.py -q` | 新 10 用例全过；CN 分支无回归（`pytest tests/dataflows -q`） |
| 单测（采集链） | `pytest tests/db/instrument -q` | 守卫反转/因子跳过/兜底异常隔离/兜底 source 标注用例全过 |
| 前端单测 | `pnpm vitest run src/modules/market/pages/MarketIndicesPanel.test.tsx` | 11 指数渲染、11 请求、0"暂不可用"卡片 |
| 真实库回填 | `python -m backend.workers.market_ingest --start 2000-01-01 --skip-bars` | 4 码各 ~6500 行、min trade_date ≈2000-01；instrument.data_source 全 'tushare'；instrument_daily.source 无 'akshare'；KOSDAQ 0 行 |
| API | `curl "http://localhost:8000/api/v1/market-data/indices/.INX/bars?market=US&interval=1d&from=2026-08-01&to=2026-09-14"` | 200、bars 非空、source=tushare；KS11 用 market=KR |
| 前端验收 | 大盘区块页面 | US 组 3 卡片 + 韩国组 KS11 卡片显示 K 线（来源 tushare），无"暂不可用"卡片 |
| 增量 | `python -m backend.workers.market_ingest` | 4 码正常入库、最新 trade_date 前移 |

## 回滚

- 数据面：`DELETE FROM market.instrument_daily / market.instrument WHERE ts_code IN ('.INX','.DJI','.IXIC','KS11')`——CN 数据独立代码空间零影响。
- 展示面：前端 4 项改回 'UNAVAILABLE'、恢复 KOSDAQ 条目。
- 代码面：实施前将 8 个待改文件备份到 `D:/code/workspace/python/.backup_us_kr_20260914/`（仓库外，不污染工作区；工作区有用户未提交改动，git 回退会连带用户改动，故用文件备份）。
