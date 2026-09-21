# 最终产出与结论

## 验证结果

| 层面 | 命令/方式 | 结果 |
|------|----------|------|
| 采集域单测 | `.venv/Scripts/python.exe -m pytest tests/db/instrument -q` | 107 passed（守卫冒烟：15 目标、.CSI 通过、未知代码拒绝） |
| 服务层集成 | `.venv/Scripts/python.exe -m pytest backend/tests/integration/market_data -q` | 7 passed（空窗口契约/as_of 与 freshness 三态/from>to） |
| 契约测试 | `.venv/Scripts/python.exe -m pytest backend/tests/contract/api/test_market_data.py -q` | 19 passed（2 趋势端点 200 形状 + 422 RANGE_TOO_LARGE） |
| OpenAPI 门禁 | CR 复跑 `pytest backend/tests/contract/test_openapi_gate.py -q` | 2 passed（openapi.v1.json 与重导出幂等一致，含两端点） |
| 前端单测 | `pnpm vitest run src/shared/charts src/modules/market/pages` | 115 passed；前端全量 336 passed |
| 前端类型 | `pnpm typecheck`（tsc 双工程） | exit 0 |
| 调色板 | dataviz `validate_palette.js`（暗色 4 槽） | ALL CHECKS PASS |
| 真实库回填 | 定向回填 000905.SH/932000.CSI（5 年分块） | 8369 bars + 8368 factors ≈12 分钟；000905.SH 5275 行/2005-01-04 起（比方案点测更全）、932000.CSI 3094 行/2013-12-31 基日行起；instrument 2 行自举；重跑断点续跑生效（首段基日 NULL 例外为已知行为） |
| 板组零采集 | SQL 核验 | 000001.SH 8729/1990-12-19、399006.SZ 3961/2010-06-01、000688.SH 1630/2019-12-31，末点均 2026-09-18，零采集动作 |
| API 端到端 | TestClient 打真实库 | 2 端点 200（cap-tiers 4 序列 / boards 3 序列）+ from>to → 422 RANGE_TOO_LARGE |
| 增量核验 | 真实 provider 跑指数步骤 3（窗口 20260916/17/18） | bars=45（15 目标 × 3 日）、factors=33（CN 11 × 3 日）——新 2 指数进入增量路径 |
| 前端目检 | 大盘页两 Tab 渲染、四条线同起点 | 待用户人工检查（后端 8000 端口当前跑旧代码，需重启后生效；共同首日实测 = 分层组 2013-12-31 / 板组 2019-12-31） |

**实测偏差记录**（相对方案预测，均非缺陷）：
1. 回填历史比方案点测更深：000905 首行 2005-01-04（方案 2007-01-04）、932000 首行 2013-12-31 基日行（方案 2014-01-02）→「全部」区间分层组共同首日 = 2013-12-31（方案预测 2014-01-02）。
2. 真实环境 freshness 当前恒 STALE：AI 交易日历 2026 缓存未覆盖近期（`get_last_trading_day('20260919')` 30 天回溯落空返回原值）——影响既有 K 线卡片同款判定，属环境日历陈旧，独立课题。

## Code Review

- **结论**：**通过**（2026-09-19，两轮收敛）。R1：verdict PASS，0 blocker / 0 major / 2 minor / 4 polish；主会话修复全部 6 条后二轮 delta 核验：**PASS 维持、无需三轮**——F1（双查询挂载成本）按决策记录于 plan 4.4.3 + 测试注释、F2（knowledge 计数）随收尾整合、F3（面板 model useMemo）落地、F4（height 死字段）删除、F5（:7 计数消歧）落地、F6（plan 实测值修正注）落地；二轮新提 1 polish（plan :159/:433 旧值残留）已随收尾修正。reviewer 独立复跑：107 + 115 passed、typecheck exit 0、openapi 门禁 2 passed
- **遗留**：无代码遗留。两处人工目检待用户真机检查（T4 大盘页区块渲染与无重排、T5 前端目检两 Tab 同起点）；预期会看到「数据滞后」角标 = AI 交易日历 2026 缓存陈旧的环境现象（非缺陷，既有 K 线卡片同款）

## 交付物

- **后端**：`backend/api/schemas/market.py`（TrendPointDTO/TrendSeriesDTO/TrendsData）、`backend/modules/market_data/application/service.py`（CAP_TIER_INDEXES/BOARD_INDEXES + get_index_trends）、`backend/api/routers/market_data.py`（/market-data/trends/{cap-tiers,boards}）、`backend/tests/integration/market_data/test_trends.py`（新建）、`backend/tests/contract/api/test_market_data.py`（追加）；`backend/openapi/openapi.v1.json` + 前端 `src/api/generated/`（codegen 重导出）
- **采集**：`db/instrument/ingest/incremental.py`（INDEX_TARGETS 15 目标、.CSI 守卫）、backfill.py/market_ingest.py/测试/前端注释随迁、CLAUDE.md 代码格式行
- **前端**：`src/shared/charts/LineChart.tsx`+测试（新建）、`src/modules/market/pages/TrendComparisonPanel.tsx`+测试（新建）、`mappers/toTrendViewModels.ts`+测试（新建）、queries.ts（2 hooks）+ queryKeys.ts（2 keys）、MarketOverviewPage.tsx（趋势对比区块 + 副标题）
- **数据**：真实库 000905.SH 5275 行、932000.CSI 3094 行（bars+factor 入库）
- **文档**：knowledge（API契约.md §8.5 + 端点表、前端平台.md、docs/index.md 计数）、memory（best-practices/backend/ingest-targeted-backfill.md、pitfalls/frontend/date-arithmetic-clamping.md）
