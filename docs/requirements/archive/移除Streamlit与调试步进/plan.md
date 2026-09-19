# 移除 Streamlit 与调试步进模式方案

> **状态**：待确认（2026-09-16，评审 3 轮收敛：R3 PASS，全维度 ≥8）
> **评审轨迹**：R1 FAIL（2 major + 9 minor）→ R2 FAIL（2 minor 残留）→ R3 PASS（0 findings）。维度 2/6/10 的 R1 findings（M1/m2/m5/m8）已在 R2/R3 核验全部落地，收尾按锚定规则更新为 9 分；其余维度 1=10/3=9/4=9/5=9/7=9/8=8/9=10。
> **关联文档**：[README.md](README.md)、../事件研究审核界面平台集成方案.md、../调试步进模式方案.md

---

## 一、背景与动机

| 维度 | 现状 | 问题 | 目标 |
|------|------|------|------|
| Streamlit 依赖 | 全仓 3 处 `import streamlit`：`AI/eventStudy/review/review_app.py`（审核页）、`AI/logviewer/app.py`（日志查看器）、`tests/utils/test_logviewer_smoke.py`（AppTest）；`pyproject.toml:43` 依赖 `streamlit>=1.40.0` | 两个 Streamlit 界面均已/正在被平台 frontend 取代：审核页已由平台集成版替代（API契约.md 7.3），日志查看器的数据层已被平台执行日志功能复用（`backend/modules/analysis/application/execution_logs.py` 等 3 处 import `AI.logviewer.logs_reader`）；用户拍板**全删**（含调试步进，2026-09-16） | 仓库零 streamlit 代码与依赖；平台是唯一 UI 入口 |
| 审核页 | `review_app.py`（2026-09-16 已恢复 HEAD 原状）+ 引用：`tests/event_study/test_review_regressions.py:387-408`（import review_app 测纯函数）+ 注释随迁点（7 个文件 11 处，清单见 4.1.1：config.py:7 / industry_news.py:121 / review_service.py 4 处 / schemas:19 / review/\_\_init\_\_.py:1 / ai_prelabel.py 2 处 / ReviewTab.tsx:8） | review_app 是唯一 Streamlit 审核 UI，删除后其测试与注释残留会引入断裂 | review_app 及其全部引用清除；审核链路只余平台 + AI 侧 review_dao |
| 日志查看器 | `AI/logviewer/app.py`（UI）+ `AI/logviewer/logs_reader.py`（纯函数数据层，无 streamlit import——已被 backend 平台执行日志/图拓扑复用）+ `run.sh` 第 5 步启动段 | app.py 可删；**logs_reader.py 不可删**（平台依赖），但它带 2 个调试步进函数（find_checkpoint/find_active_checkpoint，`logs_reader.py:127-152`）依赖 step_gate | logs_reader 平移至 `AI/utils/logs_reader.py`（去掉 checkpoint 段），3 处 backend import 与测试随迁；app.py/`__init__.py`/smoke 测试/run.sh 段删除 |
| 调试步进模式 | `AI/utils/step_gate.py`（文件检查点机制）+ 三处接线：`AI/graph/trading_graph.py:45/:394-399（含 :398 日志行）/:536`、`AI/utils/dataprovider_log.py:59/138`、`AI/utils/llm_callbacks.py:54/223/292`；控制 UI 只有 Streamlit logviewer；`.env` 当前 `LIVEPROFIT_DEBUG_STEP=true` | 用户拍板全删。**删 UI 不删接线会让分析进程在检查点永久挂死**（checkpoint 等待人工继续，无 UI 即死锁）——接线必须一并拆除 | step_gate 模块、三处接线、default_config `debug_step` 键、test_step_gate 全部删除；分析进程无暂停能力 |

## 二、架构设计

删除为主 + 一处平移：

