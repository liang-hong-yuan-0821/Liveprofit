# 趋势对比面板 任务清单

> **状态**：`已完成`（2026-09-19 五任务全部完成 + Code Review 两轮收敛 PASS，已归档）
> **进度**：5/5 任务
> **下一步**：用户真机目检（T4/T5 两处目检项，见各任务块未勾项）
> **关联方案**：[plan.md](plan.md)（同任务文件夹内方案正文）

---

## 任务总览

| 编号 | 任务 | 依赖 | 状态 |
|------|------|------|------|
| T1 | 市值分层指数采集扩展（INDEX_TARGETS +2 / 守卫 .CSI / 12 处表述随迁） | — | 已完成 |
| T2 | 后端趋势端点（3 DTO + service 1 方法 + 共用执行体与映射 + 2 路由 + codegen 重导出） | —（分层组数据依赖 T5 回填） | 已完成 |
| T3 | 前端共享折线组件（LineChart + buildLineOption 纯函数单测） | — | 已完成 |
| T4 | 趋势对比面板（两 Tab + 归一映射）+ queries + 大盘页集成 | T2、T3 | 已完成 |
| T5 | 真实库回填 + 端到端验证（2 新指数全历史 + 板组零采集核验 + 增量） | T1–T4 | 已完成 |

## 任务

### T1 市值分层指数采集扩展

- **目标**：INDEX_TARGETS 加 000905.SH 中证500、932000.CSI 中证2000（CN 9→11，US/KR 保持末尾）；`_is_cn_index_code` 后缀白名单加 .CSI；12 处数量表述随迁（对应 plan 4.1）
- **涉及文件**：
  - 修改：`db/instrument/ingest/incremental.py`（INDEX_TARGETS +2、守卫加 .CSI、:7/:18-21/:45/:46-48 四处文本）
  - 修改：`db/instrument/ingest/backfill.py`（:8 docstring 数量表述）
  - 修改：`backend/workers/market_ingest.py`（:9 docstring "CN 9" → "CN 11"）
  - 修改：`tests/db/instrument/test_index_ingestion.py`（:59/:67/:115/:206 注释）、`tests/db/instrument/test_incremental.py`（:149 注释）
  - 修改：`frontend/src/modules/market/pages/MarketIndicesPanel.tsx`（:21 注释）、`CLAUDE.md`（代码格式行加 .CSI）
- **依赖**：无
- **验收标准**：
  - [x] `.venv/Scripts/python.exe -m pytest tests/db/instrument -q` 全过（107 passed；含 test_backfill 首帧断言不破坏——新增项在 000001.SH 之后、US/KR 之前）
  - [x] 冒烟：`_assert_index_targets_valid()` 对 15 目标不抛；`_is_cn_index_code("932000.CSI")` 为 True；塞未知代码（如 "KOSDAQ1"）抛 ValueError
  - [x] 按 plan 4.1.1 表逐行核对 12 处"现文本 → 改为"已全改；辅助 grep 用 `CN 9\|9 个\|11 个\|13 个\|13 目标\|13 指数`（**结果需人工分类**：前端 11 卡片等与编制规模无关的文本不改，见 4.1.1 表后"不改的同类文本"清单）——残留命中仅 docs/requirements（本方案文档自身）与 archive/ 历史文档，属不改清单
- **状态**：`已完成`（2026-09-19）

### T2 后端趋势端点

