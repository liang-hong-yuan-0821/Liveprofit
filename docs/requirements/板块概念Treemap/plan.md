# 板块概念 Treemap 方案（sector_daily dc 每日增量 + 概念树 API + 前端 Treemap 交互）

> 状态与进度见 [README.md](README.md)（状态块权威载体）；本文件为方案正文（评审对象）。
> 关联文档：[证券市场数据库统一方案](../archive/证券市场数据库统一方案.md)（sector_daily 表设计与决策 13/16）｜[产品需求分析](../../knowledge/产品需求分析.md)（§3.1.4 板块区块契约，实现后需同步）｜[指数K线图交互优化方案](../指数K线图交互优化/plan.md)（K 线组件另一案，本方案弹窗 K 线为固定窗口首版）

---

## 一、背景与动机

| 维度 | 现状 | 问题 | 目标 |
|------|------|------|------|
| 数据底座 | `market.sector_daily`（板块指数日线）当前 **0 行**（实测 2026-09-13 查询 count=0）；其采集被[证券市场数据库统一方案](../archive/证券市场数据库统一方案.md)决策 16 排入"后续阶段"，一直未实施。`market.sector` 已有 1930 个板块（ths 899 个 type='N' 概念板块、dc 1031 个）、`market.sector_member` 已有 165,528 行成分关系（ths 72,131 + dc 93,397）、`market.instrument_daily` 已有 1383 万行个股日线（全历史） | 大盘页"板块"区块数据链断在第一步：`MarketDataService.get_hot_concepts`（[service.py:211](Liveprofit/backend/modules/market_data/application/service.py#L211)）读 `market.sector_daily WHERE source='dc'` 现场计算热度，表空 → 恒返回 `NO_HOT_CONCEPTS` 空态；前端 [HotConceptsPanel.tsx:63-65](Liveprofit/frontend/src/modules/market/pages/HotConceptsPanel.tsx#L63-L65) 恒显示"当前条件下暂无热门概念"——**这是"前端没有展示概念或板块"的根因** | 板块指数日线**每日增量入库**（dc 源、最近 33 交易日滚动窗口，历史自采集启动日起自然积累），热度现场计算启用，板块区块有真实数据可展示 |
| 板块区块展示形式 | 现状 UI 为卡片网格（[HotConceptsPanel.tsx:67-73](Liveprofit/frontend/src/modules/market/pages/HotConceptsPanel.tsx#L67-L73) 每概念一张卡片 + 迷你 K 线），且卡片 K 线因后端硬编码 `bars=[]`（[service.py:305](Liveprofit/backend/modules/market_data/application/service.py#L305)）恒显示"K 线不可用" | 用户要求改为 treemap（矩形树图）两层展示：概念 → 成分股；卡片形式不再满足诉求 | 板块区块以 treemap 展示概念（矩形大小=热度、颜色=当日涨跌幅）与其成分股（矩形大小=当日涨跌幅绝对值、颜色=涨跌），悬浮显示 tooltip 数值 |
| 悬浮/点击 K 线 | 概念 K 线无读模型（sector_daily 空）；个股 K 线无端点（`get_bars` 只服务 index：[service.py:104](Liveprofit/backend/modules/market_data/application/service.py#L104) `instrument_type != "index"` 即 404，[service.py:117-121](Liveprofit/backend/modules/market_data/application/service.py#L117-L121)） | 用户要求悬浮具体标的后展示 K 线：treemap 中点击概念/成分股矩形，应弹出该标的 K 线图，现状两条路都走不通 | 点击概念 → 弹窗展示该板块指数 K 线（dc 源，历史 = 采集启动日起积累的长度）；点击成分股 → 弹窗展示该个股 K 线（**近 180 自然日固定窗口**，instrument_daily 已有全历史数据；全历史窗口另案，见方案头关联文档） |

**用户已拍板（2026-09-14）**：① treemap 两层（概念+成分股）；② **数据先存 dc**：dc_daily 每日增量积累、不做全历史回填（原"全历史回填"拍板撤销；ths 全历史能力已验证，暂缓）；③ 矩形大小=热度、颜色=当日涨跌幅；④ 悬浮显示 tooltip 数值、**点击弹窗**展示 K 线。

## 二、架构设计

本方案延续现有三层数据链（db.instrument 采集 → MarketDataService → 前端），新增 sector_daily 采集能力与两个读模型端点，前端板块区块从卡片改为 treemap；**热度口径不变**（source='dc' 原口径成立——表从 0 行变为有数据，get_hot_concepts 现有实现无需改动）：

```
（新增）采集：dc_daily（tushare 代理端点，逐板块 70 自然日窗口请求、端点封顶回 33 交易日）→ market.sector_daily（dc 源）
              └─ db/instrument/ingest/sector_daily.py（每日增量 DO UPDATE，接入 collect_incremental；
                 无历史回填——dc_daily 对更早窗口返回 0 行（实测），历史自启动日起自然积累）

（改造）后端：MarketDataService
  get_hot_concepts  不改（热度现场计算 source='dc' 原口径，表有数据后自动启用）
  get_concept_tree  新方法：热度 top N + 当日涨跌幅 + 成分股（sector_member join instrument_daily 当日横截面）
  get_sector_bars   新方法：单板块指数 K 线（读 market.sector_daily，source='dc'）
  get_bars          放宽：instrument_type ∈ {index, stock}，个股 K 线（仅个股路径因子行缺失 → indicators=None）

（新增）端点：GET /api/v1/market-data/concepts/tree      → 概念树（treemap 数据源）
             GET /api/v1/market-data/concepts/{code}/bars → 概念 K 线
             GET /api/v1/market-data/stocks/{symbol}/bars → 个股 K 线

（改造）前端：HotConceptsPanel 卡片网格 → ConceptTreemap（echarts treemap 两层）
             悬浮 tooltip（名称/热度/涨跌幅）→ 点击 → KLineDialog 弹窗（CandlestickChart 复用）
```

无新表、无 DB 迁移（`market.sector_daily` 已由统一方案建好，[schema.sql:144](Liveprofit/db/instrument/schema.sql#L144)）。AI 分析链路不动（板块层 AI 工具仍走 Provider 直连）。

### 2.1 数据模型 / API 契约设计

`market.sector_daily` 表结构不变（列集见统一方案 2.2）；本方案确定**写入取值域**（dc 源实测列映射，见 3.1.1）：

| 列 | 类型 | 取值（dc 源实测，2026-09-13） | 说明 |
|------|------|------|------|
| source | VARCHAR(8) | 恒 `'dc'`（本方案只采 dc；ths 全历史暂缓，能力已验证） | 板块体系来源，与 sector.source 对应 |
| sector_code | VARCHAR(32) | 如 `BK1753.DC`（光刻胶）、`BK1751.DC`（2026中报首亏） | 东方财富概念板块代码 |
| trade_date | DATE | 如 `2026-09-11` | 交易日 |
| open/high/low/close | DOUBLE | 如 `1047.52 / 1049.0 / 1013.3 / 1041.38`（BK1753.DC 当日） | OHLC 实测全列有值 |
| pre_close | DOUBLE | **恒 NULL**（dc_daily 无此列，实测） | ths 源若未来采则有值 |
| change | DOUBLE | 如 `-18.45` | close - pre_close（上游直供） |
| pct_chg | DOUBLE | 如 `-1.74`（dc_daily 列名 pct_change → 统一列名 pct_chg） | 涨跌幅 % |
| vol | DOUBLE | 如 `6888712.0` | 成交量（热度公式输入） |
| amount | DOUBLE | 如 `21604826788.0` | 成交额（dc_daily 有、ths_daily 无） |
| turnover_rate | DOUBLE | 如 `2.84` | 换手率（heat_v1 不用，heat_v2 候选） |
| updated_at | TIMESTAMPTZ | 采集写入时间 | 覆盖式写入 |

**新增 API 契约（3 个端点，均 Envelope 包装）：**

`GET /api/v1/market-data/concepts/tree?market=CN&interval=1d&from=<as_of 透传>&to=<忽略>&limit=30&as_of=` 响应 `ConceptTreeData`：

```
ConceptTreeData {
  as_of: "2026-09-11" | null            // 榜单日期：缺省 = sector_daily(dc) 最新 trade_date
  algorithm_version: "heat_v1"          // 与 hot 端点同一公式口径
  result_status: "OK" | "NO_HOT_CONCEPTS"
  source: "dc"                           // 与热度口径一致（原 hot 端点硬编码值，本方案不改）
  source_updated_at: datetime | null     // 聚合 max(sector_daily.updated_at)
  freshness_status: "FRESH" | "STALE"   // as_of vs 交易日历（同 hot 端点语义）
  items: [{
    sector_code: "BK1753.DC"             // 概念代码
    sector_name: "光刻胶"                // 概念名称（sector 表 name）
    rank: 1                              // 热度降序名次
    heat_score: 5.77                     // heat_v1 得分（矩形大小；示例值 = 测试 seed 推导口径，见 3.2.3）
    pct_chg: 1.23 | null                 // as_of 当日板块涨跌幅（矩形颜色；as_of 当日该板块无行情行 → null，示例值）
    member_total: 137                    // 该概念成分总数（sector_member dc 源全量计数）
    members: [{                          // 成分股 = 当日有行情成分按 |pct_chg| 降序截断 top 100（见 3.2.1 规模定稿）
      ts_code: "600050.SH"               // 个股代码
      name: "中国联通"                    // instrument 表名称（LEFT JOIN；缺失时以 ts_code 兜底）
      pct_chg: 3.21 | null               // as_of 当日个股涨跌幅；停牌/无行 → null（前端置灰）
    }]
  }]
}
```

`GET /api/v1/market-data/concepts/{sector_code}/bars?market=CN&source=dc&interval=1d&from=&to=` 与 `GET /api/v1/market-data/stocks/{symbol}/bars?market=CN&interval=1d&from=&to=`：均复用现有 `BarsData` 契约（[schemas/market.py:74](Liveprofit/backend/api/schemas/market.py#L74)），差异仅在 `indicators` 恒为 null（板块指数与个股均无因子表数据——idx_factor_pro 不覆盖板块指数、个股因子 stk_factor_pro 是统一方案后续阶段 ③，指标不自算原则）；`asset.market` 恒回显 `'CN'`。**命名定稿（用户 2026-09-14 拍板）**：DTO 结构对日/周/月线完全相同——频率是取值不是类型，接口频率区分由 `interval` 参数 + 响应 `interval` 字段承载（bars 系不改名；未来周/月线零新 DTO、零新端点）。

## 三、详细设计

### 3.0 模块总览

| 维度 | 问题 | 方案概览 |
|------|------|---------|
| 板块日线采集 | `market.sector_daily` 0 行（实测 count=0），热度/treemap/K 线全部断在数据底座；dc_daily 端点只回最近 33 交易日（实测 20260101 区间请求仅回 33 行、最早 20260729；更早窗口 0 行）——**无历史可回填**，只能从启动日起每日增量积累 | 新增 `db/instrument/ingest/sector_daily.py`：dc 1031 板块逐板块 70 自然日窗口请求（端点封顶回 33 交易日，DO UPDATE 幂等覆盖日终修正）→ 首跑即有 33 行/板块（热度完整公式 >10 行窗口当天可用），此后每日 +1 行自然积累；接入 collect_incremental 每日常规步骤 |
| 概念树 API | treemap 需要热度值 + 当日涨跌幅 + 成分股三层数据，现有 hot 端点契约无 heat_score 字段（统一方案决策：score 只作内部排序键不返回）且无成分股；热度榜偏向成分多的大板块（ths 实测 top 30 板块 27,292 成分，dc 同量级风险） | 新端点 `GET /market-data/concepts/tree` + `get_concept_tree`：热度计算逻辑从 `get_hot_concepts` 提为私有方法共用（source='dc' 不变）；成分股按当日 |pct_chg| 降序**截断 top 100**（契约字段 `member_total` 标全量数）；成分 pct_chg 用 `instrument_daily` 当日横截面一次查询 |
| 概念/个股 K 线 API | 概念 K 线无读模型；`get_bars` 对非 index 一律 404（[service.py:117-121](Liveprofit/backend/modules/market_data/application/service.py#L117-L121)），个股 K 线无从拉起 | `get_sector_bars` 读 sector_daily（indicators 恒 None，历史 = 库内积累长度）；`get_bars` 校验放宽为 `instrument_type ∈ {index, stock}`、**仅个股路径**在因子表无行时 `indicators=None`（指数路径保持全 null 数组不变——冻结用例固化）；两个新端点（/bars 路径，频率由 interval 参数区分） |
| 前端 Treemap 与弹窗 | 板块区块为卡片网格（[HotConceptsPanel.tsx:67-73](Liveprofit/frontend/src/modules/market/pages/HotConceptsPanel.tsx#L67-L73)），无 treemap 组件、无点击弹窗交互 | 新增 `ConceptTreemap`（echarts treemap 两层 + 红涨绿跌静态逐节点着色 + treePathInfo tooltip）与 `KLineDialog`（复用 CandlestickChart + barsToCandlestickViewModel，查询 enabled 门控）；HotConceptsPanel 卡片删除改 treemap |
| 文档与经验同步 | CLAUDE.md 命名规范表 sector_daily 行写"采集后续阶段"（[CLAUDE.md](Liveprofit/CLAUDE.md) 命名规范表 sector_daily 行，行号随并发编辑漂移按文本锚定）；产品需求分析 §3.1.4 板块区块契约仍描述卡片 UI；tushare 端点坑文件无 dc_daily/ths_daily 实测结论 | CLAUDE.md 命名表 sector_daily 行更新（dc 每日增量积累现状）；产品需求分析 §3.1.4 契约与交互描述同步；pitfalls/ai/tushare-endpoints.md 追加三条实测（dc_daily 33 日截断 / ths_daily 全历史 / push2his 不可达） |

### 3.1 板块日线采集（sector_daily dc 每日增量）

#### 3.1.1 模块设计

**Provider 基类新增方法**（Data Provider 接口约定：结构化接口例外——基类默认返回 None，TushareProvider 覆写；AKShare 无等价端点不覆写）：

```python
# base_provider.py
def get_sector_daily_df(self, source: str, ts_code: str, start_date: str, end_date: str):
    """单板块指数日线（source 参数化：'dc'=dc_daily 端点；'ths'=ths_daily 端点，
    首期仅 dc 覆写，ths 全历史暂缓——能力已实测，后续扩展在 TushareProvider 加分支即可）。
    日期参数格式：YYYYMMDD（tushare 端点硬要求，与 backfill CLI 的 YYYY-MM-DD 不同——
    转换由采集函数入口完成并单测锁定）。失败/不支持返回 None。"""
    return None
```

TushareProvider 覆写（首期仅 dc 分支）：
- `source == 'dc'`：`self._api_call(self.api.dc_daily, ts_code=ts_code, start_date=start_date, end_date=end_date, idx_type="概念板块")` + `_sort_asc_by_trade_date` 升序归一（端点降序返回，实测；[tushare-endpoints.md](Liveprofit/docs/memory/pitfalls/ai/tushare-endpoints.md) 正确姿势）；**端点能力注记（实测 2026-09-13）：仅返回最近 33 交易日，更早窗口 0 行——窗口型数据源，无历史**
- `source == 'ths'`：未覆写分支（暂缓，留 TODO 注释指向本方案）

**新模块 `db/instrument/ingest/sector_daily.py`**（**无回填模式、无 checkpoint**——历史不可采，断点续跑无意义：每日重拉窗口天然自愈）：

1. `collect_sector_daily_incremental(conn, provider, window_days: int = 70) -> dict`
   - 板块清单 = `dao.sector.get_sectors(conn, "dc")`（1031 个；周刷新增板块自然进入次日增量；复用既有 DAO 不内联 SQL）
   - 窗口：`start = today - window_days(70) 自然日`（**70 自然日恒覆盖 ≥33 交易日**——45 自然日跨春节/国庆只剩 26–28 个交易日，首跑会少采；放宽到 70 后任何窗口都超端点上限，**返回行数仍由 dc_daily 端点封顶 33**，成本为零；**每次拉全窗口而非最近 3 日**——首跑即有 33 行/板块 → 热度完整公式（>10 行）当天可用；且漏采日/上游修订由每日全窗口重拉自然自愈），`end = today`（日期 YYYY-MM-DD 入口 → 内部转 YYYYMMDD）
   - 逐板块：`get_sector_daily_df("dc", ts_code, start, end)` → 列映射 `trade_date/open/high/low/close/change/pct_change→pct_chg/vol/amount/turnover_rate`（**pre_close 置 None**——dc_daily 无此列实测；**swing/category/ts_code 丢弃**）→ **补写入列：`source='dc'`（恒值，NOT NULL）+ `updated_at=采集时刻`（DAO 不自动补——[sector_daily.py:17](Liveprofit/db/instrument/dao/sector_daily.py#L17)，sector_daily.updated_at 可空无默认；对齐增量采集层显式填 updated_at 的做法（[incremental.py:252](Liveprofit/db/instrument/ingest/incremental.py#L252)）；漏填则 DO UPDATE 会把 `EXCLUDED.updated_at` NULL 覆盖回已有行 → 契约 `source_updated_at` 恒空）** → `drop_duplicates(subset=PK)`（[store-daily.md](Liveprofit/docs/memory/pitfalls/ai/store-daily.md) 实测踩坑 4"重复 con_code 同批次 PK 冲突"的双保险）→ `bulk_upsert_sector_daily(conn, df, update=True)`（**DO UPDATE**——决策 5：增量窗口覆盖日终修正）→ **每板块独立 commit**（对齐 collect_sectors 的按工作单元独立 commit 事务约定，粒度为板块：后板块失败回滚不得丢前板块成果；SQL 级失败 rollback 恢复干净状态）
   - 失败分层（同 collect_sectors）**+ 熔断**：单板块失败跳过不阻断（次日全窗口重拉自然重试）；**连续 5 个板块失败即终止本步骤、剩余板块记入 failed**（防端点整体退化时 1031 次串行 × 30s 超时 ≈ 8.6 小时/日；仓库先例 get_all_concept_boards 连续 2 次失败终止）；整体失败由调用方 try/except 兜底
   - 返回汇总 `{"boards": n, "rows": n, "failed": [...]}`
2. 无需 CLI（无回填模式）；首跑 = 人工执行一次 `collect_incremental`（或显式调本函数），此后随每日批处理自动。

**接入点**：
- `incremental.py collect_incremental`：新增步骤（板块日线增量，每日常规、非周一限定；失败不阻断后续步骤；**耗时量化：1031 板块 × (0.6s 请求 + 0.2s 间隔) ≈ 14 分钟 + 写入开销 → 约 15–25 分钟/日**，叠加进默认增量路径，写入 docstring 与 run.sh 说明）
- `backfill.py`：**不接入**（无历史可回填）
- `backend/workers/market_ingest.py`：**不改**（无新 flag——增量步骤默认跑；wrapper 仅调用方）

**规模估算**（实测推导）：每日 1031 请求 × 每板块 ~33 行 ≈ 3.4 万行写入（DO UPDATE 覆盖窗口）；库内总量自启动日起每板块每日 +1 行自然增长。

#### 3.1.2 三方依赖能力评估

2026-09-13 实测（真实 token，代理端点 gyzcloud）：

| 端点 | 实测结论 |
|------|---------|
| tushare 代理 `dc_daily` | ✅ **窗口型**：仅最近 33 交易日（start=20260101 请求仅回 33 行、最早 20260729；start=20260601+end=20260715 旧区间回 **0 行**）；列 ts_code/trade_date/close/open/high/low/change/pct_change/vol/amount/swing/turnover_rate/category 齐全，**无 pre_close**；返回降序需升序归一——**本方案按窗口型使用（每日增量积累），不依赖历史** |
| tushare 代理 `ths_daily` | ✅ 全历史单请求（883300.TI 3988 行 2010 起、最老板块 885311.TI 4,642 行首行=上市日精确吻合）；列齐全**无 amount**——**暂缓不采**（用户拍板），能力已实测留档 |
| tushare 官方端点 | ❌ 本项目 token 无效（"您的token不对"），无法绕开代理 |
| akshare 东财板块历史（`stock_board_concept_hist_em`，主机 push2his.eastmoney.com） | ❌ 直连与系统代理（127.0.0.1:7890）均连接重置（curl 实测 3 次 000）；同域 push2.eastmoney.com 直连正常——kline 主机单独不可达 |

结论：**首期唯一可行路径 = dc_daily 窗口型每日增量**（与热度原口径 source='dc' 天然一致）；ths 全历史为后续可选项（能力已验证，按 3.1.1 的 source 参数扩展即可）。

#### 3.1.3 风险与验证方式

- 风险：dc_daily 窗口内个别板块响应空（停更板块）→ 跳过不阻断、次日重试；首跑每板块 ≈33 行（dc_daily 端点封顶，与窗口是否跨假期无关）→ 热度完整公式（>10 行）可用、K 线仅 ~33 根（随积累增长，属已拍板取舍）；增量 15–25 分钟/日与周刷 35 分钟同量级 → 接受（daily_job 已有该体量先例）；**端点整体退化/慢响应**（1031 次串行最坏 8.6 小时）→ 连续 5 板块失败熔断兜底（3.1.1）；**数据中断风险**：每日增量若连续多天未跑，窗口滑动后中断期数据永久缺失（dc_daily 拉不到）→ 依赖现有三层补跑机制（cron/启动自检/周期自检）保证每日执行，实施后在 run.sh 说明中标注。
- 验证：
  1. 单测（fake provider，落点 [tests/db/instrument/](Liveprofit/tests/db/instrument/) 既有采集测试目录 test_sectors.py 同层）：列映射（pre_close 恒 NULL/amount 有值/pct_change→pct_chg/swing+category 丢弃）、**写入列 source='dc' 恒值与 updated_at=采集时刻断言**、升序归一（降序输入）、drop_duplicates、DO UPDATE 分流、单板块失败不阻断、**连续 5 失败熔断断言（第 5 次失败后不再发请求、剩余板块记入 failed）**、每板块独立 commit（失败回滚后下一板块仍写入）、窗口日期 YYYYMMDD 转换；
  2. 集成（真实端点，人工执行）：首跑 `collect_incremental`（或显式调本函数）→ `SELECT count(*) FROM market.sector_daily WHERE source='dc'` ≈ 1031 板块 × 33 行（端点封顶）断言；次日重跑幂等（行数仅 +1 日/板块）；
  3. 人工检查：起前后端，大盘页 treemap 有数据、热度排名合理。

#### 3.1.4 文件变更清单

- **新建**：
  - `db/instrument/ingest/sector_daily.py`：dc 每日增量
  - `tests/db/instrument/test_sector_daily_ingest.py`（落点沿 `tests/db/instrument/` 既有采集测试目录）
- **修改**：
  - `AI/dataflows/providers/base_provider.py`：新增 `get_sector_daily_df(source, ts_code, start_date, end_date)` 默认 None
  - `AI/dataflows/providers/cn/tushare.py`：覆写 dc 分支（dc_daily + idx_type + 升序归一）；ths 分支留 TODO 注释
  - `db/instrument/ingest/incremental.py`：新增板块日线增量步骤（含耗时量化注释）
  - `run.sh`：增量步骤说明随改（如含耗时说明）

### 3.2 概念树 API（treemap 数据源）

#### 3.2.1 模块设计

**服务方法**（`MarketDataService`）：

1. 热度计算重构：把 `get_hot_concepts` 内"逐板块三段降级打分"提为私有方法 `_compute_sector_heat(df, names) -> list[tuple[code, score, period_return, g]]`（`get_hot_concepts` 与 `get_concept_tree` 共用；输入 = query_by_window 结果 + sector 名称映射）。**source='dc' 过滤与公式均不改**——原口径成立，表有数据后自动启用。
2. `get_concept_tree(self, *, market, as_of, limit) -> tuple[date | None, list[dict], str, str, object]`：
   - market≠CN / 无数据 / 窗口不足：与 get_hot_concepts 完全同语义（NO_HOT_CONCEPTS）
   - top N = `_compute_sector_heat` 降序取 limit
   - 每概念追加：`pct_chg` = **显式按 as_of 过滤该板块行取值**（`g[g["trade_date"] == as_of]["pct_chg"]`，NaN/缺行 → None——板块当日无行情行不得静默取更早日期，契约"as_of 当日"语义）；`member_total` = 该概念成分总数；`members` = 成分股清单与当日涨跌幅：
     ```sql
     SELECT m.sector_code, m.ts_code, COALESCE(i.name, m.ts_code) AS name, d.pct_chg
     FROM market.sector_member m
     LEFT JOIN market.instrument i ON i.ts_code = m.ts_code   -- LEFT JOIN：成分中退市/未入库股无
                                                              -- instrument 记录，INNER JOIN 会静默丢弃
     LEFT JOIN market.instrument_daily d
       ON d.ts_code = m.ts_code AND d.trade_date = :as_of    -- 当日横截面；停牌无行 → pct_chg NULL
     WHERE m.source = 'dc' AND m.sector_code = ANY(:top_codes)
     ```
     成员排序与截断：按 `|pct_chg| DESC NULLS LAST` **每概念截断 top 100**（常量 `MEMBERS_PER_CONCEPT = 100`）——规模依据（**dc 实测**）：dc 1031 板块中 **216 个成分数 >100**（其成分合计 67,718）、平均 90.6/板块、按板块规模 top 30 合计 **29,062 成分**（与 ths 的 27,292 同量级）——热度榜偏向大板块，**截断确会生效**；全量返回 payload 2–3MB 且前端 2.7 万节点不可渲染；截断后 ≤3,000 成员 ≈ 300KB
   - `members` 名称兜底：instrument 表缺失（退市股）时 name=ts_code（契约 name 必填，COALESCE 在 LEFT JOIN 下真实生效）
   - 返回 `(as_of, items, "OK"|"NO_HOT_CONCEPTS", freshness, max_updated)`——items 结构见 2.1 契约
3. 新 DTO（`backend/api/schemas/market.py`）：`ConceptMemberDTO{ts_code, name, pct_chg: float | None}`、`ConceptTreeNodeDTO{sector_code, sector_name, rank, heat_score, pct_chg: float | None, member_total: int, members}`、`ConceptTreeData{as_of, algorithm_version, result_status, source, source_updated_at, freshness_status, items}`。
4. 新端点（`backend/api/routers/market_data.py`）：`GET /market-data/concepts/tree`——参数 `market/interval/from_/to/limit(ge=1,le=30)/as_of`，`from_` 作为 as_of 透传、`to` 忽略（m6 定稿同款口径）；interval≠1d 422 同 hot 端点；**`limit` 必填**（`Query(ge=1, le=30)` 无默认值，与 hot 端点同款——[market_data.py:80](Liveprofit/backend/api/routers/market_data.py#L80)）。

**payload 规模**：top 30 概念 × 每概念 ≤100 成员 = ≤3,000 成员 ≈ 300KB，可接受。

#### 3.2.2 三方依赖能力评估

本模块不依赖外部库/API（读本地表）。

#### 3.2.3 风险与验证方式

- 风险：成员当日停牌无行情行（pct_chg NULL）——契约显式允许，前端置灰处理（3.4）；一次 JOIN 拉 ≤3,000 行 + 单日横截面（instrument_daily 13.8M 行有 (ts_code, trade_date) 主键索引）毫秒级；as_of 为历史日期时个股退市/未上市自然无行——同上 NULL 语义；截断 top 100 属契约语义（`member_total` 标全量数，UI 可注明"仅展示当日波动前 100"）。
- 验证：契约测试新增 `test_concept_tree_*` 3 条——① seed dc 板块+成分+个股日线 → 断言 rank 序、heat_score 数值（沿用 BK 用例的推导口径：close 100+i → pct=(114-104)/104×100≈9.615 → score≈5.769）、pct_chg、members 名称映射与 |pct_chg| 降序、**截断 top 100 与 member_total 断言（seed 造 >100 成分板块）**、停牌成员 pct_chg null、板块当日缺行 → pct_chg null；② market≠CN 空态；③ NO_HOT_CONCEPTS（无 seed）。**落点钉死：修改 `backend/tests/contract/api/test_market_data.py`**（与 3.2.4 清单及验证总表命令一致）。

#### 3.2.4 文件变更清单

- **修改**：
  - `backend/modules/market_data/application/service.py`：`_compute_sector_heat` 提取 + `get_concept_tree`
  - `backend/api/schemas/market.py`：3 个新 DTO
  - `backend/api/routers/market_data.py`：tree 端点
  - `backend/tests/contract/api/test_market_data.py`：tree 用例
- **OpenAPI 重导出链（必做步骤，产物入清单）**：`python -m backend.scripts.export_openapi`（产物 `backend/openapi/openapi.v1.json`——[test_openapi_gate.py](Liveprofit/backend/tests/contract/test_openapi_gate.py) 断言再导出与提交件**结构等价**（解析后 JSON 对象相等），不重导出门禁红）→ `pnpm run generate:api`（产物 `frontend/src/api/generated/**`，新端点方法只能来自产物；[pnpm-openapi-codegen.md](Liveprofit/docs/memory/pitfalls/frontend/pnpm-openapi-codegen.md)）

### 3.3 概念 K 线 + 个股 K 线 API

#### 3.3.1 模块设计

1. **DAO**（`db/instrument/dao/sector_daily.py` 新增）：
   ```python
   def query_bars(conn, source: str, sector_code: str, start_date: str, end_date: str) -> pd.DataFrame:
       """单板块区间日线（升序）：SELECT ... WHERE source=%s AND sector_code=%s AND trade_date BETWEEN ... ORDER BY trade_date"""
   ```
2. **服务方法 `get_sector_bars(self, *, market, source, sector_code, from_date, to_date) -> BarsDTO`**：
   - from>to → RangeTooLargeError（同 get_bars）
   - 板块存在性：`SELECT name FROM market.sector WHERE source=%s AND sector_code=%s`，查无 → MarketAssetNotFoundError（404 语义同 get_bars）
   - bars：query_bars 区间行 → bar_dicts（open/high/low/close/vol——dc_daily 实测全列；vol 可 NaN → None；**OHLC 任一 NaN 整行丢弃**，见 3.3.3 定稿）
   - freshness：该板块 max(trade_date) vs calendar.last_trading_day（FRESH/STALE/UNAVAILABLE，同 get_bars 模式）；source_updated_at = 该板块 max(updated_at)
   - `indicators=None`（板块指数无因子表数据；技术指标不自算）
3. **`get_bars` 放宽**（[service.py:104](Liveprofit/backend/modules/market_data/application/service.py#L104)、[service.py:117-121](Liveprofit/backend/modules/market_data/application/service.py#L117-L121)）：`instrument_type == "index"` → `in ("index", "stock")`；指标段加个股路径短路：**仅当资产为 stock 且 fdf 为空时 `indicators = None`**——指数路径行为保持现状不动（fdf 空 → 全 null 数组的降级语义已被契约测试 [test_market_data.py:77-81](Liveprofit/backend/tests/contract/api/test_market_data.py#L77-L81) 固化断言 `ma[0].values == [None]`，全局改 None 会破坏冻结用例；个股路径无既有用例，None 与 BarsData 契约 `indicators: IndicatorsDTO | None = None` 一致）。
4. **端点**：
   - `GET /market-data/concepts/{sector_code}/bars?market=CN&source=dc&interval=1d&from=&to=`（source 缺省 'dc'，当前唯一可采源；interval 必填同 indices 端点——频率由参数区分，命名定稿见 2.1）
   - `GET /market-data/stocks/{symbol}/bars?market=CN&interval=1d&from=&to=`（复用 get_bars；interval≠1d 由 get_bars 抛 IntervalNotSupportedError 同现状）
   - 两者响应均复用 `BarsData` schema（indicators 可为 null 契约已支持）。**必填字段回显口径**（`BarsData` 必填集：asset{symbol,name}/interval/from_/to/as_of/source_updated_at/market_session_status/market_closed_reason，[schemas/market.py:74-88](Liveprofit/backend/api/schemas/market.py#L74-L88)）：`interval` 恒 `"1d"`；`asset.symbol` = sector_code / 个股 ts_code、`asset.name` = sector 表 name / instrument 表 name、`asset.market` 恒 `"CN"`（请求回显）；`from_/to` 回显请求参数；`as_of` = 该标的最新 trade_date（无数据 None）；`source_updated_at` = 该标的最新 updated_at；`market_session_status/market_closed_reason` 复用 `_session_status()`（同 get_bars）；`freshness_status` 同 get_bars 口径。

#### 3.3.2 三方依赖能力评估

不依赖外部库/API（读本地表）。

#### 3.3.3 风险与验证方式

- 风险：sector_daily 首跑前概念 K 线空（bars=[] + UNAVAILABLE——契约允许，前端显示"K 线不可用"）；**概念 K 线历史 = 库内积累长度（首跑 33 根、逐日增长）**，属已拍板取舍；个股 K 线无指标（首版纯 K 线，个股因子采集后自动补——届时 get_bars 指标段无需再改，fdf 有行即产出）；**定稿：OHLC 任一 NaN 的行整行不产出 bar**（BarDTO 的 open/high/low/close 为必填 float，None 化会使响应 500；close 列 NOT NULL 已由 DAO 保障，本防御只拦脏行）。
- 验证：契约测试——concept bars：seed sector_daily dc → 200 断言 bars 升序/字段/indicators null/name 映射；404 未知板块；个股 bars：seed instrument(stock)+instrument_daily → 200 纯 K 线（indicators null）、fund 代码 → 404、**指数路径回归（现有用例 [test_market_data.py:77-81](Liveprofit/backend/tests/contract/api/test_market_data.py#L77-L81) 保持通过——指数空因子仍产全 null 数组指标，行为不变）**。

#### 3.3.4 文件变更清单

- **修改**：
  - `db/instrument/dao/sector_daily.py`：新增 `query_bars`
  - `backend/modules/market_data/application/service.py`：`get_sector_bars` + `get_bars` 放宽与个股路径指标短路
  - `backend/api/routers/market_data.py`：两个新端点
  - `backend/tests/contract/api/test_market_data.py`：concept/stock bars 用例
- **OpenAPI 重导出链（必做步骤，产物入清单）**：`python -m backend.scripts.export_openapi`（产物 `backend/openapi/openapi.v1.json`，openapi gate 结构等价断言）→ `pnpm run generate:api`（产物 `frontend/src/api/generated/**`）

### 3.4 前端 Treemap 与点击弹窗交互

#### 3.4.1 模块设计

**queries.ts 新增 3 个 hook**（pattern 同现有 useHotConceptsQuery）：

```ts
useConceptTreeQuery(filters: { market, interval, from, to })        // GET concepts/tree（limit 恒 HOT_CONCEPTS_LIMIT=30）
useConceptBarsQuery(sectorCode, { market, source, interval, from, to })   // GET concepts/{code}/bars
useStockBarsQuery(symbol, { market, interval, from, to })                 // GET stocks/{symbol}/bars
```

**新组件 `frontend/src/modules/market/components/ConceptTreemap.tsx`** + option 纯函数 `conceptTreeOption.ts`（对标 topologyChartOption.ts 模式）：

- 数据映射（DTO → treemap data）：**颜色在生成 option 时静态写入每个节点**（含 children 两层）：
  ```ts
  const pctColor = (pct: number | null) => pct == null ? '#64748b' : pct > 0 ? '#ef4444' : '#22c55e';
  items.map((c) => ({
    name: c.sector_name,
    value: Math.max(c.heat_score, 0.01),          // treemap value 必须非负；负热度矩形最小块兜底
    sector_code: c.sector_code,
    itemStyle: { color: pctColor(c.pct_chg) },    // 静态色（3.4.2 实测：回调返回值被丢弃，必须静态写入）
    children: c.members.map((m) => ({
      name: m.name,
      value: m.pct_chg == null ? 0.01 : Math.abs(m.pct_chg) + 0.01,  // 大小=当日涨跌幅绝对值；停牌灰块
      ts_code: m.ts_code,
      itemStyle: { color: pctColor(m.pct_chg) },
    })),
  }))
  ```
- option 要点（对齐用户提供示例；levels 仅承担边框/间距——colorSaturation 依赖视觉映射配色，与 per-node 静态色互斥，不放）：
  - `series: [{ type: 'treemap', data, visibleMin: 20, label: { show: true, formatter: '{b}' }, itemStyle: { borderColor: '#fff' }, levels: [{ itemStyle: { borderWidth: 0, gapWidth: 5 } }, { itemStyle: { gapWidth: 1, borderColorSaturation: 0.6 } }] }]`
  - 颜色：红涨绿跌（`#ef4444`/`#22c55e`）+ 停牌灰（`#64748b`），与 CandlestickChart 项目惯例一致
  - tooltip formatter：`treePathInfo` 拼接路径（用户示例同款）；概念层显示 热度分 + 当日涨跌幅、个股层显示当日涨跌幅
  - 点击事件用 **`onEvents: { click }`**（echarts-for-react 3.0.6 的属性名，仓库先例 [AgentTopologyPage.tsx:137](Liveprofit/frontend/src/modules/analysis/pages/agents/AgentTopologyPage.tsx#L137)、[GraphTopologyPanel.tsx:153](Liveprofit/frontend/src/modules/analysis/pages/task-detail/GraphTopologyPanel.tsx#L153)）：params.data 识别层级（有 sector_code → 概念；有 ts_code → 个股）→ 回调 `onNodeClick({ kind, code, name })`
- **KLineDialog**（模块内组件）：共享 `shared/ui/dialog.tsx`；标题 = 名称 + 代码；内容 = `CandlestickChart`（`barsToCandlestickViewModel` 直接复用，indicators null → 纯 K 线）+ bars 空态"K 线不可用"；查询窗口 = 近 180 自然日（`daysAgoLocalDate(180)` → today；常量 `KLINE_DIALOG_DAYS = 180`，**概念/个股同一固定窗口**——概念 K 线返回库内实际积累的长度（首跑 ~33 根、逐日增长，可能短于 180 天），个股 K 线返回该 180 天窗口内全部可用日线；首版不做渐进加载，全历史窗口另案）；**查询 enabled 门控 = dialog 打开且标的确定时**（react-query-dialog 坑：共享 key 弹窗订阅必须 enabled 门控）；关闭即卸载、不留查询。
- **HotConceptsPanel 重写**：保留日期选择（as_of 透传 from）、STALE 徽标、空态/错误态/重试；删除卡片网格与 ConceptCard（含近 10 日涨跌幅展开——treemap 点击 K 线替代该信息）；渲染 `<ConceptTreemap data={tree.items} onNodeClick={...} />`（高度 ~560px）；点击回调 → 打开 KLineDialog（概念 → useConceptBarsQuery / 个股 → useStockBarsQuery）。
- treemap 规模：30 概念 + ≤3,000 成分（后端每概念截断 top 100，见 3.2.1 规模定稿），ECharts treemap 可流畅渲染（实现后人工检查确认）。

#### 3.4.2 三方依赖能力评估

- echarts 6.1（项目既有依赖）：treemap series、levels、treePathInfo tooltip、onEvents 均为官方能力（用户示例即官方 treemap 示例改造）；**颜色回调实测结论（本方案起草时 node SSR 实测 + R1 评审复核）**：`itemStyle.color` 回调形式（series 级或 data 级）在 echarts 6.1 下返回值被丢弃、节点填充默认视觉色（红绿不呈现）；**per-node 静态 `itemStyle.color` 字符串实测有效（含两层 children）**——3.4.1 按静态色设计，单测断言生成后的 option 节点颜色字符串。
- 弹窗/图表组件全部复用项目内既有组件（shared/ui/dialog、CandlestickChart），无新库。

#### 3.4.3 风险与验证方式

- 风险：treemap 两层 label 密度（个股层 label show:true 在 ~3,000 节点下拥挤——小矩形 label 由 ECharts 自动隐藏，视觉可接受，实现后人工调参）；颜色为生成时静态色（无运行时回调，无 params.data undefined 问题）；点击弹窗查询竞态（连续点击不同标的——queryKey 含标的代码，天然隔离；旧弹窗关闭卸载查询）。
- 验证：
  1. `conceptTreeOption.test.ts`：value 映射（heat_score 负值/0 兜底 0.01、成员 |pct|+0.01）、**节点 itemStyle.color 静态色断言（正 → '#ef4444' / 负 → '#22c55e' / null → '#64748b'，两层节点都查）**、levels 结构（两层 gapWidth/borderColorSaturation、无 colorSaturation）、tooltip formatter 路径拼接；
  2. `HotConceptsPanel.test.tsx` 重写：mock 新查询与 CandlestickChart——空态/treemap 渲染/点击概念开弹窗（断言 useConceptBarsQuery 参数）/点击个股开弹窗（断言 useStockBarsQuery 参数）/enabled 门控（未打开时查询 disabled）；mock 的 ConceptTreemap 组件捕获 `onEvents.click` 后手动调用注入点击（先例 GraphTopologyPanel.test.tsx 的 onEvents 注入模式）；
  3. `queries.test.ts` **新建**（现状无此文件——pages 目录仅有各 Panel 测试）：3 个新 hook 的 key 与参数断言；
  4. typecheck + build（codegen 链先跑）。

#### 3.4.4 文件变更清单

- **新建**：
  - `frontend/src/modules/market/components/ConceptTreemap.tsx`
  - `frontend/src/modules/market/components/conceptTreeOption.ts`
  - `frontend/src/modules/market/components/conceptTreeOption.test.ts`
  - `frontend/src/modules/market/components/KLineDialog.tsx`
  - `frontend/src/modules/market/pages/queries.test.ts`（现状无此文件，新建）
- **修改**：
  - `frontend/src/modules/market/pages/queries.ts`：3 个新 hook；**删除 `useHotConceptsQuery`**（面板切换 tree 后无消费方，dead code 不留；hot 端点本身保留——口径不变、契约不动）
  - `frontend/src/api/queryKeys.ts`：3 个新 key；**删除 `hotConcepts` key**（随 hook 删除）
  - `frontend/src/modules/market/pages/HotConceptsPanel.tsx`：卡片 → treemap + 弹窗
  - `frontend/src/modules/market/pages/HotConceptsPanel.test.tsx`：重写（mock 从 hot 端点迁到 tree 端点）
  - `frontend/src/modules/market/pages/MarketOverviewPage.test.tsx`：vi.mock 工厂的 MarketDataService 方法清单随面板切换同步——`hotConceptsApiV1MarketDataConceptsHotGet` 删除、补 `conceptTreeApiV1MarketDataConceptsTreeGet` 等新方法；**现有错误态断言依赖 `hotMock.mockRejectedValue`（热概念端点），必须迁到新方法 mock**
- **OpenAPI 重导出链（必做步骤，产物入清单）**：`python -m backend.scripts.export_openapi`（产物 `backend/openapi/openapi.v1.json`）→ `pnpm run generate:api`（产物 `frontend/src/api/generated/**`——新端点方法名只能来自产物）

### 3.5 文档与经验同步

#### 3.5.1 模块设计

1. **CLAUDE.md 命名规范表 sector_daily 行**（[CLAUDE.md](Liveprofit/CLAUDE.md) 命名规范表，行号随并发编辑漂移按文本锚定）更新：`采集后续阶段，热度功能启用前置` → `dc 源每日增量积累（dc_daily 最近 33 交易日窗口，历史自采集启动日积累不回溯；ths_daily 全历史能力已实测、暂缓）——热度口径 = dc（原口径不变）`。
2. **docs/knowledge/backend/API契约.md**：§8.3 热门概念段**不改**（source='dc' 本就是现状）；新增 tree 端点与两个 bars 端点的契约段（ConceptTreeData 字段表、members 截断 top 100/member_total 语义、BarsData 复用说明——命名定稿：bars 系不改名、频率由 interval 参数承载）；§8.2 BarsData 段（[:691](Liveprofit/docs/knowledge/backend/API契约.md#L691)）：`indicators` 说明补"个股/板块 K 线恒为 null（因子数据后续阶段）"一句。
3. **docs/knowledge/产品需求分析.md §3.1.4 板块区块**（实现收尾时同步；注意热门概念契约在 **§3.1.4.3**，非 §3.1.3——§3.1.3 是市场区块）：
   - 卡片交互描述 → treemap 两层 + 悬浮 tooltip + 点击弹窗 K 线；
   - §3.1.4.3 契约段按现状实现收敛（现文档已漂移：仍写 concept_code/concept_name、cursor、503 HOT_CONCEPTS_UPSTREAM_UNAVAILABLE——现行实现为 sector_code/sector_name、无 cursor、503 已删，R1 F17）并注明以 API契约.md 为准；新增 tree/bars 端点描述。
4. **docs/memory/pitfalls/ai/tushare-endpoints.md** 追加三条实测（2026-09-13）：① dc_daily 仅最近 33 交易日（更早区间 0 行），**窗口型数据源，不可用于历史回填，只能每日增量积累**；② ths_daily 单请求全历史（20/20 板块实测、列集、无 amount、降序归一）；③ akshare 东财 kline 主机 push2his.eastmoney.com 不可达（直连+本地代理均重置），板块历史行情不要走东财直连。

#### 3.5.2 文件变更清单

- **修改**：`CLAUDE.md`、`docs/knowledge/backend/API契约.md`、`docs/knowledge/产品需求分析.md`、`docs/memory/pitfalls/ai/tushare-endpoints.md`、`docs/memory/index.md`（如文件条目描述变化）

## 四、已确认决策 / 待确认问题

- 已确认决策：
  1. treemap 两层（概念+成分股）、矩形大小=热度、颜色=当日涨跌幅、悬浮 tooltip + 点击弹窗 K 线（用户 2026-09-14 拍板）。
  2. **数据先存 dc**（用户 2026-09-14 修订拍板，撤销"全历史回填"）：dc_daily 每日增量积累、历史自启动日起自然增长、不做回填；热度口径保持原 dc 不变（get_hot_concepts 零改动）；ths 全历史能力已验证、暂缓。**作废标注**：[证券市场数据库统一方案](../archive/证券市场数据库统一方案.md) §3.7.1（"最小窗口回填 → 全历史回填"）与决策 16 ②（"落地顺序 ① sector_daily 最小窗口"）描述的路线随本拍板撤销——归档文档不改，以本方案为准。
  3. 大盘页"板块"区块由 treemap 替换现有卡片网格（本方案默认落点；卡片 UI 与 treemap 并存会重复展示同一批数据）。
  4. hot 端点保留（口径不变、契约不动）；前端 `useHotConceptsQuery` hook 与 `queryKeys.hotConcepts` 随面板切换删除（前端 dead code 不留，与端点去留正交）。
  5. **日线数据命名定稿：bars 系不改名、频率由 `interval` 区分**（用户 2026-09-14 讨论收敛）：DTO 结构对日/周/月线完全相同（频率是取值不是类型）——`BarDTO`/`BarsData`/`get_bars`/`/bars` 端点全部保持现状；接口频率区分由 `interval` 查询参数 + 响应 `interval` 字段承载（本方案新端点同样带 `interval=1d` 参数）；未来周/月线零新 DTO、零新端点。原"bar+daily 修饰改名"方向作废（DailyBarDTO 对周/月线会名字撒谎）。
- 已确认决策（追加）：
  6. **treemap 概念层 = dc 概念按热度 top 30**（用户 2026-09-14 拍板）：limit 参数保留 1..30 可调；与 HOT_CONCEPTS_LIMIT=30 榜单口径一致。
- 待确认问题：无（方案已闭环）。

## 验证总表

| 层面 | 命令/方式 | 观察点 |
|------|----------|--------|
| 采集单测 | `pytest tests/db/instrument/test_sector_daily_ingest.py -k "not integration"` | 列映射/升序/幂等 DO UPDATE/失败分层/独立 commit/日期转换断言 |
| 采集集成 | 人工执行一次 `collect_incremental`（真实端点） | `SELECT count(*) FROM market.sector_daily WHERE source='dc'` ≈ 1031 板块 × 33 行；次日重跑幂等 |
| 后端契约 | `pytest backend/tests/contract/api/test_market_data.py -k "not integration"` | tree 三用例、concept/stock bars、**既有 hot 端点用例零改动保持通过**（口径不变） |
| 前端单测 | `pnpm --dir frontend vitest run src/modules/market` | treemap option/面板交互/弹窗门控断言 |
| 前端类型/构建 | `pnpm --dir frontend typecheck && pnpm --dir frontend build` | 无类型错误 |
| 人工检查 | 起前后端，打开大盘页 | treemap 两层渲染；悬浮 tooltip 显示名称/热度/涨跌幅；点击概念 → 板块指数 K 线弹窗（首跑 ~33 根、逐日增长）；点击成分股 → 个股 K 线弹窗（近 180 自然日固定窗口）；停牌股灰块；日期切换 as_of 榜单变化 |