```
删除：review_app.py、AI/logviewer/{app.py,__init__.py}、AI/utils/step_gate.py、
      tests/utils/test_logviewer_smoke.py、tests/utils/test_step_gate.py、
      run.sh 日志查看器段、pyproject streamlit 依赖、
      CLAUDE.md 调试步进章节、memory/debug-step-mode.md
平移：AI/logviewer/logs_reader.py → AI/utils/logs_reader.py
      （删 find_checkpoint/find_active_checkpoint 与 CHECKPOINT_FILE import；
       消费方 backend 3 处 + tests/utils/test_logs_reader.py import 随迁）
接线拆除：trading_graph.py（import + enable/disable + _prepare_run/_propagate_inner 的
      debug_step 参数链）、dataprovider_log.py、llm_callbacks.py 的 checkpoint 调用
```

### 2.1 数据模型设计

无（纯代码/依赖删除，不涉及共享状态变更；debug_step 仅存在于内存 config dict）。

## 三、设计概览

### AI（AI/）

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| review_app.py【删除】 | Streamlit 审核页整体删除 | AI/eventStudy/review/review_app.py | `streamlit run` 审核页不再存在 |
| logs_reader.py【平移】 | 移至 AI/utils/，删 checkpoint 两函数 | AI/logviewer/logs_reader.py → AI/utils/logs_reader.py | 平台执行日志/图拓扑继续可用 |
| logviewer 包【删除】 | app.py + \_\_init\_\_.py | AI/logviewer/ | 日志查看器 UI 不再存在 |
| step_gate.py【删除】 | 检查点机制模块 | AI/utils/step_gate.py | 调试步进机制废弃 |
| trading_graph.py【修改】 | 删 step_gate import/接线/debug_step 参数链 | AI/graph/trading_graph.py | 分析进程不再暂停等待 |
| dataprovider_log.py【修改】 | 删 step_gate import 与 checkpoint 调用 | AI/utils/dataprovider_log.py | DP 日志无检查点 |
| llm_callbacks.py【修改】 | 删 step_gate import 与 2 处 checkpoint 调用 | AI/utils/llm_callbacks.py | LLM 调用无检查点 |
| default_config.py【修改】 | 删 debug_step 配置键 | AI/default_config.py | config 无死字段 |
| 注释随迁【修改】 | Streamlit/review_app 相关 docstring 改平台口径 | AI/eventStudy/review/\_\_init\_\_.py、ai_prelabel.py、collectors/config.py、integration/industry_news.py、sectorAgents/charts.py、utils/dataprovider_log.py | 注释不指向已删对象 |

### backend（backend/）

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| logs_reader import【修改】 | `AI.logviewer` → `AI.utils` | backend/modules/analysis/application/execution_logs.py、graph_topology.py、backend/api/routers/execution_logs.py | 平台执行日志功能不受影响 |
| 注释随迁【修改】 | review_service.py 4 处、schemas 1 处 Streamlit 表述 | backend/modules/event_study/application/review_service.py、backend/api/schemas/event_study_review.py | 注释不指向已删对象 |

### 依赖与脚本（根目录）

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| streamlit 依赖【删除】 | pyproject.toml 行 + uv.lock | pyproject.toml、uv.lock | 无 streamlit 依赖 |
| run.sh【修改】 | 删第 5 步日志查看器段 | run.sh | 启动脚本不再拉起查看器 |
| README【修改】 | 「FastAPI + Streamlit」→「FastAPI + React」 | README.md | 技术栈描述准确 |

### 文档与测试（docs/、tests/）

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| 调试步进文档【删除】 | CLAUDE.md 章节 + memory 文件 + index 行 | CLAUDE.md、docs/memory/best-practices/ai/debug-step-mode.md、docs/memory/index.md | 无失效机制文档 |
| 现状知识随迁【修改】 | 审核链路/渲染入口 Streamlit 表述 | docs/index.md:233、docs/knowledge/backend/API契约.md:590-592、docs/knowledge/ai/市场层.md:38、docs/knowledge/ai/板块层.md（4 处 logviewer 渲染表述） | 现状文档与实际一致 |
| 测试【删除/修改】 | 删 smoke/step_gate 测试与 review_app 用例；logs_reader 测试 import 随迁 | tests/utils/test_logviewer_smoke.py、tests/utils/test_step_gate.py、tests/event_study/test_review_regressions.py:387-408、tests/utils/test_logs_reader.py | 无指向已删对象的测试 |

## 四、详细设计

### 4.0 模块总览

