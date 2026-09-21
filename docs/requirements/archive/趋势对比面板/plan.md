# 趋势对比面板方案

> **状态**：任务分解（2026-09-19：板 Tab 数据源改官方指数、归一基点定「共同首日=100」→ R1 FAIL→修复 → R2 FAIL（1 major）→修复 → **R3 最终核验 PASS**（10 维度全 ≥8，均分 9.4）→ 用户确认，tasks.md 定稿开工）
> **关联文档**：[README.md](README.md)（任务总览）

---

## 一、背景与动机

| 维度 | 现状 | 问题 | 目标 |
|------|------|------|------|
| 整体 | 大盘页（[MarketOverviewPage.tsx](../../../../frontend/src/modules/market/pages/MarketOverviewPage.tsx)）只有 市场（指数独立 K 线卡片 [MarketIndicesPanel.tsx](../../../../frontend/src/modules/market/pages/MarketIndicesPanel.tsx)）/ 板块（概念 treemap）/ 信息 三区块；图表组件只有 [CandlestickChart.tsx](../../../../frontend/src/shared/charts/CandlestickChart.tsx)，无多序列折线组件 | 无法横向对比"不同市值的趋势"（大盘蓝筹 vs 微盘的相对强弱）、"不同板的趋势"——单指数 K 线卡片是各自独立的绝对走势，跨指数无对比视图 | 大盘页新增「趋势对比」区块，两 Tab：市值分层（沪深300/中证500/中证1000/中证2000）、市场板（上证综指/创业板指/科创50），均按**共同首日=100** 归一；区间选择器 近3月/近1年/近3年/全部 |
| 数据（市值分层） | 指数采集清单 `INDEX_TARGETS`（[incremental.py:49-65](../../../../db/instrument/ingest/incremental.py)）CN 9 项中已有 沪深300（000300.SH）、中证1000（000852.SH）——实测库内各 5275 行、2005-01-04 起；中证500（000905.SH）、中证2000（932000.CSI）不在清单，**库内无任何行**（2026-09-19 查库确认）；代码守卫 `_is_cn_index_code`（[incremental.py:71-73](../../../../db/instrument/ingest/incremental.py)）只认 `.SH/.SZ/.BJ` 后缀 | 中证500/中证2000 无库内数据：`market.instrument` 指数行 15 个中无 000905.SH/932000.CSI；932000.CSI 后缀为 `.CSI`，加进清单会被 `_assert_index_targets_valid`（[incremental.py:90-98](../../../../db/instrument/ingest/incremental.py)）抛 ValueError 拒绝 | 000905.SH/932000.CSI 纳入采集清单（CN 11）+ 全历史回填 + 每日增量；四层（沪深300/中证500/中证1000/中证2000）中**已在库 2 条 + 本次回填 2 条**（T5 完成前分层组两新序列 `points` 为空，按 4.2.1 空窗口契约降级） |
| 数据（板） | **三个官方指数已在库且每日增量**（2026-09-19 查库）：上证综指 000001.SH 8729 行/1990-12-19 起、创业板指 399006.SZ 3961 行/2010-06-01 起、科创50 000688.SH 1630 行/2019-12-31 起——三者均属 `INDEX_TARGETS` CN 组 | 缺"板对比"视图；且官方指数体系里**不存在"主板指数"**（2026-09-19 实测 `index_basic`：SSE 208 个、CSI 8961 个指数中含"主板"命中 0 个，仅深交所 395001.SZ「主板A股」覆盖深市一半）；自编"全板等权曲线"需依赖 `stock_info.market`（仅同步在册股票）→ 幸存者偏差（实测 `instrument_daily` 中约 4.3%、≈250 个股票代码不在 `stock_info`；精确计数依赖"哪些代码算股票"的分类口径） | 板 Tab 用**已有官方指数**（上证综指/创业板指/科创50），零采集改动；口径差异（综指含科创板、样本指数只覆盖头部）在图注与 DTO 注释如实写明 |
| 后端 | 市场数据路由 [market_data.py](../../../../backend/api/routers/market_data.py) 有 5 端点（指数/个股/概念 K 线、概念树、热门概念），服务层 `MarketDataService.get_bars` 为单资产读模型 | 无多资产趋势端点 | 新增 2 端点 `/market-data/trends/{cap-tiers,boards}`，服务层 1 个方法（按传入指数清单逐序列查询） |
| 前端 | 大盘页区块间无趋势对比组件；Query 模式见 [queries.ts](../../../../frontend/src/modules/market/pages/queries.ts)；无多序列折线 ECharts 组件 | 无法渲染跨指数对比曲线 | 新增共享折线组件 + 趋势对比面板（两 Tab） |

## 二、架构设计

数据流（两层，本方案无新增表）：

```
tushare 代理（index_daily）
        │ 000905.SH/932000.CSI 回填 + 每日增量（db.instrument.ingest）
        │ （板三指数与 000300/000852 已在库，无采集改动）
        ▼
market.instrument_daily（指数∪个股∪基金日线，已存在）
        │ backend MarketDataService.get_index_trends（逐序列 DAO 区间查询）
        ▼
GET /api/v1/market-data/trends/{cap-tiers,boards}
        │ React Query（openapi 生成客户端）
        ▼
TrendComparisonPanel（大盘页新区块，两 Tab + 区间选择器）
  ├─ 市值分层 Tab：4 条归一曲线（共同首日=100）
  └─ 板 Tab：3 条归一曲线（共同首日=100）
```

本方案不改变现有架构：指数采集复用 `INDEX_TARGETS` 机制；趋势读模型复用 `market_conn` + `instrument_daily.query_range`（DAO 不改）；API 复用 Envelope 包装；前端复用大盘页区块 + React Query 模式。

**无数据模型变更**——不新增表、不改既有表结构、不加 DAO 方法（行业 Tab 已暂缓，见「五、已确认决策」4）。

## 三、设计概览