- **目标**：schemas/market.py 追加 3 个趋势 DTO（TrendPointDTO/TrendSeriesDTO/TrendsData）；MarketDataService 加 `get_index_trends`（+ `TrendSeries`/`TrendsDTO` + `CAP_TIER_INDEXES`/`BOARD_INDEXES` 常量）；路由加 `_trends_data` + `_run_trends` + 2 端点；服务层用例 + 契约测试；openapi 重导出 + 前端 codegen（对应 plan 4.2）
- **涉及文件**：
  - 修改：`backend/api/schemas/market.py`（3 DTO）
  - 修改：`backend/modules/market_data/application/service.py`（1 方法 + 2 常量 + 2 dataclass）
  - 修改：`backend/api/routers/market_data.py`（`_trends_data` + `_run_trends` + 2 端点，局部 `_do` 闭包 + trace_id 依赖）
  - 新建：`backend/tests/integration/market_data/test_trends.py`（`env` 取同目录 conftest；`service`/`_seed` 是 `test_market_flow.py` 的模块级 helper，需 import 或复制；`FakeCalendar` 从 `infrastructure/calendar_adapter` 导入——**不可放 `unit/market_data/`**，那里无 conftest/无 DB fixture）
  - 修改：`backend/tests/contract/api/test_market_data.py`（2 端点契约）
- **依赖**：无（代码级）；分层组两新指数数据依赖 T5 回填，T2 完成时端点对它们返回空 points 属预期降级（板组三指数已在库，可立即出真数据）
- **验收标准**：
  - [x] `.venv/Scripts/python.exe -m pytest backend/tests/integration/market_data -q` 全过（7 passed：空窗口序列条目保留 + points 空 / as_of 与 freshness 三态推导 / from>to 抛 RangeTooLargeError；既有 test_market_flow 无回归）
  - [x] `.venv/Scripts/python.exe -m pytest backend/tests/contract/api/test_market_data.py -q` 全过（19 passed：2 端点响应形状 + from>to → **422 + code "RANGE_TOO_LARGE"**）
  - [x] `.venv/Scripts/python.exe -m backend.scripts.export_openapi` + `cd frontend && pnpm run generate:api` 成功，生成客户端含 2 个趋势端点方法（capTierTrendsApiV1MarketDataTrendsCapTiersGet / boardTrendsApiV1MarketDataTrendsBoardsGet + TrendsData 模型）
- **状态**：`已完成`（2026-09-19）

### T3 前端共享折线组件

- **目标**：`LineChart.tsx`（多序列折线，x 轴 type='time'、数据为 `[日期, 值]` 坐标对、y 轴 scale: true、sampling 'lttb'、空序列保留 legend、buildLineOption 纯函数）；写码前读 dataviz skill 配色约定（对应 plan 4.3）
- **涉及文件**：
  - 新建：`frontend/src/shared/charts/LineChart.tsx`、`frontend/src/shared/charts/LineChart.test.tsx`
- **依赖**：无
- **验收标准**：
  - [x] `cd frontend && pnpm vitest run src/shared/charts/` 全过（64 passed：buildLineOption 断言 time 轴 / y 轴 scale / data 元素为二元组 / 空序列进 legend 且 data 空 / 单点不抛 + 槽位配色 + tooltip 降序 + 无 dataZoom；既有 CandlestickChart/drawings 测试无回归）；typecheck 通过（tsc 双工程无错）
- **状态**：`已完成`（2026-09-19）

### T4 趋势对比面板 + 归一映射 + queries + 大盘页集成

- **目标**：`TrendComparisonPanel.tsx`（两 Tab 按钮组 `aria-pressed`、区间选择器默认近1年、「全部」from=1990-01-01、序列名取响应 name、口径图注、"数据截至 {as_of}"+"数据滞后"/「暂无数据」角标、空序列"无数据：<name>"角标、加载态绝对定位 overlay 不挤占布局）；`mappers/toTrendViewModels.ts`（共同首日=100 归一 + 空序列）；queries.ts 2 hooks + queryKeys.ts 2 keys；MarketOverviewPage 市场区块后插入趋势对比区块 + 副标题加"· 趋势对比"（对应 plan 4.4）
- **涉及文件**：
  - 新建：`frontend/src/modules/market/pages/TrendComparisonPanel.tsx` + `.test.tsx`
  - 新建：`frontend/src/modules/market/pages/mappers/toTrendViewModels.ts` + `.test.ts`
  - 修改：`frontend/src/modules/market/pages/queries.ts`（2 hooks + queries.test.ts）、`frontend/src/api/queryKeys.ts`（2 keys）
  - 修改：`frontend/src/modules/market/pages/MarketOverviewPage.tsx`（+测试）
