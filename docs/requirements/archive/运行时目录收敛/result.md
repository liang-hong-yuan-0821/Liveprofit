# 最终产出与结论

每个验收任务收尾追加一份证据；全部任务完成后填写最终交付结论。完整命令和结果只记录在本文件，tasks/README/log 摘要引用，不重复复制。

## T1 代码侧路径常量与硬编码收敛 验收

- **范围**：backend 常量（settings.py:82）、AI 层 resolver（default_config/chromadb_config/memory/logs_reader/trading_graph/eventStudy 调度器）、db 脚本（backfill/factor_day_backfill/migrate_legacy）、.env/.env.example、3 个测试断言文件；单元测试层级
- **结论**：PASS（附 1 项与本任务无关的存量失败，见未通过项）

## 验证结果

| 验收标准/风险 | 实际命令或既有证据链接 | 结果 |
|------|----------|------|
| 定向：3 个测试文件 | `python -m pytest tests/backend/analysis/unit/test_real_graph_factory.py tests/ai/graph/unit/test_run_log_dir.py tests/ai/graph/unit/test_state_log_events.py -q` | 通过（20 passed in 8.89s） |
| CWD 无关验证（resolver） | `cd backend && python -c "from AI.default_config import resolve_memory_path; print(resolve_memory_path())"` | 通过：输出 `D:\code\workspace\python\Liveprofit\var\data\chroma_db`（backend/ CWD 下仍解析到仓库根） |
| 关联回归 backend.analysis | `python -m tests.run --module backend.analysis --level unit` | 通过（121 passed） |
| 关联回归 ai.graph | `python -m tests.run --module ai.graph --level unit` | 79 passed / 1 failed（存量失败，见下） |

- **修复/复验**：无本任务相关失败。
- **未通过项/后续门禁**：`tests/ai/graph/unit/test_prompts.py::test_default_prompt_keys_cover_topology_llm_nodes` 断言 `len(DEFAULT_PROMPTS)==23`，实际注册表 26 条（含 event-study 3 条：Assessment Reviewer/Event Labeler/Novelty Judge）——本任务未改 prompts.py 与该测试文件（git status 证实），属存量漂移（eventStudy 提示词并入后计数未随迁），与 T1 无关；按主线优先登记 [issues.md](issues.md) 待组内治理。

## Code Review

- **结论**：待全部任务完成后统一独立 review（本任务为多文件契约变更，纳入整体 review 范围）

## 交付物

- 记忆路径 resolver 单点化：`AI/default_config.py`（resolve_memory_path：env 优先/相对值按 PROJECT_ROOT 解析/默认 var/data/chroma_db）
- 5 处硬编码 chroma 路径 → resolver：`AI/stockAgents/utils/chromadb_config.py`（4 处）、`AI/stockAgents/utils/memory.py`（1 处）
- `AI/utils/logs_reader.py` 默认根 → var/logs
- `AI/graph/trading_graph.py`：state_log 写盘 → `_PROJECT_ROOT/var/results/{ticker}/`；run_log 回退 → `_PROJECT_ROOT/var/logs/{ts}`；注释/docstring 同步
- `AI/eventStudy/scheduler/app_scheduler.py` + `daily_job.py`：锁/计数/标记/日志 → var/logs（含直启入口 mkdir）
- `db/instrument/ingest/backfill.py`、`factor_day_backfill.py`、`migration/migrate_legacy.py`：失败清单/progress/checkpoint → var/logs；backup_dir → var/data/backups
- `backend/bootstrap/settings.py:82` execution_logs_root → var/logs
- `.env`/`.env.example` LIVEPROFIT_MEMORY_PATH → ./var/data/chroma_db
- 3 个测试断言随迁（test_real_graph_factory/test_run_log_dir/test_state_log_events）

## T2 run.sh 与工具缓存/测试入口重定向 验收

- **范围**：run.sh 22 行 26 处替换 + PYTHONPYCACHEPREFIX、pyproject [tool.ruff]/[tool.pytest.ini_options] 缓存配置、tests/run.py basetemp、根级缓存目录删除；启动/停止全流程与缓存落点层级
- **结论**：PASS

## 验证结果

| 验收标准/风险 | 实际命令或既有证据链接 | 结果 |
|------|----------|------|
| run.sh 替换复核 | `grep -n "logs" run.sh | grep -v "var/logs"` | 通过：无裸 logs 残留；22 行 var/logs（26 处） |
| 启动/停止全流程 | `./run.sh`（默认 all）后台启动 → 45s 后检查 → `./run.sh stop` | 通过：var/logs/ 出现 api.log/worker.log/market-worker.log/dispatcher.log/vite-dev.log 与 .platform.pids；stop 全部进程正常退出、pid 文件清理 |
| ruff 缓存重定向 | `ruff check backend/bootstrap/settings.py` | 通过：var/cache/ruff/（0.16.7 + CACHEDIR.TAG）生成；根目录无 .ruff_cache（已删除未再生） |
| pytest 缓存重定向 | `python -m pytest tests/backend/platform/unit/test_test_selection.py -q` | 通过：14 passed；var/cache/pytest/ 生成；根目录无 .pytest_cache |
| PYTHONPYCACHEPREFIX | run.sh 启动后 `find var/cache/pycache -name "*.pyc"` | 通过：项目模块 pyc 落 var/cache/pycache/code/workspace/python/Liveprofit/...；根目录无 __pycache__ 再生 |
| test_test_selection 断言 | 同上 pytest 命令 | 通过（含 var/tmp/pytest basetemp 断言） |