| 维度 | 问题 | 方案概览 |
|------|------|---------|
| 审核页删除 | `tests/event_study/test_review_regressions.py:387-408` import review_app（删文件后测试必挂） | 删 review_app + 该用例（未识别引用拦截语义已由 backend `resolve_scope_fields` 行级校验测试覆盖）+ 注释随迁 7 个文件 11 处（清单见 4.1.1） |
| logs_reader 平移 | 平台 3 处 import `AI.logviewer.logs_reader`，删 logviewer 包会断平台执行日志 | 移至 `AI/utils/logs_reader.py`，删 checkpoint 两函数与 CHECKPOINT_FILE import；3 处 import + 测试随迁 |
| 调试步进接线拆除 | 三处接线（trading_graph/dataprovider_log/llm_callbacks）——不拆则 `LIVEPROFIT_DEBUG_STEP=true` 时分析进程在检查点死锁 | 删 step_gate.py + 全部接线 + default_config debug_step 键 + test_step_gate |
| 依赖移除 | `pyproject.toml:43` streamlit 依赖；uv 命令不可用（本机实测 NO UV） | pyproject 手删该行；uv.lock 注明需用户 `uv lock` 同步（无 uv 环境不代跑） |
| run.sh 与文档 | run.sh 第 5 步启动 logviewer；CLAUDE.md 调试步进章节；memory/debug-step-mode.md；README:307 | 删段/删章/删文件/改表；现状知识文档随迁 |
| 测试与验证 | 删 3 处测试、随迁 1 处 import | 全量回归（tests/event_study、tests/utils、tests/graph、backend 单测+契约、前端 vitest/typecheck） |

### 4.1 Streamlit 审核页删除

#### 4.1.1 模块设计

- 删除 `AI/eventStudy/review/review_app.py`（当前为 HEAD 原状，2026-09-16 已撤销本任务外的 UI 改动）；
- 删除 `tests/event_study/test_review_regressions.py:387-408`（含 M6 段注释）的 `test_review_app_row_target_refs_flags_unrecognized` 用例（该用例唯一目的 = 测试 review_app 的纯函数 `row_target_refs`；未识别引用拦截语义在平台链路由 `review_dao.resolve_scope_fields`（格式非法 → EventScopeValidationError）覆盖，实测已有：`tests/event_study/test_event_routing.py:168`（parametrize 含 `["XYZ"]` → 格式非法）+ `:290`（ValueError）+ backend `test_review_service.py:408` 前置校验用例——**无需补测试**，评审 R1 已核实）；
- 注释随迁（不改行为，只改指向已删对象的文字）：
  - `AI/eventStudy/review/__init__.py:1`「人工审核：DAO + Streamlit 界面」→「人工审核：DAO（审核 UI 已平台化）」
  - `AI/eventStudy/review/ai_prelabel.py:14/252`「Streamlit 分片循环」→「调用方分片循环」
  - `AI/eventStudy/collectors/config.py:7`「daily_job / api / review_app」→「daily_job / api」
  - `AI/eventStudy/integration/industry_news.py:121`「(沿用 review_app 的既有口径)」→「(沿用审核界面既有口径)」
  - `backend/modules/event_study/application/review_service.py`：模块 docstring「行为对齐 Streamlit 审核界面（review_app.py）」→「行为对齐原审核界面语义」；「修复 Streamlit 版事务毒化隐患」→「修复原审核界面事务毒化隐患」；`:264`「Streamlit 先例」→「原审核界面先例」；`:280`「与 Streamlit『计算失败仅 warning、daily_job 兜底』行为一致」→「与原审核界面『计算失败仅 warning、daily_job 兜底』行为一致」
  - `backend/api/schemas/event_study_review.py:19`「Streamlit Selectbox 选项是 UI 层约束」→「前端下拉选项是 UI 层约束」
  - `frontend/src/modules/event-study/pages/review/ReviewTab.tsx:8`「行为对齐 Streamlit review_app 两个 Tab」→「行为对齐原审核界面两个 Tab」（评审 m1：前端注释同样在「仓库零 streamlit」目标内）

#### 4.1.2 三方依赖能力评估

不适用（纯删除）。

#### 4.1.3 风险与验证方式

