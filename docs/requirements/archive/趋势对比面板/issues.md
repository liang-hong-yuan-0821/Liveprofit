# 问题与解决

评审 findings 与实施中遇到的问题。按时间**倒序**追加，每条三要素：表象 → 根因 → 解决。

## 2026-09-19 R1 全量评审（verdict FAIL：4 major + 10 minor + 1 polish，均分 7.2）

评审员实测复核方式：连 PG 跑板聚合 SQL（3 年 0.63s / 全历史 1.25s）、重调代理端点、查 `stock_info`/`instrument_daily` 行数。**已确认成立的事实**：新增两指数端点可达（4791/3093 行）、`.CSI` 后缀可行、板聚合 SQL 原样可跑、测试断言动态计数自适应、AI 链路与本方案无冲突。

### major

- **M1 板曲线名不副实（幸存者偏差）**
  - 表象：方案称板曲线"覆盖板内全部股票"，实测 `instrument_daily` 5818 个股票代码中约 250 个（约 4.3%，评审当时报 253；**精确计数依赖分类口径，复算落在 251–258 不等**）不在 `stock_info`/`instrument`（两表只同步在册股票，`list_status` 全 `'L'`）→ 已退市股票被静默排除，且退市股集中主板、退市整理期普遍 -80~95%
  - 根因：归板依据 `stock_info.market` 的数据源本身只有在册股票；用官方头部指数替代又失真（科创板 618 只中科创50 只覆盖 50 只）
  - 解决：**用户拍板改官方指数**（上证综指/创业板指/科创50，均在库）——自编聚合整体移出，偏差来源消失；口径差异改为在图注如实标注（plan 4.4.1）
- **M2 「全部」区间归一基点不一致**
  - 表象："区间起点=100"在「全部」下各序列 0 点落在不同日期（分层组 2005/2007/2005/2014，板组 1990/2010/2019）→ 跨层对比失真
  - 根因：归一按"各序列自己的首个点"取基点，未定义共同基准
  - 解决：**用户拍板共同首日=100**（plan 4.4.1 算法），并写明「全部」发送 `from=1990-01-01`；代价（基点日之前的点丢弃）已在决策 3 与 plan 中显式记录
- **M3 HTTP 状态码写错**
  - 表象：plan 写 `from>to → 400`，实测既有契约是 422：`exception_handlers.py:66` `"RANGE_TOO_LARGE": (422, False)`，既有用例 `backend/tests/contract/api/test_market_data.py:93-98` 断言 422 + `code == "RANGE_TOO_LARGE"`
  - 根因：写方案时未核对错误码映射表
  - 解决：plan 4.2.3 验证项改为 422 + 显式 code 断言；tasks T2 验收项同步
- **M4 cum_index 基点自相矛盾**
  - 表象：plan 写"基点 2016-01-04=1000"，但 SQL 带 `BETWEEN` 截窗 → 子窗口内 cumprod 只能以窗口首行起算，二者不可同时成立
  - 根因：自算累计指数的基点定义与查询窗口语义冲突
  - 解决：自算 `cum_index` 整体取消（服务端只回原始 close，归一在前端做），矛盾项消失

### minor

- **m1 数量表述漏迁 6 处**（我在前置自检时只找到 4 处）：`incremental.py:7`（、`:45`、`:46-48`、`backend/workers/market_ingest.py:9`、`test_index_ingestion.py:67`、`:206`、`test_incremental.py:149` → 解决：plan 4.1.1 改为**12 处全量枚举表**，逐条列"现文本 → 改为"，并附"不改的同类文本"清单（前端 11 卡片等与编制规模无关的表述）
  - 表象：自检声称"四处随迁点"实为 10+ 处
  - 根因：只按关键词 `INDEX_TARGETS` 搜，漏了 `CN 9`/`9 个`/`13 指数` 等别名表述与 worker argparse docstring
  - 解决：全仓 grep `CN 9\|CN 7\|13 个\|9 个\|11 个` 逐条人工分类（改/不改）