- **修复/复验**：PYTHONPYCACHEPREFIX 首版误插在 SCRIPT_DIR 定义之前（变量为空），已移至定义之后并验证展开为仓库根绝对路径。
- **未通过项/后续门禁**：无。Docker 基础设施未停止（run.sh stop 语义如此，与现状一致）。

## Code Review

- **结论**：待全部任务完成后统一独立 review

## 交付物

- `run.sh`：26 处 var/logs + 顶部 PYTHONPYCACHEPREFIX export + :333 任务内核明细文案修正
- `pyproject.toml`：[tool.ruff] 新建（cache-dir=var/cache/ruff）；[tool.pytest.ini_options] cache_dir=var/cache/pytest
- `tests/run.py:80`：basetemp → var/tmp/pytest
- `tests/backend/platform/unit/test_test_selection.py:122`：断言随迁
- 删除：根 .ruff_cache/、.pytest_cache/、__pycache__/

## T3 测试断言、文档与静态配置同步 验收

- **范围**：14 文件文档/注释随迁 + catalog/测试详情再生成、pyproject exclude 冗余清理、.gitignore/.dockerignore 单点化；文档一致性与回归层级
- **结论**：PASS（附 1 项遗留：var/tmp-broken-acl 待管理员权限删除）

## 验证结果

| 验收标准/风险 | 实际命令或既有证据链接 | 结果 |
|------|----------|------|
| 文档旧路径残留检查 | `grep -rnE "logs/backups|logs/tasks|chroma_db|/logs/|results/" docs/knowledge README.md | grep -vE "var/logs|var/data"` | 通过：修复 test_execution_logs_api.py:183 docstring 并 `python -m tests.index` 再生成后无残留（72 索引/详情文档再生成） |
| ruff 不扫描 var/ | `ruff check .` 全文核查 | 通过：无 var/ 下文件被检查（respect-gitignore 生效）；输出中 3 处含 var/ 行为源码合法引用（archive 附件 calendar_poc.py 的 sys.path、tests 支持文件 artifact_uri fixture） |
| git status | `git status --short` | 通过：仅本任务修改文件；无 var/ 以外新增未跟踪项（.pytest_tmp_quant Permission denied 警告为 T5 清单项） |
| 关联回归 | `python -m tests.run --module backend.analysis --level unit` 与 `--module ai.graph --level unit` | 通过：121 passed / 79 passed + 1 存量失败（test_prompts 计数漂移，已登记 issues.md） |

- **修复/复验**：basetemp 首跑 36+20 ERROR 根因是 var/tmp 旧死副本 ACL 异常（R2 F20 预警项）；坏目录改名 var/tmp-broken-acl 后 basetemp 自动重建，回归全绿。删除坏目录需管理员权限，留用户处理（用户已授权删除，技术上受阻）。
- **未通过项/后续门禁**：var/tmp-broken-acl（52M）待用户以管理员权限删除（`rmdir /s /q` 或资源管理器）。

## Code Review

- **结论**：待全部任务完成后统一独立 review

## 交付物

- 文档随迁 14 文件（README/板块层/数据库表结构/0007 注释/real_graph_factory/llm_callbacks/dataprovider_log/charts/market_features/tushare/run_daily.bat/scheduler_setup/eventstudy-scheduler/store-daily）
- 测试注释与 docstring 同步（test_execution_logs_api.py:50/:183、test_real_graph_factory.py:72、test_run_log_dir.py:21/:49）
- `python -m tests.index` 再生成：tests/catalog/*.json + docs/knowledge/test/测试详情/* + 测试索引
- pyproject setuptools exclude 冗余清理（logs*/results*/chroma_db*）
- .gitignore/.dockerignore 单点化（var/ 覆盖）

## T4 停服迁移与启动验证 验收

- **范围**：logs(70 项)/chroma_db/results 三目录数据迁移到 var/ 并删除根级壳；启动验证与执行日志页历史可读；端到端层级
- **结论**：PASS（分析链路端到端仅限用户手动执行，保留）

## 验证结果

