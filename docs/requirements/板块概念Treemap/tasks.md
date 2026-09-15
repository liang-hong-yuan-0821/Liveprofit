# 板块概念Treemap 任务清单

> **状态**：`实现中`（2026-09-14）
> **进度**：5/6 任务
> **下一步**：T6 数据首跑与端到端人工验收
> **关联方案**：[plan.md](plan.md)（同任务文件夹内方案正文）

---

## 任务总览

| 编号 | 任务 | 依赖 | 状态 |
|------|------|------|------|
| T1 | 板块日线采集（sector_daily dc 每日增量，3.1） | — | 已完成 |
| T2 | 概念树 API（tree 端点 + get_concept_tree，3.2） | T1 | 已完成 |
| T3 | 概念/个股 bars API（get_sector_bars + get_bars 放宽，3.3） | T2 | 已完成 |
| T4 | 前端 Treemap 与点击弹窗（3.4） | T2、T3 | 已完成 |
| T5 | 文档与经验同步（3.5） | T4 | 已完成 |
| T6 | 数据首跑与端到端人工验收 | T1、T4 | 进行中 |

## 任务

### T1 板块日线采集（sector_daily dc 每日增量）

- **目标**：dc_daily 窗口型数据每日增量入库 market.sector_daily（方案 3.1），板块区块数据底座就绪。
- **涉及文件**：
  - 新建：`db/instrument/ingest/sector_daily.py`（collect_sector_daily_incremental：70 自然日窗口、DO UPDATE、source/updated_at 显式填、连续 5 失败熔断、每板块独立 commit）
  - 新建：`tests/db/instrument/test_sector_daily_ingest.py`（fake provider 单测）
  - 修改：`AI/dataflows/providers/base_provider.py`（`get_sector_daily_df(source, ts_code, start_date, end_date)` 默认 None，YYYYMMDD 签名约定）
  - 修改：`AI/dataflows/providers/cn/tushare.py`（dc 分支覆写：dc_daily + idx_type + 升序归一；ths 分支留 TODO 注释）
  - 修改：`db/instrument/ingest/incremental.py`（collect_incremental 新增板块日线增量步骤，失败不阻断）
  - 修改：`run.sh`（增量步骤耗时说明随改）
- **依赖**：无
- **验收标准**（全部勾选才算完成）：
  - [x] `pytest tests/db/instrument/test_sector_daily_ingest.py` 全绿：列映射（pre_close 恒 NULL/amount 有值/pct_change→pct_chg/swing+category 丢弃）、写入列 source='dc' 恒值与 updated_at=采集时刻断言、升序归一（降序输入，provider 侧测试）、drop_duplicates、DO UPDATE 分流、单板块失败不阻断、连续 5 失败熔断（第 5 次失败后不再发请求、剩余板块记入 failed）、每板块独立 commit（失败回滚后下一板块仍写入）、窗口日期 YYYYMMDD 转换
  - [x] 既有采集测试回归：`pytest tests/db/instrument/ -k "not integration"` + `tests/dataflows/providers/` 全绿（169 passed，incremental 接入不破坏现有用例）
- **状态**：`已完成`（2026-09-14）

### T2 概念树 API（tree 端点 + get_concept_tree）

- **目标**：`GET /market-data/concepts/tree` 端点与热度/成分数据产出（方案 3.2），treemap 数据源就绪。
- **涉及文件**：
  - 修改：`backend/modules/market_data/application/service.py`（`_compute_sector_heat` 提取 + `get_concept_tree`：dc 口径、LEFT JOIN 成分、|pct_chg| 降序截断 top 100、member_total、pct_chg 显式按 as_of 取值）
  - 修改：`backend/api/schemas/market.py`（ConceptMemberDTO/ConceptTreeNodeDTO/ConceptTreeData）
  - 修改：`backend/api/routers/market_data.py`（tree 端点：limit 必填 Query(ge=1,le=30)、from→as_of 透传、to 忽略、interval≠1d 422）
  - 修改：`backend/tests/contract/api/test_market_data.py`（test_concept_tree_* 3 条：seed 断言/非 CN 空态/NO_HOT_CONCEPTS）
  - 修改：`backend/openapi/openapi.v1.json`（`python -m backend.scripts.export_openapi` 重导出）
- **依赖**：T1（读 sector_daily；契约测试 seed 独立于真实数据）
- **验收标准**：
  - [x] `pytest backend/tests/contract/api/test_market_data.py -k "not integration"` 全绿：tree 3 用例（rank/heat_score 5.769 推导/pct_chg/members 名称映射与 |pct_chg| 降序/截断 top 100+member_total（>100 成分 seed）/停牌 null/板块当日缺行 null）+ 既有 hot 用例零改动通过（12 passed）
  - [x] openapi gate 通过（重导出后 `pytest backend/tests/contract/test_openapi_gate.py`，2 passed）
- **状态**：`已完成`（2026-09-14）

### T3 概念/个股 bars API（get_sector_bars + get_bars 放宽）