- 风险：删除用例后「未识别引用拦截」只剩 backend 覆盖——评审 R1 已核实覆盖存在（test_event_routing.py:168/:290 + test_review_service.py:408），无需补测试；
- 验证：`pytest tests/event_study/ backend/tests/unit/event_study/ -q` 全绿。

#### 4.1.4 文件变更清单

- **删除文件**：`AI/eventStudy/review/review_app.py`
- **修改文件**：上文注释随迁 7 个文件（含 `frontend/src/modules/event-study/pages/review/ReviewTab.tsx`）+ `tests/event_study/test_review_regressions.py`

### 4.2 logs_reader 平移（平台执行日志数据层保留）

#### 4.2.1 模块设计

- `AI/logviewer/logs_reader.py` → `AI/utils/logs_reader.py`：
  - 删除 `:127-152` 调试步进段（`from AI.utils.step_gate import CHECKPOINT_FILE`、`find_checkpoint`、`find_active_checkpoint`——仅 logviewer UI 与调试步进使用）；
  - 模块 docstring「供 app.py 与测试共用」→「供平台执行日志/图拓扑与测试共用」；`logs_root()`（`:29-34`）的 `parents[2]` 相对路径随文件移动 +1 层（`AI/utils/logs_reader.py` 到仓库根仍是 parents[2]？实测核对：`AI/utils/logs_reader.py` → parents[0]=utils、parents[1]=AI、parents[2]=仓库根——**不变**，无需改）；
- import 随迁（3 处 import + 1 处 docstring，评审 m3）：`backend/modules/analysis/application/execution_logs.py:22`（import）、同文件 `:3` 模块 docstring「复用 AI.logviewer.logs_reader 的目录枚举」→「复用 AI.utils.logs_reader 的目录枚举」、`backend/modules/analysis/application/graph_topology.py:20`、`backend/api/routers/execution_logs.py:105`（延迟导入 `from AI.logviewer import logs_reader` → `from AI.utils import logs_reader`）；
- 测试随迁（评审 m4，确定项）：`tests/utils/test_logs_reader.py:10` import 改 `from AI.utils import logs_reader`；`:2` 模块 docstring「AI/logviewer/logs_reader.py 纯函数测试」→「AI/utils/logs_reader.py 纯函数测试」；**删除 `:201-231` 调试步进检查点段**（`test_find_checkpoint`、`test_find_active_checkpoint_waiting_first` 两个用例——函数已随平移删除）。

#### 4.2.2 三方依赖能力评估

logs_reader 无 streamlit import（已核实 `import` 段仅 json/os/re/pathlib/typing）——平移零三方风险。

#### 4.2.3 风险与验证方式

- 风险：backend 执行日志/图拓扑是平台核心功能，import 漏改一处即 500——grep `AI.logviewer` 全仓归零验证；
- 验证：`pytest tests/utils/test_logs_reader.py backend/tests/unit/ -q` + 手工调 `/api/v1/execution-logs` 相关端点冒烟。

#### 4.2.4 文件变更清单

- **新建文件**：`AI/utils/logs_reader.py`
- **删除文件**：`AI/logviewer/logs_reader.py`（logviewer 包随 4.3 删除）
- **修改文件**：backend 3 处 import + `execution_logs.py:3` docstring、`tests/utils/test_logs_reader.py`（import + docstring + 删 :201-231）

### 4.3 日志查看器 UI 删除

#### 4.3.1 模块设计

- 删除 `AI/logviewer/app.py`、`AI/logviewer/__init__.py`（logs_reader 平移后 logviewer 目录归零删除）；
- 删除 `tests/utils/test_logviewer_smoke.py`（`streamlit.testing.v1.AppTest`，UI 冒烟）；
- `run.sh` 删除第 5 步「启动日志查看器」整段（**评审 m5 精确范围：`:131-154`**，含 `:149-154` 的 `powershell Start-Process` 打开浏览器块；VIEWER_PORT/VIEWER_URL/viewer.log 已核实仅在该段出现无外部引用）；删后 `:156`「# 6. 运行」编号改「# 5. 运行」；`:359` 启动提示「任务内核明细：logs/{ts}/（Streamlit 查看器）」→「（平台执行日志页）」（评审 m2 残留）；
- 注释随迁：`AI/sectorAgents/charts.py:4-5`「日志目录 … 由 logviewer …」→ 去重复后表述「产物落 logs/{ts}/reports/charts/」（评审 polish ③）；`AI/utils/dataprovider_log.py:19`「由 AI/logviewer 兼容展示」→「供平台执行日志兼容展示（parse_legacy）」。