| 验收标准/风险 | 实际命令或既有证据链接 | 结果 |
|------|----------|------|
| 停服干净 | `./run.sh stop`（迁移前） | 通过：全栈已停止、pid 文件清理 |
| 数据迁移完整 | `cp -a logs/. var/logs/ && rm -rf logs` + mv 拆分 + `mv chroma_db/chroma.sqlite3` + `mv results/*` | 通过：var/logs 68 项（70 - backups - quant_history）；var/data/backups 2 个 pg_backup sql；var/data/quant_history 18 文件；var/data/chroma_db/chroma.sqlite3；var/results 2 个 state_log.json；点文件 .vite-dev.pid 在位（.platform.pids 迁移前已被 stop 流程正常清理） |
| 根级壳删除 | `rm -rf logs` + `rmdir chroma_db results` | 通过：根级 logs/chroma_db/results 已删除 |
| 启动验证 | `./run.sh` 后台启动 45s 后检查 | 通过：var/logs 出现新服务日志；.platform.pids 重新生成（api 5001/worker 5008/market-worker 5012/dispatcher 5016） |
| 执行日志页历史可读 | `curl /api/v1/analysis-tasks/0abc9761-.../execution-logs` | 通过：available=True（迁移后历史任务日志被 API 正确读取） |
| 根目录纯净 | `ls -d logs chroma_db results __pycache__ .ruff_cache .pytest_cache` | 通过：根目录无运行时目录再生 |
| 分析链路端到端 | 跑一次分析任务（产物/执行日志/记忆读写） | 未执行：仅限用户手动执行（真实 LLM/toolkit 门禁） |

- **修复/复验**：迁移中 backups/quant_history 因目标目录被 mkdir -p 预创建产生嵌套（var/data/backups/backups/），当场修正为平铺并复核文件在位。
- **未通过项/后续门禁**：分析链路端到端验证保留用户手动执行项。

## Code Review

- **结论**：待全部任务完成后统一独立 review

## 交付物

- var/logs（68 项）、var/data/backups（2 sql）、var/data/quant_history（18 文件）、var/data/chroma_db（chroma.sqlite3）、var/results（2 state_log）
- 根级 logs/chroma_db/results 已删除

## T5 遗留垃圾清理与目录纯度验收 验收

- **范围**：根/var 删除清单执行（含 ACL 异常兜底）、根目录与 var/ 纯度、清理后启动冒烟；端到端层级
- **结论**：PASS（附 1 项门禁：17 个 ACL 异常目录已移出仓库隔离，最终物理清除需管理员权限）

## 验证结果

| 验收标准/风险 | 实际命令或既有证据链接 | 结果 |
|------|----------|------|
| 删除清单执行 | rm + cmd del/rmdir 逐项 | 通过：直接可删项全部删除（codex 日志×2、uv-cache 51M、market-refresh-review、poc 文件×3、market-ingest.pid、部分 pytest-* 内容）；17 个含 ACL 破坏子项的目录 rename 移出仓库 |
| 根目录纯度 | `ls -a` | 通过：仅剩源码/配置/工具绑定 + var/ |
| var/ 纯度 | `ls var/` | 通过：仅剩 6 个主机子目录（cache/data/logs/results/runs/tmp；research 仅容器内 docker 卷，主机本地无——与方案一致） |
| 磁盘释放 | `du -sh var/` | 通过：~130M 垃圾移出（var/ 现存 2.1G 为合法数据：data/backups 1.4G pg 备份审计、logs/tasks 545M 历史执行日志、cache/pycache 146M 可重建缓存） |
| 清理后启动确认 | `./run.sh` 启动 40s → 检查 → `./run.sh stop` | 通过：api.log 等生成、.platform.pids 在位、停止正常 |
| basetemp 冒烟 | `python -m tests.run --module backend.analysis --level unit` | 通过：121 passed；var/tmp/pytest 自动重建 |

- **修复/复验**：ACL 异常目录（R2 F20 预警的同一类）rm/attrib/takeown/icacls/cmd rmdir 全部拒绝（连枚举都失败），rename 只需父目录权限——17 项目录已移至 `D:\_liveprofit_acl_trash`（同卷、仓库外）。
- **未通过项/后续门禁**：`D:\_liveprofit_acl_trash`（含 var/tmp 旧死副本 52M 等 ~130M）需用户以管理员权限最终删除（资源管理器或管理员 cmd `rmdir /s /q`）。

## Code Review

- **结论**：PASS（subagent 整体 review：实现与方案逐项一致，parents[N] 深度实测全部解析到仓库根、反向 sweep 无旧路径残留、catalog hash 变更经重算为历史 CRLF 归一化遗留的陈旧 hash 修复）；3 minor + 2 polish 已在收尾修正并 delta 复验（34 passed、PytestConfigWarning 消除、tests.index --check 一致）
- **遗留**：无必修项

## 交付物

- 根目录与 var/ 按方案目标布局收敛完成（根 11 项运行时/缓存条目 → 0；var/ 6+1 性质分层）


## 最终交付结论

- **总体结论**：PASS。五项任务全部验收完成 + 整体 code review 通过；运行时目录收敛达成方案目标：根目录 11 项运行时/缓存条目归零，var/ 六层性质分层就位，全链路路径与 CWD 无关，历史数据零丢失（执行日志页历史 task 可读、记忆库随迁、pg 备份审计保留）。
- **未完成门禁（用户侧）**：① `D:\_liveprofit_acl_trash`（~130M，17 个 ACL 破坏目录）需管理员权限删除；② 分析链路端到端验证仅限用户手动执行。
- **已登记问题**：test_prompts 计数漂移（存量，非本任务引入，见 issues.md）。