- **目标**：概念 K 线（sector_daily）与个股 K 线（instrument_daily）端点（方案 3.3），弹窗数据源就绪。
- **涉及文件**：
  - 修改：`db/instrument/dao/sector_daily.py`（`query_bars` 单板块区间升序查询）
  - 修改：`backend/modules/market_data/application/service.py`（`get_sector_bars`：OHLC 任一 NaN 整行丢弃、indicators 恒 None、BarsData 必填字段回显口径；`get_bars` 放宽 instrument_type ∈ {index, stock} + 仅个股路径因子空 → indicators=None）
  - 修改：`backend/api/routers/market_data.py`（concepts/{code}/bars 与 stocks/{symbol}/bars 两端点，interval 必填同 indices）
  - 修改：`backend/tests/contract/api/test_market_data.py`（concept bars 200/404、stock bars 200 纯 K 线/fund 404、指数路径回归）
  - 修改：`backend/openapi/openapi.v1.json`（重导出）
- **依赖**：T2（同文件顺序改）
- **验收标准**：
  - [x] `pytest backend/tests/contract/api/test_market_data.py -k "not integration"` 全绿：新增用例 + 指数路径冻结用例（:77-81 全 null 数组断言）保持通过（16 passed）；集成 test_market_flow.py 4 passed
  - [x] openapi gate 通过（2 passed）
- **状态**：`已完成`（2026-09-14）

### T4 前端 Treemap 与点击弹窗

- **目标**：板块区块卡片 → treemap 两层 + 悬浮 tooltip + 点击弹窗 K 线（方案 3.4）。
- **涉及文件**：
  - 修改：`frontend/src/api/generated/**`（`pnpm run generate:api` 产物）
  - 新建：`frontend/src/modules/market/components/conceptTreeOption.ts`（静态逐节点着色/levels/tooltip treePathInfo）
  - 新建：`frontend/src/modules/market/components/conceptTreeOption.test.ts`
  - 新建：`frontend/src/modules/market/components/ConceptTreemap.tsx`（onEvents.click 层级识别）
  - 新建：`frontend/src/modules/market/components/KLineDialog.tsx`（enabled 门控 + KLINE_DIALOG_DAYS=180）
  - 新建：`frontend/src/modules/market/pages/queries.test.ts`
  - 修改：`frontend/src/modules/market/pages/queries.ts`（useConceptTreeQuery/useConceptBarsQuery/useStockBarsQuery 新增；useHotConceptsQuery 删除）
  - 修改：`frontend/src/api/queryKeys.ts`（3 新 key；hotConcepts key 删除）
  - 修改：`frontend/src/modules/market/pages/HotConceptsPanel.tsx`（卡片 → treemap + 弹窗）
  - 修改：`frontend/src/modules/market/pages/HotConceptsPanel.test.tsx`（重写）
  - 修改：`frontend/src/modules/market/pages/MarketOverviewPage.test.tsx`（vi.mock 方法清单随迁：hotConcepts 方法删、补 tree 方法；错误态 mock 迁移）
- **依赖**：T2、T3（codegen 产物）
- **验收标准**：
  - [x] `pnpm --dir frontend vitest run src/modules/market` 全绿：option（静态色断言 正红/负绿/null 灰、value 映射、levels 无 colorSaturation、tooltip 路径拼接）、面板交互（空态/点击概念→useConceptBarsQuery 参数/点击个股→useStockBarsQuery 参数/enabled 门控）、queries key 与参数（35 passed；全量 229 passed）
  - [x] `pnpm --dir frontend typecheck && pnpm --dir frontend build` 无类型错误
- **状态**：`已完成`（2026-09-14）

### T5 文档与经验同步

- **目标**：方案 3.5 的四处文档与经验同步。
- **涉及文件**：
  - 修改：`CLAUDE.md`（命名规范表 sector_daily 行）
  - 修改：`docs/knowledge/backend/API契约.md`（§8.3 补 tree/bars 契约段、§8.2 indicators 补注）
  - 修改：`docs/knowledge/产品需求分析.md`（§3.1.4 卡片→treemap、§3.1.4.3 契约收敛）
  - 修改：`docs/memory/pitfalls/ai/tushare-endpoints.md`（三条实测追加）
  - 修改：`docs/memory/index.md`（如条目描述变化）
- **依赖**：T4（交互定稿后文档才准确）
- **验收标准**：
  - [x] 人工检查：四处文档按方案 3.5.1 逐条对照已更新；grep `docs/(done|plans)` 于上述文档 0 命中（含顺修 API契约.md 两处存量死链）
- **状态**：`已完成`（2026-09-14）

### T6 数据首跑与端到端人工验收

- **目标**：真实数据首跑 + 大盘页端到端验收（验证总表人工检查行）。
- **涉及文件**：无（执行类任务）
- **依赖**：T1、T4
- **验收标准**：
  - [x] 人工执行增量（真实端点，`collect_sector_daily_incremental` 直调）：**1031 板块全采、51,381 行、0 失败**（实际每板块 ~50 行——端点窗口比探测时更宽，2026-07-06 ~ 2026-09-11）；updated_at 全非 NULL；服务链真数据验证通过（hot OK / tree OK heat=866.05 / 概念 K 线 24 根 FRESH / 个股 K 线 30 根纯 K 线）
  - [ ] 起前后端打开大盘页：板块区块 treemap 两层渲染；悬浮 tooltip 显示名称/热度/涨跌幅；点击概念 → 板块指数 K 线弹窗（~50 根）；点击成分股 → 个股 K 线弹窗（180 天窗口）；停牌股灰块；日期切换 as_of 榜单变化；次日重跑增量幂等（行数 +1 日/板块）——**浏览器渲染检查待用户执行**
- **状态**：`进行中`（2026-09-14，数据侧完成；浏览器端人工检查待用户）