- **m2 路由草图缺 `trace_id` 依赖、且 `_run_get_bars` 不可复用**（它绑定 `get_bars`）→ 解决：plan 4.2.1 改按 `sector_bars` 先例写局部 `_do` 闭包 + `trace_id: str = Depends(ensure_trace_context)`
- **m3 服务层返回类型未定义**→ 解决：定义应用层 `TrendSeries`/`TrendsDTO` + 路由侧 `_trends_data` 映射函数（`BarsDTO`→`_bars_data` 先例）
- **m4 DTO 风格/命名无依据**→ 解决：Data 层带 `as_of`/`freshness_status`；`points`/`date` 命名给出理由并说明分族方式（应用层 `TrendSeries` vs API 层 `TrendSeriesDTO`，避免路由同文件 import 冲突）。（**R2 修订**：初版修复曾给逐序列加 `source`、Data 层加 `source_updated_at`，R2 复核判定二者均无消费方且多资产聚合下语义失真 → 已删除并写明理由，见 4.2.1）
- **m5 空窗口契约与连接纪律未写**→ 解决：plan 4.2.1 第 4 条定义空窗口语义（序列条目恒返回、points 空、as_of 取全历史口径、不抛错不 404），第 5 条写连接纪律（`with` 上下文 + 若出现 except 必须 `conn.rollback()`）
- **m6 首日过滤漏极端涨跌幅**（实测 20 行 \|pct_chg\|>50：601091.SH +177.74%、000670.SZ +488.44%，科创板单日扭曲 1.549pp/累计 2.50pp）→ 解决：随 M1 一并消失（不再聚合个股）
- **m7 单测路径写错 + 踩 `role="tab"` 断言**：`backend/tests/unit/modules/market_data/` 不存在，真实目录是 `backend/tests/unit/market_data/`；且 `MarketOverviewPage.test.tsx:97` 断言页面 `queryByRole('tab')` 为空 → 解决：面板 Tab 明确用 `aria-pressed` 按钮组。（**R2 修订**：路径初版改为 `unit/market_data/`，R2 核出该目录只有 `test_indicators.py`、无 conftest 与 `env`/`service` DB fixture → 服务层用例落点改 `backend/tests/integration/market_data/test_trends.py`，复用同目录 fixture）
- **m8 DTO→ViewModel 映射落点未写**→ 解决：`modules/market/pages/mappers/toTrendViewModels.ts`（`toChartViewModels.ts` 同目录先例）+ 归一纯函数单测
- **m9 层级标签来源未定义**→ 解决：序列名一律取响应 `series[].name`，前端不硬编码
- **m10 ECharts time 轴数据格式静默不渲染风险**→ 解决：ViewModel `data: Array<[string, number]>` 坐标对写死进 plan + 单测断言元素形状
- **p1 润色并入**：日期改 2026-09-19、`idx_factor_pro` 表述改为实测值、`shared/feedback/` 路径补全、`test_incremental.py:161` 更正为集合推导。（**R2 修订**：该实测值初版写 1386 行，R2 复测为 **1220 行**——932000.CSI 与 000905.SH 在 2014-01-01~2018-12-31 窗口各 1220 行（20140102~20181228），plan 4.1.2 已改）

### 修复后状态

plan.md 全文重写（板 Tab 数据源、归一基点、DTO 形态、验证项），tasks.md 同步重拆，decisions.md 追加 2 条决策，README 状态更新。**进入 R2 修复核验**（delta 口径：核 R1 findings 落地 + 修复点与周边文字交互，只对本轮涉及维度重新打分）。

## 2026-09-19 R2 修复核验（verdict FAIL：1 major，均分 8.9）

delta 口径：只核 R1 findings 落地 + 修复点与周边文字交互（同类表述残留 / 新引入矛盾），只对本轮涉及维度重打分。**R1 的 4 major / 10 minor / 1 polish 全部确认落地**；以下为 R2 新提出的 1 条 major 与 10 条 minor（9 条条目，F9/F10 合并为一条）。

维度分数（R1 → R2；**仅维度 7 = 6 <8**，分界点只在这一条 major，若按 minor 口径则全部 ≥8 → PASS）：

