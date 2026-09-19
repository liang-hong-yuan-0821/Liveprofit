# 移除 Streamlit 与调试步进模式 任务清单

> **状态**：`实现完成`（2026-09-19）
> **进度**：7/7 任务
> **下一步**：启动 subagent Code Review（按 CLAUDE.md Code Review 规则）
> **关联方案**：[plan.md](plan.md)（R3 PASS 已确认，2026-09-19 用户指示生成任务清单）

---

## 任务总览

| 编号 | 任务 | 依赖 | 状态 |
|------|------|------|------|
| T1 | 审核页删除与注释随迁（方案 4.1） | — | 已完成 |
| T2 | logs_reader 平移（方案 4.2） | — | 已完成 |
| T3 | 调试步进机制拆除（方案 4.4） | T2 | 已完成 |
| T4 | 日志查看器 UI 删除（方案 4.3） | T2、T3 | 已完成 |
| T5 | streamlit 依赖移除（方案 4.5） | T1、T4 | 已完成 |
| T6 | 文档随迁（方案 4.6） | — | 已完成 |
| T7 | 全量回归与收尾（方案 4.7） | T1–T6 | 已完成 |

> T3/T4 相对方案章节顺序互换：删 step_gate.py 前必须先把 logs_reader 的 CHECKPOINT_FILE import 随平移删掉（T2 先行是 T3/T4 硬前提），T4 收尾 logviewer 目录归零。

**实施注意（全任务适用）**：

- 测试一律 `.venv/Scripts/python.exe -m pytest`（pytest 命令示例见方案 4.7，本清单只写相对范围）
- git 暂存按路径显式 add，**禁止 `git add -A`**（用户并发编辑中）
- 工作区已改（M）文件（`backend/modules/event_study/application/review_service.py`、`backend/api/schemas/event_study_review.py`、`AI/eventStudy/review/ai_prelabel.py` 等）按路径精确编辑，不得整文件覆盖（方案 4.4.1 实施注意②）

## 任务

### T1 审核页删除与注释随迁（方案 4.1）

- **目标**：删除 Streamlit 审核页 review_app.py 及其唯一引用用例；7 个文件 11 处注释改平台口径（只改文字、不改行为）
- **涉及文件**：
  - 删除：`AI/eventStudy/review/review_app.py`
  - 修改：`tests/event_study/test_review_regressions.py`（删 :387-408 `test_review_app_row_target_refs_flags_unrecognized` 用例含 M6 段注释——未识别引用拦截语义已由 backend `resolve_scope_fields` 校验测试覆盖：test_event_routing.py:168/:290 + test_review_service.py:408，无需补测试）
  - 修改（注释随迁，逐处精确替换）：
    - `AI/eventStudy/review/__init__.py:1`「人工审核：DAO + Streamlit 界面」→「人工审核：DAO（审核 UI 已平台化）」
    - `AI/eventStudy/review/ai_prelabel.py:14/252`「Streamlit 分片循环」→「调用方分片循环」
    - `AI/eventStudy/collectors/config.py:7`「daily_job / api / review_app」→「daily_job / api」
    - `AI/eventStudy/integration/industry_news.py:121`「(沿用 review_app 的既有口径)」→「(沿用审核界面既有口径)」
    - `backend/modules/event_study/application/review_service.py` 4 处：docstring「行为对齐 Streamlit 审核界面（review_app.py）」→「行为对齐原审核界面语义」；「修复 Streamlit 版事务毒化隐患」→「修复原审核界面事务毒化隐患」；:264「Streamlit 先例」→「原审核界面先例」；:280「与 Streamlit『计算失败仅 warning、daily_job 兜底』行为一致」→「与原审核界面『计算失败仅 warning、daily_job 兜底』行为一致」
    - `backend/api/schemas/event_study_review.py:19`「Streamlit Selectbox 选项是 UI 层约束」→「前端下拉选项是 UI 层约束」
    - `frontend/src/modules/event-study/pages/review/ReviewTab.tsx:8`「行为对齐 Streamlit review_app 两个 Tab」→「行为对齐原审核界面两个 Tab」
- **依赖**：无（与 T2 可并行）
- **验收标准**（全部勾选才算完成）：
  - [x] `grep -rn "review_app" AI/ backend/ tests/ frontend/src/`（排除 __pycache__）归零
  - [x] `.venv/Scripts/python.exe -m pytest tests/event_study backend/tests/unit/event_study -q` 全绿
  - [x] 清理 `AI/eventStudy/review/__pycache__/review_app.*.pyc`（残留会让不加 include 的 grep 出现 Binary file 命中）