- **依赖**：T2（生成客户端）、T3（LineChart）
- **验收标准**：
  - [x] `cd frontend && pnpm vitest run src/modules/market/pages/` 全过（51 passed：归一映射五断言：共同首日取最大 / 基点前点丢弃且首点=100 / 空序列不干扰基点 / 全空返回 [] / 四舍五入 2 位；面板两 Tab 切换 aria-pressed 翻转、**页面无 role="tab"**、区间切换换查询 key、**区间 from 口径**（近1年=今天减12月，全部=1990-01-01；日溢出夹取 2026-03-31→2026-02-28）、**as_of/freshness 消费**（FRESH 无角标+"数据截至 {as_of}"；STALE → "数据滞后"；UNAVAILABLE → "暂无数据"且无"数据截至"；as_of=null → 整条省略）、空序列角标；queries 请求参数与 key 断言）；前端全量 336 passed、typecheck 双工程无错
  - [ ] 人工目检：大盘页 市场 与 板块 区块之间出现趋势对比区块，两 Tab 切换无页面重排（并入 T5 前端目检）
- **状态**：`已完成`（2026-09-19）

### T5 真实库回填 + 端到端验证

- **目标**：真实 PG 库回填 2 个新指数全历史并验证全链路（对应 plan 4.1.3 验证节；真实端点拉取需代理环境。板组三指数零采集——直接核验已在库）
- **涉及文件**：无代码改动（可能修回填过程中暴露的问题）
- **依赖**：T1–T4
- **验收标准**：
  - [x] 定向回填 000905.SH/932000.CSI 成功（bars 8369 / factors 8368）；SQL 实测：000905.SH 首行 **2005-01-04**（5275 行，比方案实测 2007-01-04 更全——分块回填拿到全历史）、932000.CSI 首行 **2013-12-31**（基日 close=1000 行，比方案预测 2014-01-02 多 1 日）；instrument 表新增 2 行（中证500/中证2000）且因子已入库；断点续跑重跑 000905 全跳过（932000 首段因基日 pre_close 合法 NULL 恒重拉——已知行为，与 000688.SH 同款）
  - [x] 板组零采集核验：000001.SH 8729 行/1990-12-19 起、399006.SZ 3961 行/2010-06-01 起、000688.SH 1630 行/2019-12-31 起，末点均 2026-09-18（无需任何采集动作）
  - [x] API：TestClient 打真实库——2 趋势端点全部 200（cap-tiers 4 条曲线 / boards 3 条曲线）+ from>to → 422 RANGE_TOO_LARGE；freshness 当前返回 STALE = AI 交易日历 2026 缓存未覆盖近期（`get_last_trading_day(20260919)` 30 天回溯落空返回原值），属环境日历陈旧，影响既有 K 线卡片同款判定，非本任务代码缺陷
  - [x] 增量：真实 provider 跑指数步骤 3（板块日线增量 15–25 分钟不在验收范围）——bars=45（15 目标 × 3 日）、factors=33（**CN 11 × 3 日**），新 2 指数 bars/factor 末行均 2026-09-18
  - [ ] 前端目检：大盘页趋势对比区块两 Tab 正常渲染、「全部」区间下分层组四条线同起点（共同首日实测 = **2013-12-31**，因 932000 多 1 个基日行，比方案预测 2014-01-02 早 1 日）、板组三条线同起点（共同首日 2019-12-31）（待用户人工检查）
- **状态**：`已完成`（2026-09-19，前端目检项待用户人工检查）

---

## 拆分与维护规则

- 任务状态实时更新（`待开始`→`进行中`→`已完成`），完成即勾选验收项并同步总览表与 README.md 进度
- 实现完成后按 CLAUDE.md 启动 subagent code review，收尾时架构变更整合进 docs/knowledge/（API契约.md、前端平台.md）