| # | 维度 | R1 | R2 | 口径 |
|---|------|----|----|------|
| 1 | 现状代码事实 | 6 | 9 | 重打（polish F4/F5） |
| 2 | 数据流闭环 | 6 | 8 | 重打（polish F9/F10/F11） |
| 3 | 接口一致性 | 6 | 10 | 重打（无 finding；**评时 `source` 仍在**，本轮删除后需 R3 重核） |
| 4 | 结构改造随迁 | 7 | 8 | 重打（minor F2/F3） |
| 5 | 向后兼容 | 10 | 10 | 沿用（本轮未动） |
| 6 | 边界与失败路径 | 7 | 10 | 重打（无 finding） |
| 7 | 测试可构造性 | 7 | **6** | 重打（**major F1** + polish F8） |
| 8 | 三方依赖能力 | 7 | 9 | 重打（polish F7） |
| 9 | 跨链路覆盖 | 10 | 10 | 沿用（本轮未动） |
| 10 | 数值推导自洽 | 6 | 9 | 重打（polish F5/F6） |

### major

- **F1 服务层用例落点仍错（m7 的残留）**
  - 表象：修复后路径 `backend/tests/unit/market_data/test_trends.py` 的目录**只有 `test_indicators.py`，无 conftest、无 `env`/`service`/`_seed` fixture**——本用例要 seed `market.instrument_daily` 并注入 `market_conn` 工厂，纯单测目录里 DB 场景构造不出来
  - 根因：改路径时只核"目录存在 + 同类文件在"，未核**该目录的 fixture 能力**（m7 修了"路径不存在"，没修"落点能力不匹配"）
  - 解决：落点改 **`backend/tests/integration/market_data/test_trends.py`**（同目录 `test_market_flow.py` 已有 `env`/`service`/`_seed`/`FakeCalendar` 四件套，[:23-37](../../../../backend/tests/integration/market_data/test_market_flow.py)）；tasks T2 验收命令改 `pytest backend/tests/integration/market_data -q`；plan 4.2.3/4.2.4 同步

### minor

- **F2 决策编号残留**：decisions.md「行业 Tab 暂缓」条指向「五、已确认决策 3」，但行业 Tab 重编号后是 **4**（plan.md:40 与 README 已是 4）→ 已改
- **F3 DTO 缺口径差异注释**：板 Tab 三条线是官方指数编制口径（综指 vs 样本指数），响应里无字段承载 → 已在 `TrendsData` docstring 写明（4.2.1）
- **F5 数量表述精确化过度**：plan/decisions 的"253 个退市股"不可复现（分类口径不同 → 251/254/258）→ 改为"约 250 个（约 4.3%），精确计数依赖分类口径"，issues 归档保留原数并加口径说明
- **F6 `idx_factor_pro` 行数写错**：初版 1386 → 复测 **1220 行** → 已改（见 p1 修订）
- **F7 akshare 兜底能力"未知"**：实为**确定不可用**（`.CSI` 后缀 → `_index_symbol` 产出 `csi932000`，[akshare.py:1953-1963](../../../../AI/dataflows/providers/cn/akshare.py)）→ 4.1.3 风险项改为"实为单源（tushare）"，不再挂"能力未知"
- **F8 T1 验收 grep 口径太窄**：`CN 9\|13 个目标\|13 目标\|13 指数` 漏 `9 个\|11 个\|13 个` 等别名 → 改为"按 4.1.1 表逐行核对 + 辅助 grep 需人工分类"
- **F9/F10 `source` / `source_updated_at` 无消费方**：多资产聚合下逐序列 provenance 无人读、聚合成单值语义失真 → 从 `TrendSeries`/`TrendsDTO`/`TrendSeriesDTO`/`TrendsData` 全部删除（方法行为第 3 点写明理由），聚合查询简化为 `SELECT ts_code, max(trade_date) ... GROUP BY ts_code`；同时补**真实消费方**：面板展示「数据截至 {as_of}」+ STALE/UNAVAILABLE 角标（4.4.1/4.4.3 + tasks T4）
- **F11 区间 `from` 口径未定义**：近3月/近1年/近3年只说"近 N 月"未给算法 → 4.4.1 补 `addMonths(今天, -N)` 定义表（含日溢出夹取：`2026-03-31` 减 1 月 = `2026-02-28`）与"边界只决定取多少历史、不影响归一曲线"的说明
- **F4 数据齐备表述**：三、设计概览"四层数据齐备"未区分「已在库 2 层 / 待回填 2 层」→ 已改为分层表述