- **状态**：`已完成`（2026-09-19）

### T2 logs_reader 平移（方案 4.2）

- **目标**：logs_reader 平移至 `AI/utils/` 并去掉调试步进段；backend 3 处 import + 测试随迁（平台执行日志/图拓扑数据层保留）
- **涉及文件**：
  - 新建：`AI/utils/logs_reader.py`（删 :127-152 调试步进段：`from AI.utils.step_gate import CHECKPOINT_FILE`、`find_checkpoint`、`find_active_checkpoint`；docstring「供 app.py 与测试共用」→「供平台执行日志/图拓扑与测试共用」；`logs_root()` 的 `parents[2]` 随文件移动路径不变，无需改）
  - 删除：`AI/logviewer/logs_reader.py`
  - 修改：`backend/modules/analysis/application/execution_logs.py`（:22 import + :3 docstring「复用 AI.logviewer.logs_reader 的目录枚举」→「复用 AI.utils.logs_reader 的目录枚举」）、`backend/modules/analysis/application/graph_topology.py:20`、`backend/api/routers/execution_logs.py:105`（延迟导入 → `from AI.utils import logs_reader`）
  - 修改：`tests/utils/test_logs_reader.py`（:10 import 改 `from AI.utils import logs_reader`；:2 docstring「AI/logviewer/logs_reader.py」→「AI/utils/logs_reader.py」；删 :201-231 `test_find_checkpoint` / `test_find_active_checkpoint_waiting_first` 两用例——函数已随平移删除）
- **依赖**：无（与 T1 可并行）
- **验收标准**（全部勾选才算完成）：
  - [x] `grep -rn "AI.logviewer" AI/ backend/ tests/ --include="*.py"` 剩余命中全在 T4 范围内（app.py:17/:26 将删、dataprovider_log.py:19 注释 T4 改），backend/tests 零命中
  - [x] `.venv/Scripts/python.exe -m pytest tests/utils/test_logs_reader.py backend/tests/unit -q` 通过（backend/tests/unit 除 2 个预存失败：test_task_service ← 用户并发改动 execution_control.py，与本任务文件零交集，见 issues.md）
  - [x] 冒烟：backend 3 模块 import 成功 + 真实 logs/ 目录读取正常（1 run/3 nodes/4 dp calls）+ 契约测试 `test_execution_logs_api.py`/`test_graph_topology_api.py` 15 passed（覆盖 /api/v1/execution-logs 端点链路）
- **状态**：`已完成`（2026-09-19）

### T3 调试步进机制拆除（方案 4.4）

- **目标**：删除 step_gate 模块、三处接线与 debug_step 参数链、死配置键；两个真实测试消费方随迁；分析进程不再有检查点暂停能力
- **涉及文件**：
  - 删除：`AI/utils/step_gate.py`、`tests/utils/test_step_gate.py`
  - 修改：`AI/graph/trading_graph.py`（删 :45 import、:394-399 调试步进块含 :398 `trace_step("调试步进模式已启用"…)` 整块、:536 disable；`_prepare_run` 返回元组去 debug_step + `_propagate_inner` 签名/调用点沿线清理——体内仅 1 个 return、无 except/raise，删参无失败路径个数问题）
  - 修改：`AI/utils/dataprovider_log.py`（删 :59 import、:138 checkpoint 调用及其包裹上下文、:30-31 调试步进 docstring 段；删块后 `written = _finalize_call(...)` 与 `rel` 无用赋值改直接调用）
  - 修改：`AI/utils/llm_callbacks.py`（删 :54 import、:223/:292 两处 checkpoint 调用及其包裹上下文、:40-42 调试步进 docstring 段）
  - 修改：`AI/default_config.py`（删 :44 `debug_step` 键 + :43 注释行）
  - 修改：`tests/utils/test_tushare_call_log.py`（删 :234-236：step_gate import + CHECKPOINT_FILE 断言三行——删 step_gate 后 ImportError 必挂）
  - 修改：`tests/graph/test_rerun_entry.py`（:129-133 `_propagate_inner` mock lambda 改 4 参 `lambda state, log_dir, ctx, cb:`——全仓唯一位置耦合测试桩）
  - 不代改：`.env` 的 `LIVEPROFIT_DEBUG_STEP=true` 留待用户自行删除（config 键删除后无消费方，无行为影响）