#### 4.3.2 三方依赖能力评估

不适用（纯删除）。

#### 4.3.3 风险与验证方式

- 风险：热力图 HTML（`logs/{ts}/reports/charts/sector_daily_heatmaps.html`）仍由 charts.py 生成，但查看入口随 logviewer 移除——平台是否补渲染属后续需求，本任务只删不补（用户拍板「全删」）；
- 验证：`grep -rln "logviewer"`（代码层）归零；`bash -n run.sh` 语法检查。

#### 4.3.4 文件变更清单

- **删除文件**：`AI/logviewer/app.py`、`AI/logviewer/__init__.py`（目录归零）、`tests/utils/test_logviewer_smoke.py`
- **修改文件**：`run.sh`、`AI/sectorAgents/charts.py`、`AI/utils/dataprovider_log.py`

### 4.4 调试步进机制拆除

#### 4.4.1 模块设计

- 删除 `AI/utils/step_gate.py`、`tests/utils/test_step_gate.py`；
- `AI/graph/trading_graph.py`：删 `:45` import、`:394-399` 调试步进块（**评审 polish ⑤：含 `:398` 的 `trace_step("调试步进模式已启用"…)` 整块**，非仅 :397/:399）、`:536` disable；`_prepare_run`（`:353-416`）返回元组中的 `debug_step` 与 `_propagate_inner`（`:418+`）签名/调用点沿线清理（评审 R1 已核实：`_propagate_inner` 体内全程仅 1 个 return（:542）、无 except/raise、`debug_step` 仅 :535 一处消费——删参无失败路径个数问题）；
- `AI/utils/dataprovider_log.py`：删 `:59` import、`:138` checkpoint 调用（及其包裹的上下文）、`:30-31` 调试步进 docstring 段（评审 m6）；删 checkpoint 块后 `written = _finalize_call(...)` 与 `rel` 成无用赋值，顺手改为直接调用（评审 polish ④）；
- `AI/utils/llm_callbacks.py`：删 `:54` import、`:223/:292` 两处 checkpoint 调用（及其包裹的上下文）、`:40-42` 调试步进 docstring 段（评审 m6）；
- `AI/default_config.py`：删 `:44` `debug_step` 键 + `:43` 注释行（消费方归零后为死配置，评审 polish ①）；
- **测试随迁（评审 M1/M2 漏枚举的两个真实消费方）**：`tests/utils/test_tushare_call_log.py:234-236` 删除（`from AI.utils import step_gate` + `assert not (... / step_gate.CHECKPOINT_FILE).exists()` 三行——删 step_gate 后 ImportError 必挂）；`tests/graph/test_rerun_entry.py:129-133` 的 `mock.patch.object(graph, "_propagate_inner", side_effect=lambda state, log_dir, debug, ctx, cb: ...)` 位置耦合 `debug_step` 形参 → lambda 改 4 参 `lambda state, log_dir, ctx, cb:`（全仓仅此一处测试桩耦合，已 grep 确认）；
- `.env` 不代改（用户本地配置）：`LIVEPROFIT_DEBUG_STEP=true` 行留待用户自行删除；config 键删除后该 env 无任何消费方，不产生行为影响。

**实施注意（评审 polish ⑥⑦）**：① 所有被删模块对应的 `__pycache__` 一并清理（实测另有 4 处：`AI/utils/__pycache__/step_gate.*.pyc`、`AI/eventStudy/review/__pycache__/review_app.*.pyc`、`tests/utils/__pycache__/test_step_gate.*.pyc`、`tests/utils/__pycache__/test_logviewer_smoke.*.pyc`，残留会让 grep 出现 Binary file 命中、归零不干净；验证命令也可加 `--include='*.py'` 兜底）；② 本任务涉及的 `backend/modules/event_study/application/review_service.py`、`backend/api/schemas/event_study_review.py`、`AI/eventStudy/review/ai_prelabel.py` 均为工作区已改（M）状态（含用户并发改动），改注释一律按路径精确编辑（Edit 小块替换），不得整文件覆盖。