> 按顶层分组（backend / frontend），层内从底向上分层：backend（采集 → 服务 → DTO → 路由）、frontend（共享组件 → API 接入 → 页面区块 → 页面集成）。无内容的分层已跳过。
> 本方案不涉及 AI 层与数据表/DAO 层（不改 Provider、不动 AI 分析链路、无新增表、无新 DAO 方法）。
> 「四、详细设计」模块与此分层一一对应：4.1 采集 / 4.2 服务+DTO+路由 / 4.3 共享组件 / 4.4 面板+接入+集成。

### backend

#### 采集与 Worker

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| INDEX_TARGETS【修改】 | 加 000905.SH 中证500、932000.CSI 中证2000（CN 9→11，US/KR 保持末尾） | db/instrument/ingest/incremental.py | 每日增量自动采集新 2 指数日线+因子；回填自动覆盖 |
| `_is_cn_index_code` 守卫【修改】 | 后缀白名单加 .CSI | db/instrument/ingest/incremental.py、db/instrument/ingest/backfill.py（共用） | 932000.CSI 通过守卫校验并走因子路径 |
| 数量表述随迁【修改】 | 12 处"CN 9/13 个/9 个"文本同步 | incremental.py、backfill.py、backend/workers/market_ingest.py、tests/db/instrument/*、MarketIndicesPanel.tsx | 注释与代码事实一致（清单见 4.1.1） |

#### 服务层

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| MarketDataService.get_index_trends【新增】 | 传入指数清单，逐序列区间查询 + 汇总 as_of/freshness | backend/modules/market_data/application/service.py | 两个 Tab 共用一个读方法（组语义由常量承载） |

#### DTO 与契约

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| 3 个趋势 DTO【新增】 | TrendPointDTO / TrendSeriesDTO / TrendsData | backend/api/schemas/market.py | 趋势响应模型（两端点共用同一族） |

#### API 路由

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| 2 端点 + 共用执行体与映射【新增】 | /market-data/trends/{cap-tiers,boards} + `_run_trends` + `_trends_data` | backend/api/routers/market_data.py | 趋势数据 API（Envelope 包装，tag market-data） |

### frontend

#### 共享组件

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| LineChart【新增】 | 多序列折线组件（time 轴 + buildLineOption 纯函数） | frontend/src/shared/charts/LineChart.tsx | 两 Tab 复用同一图表组件 |

#### API 接入

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| queries 2 hooks【新增】 | useCapTierTrendsQuery / useBoardTrendsQuery | frontend/src/modules/market/pages/queries.ts、frontend/src/api/queryKeys.ts | 趋势数据 React Query 接入 |

#### 页面区块

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| 归一映射【新增】 | toTrendChartSeries（共同首日=100、空序列） | frontend/src/modules/market/pages/mappers/toTrendViewModels.ts | DTO → 图表 ViewModel（纯函数，可单测） |
| TrendComparisonPanel【新增】 | 两 Tab 按钮组 + 区间选择器 + 未采集角标 | frontend/src/modules/market/pages/TrendComparisonPanel.tsx | 大盘页新「趋势对比」区块组件 |

#### 页面集成

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| MarketOverviewPage【修改】 | 市场区块后插入趋势对比区块 + 副标题 | frontend/src/modules/market/pages/MarketOverviewPage.tsx | 页面多一个区块 |

## 四、详细设计

模块按实现层**从底向上**排序，与「三、设计概览」分层一一对应：backend（4.1 采集 → 4.2 服务+DTO+路由）→ frontend（4.3 共享组件 → 4.4 面板+接入+集成）。每个模块内部按固定子节展开，不涉及的子节跳过。

### 4.0 模块总览

> **每一行与下方 4.1、4.2... 的模块小节一一对应**（维度名 = 小节标题）；只列设计模块，三方依赖能力评估 / 风险与验证 / 文件变更等过程小节不入表。**"问题"列必须附一个具体例子**（现状中的代码事实：文件路径/函数名/可观测现象），禁止抽象措辞。

| 维度 | 问题 | 方案概览 |
|------|------|---------|
| 市值分层指数采集扩展 | `INDEX_TARGETS`（incremental.py:49-65）CN 9 项缺 000905.SH/932000.CSI——2026-09-19 查库确认两代码在 `market.instrument_daily` **无任何行**；932000.CSI 的 `.CSI` 后缀会被 `_is_cn_index_code`（incremental.py:71-73）判为非 CN、被 `_assert_index_targets_valid` 拒绝 | 清单加 2 项 + 守卫后缀加 .CSI + 12 处数量表述随迁；回填/增量自动生效（实测单请求全历史 4791/3093 行不截断） |
| 后端趋势端点 | 市场数据 API 只有单资产 K 线与概念树（market_data.py 5 端点），无多资产趋势读模型 | 1 个服务方法 + 3 个 DTO + 2 端点：按传入指数清单逐序列 `query_range` 取 close，汇总 as_of/freshness |
| 前端共享折线组件 | 无多序列折线组件（`shared/charts/` 只有 CandlestickChart、drawings） | LineChart（time 轴、`[日期, 值]` 坐标对、y 轴 scale、空序列降级）+ buildLineOption 纯函数 |
| 趋势面板与页面集成 | 大盘页无对比视图（三个区块均为独立 Query 边界，无跨指数归一曲线） | TrendComparisonPanel 两 Tab 区块（按钮组，非 role="tab"）+ 归一映射 + 2 个 hooks + 大盘页插入 |

### 4.1 市值分层指数采集扩展

#### 4.1.1 模块设计

- `INDEX_TARGETS`（incremental.py:49-65）CN 组内新增两项，US/KR 四项保持末尾（[test_backfill.py:370-375](../../../../tests/db/instrument/test_backfill.py) 首帧断言 = 000001.SH，新增项在其后不破坏）：
  - `"000905.SH": "中证500"`（插在 `"000852.SH"` 行后）
  - `"932000.CSI": "中证2000"`（插在 `"000698.SH"` 行后）
- `_is_cn_index_code`（incremental.py:71-73）后缀白名单 `.SH/.SZ/.BJ` → `.SH/.SZ/.BJ/.CSI`。932000.CSI 纳入 CN 后因子路径（idx_factor_pro）自动生效；`market.instrument.ts_code VARCHAR(16)` 容纳 '932000.CSI'（10 字符）
- 清单同步不变式核对：前端 `MARKET_INDEX_CATALOG` CN 7（[MarketIndicesPanel.tsx:23-35](../../../../frontend/src/modules/market/pages/MarketIndicesPanel.tsx)）⊆ 后端 CN 11 ✓ 保持（先例：000300.SH/000698.SH 仅采集不上平台，本次再增 000905.SH/932000.CSI 两项同性质）
- **数量表述随迁（维度 4：全量枚举，12 处）**——"13 个：CN 9 + US 3 + KS11" → "15 个：CN 11 + US 3 + KS11"，"9 个" → "11 个"：

  | # | 位置 | 现文本 | 改为 |
  |---|------|--------|------|
  | 1 | [incremental.py:7](../../../../db/instrument/ingest/incremental.py) | "INDEX_TARGETS 9 个（前端写死清单 7 CN ∪ TARGET_ASSETS 四指数去重）" | "INDEX_TARGETS 11 个" |
  | 2 | [incremental.py:18-21](../../../../db/instrument/ingest/incremental.py) | "INDEX_TARGETS 是 13 个（CN 9 + US 3 + KS11——…+ 000300.SH/000698.SH 仅采集不上平台）" | "15 个（CN 11 + US 3 + KS11）"；"仅采集不上平台" 2 项 → 4 项（加 000905.SH/932000.CSI） |
  | 3 | [incremental.py:45](../../../../db/instrument/ingest/incremental.py) | "# 采集目标 9 个（决策 13 前置改造后：…）" | "# 采集目标 11 个" |
  | 4 | [incremental.py:46-48](../../../../db/instrument/ingest/incremental.py) | "000300.SH/000698.SH 用 TARGET_ASSETS 名称" | 四代码并列（加 000905.SH/932000.CSI） |
  | 5 | [backfill.py:8](../../../../db/instrument/ingest/backfill.py) | "INDEX_TARGETS 13 个：CN 9 + US 3 + KS11" | "INDEX_TARGETS 15 个：CN 11 + US 3 + KS11" |
  | 6 | [market_ingest.py:9](../../../../backend/workers/market_ingest.py) | "2026-09-14 US/KR 上线后含 CN 9 + US 3 + KS11" | "含 CN 11 + US 3 + KS11" |
  | 7 | [test_index_ingestion.py:59](../../../../tests/db/instrument/test_index_ingestion.py) | "13 个目标均返回同帧" | "15 个目标" |
  | 8 | [test_index_ingestion.py:67](../../../../tests/db/instrument/test_index_ingestion.py) | "自举落库：13 指数 instrument 行存在" | "15 指数" |
  | 9 | [test_index_ingestion.py:115](../../../../tests/db/instrument/test_index_ingestion.py) | "仅 CN 9 目标 × 2 行" | "仅 CN 11 目标" |
  | 10 | [test_index_ingestion.py:206](../../../../tests/db/instrument/test_index_ingestion.py) | "13 目标（9 CN + 4 白名单）不抛" | "15 目标（11 CN + 4 白名单）" |
  | 11 | [test_incremental.py:149](../../../../tests/db/instrument/test_incremental.py) | "全部 13 目标都走完兜底" | "全部 15 目标" |
  | 12 | [MarketIndicesPanel.tsx:21](../../../../frontend/src/modules/market/pages/MarketIndicesPanel.tsx) | "（13 个：CN 9 + US 3 + KS11）" | "（15 个：CN 11 + US 3 + KS11）" |

  **仅注释改动、无逻辑改动**——已核实其余断言自适应：[test_incremental.py:153](../../../../tests/db/instrument/test_incremental.py) 与 [:161](../../../../tests/db/instrument/test_incremental.py) 用 `len(inc.INDEX_TARGETS)` / 集合推导、[test_index_ingestion.py:116](../../../../tests/db/instrument/test_index_ingestion.py) 用 `cn_count = sum(...)` 动态计数、[test_backfill.py:494](../../../../tests/db/instrument/test_backfill.py) 用 `_is_cn_index_code` 过滤
  **不改的同类文本**（与 INDEX_TARGETS 规模无关，已逐条核对）：[incremental.py:18](../../../../db/instrument/ingest/incremental.py) 前半句"前端 MARKET_INDEX_CATALOG 是 11 个（7 CN + US 3 + KS11）"、[incremental.py:165](../../../../db/instrument/ingest/incremental.py) "CN 7 平台口径优先"、[MarketIndicesPanel.tsx:22](../../../../frontend/src/modules/market/pages/MarketIndicesPanel.tsx) "CN 7 与后端 CN 子集一致"、[MarketIndicesPanel.test.tsx:108/120/124/146](../../../../frontend/src/modules/market/pages/MarketIndicesPanel.test.tsx) 前端 11 卡片断言、[0003_seed_market_assets.py:3](../../../../backend/migrations/versions/0003_seed_market_assets.py)（历史迁移文件不动）
- CLAUDE.md「证券市场数据库命名规范」代码格式行同步：`CN 资产 ts_code = 6 位数字 + .SH/.SZ/.BJ/.CSI 后缀`

#### 4.1.2 三方依赖能力评估

实测（2026-09-18/19，代理端点 ts.gyzcloud.top）：
- `index_daily(000905.SH)` 单请求全历史 4791 行、20070104~20260918 ✓；`index_daily(932000.CSI)` 3093 行、20140102~20260918 ✓（回填仍走既有 5 年分块路径 `_iter_date_chunks`）——**T5 实测修正**：分块回填实际拿到更全历史（000905 5275 行/2005-01-04 起、932000 3094 行/2013-12-31 基日行起），单请求测量低估了深度
- `idx_factor_pro` 对两代码均可用：实测 932000.CSI 与 000905.SH 在 2014-01-01~2018-12-31 窗口**各 1220 行**（20140102~20181228）✓；回填按 5 自然年分段（`INDEX_CHUNK_DAYS=1825`），与既有 CN 指数同款路径，无截断风险
- `index_daily(8841431.WI)` 万得微盘股指数 0 行 ✗（index_basic 全市场检索亦无"微盘"）→ 用户已拍板微盘 = 中证2000 一条线
- **板 Tab 零外部依赖**：上证综指/创业板指/科创50 已在库（见「一、背景与动机」数据（板）行实测行数与起点）

#### 4.1.3 风险与验证方式

- 风险：932000.CSI 实为**单源**（tushare）——AKShare 兜底对 `.CSI` 后缀恒不生效：[akshare.py:1953-1959](../../../../AI/dataflows/providers/cn/akshare.py) `_index_symbol('932000.CSI')` 走 `if "." in code` 分支产出 `csi932000`，而 `ak.stock_zh_index_daily` 只认 `sh000300` 这类 exchange+num 形式 → 兜底请求必失败。主源失败时按既有语义跳过该指数 bars、次日窗口重拉自愈（既有 CN 指数如 000300.SH 有可用兜底，本差异仅限 `.CSI`；如需双源，须另在 Provider 补 `.CSI` 分支，本方案不做）
- 验证：回填后实测断言——`market.instrument_daily` 中 000905.SH 首行 2007-01-04、932000.CSI 首行 2014-01-02；`python -m db.instrument.ingest.backfill` 断点续跑跳过已入库段（**T5 实测修正**：000905 首行实际 2005-01-04、932000 首行实际 2013-12-31 基日行，见 4.1.2 修正注）

#### 4.1.4 文件变更清单

- **修改文件**：
  | 路径 | 改动说明 |
  |------|---------|
  | db/instrument/ingest/incremental.py | INDEX_TARGETS +2 项；`_is_cn_index_code` 加 .CSI；:7/:18-21/:45/:46-48 四处文本同步 |
  | db/instrument/ingest/backfill.py | :8 模块 docstring 数量表述同步 |
  | backend/workers/market_ingest.py | :9 docstring "CN 9" → "CN 11" |
  | tests/db/instrument/test_index_ingestion.py | :59/:67/:115/:206 注释同步（断言动态计数不改） |
  | tests/db/instrument/test_incremental.py | :149 注释同步 |
  | frontend/src/modules/market/pages/MarketIndicesPanel.tsx | :21 注释 "13 个：CN 9" → "15 个：CN 11" |
  | CLAUDE.md | 命名规范代码格式行加 .CSI |

### 4.2 后端趋势端点

#### 4.2.1 模块设计

**服务层**（backend/modules/market_data/application/service.py，新增模块级常量 + 1 个方法）：

```python
# 组语义常量（服务端硬编码，前端不持有名单——沿用目录写死先例）
CAP_TIER_INDEXES: list[tuple[str, str]] = [
    ("000300.SH", "沪深300"), ("000905.SH", "中证500"),
    ("000852.SH", "中证1000"), ("932000.CSI", "中证2000"),
]
BOARD_INDEXES: list[tuple[str, str]] = [
    ("000001.SH", "上证综指"), ("399006.SZ", "创业板指"), ("000688.SH", "科创50"),
]

@dataclass(frozen=True)
class TrendSeries:
    symbol: str
    name: str
    points: list[dict]      # [{"date": date, "close": float}]，按 date 升序

@dataclass(frozen=True)
class TrendsDTO:
    from_date: date         # 请求区间回显（_trends_data 映射到 TrendsData.from_/to）
    to_date: date
    series: list[TrendSeries]
    as_of: date | None
    freshness_status: str   # FRESH/STALE/UNAVAILABLE

def get_index_trends(self, *, indexes, from_date: date, to_date: date) -> TrendsDTO
```

方法行为：
1. `from_date > to_date` → `RangeTooLargeError`（与 `get_bars` 同语义，[service.py:122-123](../../../../backend/modules/market_data/application/service.py)）
2. 单连接内逐序列 `instrument_daily.query_range(conn, ts_code, start, end)`（[dao/instrument_daily.py:71](../../../../db/instrument/dao/instrument_daily.py)，**DAO 不改**）取 `trade_date`/`close`，`close` 为 NaN 的点跳过
3. 逐序列查询后，在同一连接内做一次汇总查询取各序列末点：
   `SELECT ts_code, max(trade_date) FROM market.instrument_daily WHERE ts_code = ANY(%s) GROUP BY ts_code`
   - `as_of` = 汇总行 `max(trade_date)` 的最大值（**全历史口径**，与 `get_bars` 的 `latest` 同款——用于"数据截至"展示与 freshness 判定）；无任何行 → `None`
   - `freshness_status` = 既有 `_last_trading_day()` 口径（[service.py:155-158](../../../../backend/modules/market_data/application/service.py)）：as_of ≥ 最近交易日 → FRESH；否则 STALE；as_of 为 None → UNAVAILABLE
   - **不返回 `source` / `source_updated_at`**：本读模型是多资产聚合（4 层指数 / 3 板指数），逐序列 provenance 无消费方，聚合成单值（如"最大值"）语义失真；`data_source` 两组合计 7 个指数同为 `tushare`，前端图注用固定文案即可。若后续要做"来源不一致"提示，再按需加回（届时以逐序列字段形态加）
4. **空窗口契约（与 `get_bars` 一致，不得新增分支语义）**：区间内无行 → 该序列 `points: []`（**序列条目仍返回**，前端据此显示"无数据"角标）；全部序列皆空 → `series` 仍含全部条目、`as_of` 取全历史口径（可能非 None）、`freshness_status` 由 as_of 判定。**不抛错、不 404**
5. 连接纪律：全程 `with self._market_conn() as conn:` 上下文管理器（同 `get_bars`）；**不新增吞异常的 `try/except` 分支**——若实现中出现 `except`，必须在该分支 `conn.rollback()`（共享 PG conn 纪律，[CLAUDE.md Code Review 规则](../../../../CLAUDE.md)）

**DTO**（backend/api/schemas/market.py 追加 3 个）：

```python
class TrendPointDTO(BaseModel):
    date: date            # 交易日（无分时，故用 date 而非既有 BarDTO.timestamp）
    close: float

class TrendSeriesDTO(BaseModel):
    symbol: str
    name: str             # 展示名一律取本字段（前端不硬编码层级名/板名）
    points: list[TrendPointDTO]

class TrendsData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: date = Field(alias="from")
    to: date
    series: list[TrendSeriesDTO]
    as_of: date | None
    freshness_status: Freshness
```

> **口径差异注释（写进 `TrendsData` docstring，供后续维护者看到）**：三条板曲线是**官方指数的编制口径**，非"全板等权"——上证综指 2020-07-22 修订后纳入科创板、创业板指为 100 只样本股、科创50 为 50 只样本股；四条分层曲线同理是指数公司样本口径。本字段组不承载口径元数据（响应里只回 `name` 与点），口径文案在前端图注固定写明（4.4.1）。

> 命名说明（沿用既有分族，避免与 `BarDTO`/`BarsData` 混淆）：点=一个交易日一个 close，故为**新响应族** `points`（不复用 `bars`，无 OHLCV 负担）；应用层用 `TrendSeries`/`TrendsDTO`、API 层用 `TrendSeriesDTO`/`TrendsData`——与既有 `BarsDTO`（应用层）/`BarDTO`+`BarsData`（API 层）分族方式一致，路由同时 import 两模块不冲突。`TrendsData` 回显 `from`/`to`（同 `BarsData`）；不返回 `market_session_status`/`market_closed_reason`（概念/热点端点同款：与休市无关的读模型不带会话状态）；**不返回 `source_updated_at`**（多资产聚合下取值口径无意义，见上条方法行为第 3 点）。

**路由**（backend/api/routers/market_data.py，tag 沿用 "market-data"）：

```python
def _trends_data(trends_dto) -> TrendsData:
    """TrendsDTO → TrendsData（两趋势端点共用，同 _bars_data 先例）。"""
    return TrendsData(
        from_=trends_dto.from_date, to=trends_dto.to_date,
        series=[TrendSeriesDTO(symbol=s.symbol, name=s.name,
                               points=[TrendPointDTO(**point) for point in s.points])
                for s in trends_dto.series],
        as_of=trends_dto.as_of,
        freshness_status=trends_dto.freshness_status,
    )

async def _run_trends(request: Request, indexes: list[tuple[str, str]], from_: date, to: date):
    """趋势端点共用执行体（sector_bars 同款局部 _do 闭包；_run_get_bars 绑定 get_bars，不可复用）。"""
    services = request.app.state.analysis_services
    calendar = request.app.state.market_calendar
    market_conn = request.app.state.market_conn

    def _do():
        return MarketDataService(calendar=calendar, market_conn=market_conn).get_index_trends(
            indexes=indexes, from_date=from_, to_date=to)

    return await services.run(_do)

@router.get("/market-data/trends/cap-tiers", response_model=Envelope[TrendsData])
async def cap_tier_trends(
    request: Request,
    from_: date = Query(alias="from"),
    to: date = Query(),
    trace_id: str = Depends(ensure_trace_context),
):
    trends_dto = await _run_trends(request, CAP_TIER_INDEXES, from_, to)
    meta = EnvelopeMeta(request_id=request.state.trace_id)
    return Envelope(data=_trends_data(trends_dto), meta=meta).model_dump(by_alias=True)

@router.get("/market-data/trends/boards", response_model=Envelope[TrendsData])
async def board_trends(request, from_, to, trace_id):  # 同上，传 BOARD_INDEXES
    ...
```

（`from_date`/`to_date` 驱动 `TrendsDTO` → `TrendsData` 的区间回显；`trace_id: str = Depends(ensure_trace_context)` 是每个端点的既有必需项——[market_data.py:87](../../../../backend/api/routers/market_data.py)。）

改完后按 CLAUDE.md 约定：`python -m backend.scripts.export_openapi` + `cd frontend && pnpm run generate:api` 再动前端消费代码。

#### 4.2.2 三方依赖能力评估

本模块只依赖库内数据（已实测：`instrument_daily` 覆盖两组的全部 7 个指数代码，其中板组 3 个、分层组 2 个已在库、2 个待 4.1 回填）；无新外部依赖。

#### 4.2.3 风险与验证方式

- 风险：分层组 000905.SH/932000.CSI 未回填前 `points` 为空 → 前端"无数据"角标（4.4 已设计），不整面板报错
- 验证：
  - 服务层用例（**新建 `backend/tests/integration/market_data/test_trends.py`**，与既有 `test_market_flow.py` 同目录）：`get_index_trends` 读真库，复用该目录的沙箱与 fixture——`env` 由该目录 `conftest.py` 提供（PG 沙箱，自动可用）；`service(env)`（注入 `market_conn` 工厂）+ `_seed(env, sql, params)` 是 **`test_market_flow.py` 的模块级定义**（[test_market_flow.py:23-37](../../../../backend/tests/integration/market_data/test_market_flow.py)），pytest 模块级 fixture/helper 不跨文件共享 → 新文件需从该模块 **import 或复制这两个 helper**（或先把二者上提到 `conftest.py`）；`FakeCalendar` 从 [infrastructure/calendar_adapter.py](../../../../backend/modules/market_data/infrastructure/calendar_adapter.py) 导入（`FakeCalendar(trading_day=..., last_day=...)`，同 [test_market_flow.py:20](../../../../backend/tests/integration/market_data/test_market_flow.py)）。**不能放 `backend/tests/unit/market_data/`**：该目录只有 `test_indicators.py`，无 conftest、无 `env`/`service` fixture，DB 场景构造不出来。断言：① 空窗口 → 序列条目保留且 `points == []`；② `as_of`/`freshness_status` 推导（seed 末点=最近交易日 → FRESH；末点落后 → STALE；无行 → UNAVAILABLE）；③ `from > to` → `RangeTooLargeError`
  - 契约测试（`backend/tests/contract/api/test_market_data.py` 追加，复用 `_seed_instrument`/`_seed_bars` 直插 market schema 模式）：2 端点 200 响应形状 + **`from>to` → 422 + `code == "RANGE_TOO_LARGE"`**（[exception_handlers.py:66](../../../../backend/api/exception_handlers.py) `"RANGE_TOO_LARGE": (422, False)`；断言照抄既有 [:93-98](../../../../backend/tests/contract/api/test_market_data.py)）
  - 端到端：两端点各打一次真实库，断言 cap-tiers 4 条 / boards 3 条序列

#### 4.2.4 文件变更清单

- **修改文件**：
  | 路径 | 改动说明 |
  |------|---------|
  | backend/api/schemas/market.py | 3 个趋势 DTO（TrendPointDTO/TrendSeriesDTO/TrendsData） |
  | backend/modules/market_data/application/service.py | `get_index_trends` + `TrendSeries`/`TrendsDTO` + 2 个常量 |
  | backend/api/routers/market_data.py | `_trends_data` + `_run_trends` + 2 端点 |
  | backend/tests/contract/api/test_market_data.py | 追加 2 端点契约用例（复用既有 `_seed_instrument`/`_seed_bars`） |
- **新建文件**：
  | 路径 | 说明 |
  |------|---------|
  | backend/tests/integration/market_data/test_trends.py | 服务层用例（`env` 走同目录 conftest；`service`/`_seed` 从 `test_market_flow` import 或复制；`FakeCalendar` 从 `infrastructure/calendar_adapter` 导入） |

### 4.3 前端共享折线组件

#### 4.3.1 模块设计

- **共享组件 LineChart**（frontend/src/shared/charts/LineChart.tsx）：

  ```ts
  export interface LineChartSeries {
    name: string;
    color?: string;
    data: Array<[string, number]>;   // [交易日 ISO 字符串, 值] —— ECharts time 轴的坐标对格式
  }
  export interface LineChartViewModel { series: LineChartSeries[]; height?: number }
  export function buildLineOption(vm: LineChartViewModel): EChartsOption   // 纯函数，单测入口
  ```

  - **x 轴 `type: 'time'`**（不设 `type: 'category'`——类目轴 markLine 小数坐标坑；time 轴天然支持任意日期落点）
  - **数据必须是 `[日期, 值]` 坐标对**，不得写成 `{time, value}` 对象（time 轴对非 pair 结构静默不渲染）
  - **y 轴 `scale: true`**：归一曲线值域集中在 100 附近，y 轴从 0 起会把差异压平到看不出（关键显示参数）
  - `series[].showSymbol: false`、`sampling: 'lttb'`（全历史降采样）、`connectNulls: false`
  - tooltip `trigger: 'axis'` + `axisPointer: { type: 'cross' }`，按值降序展示
  - **空序列降级**：`data: []` 的序列保留在 legend（用户可见"哪条线没有数据"）但不画点
  - **不启用 dataZoom**（区间切换在 React 层换查询，规避 `echarts-datazoom-anchors` 坑）

#### 4.3.2 三方依赖能力评估

- ECharts 已在前端使用（CandlestickChart/ConceptTreemap）；`line` 为 ECharts 内建 series 类型，无新依赖
- 写图表代码前读 dataviz skill（配色/对比度/深浅色一致约定）
- 时间轴坐标对格式与 `scale` 语义以官方 API 为准（本方案已按上条约束写死，不做数据极值近似）

#### 4.3.3 风险与验证方式

- 风险：无（纯渲染组件，空/单点序列降级在设计内）
- 验证：`buildLineOption` 纯函数单测（jsdom，既有 chart 测试模式）——断言 ① x 轴 type='time'；② y 轴 scale=true；③ 序列 data 元素为 `[string, number]` 二元组；④ 空序列仍进 legend 且 data 为空；⑤ 单点序列不抛错

#### 4.3.4 文件变更清单

- **新建文件**：
  | 路径 | 说明 |
  |------|------|
  | frontend/src/shared/charts/LineChart.tsx | 多序列折线组件 + buildLineOption 纯函数 |
  | frontend/src/shared/charts/LineChart.test.tsx | 纯函数单测 |

### 4.4 趋势面板与页面集成

#### 4.4.1 模块设计

- **归一映射**（新建 frontend/src/modules/market/pages/mappers/toTrendViewModels.ts，与 `toChartViewModels.ts` 同目录先例）：

  ```ts
  /** 共同首日 = 各非空序列首点日期的最大值（= 所有序列都有数据的第一个交易日）。 */
  export function commonBaseDate(series: TrendSeriesDTO[]): string | null
  /** DTO → 图表 ViewModel：按共同首日归一（共同首日 = 100），baseDate 之前的点丢弃。 */
  export function toTrendChartSeries(series: TrendSeriesDTO[]): LineChartSeries[]
  ```

  算法（**共同首日=100**，2026-09-19 用户拍板）：
  1. `baseDate` = 全部非空序列首点日期的**最大值**
  2. 每条非空序列：基点 = 该序列中 `date >= baseDate` 的首个点（正常即 baseDate 当天；某序列在 baseDate 缺行时才落到其后首个有数据的交易日）→ 基点 close 记为 100
  3. 该序列只保留 `date >= 基点日期` 的点，值 = `round(close / baseClose * 100, 2)`
  4. 空序列 → `data: []`（保留在 legend，面板另出"无数据"角标）；全部为空 → 返回 `[]`
  5. **`baseDate` 之前的点被丢弃是共同首日口径的必然代价**——实测两组的共同首日：「全部」区间下 市值分层组 = **2014-01-02**（中证2000 起点，故沪深300/中证1000 的 2005–2013 段不显示）、板 组 = **2019-12-31**（科创50 起点，故上证综指 1990–2019 段不显示）——**T5 实测修正**：932000.CSI 回填后首行为 2013-12-31 基日行 → 分层组共同首日实际 = **2013-12-31**（比预测早 1 个交易日）
- **TrendComparisonPanel**（market/pages/TrendComparisonPanel.tsx）：
  - 两 Tab 用**按钮组**（`<button type="button" aria-pressed>`）——**不得使用 `role="tab"`**：[MarketOverviewPage.test.tsx:97](../../../../frontend/src/modules/market/pages/MarketOverviewPage.test.tsx) 断言页面 `queryByRole('tab')` 为空、且 shared/ui 无 Tabs 组件
  - 区间选择器（近3月/近1年/近3年/全部，默认近1年）；**区间口径写死如下**（`to` 一律 = 今天）：
    | 选项 | `from` | 取值示例（今天 = 2026-09-19） |
    |------|--------|------------------------------|
    | 近3月 | `addMonths(今天, -3)` | 2026-06-19 |
    | 近1年（默认） | `addMonths(今天, -12)` | 2025-09-19 |
    | 近3年 | `addMonths(今天, -36)` | 2023-09-19 |
    | 全部 | 固定 `1990-01-01` | 早于任何库内数据（上证综指首行 1990-12-19） |

    `addMonths(d, -n)` = 目标月同日，**日溢出时夹取到目标月最后一天**（如 `2026-03-31` 减 1 月 → `2026-02-28`），不得用 `setMonth` 裸调（会把 1/31 滚成 3/3）。区间边界只决定**取多少历史**：归一按共同首日裁掉基点日之前的点（4.4.1 归一算法），故几天级的日期漂移不影响曲线正确性。服务端只做 BETWEEN 查询、无区间上限校验（`RANGE_TOO_LARGE` 仅在 `from > to` 时抛）
  - 序列名、颜色一律**取响应的 `series[].name`**（前端不硬编码"沪深300/创业板指"等名单）；图注固定展示口径说明：「市值分层：指数收盘归一（共同首日=100）」/「市场板：上证综指含科创板（2020-07-22 修订）、创业板指 100 只样本、科创50 50 只样本」
  - **数据时点与新鲜度**（响应 `as_of`/`freshness_status` 的消费方）：图表下方右侧固定展示「数据截至 {as_of}」（`as_of` 为 `null` 时整条省略）；`freshness_status === 'STALE'` → 追加角标「数据滞后」；`'UNAVAILABLE'` → 追加角标「**暂无数据**」。`FRESH` 不出角标。**面板级文案用「暂无数据」，与下一行逐序列的「无数据：<name>」刻意区分**（前者=整个面板两组合计无任何数据，后者=某一条序列无数据）
  - 空序列 → 图表下方"无数据：<name>"角标（序列条目由服务端恒返回，契约见 4.2.1 第 4 条）
  - 加载/错误/空态复用 `shared/feedback/`（LoadingState/ErrorState/EmptyState）；**加载提示绝对定位 overlay，不挤占布局**（CLAUDE.md 前端页面重排规避规则）；图表容器固定高度 420px
- **queries**（queries.ts + queryKeys.ts）：
  - `useCapTierTrendsQuery(filters)` / `useBoardTrendsQuery(filters)`（`filters = { from, to }`），queryFn 走生成客户端 + `requestEnvelope`（[queries.ts:22-41](../../../../frontend/src/modules/market/pages/queries.ts) `useMarketBarsQuery` 同款）
  - queryKeys 注册 `marketCapTierTrends = makeQueryKeys('market-cap-tier-trends')` / `marketBoardTrends = makeQueryKeys('market-board-trends')`
- **MarketOverviewPage**：市场区块（`MarketIndicesPanel`）之后、板块区块之前插入 `<section aria-label="趋势对比区块">` + `<h2>趋势对比</h2>`；页头副标题 "全球指数 · 热门概念 · 已审核宏观信息" → 追加 " · 趋势对比"

#### 4.4.2 三方依赖能力评估

- 依赖 4.2 的 2 个趋势端点（openapi 生成客户端；backend 改路由后必须先 `export_openapi` + `generate:api` 再动前端消费代码）；React Query、ECharts 均为既有依赖，无新外部库

#### 4.4.3 风险与验证方式

- 风险：分层组新指数未回填 → 空序列降级（4.2.1 第 4 条契约 + 面板角标），不整面板报错
- 风险：「全部」区间请求跨度大（1990→今）→ 单序列仍是索引区间扫描（PK (ts_code, trade_date)），实测毫秒级；加载态 overlay 覆盖
- 成本已实测（T5/CR 复核）：「全部」区间下两 Tab 合计 33,239 点、响应 ≈1.3MB、后端 7 条序列区间扫描（另各 1 条 as_of 聚合）——两端点无条件并行挂载是**有意设计**（Tab 切换零等待、缓存直读；区间切换才换 key 重拉），默认「近1年」窗口体量远小于此，维持不 gating
- 验证：
  - `toTrendViewModels.test.ts`：① 共同首日取最大值（构造三条起点不同的序列）；② 基点日之前点被丢弃、首点值 = 100；③ 空序列返回 `data: []` 且不影响其他序列基点；④ 全空返回 `[]`；⑤ 值四舍五入 2 位
  - `TrendComparisonPanel.test.tsx`：两 Tab 切换（按钮 `aria-pressed` 翻转、无 `role="tab"`）、区间切换换查询 key、**区间 `from` 口径断言**（近1年 → `addMonths(今天,-12)`；「全部」→ `from=1990-01-01`；`addMonths` 日溢出夹取：`2026-03-31` 减 1 月 = `2026-02-28`）、**`as_of`/`freshness_status` 消费断言**（FRESH 无角标 + 展示"数据截至"；STALE → 「数据滞后」；UNAVAILABLE → 「暂无数据」且不渲染"数据截至"；`as_of=null` → 整条省略）、空序列角标渲染、请求参数断言
  - `queries.test.ts`：两 hook 的请求参数与 key 断言（既有模式）
  - 页面测试：新区块标题存在 + 页面仍无 `role="tab"`

#### 4.4.4 文件变更清单

- **新建文件**：
  | 路径 | 说明 |
  |------|------|
  | frontend/src/modules/market/pages/mappers/toTrendViewModels.ts | 共同首日归一 + DTO→ViewModel |
  | frontend/src/modules/market/pages/mappers/toTrendViewModels.test.ts | 归一纯函数单测 |
  | frontend/src/modules/market/pages/TrendComparisonPanel.tsx | 两 Tab 趋势面板 |
  | frontend/src/modules/market/pages/TrendComparisonPanel.test.tsx | 面板单测 |
- **修改文件**：
  | 路径 | 改动说明 |
  |------|---------|
  | frontend/src/modules/market/pages/queries.ts | 2 个 trend hooks（含 queries.test.ts 断言） |
  | frontend/src/api/queryKeys.ts | 2 个 key 注册 |
  | frontend/src/modules/market/pages/MarketOverviewPage.tsx | 插入趋势对比区块 + 副标题（含页面测试） |
  | frontend/src/modules/market/pages/MarketIndicesPanel.tsx | :21 注释数量同步（见 4.1.1 表 #12） |

## 五、已确认决策

1. **微盘口径 = 中证2000 一条线**（2026-09-18 用户拍板）。背景：万得微盘股指数 8841431.WI 在 tushare 代理实测 `index_daily` 0 行、`index_basic` 全市场检索无"微盘"；官方端点本项目 token 无效、东财接口本机不通。国证2000（399303.SZ）实测可用但用户未选双线。
2. **板口径 = 官方指数，不自编等权曲线**（2026-09-19 用户拍板，**修订** 2026-09-18 的"全板等权自算 3 板"）。现口径：上证综指 000001.SH（沪市，2020-07-22 修订后含科创板）/ 创业板指 399006.SZ（100 只样本）/ 科创50 000688.SH（50 只样本），三者均已在库、零采集改动。修订理由：① 官方指数体系**不存在"主板指数"**（实测 `index_basic` SSE 208 + CSI 8961 个指数含"主板"命中 0，仅深交所 395001.SZ「主板A股」且只覆盖深市）；② 自编等权需经 `stock_info.market` 归板，而该表只同步在册股票——实测 `instrument_daily` 5818 个股票代码中 **约 250 个（约 4.3%）已退市不在 `stock_info`**（精确计数依赖分类口径，复算 251–258 不等；幸存者偏差，且各板偏差幅度不一致）；③ 官方指数无自维护分类规则、无极端值过滤问题，实现面显著收窄。**代价已知并接受**：三条线编制口径不同（综指 vs 样本指数），图注如实标注（4.4.1）
3. **归一基点 = 共同首日=100**（2026-09-19 用户拍板）。口径：各序列以"所有序列都有数据的第一个交易日"为基点日，各自该日 close 记 100，基点日之前的点丢弃（4.4.1 算法）。代价：「全部」区间下 市值分层组从 2014-01-02 起（丢沪深300/中证1000 的 2005–2013 段）、板 组从 2019-12-31 起（丢上证综指 1990–2019 段）——换取三条线在图上同起点可比（**T5 实测修正**：分层组共同首日实际 = 2013-12-31，因 932000.CSI 回填后首行为 2013-12-31 基日行）
4. **行业 Tab 暂缓**（2026-09-19 用户拍板）。本方案不交付行业 Tab；原设计的行业数据底座整体移出范围。完成于 2026-09-18 的三方实测与设计结论留档于下（恢复时无需重测）：
   - **端点实测**：`sw_daily(801010.SI, 20000101~20260918)` 单请求 3434 行、20120801~20260917、**降序返回**（消费前必须 `_sort_asc_by_trade_date` 升序归一）、响应列 ts_code/trade_date/name/open/low/high/close/change/pct_change/vol/amount/pe（**无 pre_close**）；SW2021 一级行业 31 个已在库（`market.industry`，industry_code 去 .SI 后缀的 6 位码，如 801010=农林牧渔）
   - **被移出的设计要点**：① 新表 `market.industry_daily`（PK (source, industry_code, trade_date)，列 open/high/low/close/change/pct_chg/vol/amount/pe/updated_at，无 pre_close）；② DAO `db/instrument/dao/industry_daily.py`（bulk_upsert + query_window）；③ 采集 `db/instrument/ingest/industry_daily.py`（每日窗口增量：31 请求 ≈ 8s、连续 5 失败熔断；全历史回填：5 年分块 + 按行业断点续跑）；④ Provider `get_industry_daily_df`（基类默认 None + Tushare 覆写 sw_daily）；⑤ 端点 `/trends/industries` + 前端 HeatmapChart（近 20 交易日 pct_chg 矩阵）+ 可勾选折线
   - **不落库的替代路径已否决**：行业数据实时拉（每次 31 请求、依赖外部端点、无"全部"区间历史）；AI 层 `_fetch_industry_daily_series`（tushare.py:995-1031，直连 sw_daily 拉近 10 日不落库）现状不变，与本方案无冲突
   - 恢复方式：重新起草方案（本任务归档后可参照本节留档）
5. **展示位置 = 大盘页新区块两 Tab**（2026-09-19 修订，原为三 Tab）。插在 市场 与 板块 区块之间。
