# 最终产出与结论

## 验证结果

| 层面 | 命令/方式 | 结果 |
|------|----------|------|
| 单测（provider 新用例） | `pytest tests/dataflows/providers/test_tushare_index_global.py tests/dataflows/test_akshare_index_data_df.py -q` | 18 passed（映射分派 / 标准帧 / 先算后过滤核心回归 / 异常 None / CN 不变 / AI 面 kospi） |
| 单测（dataflows 全量） | `pytest tests/dataflows -q` | 273 passed（CN 路径无回归） |
| 单测（采集链全量） | `pytest tests/db/instrument -q` | 100 passed（守卫反转 / 因子跳过 / 兜底异常隔离 / 兜底 source 标注） |
| 前端单测 | `pnpm vitest run src/modules/market/pages/MarketIndicesPanel.test.tsx` | 6 passed（11 名称 / 11 请求 / 0"暂不可用"卡片 / STALE 计数 11） |
| 真实库回填 | 定向回填（进程内 INDEX_TARGETS 缩为 4 码 + skip_concepts/skip_daily，2000-01-01 起） | 26723 行：.DJI 6714 / .INX 6713 / .IXIC 6713 / KS11 6583，min trade_date 2000-01，KS11 到 2026-09-14 |
| SQL 校验 | instrument.data_source / instrument_daily.source / pre_close NULL / KOSDAQ 残留 | 全 'tushare' / 全 'tushare' / 0 行 / 0 行；遗留 KOSPI/KOSDAQ 孤儿行已删 |
| API | `GET /api/v1/market-data/indices/{.INX/.DJI/.IXIC/KS11}/bars` | 4 码全 200 + bars + source=tushare；KS11 FRESH（到 2026-09-14）；US 3 码 STALE（CN 日历判定已知限制：周一美股未开盘） |
| 增量 | 真实 provider 跑步骤 3（窗口 20260910/11/14） | bars=36（9 CN×3 + US 3×2 + KS11×3）、factors=27（仅 CN 9 码×3，非 CN 正确跳过） |
| 前端目检 | 大盘区块页面 | 待用户人工确认（API 与前端代码均已就绪） |

## Code Review

- **结论**：PASS（0 blocker / 0 major / 3 minor / 5 polish）；akshare 157 行高风险平移经 AST 结构 + 字节级 diff 双重核对确认干净
- **修复**：minor-1×2（非 CN 方法构帧段入 try，None 契约闭环）、minor-2（断点缺列探测加 `source='tushare'`，akshare 兜底行不再永久重拉）、polish-1~4（非 CN 日志文案分支、兜底身份判断、占位值等价注释、lambda→def）——修复后 118 passed 复验
- **遗留**：minor-3（AI 面 tushare `get_global_index` 海外指数仍为占位——默认主源下 kr_tech_analyst 拿不到 KOSPI，建议另立小任务）；polish-5（前端 UNAVAILABLE 渲染分支现为死分支，保留待 KOSDAQ 后续上线复用）

## 交付物

- 代码：`AI/dataflows/providers/cn/tushare.py`（GLOBAL_INDEX_CODE_MAP + `_get_global_index_df`）、`AI/dataflows/providers/cn/akshare.py`（`_get_non_cn_index_data_df` + kospi 分支改新浪 + **存量死代码块修复**）、`db/instrument/ingest/incremental.py`（13 目标/白名单守卫/因子跳过）、`db/instrument/ingest/backfill.py`、`backend/workers/market_ingest.py`（门控删除）、`frontend/src/modules/market/pages/MarketIndicesPanel.tsx`（4 项 AVAILABLE + KOSDAQ 移除）
- 测试：新 2 件（test_tushare_index_global.py / test_akshare_index_data_df.py）+ 改 5 件
- 数据：market.instrument +4 行、market.instrument_daily +26723 行；遗留 KOSPI/KOSDAQ 孤儿行删除
- 文档：本任务文件夹 + knowledge 同步（产品需求分析.md v1.5 / Q-01 修订 / API契约.md §8.1）
