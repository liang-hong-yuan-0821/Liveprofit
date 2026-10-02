# 运行时目录收敛 任务清单

> **状态**：`已完成`（2026-10-02）
> **进度**：5/5 任务（T1-T5 全部完成）
> **下一步**：无（任务已归档）
> **关联方案**：[plan.md](plan.md)

---

## 任务总览

| 编号 | 任务 | 依赖 | 状态 |
|------|------|------|------|
| T1 | 代码侧路径常量与硬编码收敛（settings + AI resolver + 9 处写入方 + db 脚本） | — | 已完成 |
| T2 | run.sh 与工具缓存/测试入口重定向（26 处替换 + pyproject 缓存 + PYTHONPYCACHEPREFIX + basetemp） | T1 | 已完成 |
| T3 | 测试断言、文档与静态配置同步（14 文件随迁 + catalog 再生成 + exclude/gitignore/dockerignore） | T2 | 已完成 |
| T4 | 停服迁移与启动验证（cp -a/mv 数据迁移 + 删壳 + ./run.sh 启动 + 执行日志页历史可读） | T3 | 已完成 |
| T5 | 遗留垃圾清理与目录纯度验收（删除清单 + ACL 兜底 + 根目录/var 纯度检查） | T4 | 已完成 |

## 任务

### T1 代码侧路径常量与硬编码收敛

- **目标**：plan.md 4.2 全部落地——backend 常量改 var/logs；记忆路径 resolver 单点化；9 处代码写入方随迁；3 个测试断言同步；单测通过
- **涉及文件**：
  - 修改：`backend/bootstrap/settings.py`（:82）、`AI/default_config.py`（resolver 新增）、`AI/stockAgents/utils/chromadb_config.py`（:35/42/54/81）、`AI/stockAgents/utils/memory.py`（:59）、`AI/utils/logs_reader.py`（:34 + docstring :5/:30）、`AI/graph/trading_graph.py`（:657/:76 + docstring :69-70 + 注释 :443）、`AI/eventStudy/scheduler/app_scheduler.py`（:47-48/:68 + docstring :8-23 + :185 消息 + 启动 mkdir）、`AI/eventStudy/scheduler/daily_job.py`（:32 + basicConfig 前 mkdir）、`db/instrument/ingest/backfill.py`（:57/58/460/757 + docstring :14）、`db/instrument/ingest/factor_day_backfill.py`（:34）、`db/instrument/migration/migrate_legacy.py`（:35/440 + docstring :8）、`.env`（:39）、`.env.example`（:52）、`tests/backend/analysis/unit/test_real_graph_factory.py`（:101 + 注释 :72）、`tests/ai/graph/unit/test_run_log_dir.py`（:50/56/62/69 + 注释 :21/:49）、`tests/ai/graph/unit/test_state_log_events.py`（:64，setattr 方案）
- **依赖**：无
- **必验风险/范围**：记忆路径 CWD 无关性（resolver）；`from AI.default_config import resolve_memory_path` 无循环导入；backfill mkdir parents=True；daily_job 直启入口 mkdir 覆盖。定向：T1 全部修改文件；关联：AI 层 graph/scheduler 与 backend bootstrap 相关单测
- **验收标准**（全部勾选才算完成）：
  - [x] `python -m pytest tests/backend/analysis/unit/test_real_graph_factory.py tests/ai/graph/unit/test_run_log_dir.py tests/ai/graph/unit/test_state_log_events.py` 通过
  - [x] `cd backend && python -c "from AI.default_config import resolve_memory_path; print(resolve_memory_path())"` 输出仓库根 `var/data/chroma_db`（CWD 无关验证）
  - [x] `python -m tests.run --module backend.analysis --level unit` 与 `--module ai.graph --level unit` 关联回归通过（无 --allow-db 范围；ai.graph 1 条存量失败 test_prompts 计数漂移已登记 issues.md，与本任务无关）