#### 4.4.2 三方依赖能力评估

不适用（纯删除）。

#### 4.4.3 风险与验证方式

- 风险：checkpoint 调用常嵌在异常保护/生成器流程中（dataprovider_log/llm_callbacks 是日志写入主链路），删除时只拆 step_gate 调用、不误伤相邻日志逻辑——实现后全量跑 tests/utils（含日志写入相关测试）；
- 验证：`grep -rn "step_gate\|debug_step" AI/ backend/ tests/`（排除已删文件）归零；`pytest tests/graph tests/utils -q` 全绿。

#### 4.4.4 文件变更清单

- **删除文件**：`AI/utils/step_gate.py`、`tests/utils/test_step_gate.py`
- **修改文件**：`AI/graph/trading_graph.py`、`AI/utils/dataprovider_log.py`、`AI/utils/llm_callbacks.py`、`AI/default_config.py`、`tests/utils/test_tushare_call_log.py`（删 :234-236）、`tests/graph/test_rerun_entry.py`（lambda 改 4 参）

### 4.5 streamlit 依赖移除

#### 4.5.1 模块设计

- `pyproject.toml:43` 删 `"streamlit>=1.40.0",` 行；`:40` 附近依赖组注释「REST API + 审核界面」实现时复核口径（评审 polish ②）；
- `uv.lock`：本机无 uv CLI（实测 `NO UV`）——不手工编辑锁文件（格式易错），任务注明「用户侧执行 `uv lock` 同步（或 pip 环境自行更新）」；**后果边界（评审 m7）**：锁文件同步前 `uv sync`/`uv run` 路径仍会安装 streamlit（uv.lock:4867/4897/4899/5954/5977 残留 `streamlit 1.61.1` 与 `>=1.40.0` specifier）；`pip install -e .` 路径（run.sh 实际使用）在 pyproject 删除后即闭环；
- `.venv` 中的 streamlit 包不卸载（环境属用户本地，pyproject 是唯一事实源；后续 `pip install -e .` 自然不带 streamlit）。`liveprofit.egg-info/requires.txt` 是生成物，随用户下次安装刷新，本任务不代改。

#### 4.5.2 三方依赖能力评估

pandas 不在删除范围（review_app 之外的 AI 链路仍重度使用）；plotly 不在删除范围（热力图产物仍生成）。

#### 4.5.3 风险与验证方式

- 验证：`grep -rn "streamlit" pyproject.toml` 归零；全仓 `import streamlit` 归零（4.1/4.3 删除后）。

#### 4.5.4 文件变更清单

- **修改文件**：`pyproject.toml`
- **生成物说明**：`uv.lock`、`liveprofit.egg-info/requires.txt` 由用户侧工具同步（本机无 uv）

### 4.6 文档随迁

#### 4.6.1 模块设计

- **删除**：CLAUDE.md「调试步进模式（Debug Step Mode）」章节（含 `streamlit run AI/logviewer/app.py` 描述）；`docs/memory/best-practices/ai/debug-step-mode.md`；`docs/memory/index.md` 对应行；
- **现状知识随迁**：
  - `README.md:307`「\| API/Web UI \| FastAPI + Streamlit \|」→「\| API/Web UI \| FastAPI + React \|」（React 前端为实际 UI）
  - `docs/index.md:233`「人工审核：Streamlit 审核界面（…）」→「人工审核：平台审核界面（…）」（其余描述保留）
  - `docs/knowledge/backend/API契约.md:590-592`「平台集成版替代 Streamlit review_app」→「审核 API」；「行为对齐原 Streamlit 审核界面；」→「审核流复用 AI 侧 review_dao；」（行为对齐的锚点已不存在，改为事实表述）
  - `docs/knowledge/ai/市场层.md:38`「两条审核链路：Streamlit + 平台」→「平台审核链路」
  - `docs/knowledge/ai/板块层.md` 4 处 logviewer 渲染表述（评审 m9 按行替换，避免同句重复路径）：`:15/:78/:98` 句内已含 `logs/{ts}/reports/charts/sector_daily_heatmaps.html`，将「logviewer 报告 tab 渲染」改尾注「（原 logviewer 渲染入口已移除，2026-09-16）」；`:31` 树状图「（logviewer 渲染）」→「（HTML 产物）」
