# US/KR 指数上线 任务清单

> **状态**：`任务分解`（2026-09-14）
> **进度**：6/6 任务
> **下一步**：code review findings 修复后归档
> **关联方案**：[plan.md](plan.md)（同任务文件夹内方案正文）

---

## 任务总览

| 编号 | 任务 | 依赖 | 状态 |
|------|------|------|------|
| T1 | tushare 主源非 CN 分支 + 单测 | — | 已完成 |
| T2 | akshare 新浪兜底分支 + AI 面 kospi 修复 + 单测 | — | 已完成 |
| T3 | 采集链改造（INDEX_TARGETS/守卫/因子跳过）+ 测试同步 | T1、T2 | 已完成 |
| T4 | CLI --market 门控删除 + wrapper 测试 | — | 已完成 |
| T5 | 前端目录上线（AVAILABLE/KS11/KOSDAQ 移除）+ 测试 | — | 已完成 |
| T6 | 真实库回填 + 端到端验证（SQL/API/前端/增量） | T1–T5 | 已完成 |

## 任务

### T1 tushare 主源非 CN 分支

- **目标**：`TushareProvider.get_index_data_df` 对 `.INX/.DJI/.IXIC/KS11` 走 `api.index_global`，产出 10 列标准帧（对应方案 3.1）
- **涉及文件**：
  - 修改：`AI/dataflows/providers/cn/tushare.py`（GLOBAL_INDEX_CODE_MAP + 入口分支 + `_get_global_index_df`）
  - 新建：`tests/dataflows/providers/test_tushare_index_global.py`
- **依赖**：无
- **验收标准**：
  - [x] `pytest tests/dataflows/providers/test_tushare_index_global.py -q` 全过（映射分派 / 标准帧升序 / amount NaN / 日期归一 / 异常空 None）——10 passed
  - [x] `pytest tests/dataflows/providers/test_tushare_store_methods.py test_tushare_factor.py -q` 无回归——34 passed
- **状态**：`已完成`（2026-09-14）

### T2 akshare 新浪兜底分支

- **目标**：`AKShareProvider.get_index_data_df` 非 CN 兜底分支 + `_fetch_global_index` kospi 改新浪（对应方案 3.2）
- **涉及文件**：
  - 修改：`AI/dataflows/providers/cn/akshare.py`（入口分支 + `_get_non_cn_index_data_df` + kospi 分支 + docstring）
  - 新建：`tests/dataflows/test_akshare_index_data_df.py`
- **依赖**：无
- **验收标准**：
  - [ ] `pytest tests/dataflows/test_akshare_index_data_df.py -q` 全过（区间首行 pre_close = 区间外前一行 close 核心回归 / KS11 走 '首尔综合指数' / 异常空 None / CN 分支不变 / AI 面 kospi 走新浪）
  - [ ] `pytest tests/dataflows/test_akshare_board_daily_sections.py -q` 无回归
- **状态**：`待开始`

### T3 采集链改造

- **目标**：INDEX_TARGETS 13 个、白名单守卫、非 CN 跳过因子、兜底 try/except（对应方案 3.3）
- **涉及文件**：
  - 修改：`db/instrument/ingest/incremental.py`、`db/instrument/ingest/backfill.py`
  - 修改：`tests/db/instrument/test_index_ingestion.py`（守卫反转 + 因子计数）、`tests/db/instrument/test_incremental.py`（兜底异常隔离 / 非 CN 无因子请求）、`tests/db/instrument/test_backfill.py`（兜底 source 标注 / 非 CN 跳过因子）
- **依赖**：T1、T2
- **验收标准**：
  - [x] `pytest tests/db/instrument -q` 全过——100 passed（守卫反转 / 因子跳过 / 兜底异常隔离 / 兜底 source 标注用例全过）
  - [x] 冒烟：`_assert_index_targets_valid()` 对 13 目标不抛、塞 KOSDAQ 抛 ValueError
- **状态**：`已完成`（2026-09-14）

### T4 CLI 门控删除

- **目标**：`market_ingest.py` 删 `--market != "CN"` 拒绝块（对应方案 3.4）
- **涉及文件**：
  - 修改：`backend/workers/market_ingest.py`
  - 修改：`tests/db/instrument/test_market_ingest_wrapper.py`
- **依赖**：无
- **验收标准**：
  - [x] `pytest tests/db/instrument/test_market_ingest_wrapper.py -q` 全过（任意 --market 正常进增量模式）
- **状态**：`已完成`（2026-09-14）

### T5 前端目录上线

- **目标**：US 3 + KS11 转 AVAILABLE、KOSDAQ 条目删除（对应方案 3.5）
- **涉及文件**：
  - 修改：`frontend/src/modules/market/pages/MarketIndicesPanel.tsx`（含头部注释）
  - 修改：`frontend/src/modules/market/pages/MarketIndicesPanel.test.tsx`
- **依赖**：无
- **验收标准**：
  - [x] `cd frontend && pnpm vitest run src/modules/market/pages/MarketIndicesPanel.test.tsx` 全过——6 passed（11 名称 / 11 请求 / 0"暂不可用"卡片 / STALE 计数 11）
- **状态**：`已完成`（2026-09-14）

### T6 真实库回填 + 端到端验证

- **目标**：真实 PG 库回填 4 指数全历史并验证全链路（对应 plan.md 验证总表）
- **涉及文件**：无代码改动（可能修回填过程中暴露的问题）
- **依赖**：T1–T5
- **验收标准**：
  - [x] 定向回填（进程内 INDEX_TARGETS 缩为 4 码 + skip_concepts/skip_daily）执行成功——26723 行
  - [x] SQL 校验：4 码 6713/6714/6713/6583 行、min trade_date 2000-01；instrument.data_source 全 'tushare'；instrument_daily.source 全 'tushare'；pre_close NULL 0 行；KOSDAQ 0 行
  - [x] API：.INX/.DJI/.IXIC/KS11 全部 200 + bars + source=tushare；KS11 FRESH（到 2026-09-14）、US 3 码 STALE（CN 日历判定已知限制，周一美股未开盘）
  - [x] 增量：真实 provider 跑步骤 3 → bars=36（9 CN×3 + US 3×2 + KS11×3）、factors=27（仅 CN 9 码×3）
  - [ ] 前端大盘区块：US 组 3 卡片 + KS11 卡片显示 K 线，无"暂不可用"卡片（待用户人工检查）
- **状态**：`已完成`（2026-09-14，前端目检待用户确认）

---

## 拆分与维护规则

- 任务状态实时更新（`待开始`→`进行中`→`已完成`），完成即勾选验收项并同步总览表与 README.md 进度
- 实现完成后按 CLAUDE.md 启动 subagent code review，收尾时同步 knowledge/ 文档（产品需求分析.md §7.2 相关表述）