- **验收证据**：[result.md](result.md#t1)
- **状态**：`已完成`（2026-10-02）

### T2 run.sh 与工具缓存/测试入口重定向

- **目标**：plan.md 4.3 全部落地——run.sh 22 行 26 处替换 + PYTHONPYCACHEPREFIX export；pyproject 新建 [tool.ruff] cache-dir 与 pytest cache_dir；tests/run.py basetemp 迁 var/tmp/pytest + test_test_selection.py:122 断言；删除根级缓存目录
- **涉及文件**：
  - 修改：`run.sh`（22 行 26 处 + 顶部 export + :333 文案）、`pyproject.toml`（[tool.ruff] 新建 + [tool.pytest.ini_options] cache_dir）、`tests/run.py`（:80）、`tests/backend/platform/unit/test_test_selection.py`（:122）
  - 删除：`.ruff_cache/`、`.pytest_cache/`、根 `__pycache__/`
- **依赖**：T1
- **必验风险/范围**：26 处替换逐行核对（机械 sed 漏 202/310 无斜杠 mkdir）；`./run.sh stop` 能读 pid；三个缓存目录不再于根生成。定向：run.sh 启动/停止全流程、test_test_selection 单测；关联：platform 单测
- **验收标准**（全部勾选才算完成）：
  - [ ] `grep -c "logs" run.sh` 复核：所有命中均为 `var/logs`，无裸 `logs/` 残留
  - [ ] `./run.sh`（默认 all）启动：`ls var/logs/` 出现 api.log 等与 `.platform.pids`；`./run.sh stop` 正常停服
  - [ ] `ruff check .` 后 `var/cache/ruff/` 生成、根目录无 `.ruff_cache`
  - [ ] 裸跑 `python -m pytest tests/backend/analysis/unit/test_real_graph_factory.py` 后 `var/cache/pytest/` 生成、根目录无 `.pytest_cache`
  - [ ] run.sh 启动的服务运行后 `var/cache/pycache/` 生成、根目录无 `__pycache__` 再生
  - [ ] `python -m pytest tests/backend/platform/unit/test_test_selection.py` 通过
- **验收证据**：[result.md](result.md#t2)
- **状态**：`已完成`（2026-10-02）

### T3 测试断言、文档与静态配置同步

- **目标**：plan.md 4.4 全部落地——文档与注释随迁 14 文件（C-a~C-e 组）+ catalog/测试详情再生成；pyproject exclude 冗余清理；.gitignore/.dockerignore 单点化
- **涉及文件**：
  - 修改：`README.md`（:142/:203/:253）、`docs/knowledge/ai/板块层.md`（:15/:78/:98）、`docs/knowledge/backend/数据库表结构.md`（:34）、`backend/migrations/versions/0007_drop_legacy_market_tables.py`（:6 注释）、`backend/modules/analysis/infrastructure/real_graph_factory.py`（:112 docstring）、`AI/utils/llm_callbacks.py`（:6）、`AI/utils/dataprovider_log.py`（:7）、`AI/sectorAgents/charts.py`（:5/:103）、`AI/dataflows/market_features.py`（:27）、`AI/dataflows/providers/cn/tushare.py`（:3236）、`AI/eventStudy/scheduler/run_daily.bat`（:3）、`AI/eventStudy/scheduler/scheduler_setup.md`（:16/:36/:38/:123）、`tests/backend/analysis/contract/api/test_execution_logs_api.py`（:50 注释）、`docs/experience/best-practices/ai/eventstudy-scheduler.md`（:8/:13，可选）、`docs/experience/pitfalls/ai/store-daily.md`（:54，可选）、`pyproject.toml`（:89 exclude）、`.gitignore`、`.dockerignore`
  - 生成：`tests/catalog/ai/graph.json`、`tests/catalog/backend/analysis.json`、`docs/knowledge/test/测试详情/ai/graph.md`、`docs/knowledge/test/测试详情/backend/analysis.md`（tests/index.py 再生成）
- **依赖**：T2
- **必验风险/范围**：exclude 删除不影响 setuptools 打包语义（include 已限定 AI*/backend*/db*）；catalog 再生成不引入旧表述；文档无陈旧引用残留。定向：全部随迁文件 grep 复核；关联：`python -m tests.run` 相关套件
- **验收标准**（全部勾选才算完成）：
  - [ ] `grep -rn "logs/backups\|logs/tasks\|chroma_db\|/logs/\|results/" docs/knowledge README.md` 仅剩新路径表述，无旧根路径残留（排除历史语境文件）
  - [ ] `ruff check .` 输出中不含 var/ 路径（var/ 相关错误计数为 0；全量基线 2943 errors 不承诺归零）
  - [ ] `git status` 无 var/ 以外的新增未跟踪项；catalog/测试详情为生成产物
  - [ ] 关联回归：`python -m tests.run --module backend.analysis --level unit` 与 `--module ai.graph --level unit` 通过
- **验收证据**：[result.md](result.md#t3)
- **状态**：`已完成`（2026-10-02）

### T4 停服迁移与启动验证

- **目标**：plan.md 4.1 全部落地——停服 → cp -a/mv 数据迁移 → 删壳 → `./run.sh` 启动 → 执行日志页历史可读 → 根目录纯净检查；回滚预案就绪（git revert + 反向迁移 + .env 手工回改）
- **涉及文件**：
  - 新建：无（目录由运行时自动创建）
  - 删除：`logs/`、`chroma_db/`、`results/` 根级目录（迁移后删除）
- **依赖**：T3
- **必验风险/范围**：点文件漏迁（cp -a 后抽查 .platform.pids/.vite-dev.pid 到位）；历史任务执行日志迁移后可读（API 按 task_id 读 logs/tasks 同构）；迁移后无根目录再生。定向：logs/chroma_db/results 三个目录逐项；关联：./run.sh 全流程
- **验收标准**（全部勾选才算完成）：
  - [ ] `./run.sh stop` 停服干净（ps/端口检查无残留进程）
  - [ ] 迁移执行：70 项全量到位（`ls var/logs | wc -l` ≥ 迁移前 logs 项数；`ls -la var/logs` 含点文件）；var/data/chroma_db、var/data/quant_history、var/data/backups、var/results 内容与源一致（`diff -r` 或 checksum 抽查）
  - [ ] 根级 `logs/`、`chroma_db/`、`results/` 已删除
  - [ ] `./run.sh` 启动：`ls var/logs/` 出现新服务日志与 `.platform.pids`
  - [ ] API 执行日志页读取一个历史 task_id 有数据（`curl` 执行日志端点或人工页面观察）
  - [ ] 根目录未再生 `logs/`、`chroma_db/`、`results/`；`git status` 无 var/ 以外新增未跟踪项
  - [ ] 分析链路端到端（跑一次分析任务，产物落 var/runs、执行日志落 var/logs/tasks/、记忆读写 var/data/chroma_db）**仅限用户手动执行**
- **验收证据**：[result.md](result.md#t4)
- **状态**：`已完成`（2026-10-02）

### T5 遗留垃圾清理与目录纯度验收

- **目标**：plan.md 4.5 全部落地——删除清单执行（含 ACL 异常路径 Windows 原生兜底）；根目录与 var/ 纯度验收
- **涉及文件**：
  - 删除：根 `.codex-joint-acceptance.log`、`.codex-joint-remaining.log`、`.pytest_tmp_quant/`；var 下 `uv-cache/`、`tmp/`、12 个 `pytest-*`、`market-refresh-*`（3 目录 3 文件）、`test-file-index/`、`test-layout-refactor/`、`market-ingest.pid`
- **依赖**：T4
- **必验风险/范围**：删除清单逐项已核实引用（var/runs、var/research 不在此清单）；ACL 异常路径（var/tmp/pytest、var/pytest/<uuid>、var/test-file-index/*、var/test-layout-refactor/*、var/market-refresh-implementation/pytest-tmp）用 `cmd //c rmdir /s /q` 或 icacls 兜底；删除不可逆
- **验收标准**（全部勾选才算完成）：
  - [ ] 删除清单全部执行完成（每项确认已删）
  - [ ] `ls` 根目录仅剩方案允许条目（源码/配置/工具绑定 + var/）
  - [ ] `ls var/` 仅剩 7 个子目录：runs/research/results/logs/data/cache/tmp
  - [ ] `du -sh var/` 与迁移前对比（预期释放 ~130M；ACL 异常目录统计误差以实际删除结果为准）
  - [ ] `./run.sh` 全服务正常（清理后再次启动确认）
  - [ ] `python -m tests.run --module backend.analysis --level unit` 冒烟（basetemp 自动重建于 var/tmp/pytest）
- **验收证据**：[result.md](result.md#t5)
- **状态**：`已完成`（2026-10-02）

---

## 拆分与维护规则

- **拆分粒度**：按契约边界（T1 路径常量/T2 工程脚本/T3 文档一致性）与独立风险（T4 数据迁移保真/T5 不可逆删除）划分；辅助模块、函数与测试文件并入所属任务，不单独验收
- **验收标准必须可执行**：全部为具体命令与观察点；人工检查写明看什么、期望看到什么
- **状态取值**：`待开始` → `进行中` → `已完成`；被阻塞时标 `阻塞：<原因>`
- **生命周期**：任务清单随任务文件夹保留——方案实现完成并归档时随文件夹一并归档
- **无需额外评审**：任务清单直接由已评审通过的方案拆出，只做拆解、不复述设计
- **集中收尾**：实现中按需检查，任务完成跑定向和关联回归，并做适用的独立代码review；修复只复验受影响范围
- **记录分工**：本文件只维护标准、状态和证据链接；README 维护总状态，log 留简短时间线，result 保存完整证据