- **依赖**：T2（logs_reader 的 CHECKPOINT_FILE import 必须随平移先行删除，否则删 step_gate.py 后 import 断裂、backend 执行日志 500）
- **验收标准**（全部勾选才算完成）：
  - [x] `grep -rn "step_gate\|debug_step" AI/ backend/ tests/ --include="*.py"` 归零（当时仅剩 app.py:15 文档提及，已随 T4 删除）
  - [x] 清理 `AI/utils/__pycache__/step_gate.*.pyc`、`tests/utils/__pycache__/test_step_gate.*.pyc`
  - [x] `.venv/Scripts/python.exe -m pytest tests/graph tests/utils -q` 全绿（123 passed，含 test_rerun_entry 4 参 lambda、test_tushare_call_log）
  - [x] 人工检查：`_prepare_run` 3 元组返回与 `_propagate_inner` 4 参签名、propagate/rerun_from_node 两调用点参数一致；checkpoint 块删除后 `_finalize_call` 直接调用、相邻日志逻辑未动
- **状态**：`已完成`（2026-09-19）

### T4 日志查看器 UI 删除（方案 4.3）

- **目标**：logviewer 包归零删除、UI 冒烟测试删除、run.sh 启动段拆除、2 处注释随迁
- **涉及文件**：
  - 删除：`AI/logviewer/app.py`、`AI/logviewer/__init__.py`（logs_reader 平移后目录归零）、`tests/utils/test_logviewer_smoke.py`（AppTest UI 冒烟）
  - 修改：`run.sh`（删 :131-154 第 5 步「启动日志查看器」整段，含 :149-154 `powershell Start-Process` 打开浏览器块；删后「# 6. 运行」改「# 5. 运行」；:359 启动提示「（Streamlit 查看器）」→「（平台执行日志页）」）
  - 修改：`AI/sectorAgents/charts.py:4-5`「日志目录 … 由 logviewer …」去重复后表述「产物落 logs/{ts}/reports/charts/」
  - 修改：`AI/utils/dataprovider_log.py:19`「由 AI/logviewer 兼容展示」→「供平台执行日志兼容展示（parse_legacy）」
- **依赖**：T2（logs_reader 已平移，logviewer 目录才能归零）、T3（dataprovider_log.py 与 T3 同文件，串行避免并发编辑冲突）
- **验收标准**（全部勾选才算完成）：
  - [x] `grep -rn "logviewer" AI/ backend/ tests/ run.sh`（排除 __pycache__）归零；`AI/logviewer/` 目录不存在（含其 __pycache__）
  - [x] `bash -n run.sh` 语法通过；步骤编号 0→1→2→3→4→5 连续、run.sh 全文 grep streamlit 零命中
  - [x] 清理 `tests/utils/__pycache__/test_logviewer_smoke.*.pyc`
- **状态**：`已完成`（2026-09-19）

### T5 streamlit 依赖移除（方案 4.5）

- **目标**：pyproject.toml 删除 streamlit 依赖，全仓零 streamlit import
- **涉及文件**：
  - 修改：`pyproject.toml`（删 :43 `"streamlit>=1.40.0",`；:40 附近依赖组注释「REST API + 审核界面」实现时复核口径）
  - 不代改（用户侧）：`uv.lock`（本机无 uv CLI，由用户执行 `uv lock` 同步；同步前 uv sync/uv run 路径仍会安装 streamlit，`pip install -e .` 路径即刻闭环）、`.venv` 已装包、`liveprofit.egg-info/requires.txt`（生成物，随用户下次安装刷新）
- **依赖**：T1、T4（全仓 `import streamlit` 归零需 review_app.py 与 app.py 已删）
- **验收标准**（全部勾选才算完成）：
  - [x] `grep -rn "streamlit" pyproject.toml` 归零
  - [x] `grep -rn "import streamlit" AI/ backend/ tests/ --include="*.py"` 归零
- **状态**：`已完成`（2026-09-19）

### T6 文档随迁（方案 4.6）