### 修复后状态

plan.md（用例落点、DTO 字段、口径注释、区间定义）、tasks.md（T2 命令/文件、T4 断言）、decisions.md（编号、数量）、README 同步。**R2 的 FAIL 仅由 F1 一条 major 构成，且为 R1 m7 的修复残留（非新缺陷）→ 未触发终止条件**，进入 R3 最终核验（delta 口径：核本轮 11 条的落地）。

## 2026-09-19 R3 最终核验（verdict **PASS**：无 blocker/major，**10 维度全 ≥8**，均分 9.4）

delta 口径（只核 R2 findings 落地 + 修复点与周边交互，只重打被波及维度）。**R2 的 11 条：10 条完全落地、1 条部分落地（F4 的一句目标列措辞）**；本轮新提 1 minor + 5 polish（收尾顺手修，不阻塞确认）。

维度分数（R2 → R3）：1 现状代码事实 9→9｜2 数据流闭环 8→**9**｜3 接口一致性 10→9｜4 结构改造随迁 8→**9**｜5 向后兼容 10 沿用｜6 边界与失败路径 10 沿用｜7 测试可构造性 **6→8**｜8 三方依赖能力 9→**10**｜9 跨链路覆盖 10 沿用｜10 数值推导自洽 9→**10**；均分 8.9 → **9.4**。

### minor（不影响实施一致性，收尾顺手修）

- **fixture 复用表述不精确**（维度 7）：`env` 由 `integration/market_data/conftest.py:44` 提供（新文件自动可用），但 `service(:23-29)` / `_seed(:32-37)` 是 **`test_market_flow.py` 的模块级定义**，pytest 模块级 fixture/helper **不跨文件共享** → 新文件直接 `def test_x(env, service)` 会报 fixture not found。判定不构成实施阻断（同目录 `env` 自动可用、两个 helper 各 6 行且 plan 已给准确定位，错误必在首轮 pytest 暴露、无静默失败）→ 解决：plan 4.2.3/4.2.4 + tasks T2 三处改为"`env` 取同目录 conftest；`service`/`_seed` 从 `test_market_flow` import 或复制（或先上提到 conftest）"

### polish（收尾已修）

- **P1 指代范围冲突**（维度 3）：4.2.1 删除 `source` 的理由句前半写"多资产聚合（4 层指数 / 3 板指数）"、后半例证写"三个指数同为 tushare" → 改"两组合计 7 个指数"
- **P2 目标列措辞**（维度 1，F4 残句）：一、背景表"四层…数据齐备" → 改"已在库 2 条 + 本次回填 2 条（T5 前空 `points` 按空窗口契约降级）"
- **P3 历史条目编号错位**（维度 4）：log.md 的"砍掉行业 Tab"行"留档于五、已确认决策 3" → 补注"（2026-09-19 追加决策 1-2 后重编号为 4）"
- **P4 文档状态未随轮次更新**：plan.md/tasks.md 状态行 → 已改（`待确认` + 三轮收敛说明）
- **P5 角标撞词**（维度 2/3）：面板级 `UNAVAILABLE` 文案由「无数据」改「**暂无数据**」（与逐序列「无数据：<name>」区分），并在 4.4.3 + tasks T4 补 UNAVAILABLE 渲染断言

### 核验旁证（评审员实测）

F1 落点三处一致且目录确有 DB fixture；F7 机制读码复核（`_index_symbol` akshare.py:1954-1962 → `csi932000`，调用点 :1861，空帧/异常均 `return None` → 兜底必失败）；4.1.1 表 6 行与代码逐字一致；1220 与 `INDEX_CHUNK_DAYS=1825` 分块自洽；`addMonths` 夹取与示例日期算术正确；区间起点晚于共同首日的推演成立（基点 = 区间内所有序列都有数据的首个交易日，各线仍同起点、比值不变）；`Freshness` 值域与 `UNAVAILABLE ⟺ as_of is None` 与既有 `get_bars` 口径一致。

> 以下为模板示例，保留备查：
>
> ## 2026-09-14 <问题一句话>
>
> - **表象**：<可观测现象/报错>
> - **根因**：<定位到的原因>
> - **解决**：<修复方式 + 验证结果>