- **保留不动**：docs/requirements/archive/** 全部历史方案（含 Streamlit 描述，历史记录不回溯）；docs/memory/pitfalls/workspace/评审循环踩坑.md（历史踩坑故事）。

#### 4.6.2 三方依赖能力评估

不适用。

#### 4.6.3 风险与验证方式

- 验证：现状文档类（CLAUDE.md/README/docs/index.md/docs/knowledge/**）grep streamlit 仅剩「已移除/历史」语境或归零；**frontend/src 一并纳入 grep 口径**（评审 m1：前端注释同属「仓库零 streamlit」目标）；archive/** 与 memory/pitfalls 不查。

#### 4.6.4 文件变更清单

- **删除文件**：`docs/memory/best-practices/ai/debug-step-mode.md`
- **修改文件**：`CLAUDE.md`、`README.md`、`docs/index.md`、`docs/knowledge/backend/API契约.md`、`docs/knowledge/ai/市场层.md`、`docs/knowledge/ai/板块层.md`、`docs/memory/index.md`

### 4.7 测试

#### 4.7.1 模块设计

| 文件 | 改动 |
|------|------|
| `tests/utils/test_logviewer_smoke.py` | 删除（AppTest UI 冒烟） |
| `tests/utils/test_step_gate.py` | 删除（模块已删） |
| `tests/utils/test_logs_reader.py` | import 随迁 `from AI.utils import logs_reader`；`:2` docstring 随迁；删 `:201-231` 调试步进用例段（评审 m4 确定项） |
| `tests/utils/test_tushare_call_log.py` | 删 `:234-236`（step_gate import + CHECKPOINT_FILE 断言，评审 M1） |
| `tests/graph/test_rerun_entry.py` | `:129-133` 的 `_propagate_inner` mock lambda 改 4 参 `lambda state, log_dir, ctx, cb:`（评审 M2） |
| `tests/event_study/test_review_regressions.py` | 删 `test_review_app_row_target_refs_flags_unrecognized`（`:387-408` 含 M6 段注释；未识别引用拦截覆盖已核实存在，无需补测试） |

验证命令（python 在仓库根、pnpm 在 frontend/）：
- `.venv/Scripts/python.exe -m pytest tests/event_study tests/utils tests/graph -q`（先 grep 确认无 real_llm 用例）
- `.venv/Scripts/python.exe -m pytest backend/tests/unit backend/tests/contract -q`
- `pnpm exec vitest run` + `pnpm run typecheck`（前端不涉及本任务改动，回归确认；typecheck 若报 analysis 模块错误属用户并发改动，与本任务无关）

#### 4.7.2 三方依赖能力评估

不适用。

#### 4.7.3 风险与验证方式

见各模块。

#### 4.7.4 文件变更清单

见各模块（4.1.4 / 4.4.4 测试文件）。

## 五、已确认决策 / 待确认问题

已确认决策（2026-09-16 用户拍板）：

1. **全删（含调试步进）**：删除审核页、日志查看器 UI、step_gate 机制与接线、streamlit 依赖、调试步进文档。用户已知悉：.env 的 `LIVEPROFIT_DEBUG_STEP=true` 需自行删除（本任务不代改 .env）；删除后分析进程不再有任何检查点暂停能力。
2. **logs_reader 平移保留**：`AI/logviewer/logs_reader.py` 是平台执行日志/图拓扑的数据层（backend 3 处 import），不可随 logviewer 删除——平移至 `AI/utils/logs_reader.py` 并去掉调试步进函数。此条为方案推导的必要处理，非用户可选决策。
3. **uv.lock 不同步**：本机无 uv CLI，锁文件由用户侧 `uv lock` 同步；`.venv` 环境包与 egg-info 生成物不代改。后果边界（评审 m7）：锁文件同步前 `uv sync`/`uv run` 路径仍会安装 streamlit（uv.lock 残留 specifier）；`pip install -e .` 路径（run.sh 实际使用）在 pyproject 删除后即闭环。