- **目标**：删调试步进文档三处（CLAUDE.md 章节 + memory 文件 + index 行），现状知识 6 文件随迁；archive/** 与历史 pitfalls 不回溯
- **涉及文件**：
  - 删除：`docs/memory/best-practices/ai/debug-step-mode.md`
  - 修改：`CLAUDE.md`（删「调试步进模式（Debug Step Mode）」章节，含 `streamlit run AI/logviewer/app.py` 描述）、`docs/memory/index.md`（删 debug-step-mode 行）、`README.md:307`「FastAPI + Streamlit」→「FastAPI + React」、`docs/index.md:233`「Streamlit 审核界面」→「平台审核界面」（其余描述保留）、`docs/knowledge/backend/API契约.md:590-592`（「平台集成版替代 Streamlit review_app」→「审核 API」；「行为对齐原 Streamlit 审核界面；」→「审核流复用 AI 侧 review_dao；」）、`docs/knowledge/ai/市场层.md:38`「两条审核链路：Streamlit + 平台」→「平台审核链路」、`docs/knowledge/ai/板块层.md` 4 处（:15/:78/:98 句内已含产物路径，「logviewer 报告 tab 渲染」改尾注「（原 logviewer 渲染入口已移除，2026-09-16）」；:31 树状图「（logviewer 渲染）」→「（HTML 产物）」）
  - 不动：`docs/requirements/archive/**` 全部历史方案、`docs/memory/pitfalls/workspace/评审循环踩坑.md`
- **依赖**：无（可与代码任务并行）
- **验收标准**（全部勾选才算完成）：
  - [x] `grep -rn "streamlit\|Streamlit" CLAUDE.md README.md docs/index.md docs/knowledge/ frontend/src/` 归零（archive/** 与 pitfalls 不查；ReviewTab.tsx:8 已由 T1 处理）
  - [x] `grep -rn "logviewer" docs/index.md docs/knowledge/` 仅剩板块层 3 处指定尾注（:15/:78/:98）
  - [x] `grep -rn "调试步进" CLAUDE.md docs/memory/index.md` 归零
- **状态**：`已完成`（2026-09-19）

### T7 全量回归与收尾（方案 4.7）

- **目标**：AI/backend/前端全量回归 + 三项归零终验 + pycache 残留清理终验
- **涉及文件**：无预期代码改动（回归发现的问题修复后回对应任务块补记）
- **依赖**：T1–T6
- **验收标准**（全部勾选才算完成）：
  - [x] 先 `grep -rl "real_llm\|real_toolkit" tests/` 确认排除范围（fixture 仅在 tests/agents/** 与 conftest，回归目录不含），再 `.venv/Scripts/python.exe -m pytest tests/event_study tests/utils tests/graph -q` 全绿（321 passed）
  - [x] `.venv/Scripts/python.exe -m pytest backend/tests/unit backend/tests/contract -q` 通过（358 passed + 2 个预存失败 test_task_service ← 用户并发改动 execution_control.py，见 issues.md）
  - [x] frontend 目录 `pnpm exec vitest run` 全绿（336 passed，44 files）+ `pnpm run typecheck` 通过
  - [x] 终验 grep 三连（排除 __pycache__）均归零：`import streamlit`；`step_gate\|debug_step`；`logviewer`（代码层 AI/ backend/ tests/ run.sh）
  - [x] 所有被删模块 pyc 残留清理完毕（review_app / step_gate / test_step_gate / test_logviewer_smoke / logviewer 目录 find 零命中）
  - [x] README.md 状态块更新、log.md 时间线完整（Code Review 与归档按 CLAUDE.md 工作流程在任务清单完成后另行执行）
- **状态**：`已完成`（2026-09-19）

---

## 拆分与维护规则

- **拆分粒度**：每个任务 = 一个可独立验收的实现单元（新建一个模块 / 改造一个文件 / 写一组单测）；按依赖排序，无依赖任务可并行。通常 3–10 个任务，超出说明拆分过细，可合并
- **验收标准必须可执行**：优先单测命令与可运行检查；人工检查需写明看什么、期望看到什么。禁止"完成 XX 功能"式模糊表述
- **状态取值**：`待开始` → `进行中` → `已完成`；被阻塞时标 `阻塞：<原因>`，解除后恢复流转
- **生命周期**：任务清单随任务文件夹保留——方案实现完成并归档（移入 `docs/requirements/archive/<任务名>/`）时，本文件随文件夹一并归档（实施记录价值保留，不再删除）
- **无需额外评审**：任务清单直接由已评审通过的方案拆出，只做拆解、不复述设计
