# 运行时目录收敛方案

> **状态**：已确认（2026-10-02，用户拍板：性质分层 7 子目录 + PYTHONPYCACHEPREFIX 启用），进入任务分解
> **关联文档**：[开发流程](../../../standards/workspace/开发流程.md)｜[方案评审](../../../standards/workspace/方案评审.md)｜[评审 findings 归档](issues.md)

---

## 一、背景与动机

| 维度 | 现状 | 问题 | 目标 |
|------|------|------|------|
| 根目录整洁度 | 根目录散落 4 类共 11 个运行时/缓存条目：`chroma_db/`（向量库）、`results/`（分析状态日志）、`logs/`（服务日志+执行日志+backups 备份+quant_history 数据，70 项＝69 可见 + 1 点文件 .vite-dev.pid）、`var/`（产物卷）、`.ruff_cache/`、`.pytest_cache/`、`__pycache__/`、`liveprofit.egg-info/`、`.pytest_tmp_quant/`、`.codex-joint-*.log`×2（根 `node_modules/` 10-01 误跑 vitest 产生，现已不存在） | 运行时数据与源码混居；`logs/`、`chroma_db/`、`results/` 三个运行时目录各占一个根级位置，而 `var/` 本就是项目定义的"平台产物卷"（[.gitignore](../../../../.gitignore) 注释、[settings.py:73-74](../../../../backend/bootstrap/settings.py#L73-L74)），同类数据却分居四处 | 根目录只留源码与配置 + 工具绑定目录；运行时数据全部收敛进 `var/` 单一运行时卷 |
| 路径配置一致性 | 记忆路径五处硬编码：`AI/stockAgents/utils/chromadb_config.py:35,42,54,81` 与 `AI/stockAgents/utils/memory.py:59` 写死 `path="./chroma_db"`，绕过 [.env:39](../../../../.env#L39) 的 `LIVEPROFIT_MEMORY_PATH`；[trading_graph.py:657](../../../../AI/graph/trading_graph.py#L657) 硬编码 `results/{ticker}/`；`resolve_run_log_dir` 回退写 `logs/{时间戳}`（[trading_graph.py:76](../../../../AI/graph/trading_graph.py#L76)，`./run.sh classic` 可达）；eventStudy 调度器写 `logs/daily_job.*`（[app_scheduler.py:47-48](../../../../AI/eventStudy/scheduler/app_scheduler.py#L47-L48)）；回填/迁移脚本写 `logs/*.json/.log`（[backfill.py:57,58,460,757](../../../../db/instrument/ingest/backfill.py#L57)、[factor_day_backfill.py:34](../../../../db/instrument/ingest/factor_day_backfill.py#L34)、[migrate_legacy.py:35,440](../../../../db/instrument/migration/migrate_legacy.py#L35)）；[logs_reader.py:34](../../../../AI/utils/logs_reader.py#L34) 默认 `parents[2]/"logs"` | 硬编码相对路径按进程 CWD 解析：从 backend/ 启动进程时记忆库/日志会写到错误位置；改路径必须同时改 20+ 处，漏一处即数据分叉或目录再生（删除根 logs/ 后跑一次 `./run.sh classic` 就会重建根 `logs/{ts}/`） | 全部运行时路径收敛为两个口径：backend 走 `resolve_execution_logs_root` 单一出口（按 PROJECT_ROOT 解析）；AI/db 脚本层走 `Path(__file__)` 推导 PROJECT_ROOT 的绝对路径——一律与进程 CWD 无关 |
| 工具缓存归属 | `.ruff_cache/`、`.pytest_cache/`、根 `__pycache__/` 散落根目录 | 每次新工具或误操作都可能再长出新根级缓存目录，根目录持续膨胀 | ruff/pytest 缓存按各自官方配置重定向到 `var/` 下 |
| 遗留垃圾 | `.codex-joint-*.log`（9-27 Codex 验收日志）、`.pytest_tmp_quant/`（空目录且 ACL 异常，git 读不了）、`var/` 内 ~130M 残留（`uv-cache` 51M 死副本——当前 uv 缓存实际在 `C:\Users\qiyanqiao\AppData\Local\uv\cache`、`tmp` 52M、`pytest-*` 12 个、`market-refresh-*` 14M、`market-ingest.pid` 旧版 run.sh 残留）；多个残留目录 ACL 异常（var/tmp/pytest、var/pytest/<uuid>、var/test-file-index/*、var/test-layout-refactor/*、var/market-refresh-implementation/pytest-tmp 实测 Permission denied） | 磁盘浪费 + `var/` 作为产物卷的语义被测试残留稀释 | 遗留项全部删除，`var/` 只含受生命周期管理的运行时数据 |

## 二、架构设计

本方案不改变任何业务架构、数据模型与接口契约，只做运行时数据落点的收敛与路径口径统一。

**目标布局**（`var/` 为唯一运行时卷，按性质四层分组，语义对齐 FHS `/var`：logs≈/var/log、data≈/var/lib、cache≈/var/cache、tmp≈/var/tmp）：

```
Liveprofit/
├── AI/  backend/  frontend/  db/  docs/  tests/  docker/   ← 源码（不动）
├── AGENTS.md  README.md  alembic.ini  docker-compose.yml
├── main.py  pyproject.toml  run.sh  uv.lock
├── .env  .env.example  .gitignore  .dockerignore  .python-version
├── .venv/  liveprofit.egg-info/      ← 工具绑定项目根，无法迁移（VSCode/uv 按约定发现；setuptools editable 强制生成）
└── var/                               ← 唯一运行时卷（.gitignore 已忽略）
    ├── runs/             ← 分析任务产物归档（既有，不动；docker 卷 liveprofit_artifacts）
    ├── research/         ← 量化研究数据集（既有，不动；仅容器内 docker 卷 liveprofit_research，主机本地不存在）
    ├── results/          ← 【迁入】原 results/：trading graph 状态日志
    ├── logs/             ← 【迁入】原 logs/（除 backups、quant_history 外全部）：run.sh 服务日志+pid + tasks/ 执行调用日志 + 回填/迁移脚本日志 + eventStudy 调度器日志
    ├── data/             ← 持久业务数据（不可随意清）
    │   ├── chroma_db/    ← 【迁入】原 chroma_db/：AI 个股分析长期记忆
    │   ├── quant_history/ ← 【迁入】原 logs/quant_history/：baostock parquet 行情数据
    │   └── backups/      ← 【迁入】原 logs/backups/：pg 备份审计数据（pg_backup_20260819.sql 等）
    ├── cache/            ← 可重建缓存（全部按各自官方配置重定向）
    │   ├── ruff/         ← 【新指向】ruff cache-dir
    │   ├── pytest/       ← 【新指向】pytest cache_dir
    │   └── pycache/      ← 【新指向】PYTHONPYCACHEPREFIX（已确认启用，run.sh export）
    └── tmp/              ← 临时文件
        └── pytest/       ← 【新指向】测试 basetemp（原 var/pytest，tests/run.py 迁移）
```

**路径解析原则（本方案强制）**：
1. backend 侧：相对路径常量由 `resolve_execution_logs_root`（[settings.py:151](../../../../backend/bootstrap/settings.py#L151)）按 PROJECT_ROOT 解析——这是既有经验（worker 与 API 是两个进程，CWD 不同会导致写入/读取目录分叉）
2. AI/db 脚本层（无 pydantic settings 的模块）：用 `Path(__file__).resolve().parents[N]` 推导仓库根，拼接绝对路径——chromadb `PersistentClient`、`logging.FileHandler`、裸 f-string 路径均按进程 CWD 解析，相对值在 backend/ 等非根 CWD 下必然写错位置，故一律绝对化
3. env 变量 `LIVEPROFIT_MEMORY_PATH` 允许相对值，读取时按 PROJECT_ROOT 解析（单点 resolver，见 4.2）

**Docker 影响**：零改动。docker-compose 卷挂载的是 `/app/var/runs`、`/app/var/research`（[docker-compose.yml:125-126](../../../../docker-compose.yml#L125-L126)），本方案不动这两个路径；`execution_logs_root` 在容器内经 PROJECT_ROOT(/app) 解析为 `/app/var/logs`，与现状 `logs/` 一样未挂卷、容器重建即丢，行为等价（执行日志本就不持久化）。

## 三、设计概览

### backend

#### 服务层

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| CoreSettings.execution_logs_root【修改】 | 常量 `Path("logs")` → `Path("var/logs")`，解析仍走 resolve_execution_logs_root 单一出口 | [backend/bootstrap/settings.py:82](../../../../backend/bootstrap/settings.py#L82) | worker 写、API 读的执行调用日志统一落在 var/logs/tasks/，前端"平台执行日志页"无感知 |

#### 测试

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| test_real_graph_factory 路径断言【修改】 | 断言 `PROJECT_ROOT / "logs"` → `PROJECT_ROOT / "var" / "logs"` | [tests/backend/analysis/unit/test_real_graph_factory.py:101](../../../../tests/backend/analysis/unit/test_real_graph_factory.py#L101) | 断言与实际常量一致，回归通过 |
| test_run_log_dir 回退断言【修改】 | 回退路径断言（50/56/62/69 行）随 resolve_run_log_dir 新回退值更新；35-37 行 platform_log_dir 注入断言不变（注入路径原样使用） | [tests/ai/graph/unit/test_run_log_dir.py](../../../../tests/ai/graph/unit/test_run_log_dir.py) | 断言与回退行为一致，回归通过 |
| test_state_log_events 写盘断言【修改】 | 测试改为 `monkeypatch.setattr(trading_graph, "_PROJECT_ROOT", tmp_path)` 后断言写盘于 `tmp_path/"var"/"results"/...`（trading_graph 改 var/results 且为 PROJECT_ROOT 绝对路径后，原 `monkeypatch.chdir(tmp_path)` 无法重定向写盘位置——绝对路径不受 chdir 影响，会写进仓库真实 var/results；必须替换模块常量保持 tmp_path 隔离；不改必 FileNotFoundError） | [tests/ai/graph/unit/test_state_log_events.py:64](../../../../tests/ai/graph/unit/test_state_log_events.py#L64) | 断言与实际写盘位置一致且保持测试隔离，回归通过 |
| test_test_selection basetemp 断言【修改】 | 断言 `temporary_path.parent == tmp_path / 'var/pytest'` → `tmp_path / 'var/tmp/pytest'`（basetemp 随 tests/run.py 迁 var/tmp/pytest） | [tests/backend/platform/unit/test_test_selection.py:122](../../../../tests/backend/platform/unit/test_test_selection.py#L122) | 断言与 runner 实际 basetemp 一致，回归通过 |

### AI

#### Agent 节点

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| 记忆路径 resolver【新增】 | 新增 `resolve_memory_path()`：env `LIVEPROFIT_MEMORY_PATH` 绝对值原样用、相对值按 PROJECT_ROOT（`Path(__file__).resolve().parents[1]`）解析；未设 env 时默认 `PROJECT_ROOT/var/data/chroma_db`；`memory_path` 字段改用该 resolver | [AI/default_config.py:38](../../../../AI/default_config.py#L38) | 记忆路径与 CWD 无关，单一默认值来源 |
| chromadb 客户端工厂【修改】 | 4 处硬编码 `path="./chroma_db"` 统一改为 `path=str(resolve_memory_path())`（from AI.default_config import resolve_memory_path；无循环导入——default_config 仅依赖 os/pathlib） | [AI/stockAgents/utils/chromadb_config.py:35,42,54,81](../../../../AI/stockAgents/utils/chromadb_config.py#L35) | 修复硬编码绕过 env 的隐患：无论 CWD 在哪，记忆库都落在 resolver 决定的位置 |
| 记忆封装【修改】 | `PersistentClient(path="./chroma_db")` 同上改为 resolver | [AI/stockAgents/utils/memory.py:59](../../../../AI/stockAgents/utils/memory.py#L59) | 同上，与客户端工厂同口径 |
| 日志读取根【修改】 | `logs_root()` 默认 `parents[2]/"logs"` → `parents[2]/"var"/"logs"`（env `LIVEPROFIT_LOGS_DIR` 优先逻辑保留） | [AI/utils/logs_reader.py:34](../../../../AI/utils/logs_reader.py#L34) | `list_runs()` 缺省调用不再静默空扫已删除的根目录 |

#### 图编排

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| trading graph 状态日志路径【修改】 | `f"results/{self.ticker}/analysis_logs/"` → `_PROJECT_ROOT / "var" / "results" / self.ticker / "analysis_logs"`（`_PROJECT_ROOT = Path(__file__).resolve().parents[2]`；该文件是 state_log.json 唯一写入方，无代码内读取方） | [AI/graph/trading_graph.py:657](../../../../AI/graph/trading_graph.py#L657) | 个股分析状态日志落 var/results/，与产物卷同生命周期且与 CWD 无关 |
| resolve_run_log_dir 回退【修改】 | 回退分支 `Path(f"logs/{run_ts}")` → `_PROJECT_ROOT / "var" / "logs" / run_ts`（platform_log_dir 注入分支不变）；docstring 同步 | [AI/graph/trading_graph.py:76](../../../../AI/graph/trading_graph.py#L76) | `./run.sh classic` 的内核日志落 var/logs/{ts}/，不再重建根 logs/ |

#### 调度与脚本

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| eventStudy 调度器路径【修改】 | `LOCK_PATH/ATTEMPTS_PATH/_marker_path` 的 `Path("logs/...")` → `_PROJECT_ROOT/"var/logs/...`（`parents[3]`）；子进程 `FileHandler("logs/event_study_daily.log")` 同改；模块 docstring 同步 | [AI/eventStudy/scheduler/app_scheduler.py:47-48,68](../../../../AI/eventStudy/scheduler/app_scheduler.py#L47-L48)、[AI/eventStudy/scheduler/daily_job.py:32](../../../../AI/eventStudy/scheduler/daily_job.py#L32) | 事件研究常驻调度器（独立 API 端口 8100 启动）的锁/计数/日志落 var/logs/，不再重建根 logs/ |
| 回填脚本路径【修改】 | `FAILURE_LIST_PATH/PROGRESS_LOG_PATH/STOCK_FACTOR_FAILURE_LIST_PATH/FAILURE_LIST` 的 `Path("logs/...")` 与 `Path("logs").mkdir` → `_PROJECT_ROOT/"var/logs/...`（`parents[3]`） | [db/instrument/ingest/backfill.py:57,58,460,757](../../../../db/instrument/ingest/backfill.py#L57)、[db/instrument/ingest/factor_day_backfill.py:34](../../../../db/instrument/ingest/factor_day_backfill.py#L34) | 回填失败清单/进度日志落 var/logs/ |
| 迁移脚本路径【修改】 | `CHECKPOINT_PATH` 的 `Path("logs/...")` → `_PROJECT_ROOT/"var/logs/...`；`backup_dir = Path("logs/backups")` → `_PROJECT_ROOT/"var/data/backups"` | [db/instrument/migration/migrate_legacy.py:35,440](../../../../db/instrument/migration/migrate_legacy.py#L35) | 迁移 checkpoint 与 pg 备份目录落 var/ 下 |

### 工程脚本与配置

| 具体对象 | 修改范围 | 主要文件或目录 | 交付行为变化 |
|----------|----------|----------------|--------------|
| 平台启动脚本【修改】 | 按单词 `logs` 全量替换为 `var/logs`，共 22 行 26 处（150/202/211/215/236/309-312/332-346/413-432 行等；含 202/310 两处 `mkdir -p logs` 与 431-432 的 `.vite-e2e.pid` kill/rm——机械 sed `logs/` 会漏掉无斜杠的 mkdir，须逐行核对）；333 行文案顺手修正为 `var/logs/tasks/{task_id}/{attempt_no}/`（原"logs/{ts}"表述陈旧）；顶部新增 `export PYTHONPYCACHEPREFIX="$SCRIPT_DIR/var/cache/pycache"`（已确认启用） | [run.sh](../../../../run.sh) | `./run.sh`（默认 all）后服务日志与 pid 落在 var/logs/，pyc 落在 var/cache/pycache |
| ruff 缓存位置【修改】 | **新建** `[tool.ruff]` 段配 `cache-dir = "var/cache/ruff"`（仓库当前无任何 ruff 配置；该值按 pyproject 所在目录解析，与 CWD 无关） | [pyproject.toml](../../../../pyproject.toml) | 根目录不再生成 .ruff_cache |
| pytest 缓存位置【修改】 | `[tool.pytest.ini_options]` 增配 `cache_dir = "var/cache/pytest"`（pytest 按 rootpath 解析，与 CWD 无关；正式入口 tests/run.py 本就 `-p no:cacheprovider`，此配置管住裸跑 pytest） | [pyproject.toml:95](../../../../pyproject.toml#L95) | 根目录不再生成 .pytest_cache |
| 测试 runner basetemp【修改】 | `temporary_root = PROJECT_ROOT / 'var/pytest'` → `PROJECT_ROOT / 'var/tmp/pytest'`（一行；mkdir 已带 parents） | [tests/run.py:80](../../../../tests/run.py#L80) | pytest 临时目录落 var/tmp/pytest，语义归位 |
| setuptools exclude 冗余清理【修改】 | 删除 `[tool.setuptools.packages.find]` exclude 中 `"logs*"、"results*"、"chroma_db*"` 三条（该行属 setuptools 而非 ruff；include 已限定 `AI*/backend*/db*`，此三条冗余，删除不影响打包语义） | [pyproject.toml:89](../../../../pyproject.toml#L89) | exclude 只保留有效条目 |
| .gitignore【修改】 | 删除 `chroma_db/`、`results/`、`logs/` 三行（`var/` 已覆盖） | [.gitignore](../../../../.gitignore) | 忽略规则单点化 |
| .dockerignore【修改】 | 删除语义陈旧的 `logs`、`results`、`chroma_db` 三行（第 10/11/13 行），保留 `var`（第 12 行） | [.dockerignore:10-13](../../../../.dockerignore#L10-L13) | 镜像构建排除项与新布局一致 |
| 环境变量示例【修改】 | `LIVEPROFIT_MEMORY_PATH=./var/data/chroma_db`（相对值由 resolver 按 PROJECT_ROOT 解析） | [.env:39](../../../../.env#L39)、[.env.example:52](../../../../.env.example#L52) | 与 resolver 默认值一致 |
| 文档与注释随迁【修改】 | 存活引用同步：README.md:142（logs/api.log 列表）、:203（LIVEPROFIT_MEMORY_PATH 默认值）、:253（目录树 chroma_db）；docs/knowledge/ai/板块层.md:15/78/98（logs/{ts} 热力图）；docs/knowledge/backend/数据库表结构.md:34（logs/backups → var/data/backups）；backend/migrations/versions/0007_drop_legacy_market_tables.py:6 注释（backups 路径 → var/data/backups）；docstring/注释类 AI/dataflows/market_features.py:27、AI/dataflows/providers/cn/tushare.py:3236、backend/modules/analysis/infrastructure/real_graph_factory.py:112、AI/utils/llm_callbacks.py:6、AI/utils/dataprovider_log.py:7、AI/utils/logs_reader.py:5,30、AI/sectorAgents/charts.py:5,103、AI/graph/trading_graph.py:443、AI/eventStudy/scheduler/app_scheduler.py:185、db/instrument/ingest/backfill.py:14、db/instrument/migration/migrate_legacy.py:8；调度资产 AI/eventStudy/scheduler/run_daily.bat:3、AI/eventStudy/scheduler/scheduler_setup.md:16/36/38/123；测试注释 tests/ai/graph/unit/test_run_log_dir.py:21,49、tests/backend/analysis/unit/test_real_graph_factory.py:72、tests/backend/analysis/contract/api/test_execution_logs_api.py:50；生成物（测试改动后跑 tests/index.py 重新生成）tests/catalog/ai/graph.json、tests/catalog/backend/analysis.json、docs/knowledge/test/测试详情/ai/graph.md、docs/knowledge/test/测试详情/backend/analysis.md；可选（历史语境或顺手更新）docs/experience/best-practices/ai/eventstudy-scheduler.md:8,13、docs/experience/pitfalls/ai/store-daily.md:54 | 上述文件 | 文档/注释与实际路径一致，无陈旧引用 |

## 四、详细设计

### 4.0 模块总览

| 维度 | 问题 | 方案概览 |
|------|------|---------|
| 目录迁移与时序 | `logs/`、`chroma_db/`、`results/` 三个运行时目录散落根级；服务运行中会持有旧日志 fd（[run.sh:211](../../../../run.sh#L211) nohup 重定向）；logs/ 内 70 项含 backups（pg 备份）与 quant_history（parquet 数据）两类非日志数据 | 先停服 → 复制迁移（点文件安全）→ 删空壳 → 改代码 → 启动验证，一次变更内完成；backups/quant_history 单独落位；回滚预案含 .env 手工回改与不可逆说明 |
| 路径常量与硬编码收敛 | 记忆路径 5 处硬编码绕过 env；resolve_run_log_dir 回退、eventStudy 调度器、回填/迁移脚本、logs_reader 共 9 处仍写根 logs/；results 与 chroma 路径按 CWD 解析 | resolver 单点化：backend 走 resolve_execution_logs_root；AI/db 层按 Path(__file__) 推导 PROJECT_ROOT 绝对化，全部与 CWD 无关 |
| run.sh 与工具缓存重定向 | run.sh 22 行 26 处 logs 字面量；ruff/pytest 缓存在根生成 | 词级替换 run.sh；新建 [tool.ruff] cache-dir 与 pytest cache_dir 重定向（两者官方语义均与 CWD 无关） |
| 测试、文档与静态配置同步 | test_real_graph_factory:101、test_run_log_dir、test_state_log_events:64 断言旧路径；README/knowledge 文档、0007 迁移注释、.dockerignore 残留旧目录名；验证命令 `bash run.sh start`/`python tests/run.py --level unit` 均不存在 | 断言与文档随常量更新；验证命令改为实际入口（`./run.sh`、`python -m tests.run --module ...`）；ruff 验收口径改为"输出不含 var/ 路径" |
| 遗留垃圾清理 | 根级 `.codex-joint-*.log`、`.pytest_tmp_quant/`（ACL 异常）；var 内 ~130M 残留（uv-cache 死副本、tmp、12 个 pytest-*、market-refresh-*、market-ingest.pid），多处 ACL Permission denied | 停服后统一删除，Windows 原生命令兜底 ACL 异常路径，附删除清单与磁盘前后对比 |

### 4.1 目录迁移与时序

#### 4.1.1 模块设计

迁移清单（同一变更内完成；logs 用 `cp -a` 保证点文件不漏，`mv logs/*` 的 bash 通配符不匹配 `.platform.pids`、`.vite-dev.pid` 等点文件）：

```bash
mkdir -p var/logs var/data/chroma_db var/data/quant_history var/data/backups var/results
cp -a logs/. var/logs/ && rm -rf logs            # 70 项全量迁入 var/logs/；cp -a 保点文件（.platform.pids/.vite-dev.pid），复制完成即整目录删除，无空壳校验环节
mv var/logs/backups   var/data/backups          # pg 备份审计数据单独落位（持久业务数据）
mv var/logs/quant_history var/data/quant_history # baostock parquet 行情数据单独落位
mv chroma_db/*        var/data/chroma_db/        # chroma.sqlite3 记忆库
mv results/*          var/results/               # {ticker}/analysis_logs/state_log.json
# mv 后显式校验（ls -la 确认无残留）再 rmdir 删除 chroma_db/、results/ 空壳
```

时序约束：
1. `./run.sh stop` 停全部平台服务（否则 nohup 日志 fd 仍指向旧文件路径）
2. 执行目录迁移
3. 改代码（4.2/4.3/4.4）
4. `./run.sh`（默认 all）启动后验证（见下）
5. 回滚预案：代码 `git revert` + 反向迁移；**`.env` 不在 git 跟踪内，需手工回改 `LIVEPROFIT_MEMORY_PATH` 原值 `./chroma_db`**；删除类操作不可逆（删除清单见 4.5.1，恢复只能靠重建）

#### 4.1.2 三方依赖能力评估

本模块不依赖外部库/API，纯文件系统操作。

#### 4.1.3 风险与验证方式

- 风险：服务未停干净时迁移日志（fd 悬空、新日志写回旧路径）；点文件漏迁（`cp -a` 已缓解，但迁移后应抽查 `.platform.pids` 等点文件已到位）导致迁移数据缺失
- 验证：`./run.sh` 启动后观察 `var/logs/api.log` 等新文件生成；API 执行日志页读取一个历史 task_id 验证迁移后仍可读；`ls` 根目录确认三个根级目录（logs/chroma_db/results）已删除

#### 4.1.4 文件变更清单

- **新建文件**：无（目录由运行时自动创建）
- **修改文件**：无（纯文件系统操作）
- **删除文件**：`logs/`、`chroma_db/`、`results/` 根级目录（迁移后删除）

### 4.2 路径常量与硬编码收敛

#### 4.2.1 模块设计

1. [AI/default_config.py](../../../../AI/default_config.py)：新增 `resolve_memory_path()` resolver（`_PROJECT_ROOT = Path(__file__).resolve().parents[1]`；env `LIVEPROFIT_MEMORY_PATH` 绝对值原样用、相对值按 PROJECT_ROOT 解析、未设时返回 `_PROJECT_ROOT/"var/data/chroma_db"`）；`memory_path` 字段（:38）改用之
2. [AI/stockAgents/utils/chromadb_config.py](../../../../AI/stockAgents/utils/chromadb_config.py)：35/42/54/81 四处 `path="./chroma_db"` → `path=str(resolve_memory_path())`（`from AI.default_config import resolve_memory_path`，无循环导入）
3. [AI/stockAgents/utils/memory.py:59](../../../../AI/stockAgents/utils/memory.py#L59)：同上
4. [backend/bootstrap/settings.py:82](../../../../backend/bootstrap/settings.py#L82)：`execution_logs_root: Path = Path("var/logs")`；解析仍走 [resolve_execution_logs_root](../../../../backend/bootstrap/settings.py#L151)
5. [AI/graph/trading_graph.py](../../../../AI/graph/trading_graph.py)：新增 `_PROJECT_ROOT = Path(__file__).resolve().parents[2]`；:657 改 `_PROJECT_ROOT/"var/results"/self.ticker/"analysis_logs"`；:76 回退分支改 `_PROJECT_ROOT/"var/logs"/run_ts`（platform_log_dir 注入分支原样保留）；:69-70 docstring 同步
6. [AI/utils/logs_reader.py:34](../../../../AI/utils/logs_reader.py#L34)：默认 `parents[2]/"var"/"logs"`（env `LIVEPROFIT_LOGS_DIR` 优先逻辑保留）；:5 docstring 同步
7. [AI/eventStudy/scheduler/app_scheduler.py](../../../../AI/eventStudy/scheduler/app_scheduler.py)：新增 `_PROJECT_ROOT = Path(__file__).resolve().parents[3]`；:47-48 `LOCK_PATH/ATTEMPTS_PATH`、:68 `_marker_path` 改 `_PROJECT_ROOT/"var/logs"/...`；模块 docstring（:8-23）同步
8. [AI/eventStudy/scheduler/daily_job.py:32](../../../../AI/eventStudy/scheduler/daily_job.py#L32)：`FileHandler("logs/event_study_daily.log")` → `_PROJECT_ROOT/"var/logs/event_study_daily.log"`；并在 app_scheduler 启动路径确保 `(_PROJECT_ROOT/"var"/"logs").mkdir(parents=True, exist_ok=True)`；另在 daily_job.py 的 basicConfig 前加同一行 mkdir（FileHandler 为模块级，run_daily.bat:5 直启 `python -m AI.eventStudy.scheduler.daily_job` 不经 app_scheduler 时 var/logs 缺失即导入期 FileNotFoundError，一行覆盖全部入口）
9. [db/instrument/ingest/backfill.py](../../../../db/instrument/ingest/backfill.py)：新增 `_PROJECT_ROOT = Path(__file__).resolve().parents[3]`；:57/58/460 路径常量改 `_PROJECT_ROOT/"var/logs/...`；:757 `Path("logs").mkdir(exist_ok=True)` 改为 `(_PROJECT_ROOT/"var"/"logs").mkdir(parents=True, exist_ok=True)`（单级 mkdir 在 var/ 不存在时 FileNotFoundError——新克隆/var 清理后直接跑脚本即触发）
10. [db/instrument/ingest/factor_day_backfill.py:34](../../../../db/instrument/ingest/factor_day_backfill.py#L34)：同上
11. [db/instrument/migration/migrate_legacy.py](../../../../db/instrument/migration/migrate_legacy.py)：:35 `CHECKPOINT_PATH` 改 `_PROJECT_ROOT/"var/logs/migrate_legacy_checkpoint.json"`；:440 `backup_dir` 改 `_PROJECT_ROOT/"var/data/backups"`
12. [.env:39](../../../../.env#L39)、[.env.example:52](../../../../.env.example#L52)：`LIVEPROFIT_MEMORY_PATH=./var/data/chroma_db`

#### 4.2.2 三方依赖能力评估

chromadb `PersistentClient(path=...)`、`logging.FileHandler` 均接受绝对路径，仅换参数，无能力变化。

#### 4.2.3 风险与验证方式

- 风险：记忆路径改错会导致个股分析读不到历史记忆（静默降级为无记忆，不易察觉）；`chroma.sqlite3` 迁移后路径不对会新建空库
- 验证（agent 侧，不触发真实 LLM）：
  - `python -m pytest tests/backend/analysis/unit/test_real_graph_factory.py tests/ai/graph/unit/test_run_log_dir.py tests/ai/graph/unit/test_state_log_events.py` 通过
  - 从 `backend/` 子目录 CWD 跑一个只构造 resolver 的检查：`python -c "from AI.default_config import resolve_memory_path; print(resolve_memory_path())"` 输出 `.../var/data/chroma_db`（与 CWD 无关）
  - 个股分析读记忆的端到端验证**仅限用户手动执行**（触发真实 LLM/toolkit/embedding 门禁），观察点：`var/data/chroma_db/chroma.sqlite3` mtime 更新且根目录未新生成 chroma_db/

#### 4.2.4 文件变更清单

- **新建文件**：无
- **修改文件**：
  - `AI/default_config.py`（resolver 新增 + 字段改用）
  - `AI/stockAgents/utils/chromadb_config.py`（4 处硬编码 → resolver）
  - `AI/stockAgents/utils/memory.py`（1 处硬编码 → resolver）
  - `AI/utils/logs_reader.py`（默认根 + docstring）
  - `AI/graph/trading_graph.py`（results 路径 + 回退 + docstring）
  - `AI/eventStudy/scheduler/app_scheduler.py`（3 处路径 + docstring）
  - `AI/eventStudy/scheduler/daily_job.py`（FileHandler）
  - `backend/bootstrap/settings.py`（execution_logs_root 常量）
  - `db/instrument/ingest/backfill.py`（4 处）
  - `db/instrument/ingest/factor_day_backfill.py`（1 处）
  - `db/instrument/migration/migrate_legacy.py`（2 处）
  - `.env`、`.env.example`（记忆路径）
- **删除文件**：无

### 4.3 run.sh 与工具缓存重定向

#### 4.3.1 模块设计

1. [run.sh](../../../../run.sh)：按单词 `logs` 全量替换为 `var/logs`，共 **22 行 26 处**（150 `PLATFORM_PID_FILE`、202 `mkdir -p logs`、211/311/414 nohup 重定向、215/236 错误提示、309-312 vite pid、332-346 日志查看与 stop_all、413-432 e2e dev 日志与 pid kill/rm、310 `mkdir -p logs` 等）；**替换后逐行核对 26 处**（机械 sed `logs/` 会漏 202/310 两处无斜杠的 mkdir，漏改会导致 nohup 重定向目标目录不存在）；333 行文案修正为 `var/logs/tasks/{task_id}/{attempt_no}/`；顶部新增 `export PYTHONPYCACHEPREFIX="$SCRIPT_DIR/var/cache/pycache"`（已确认启用）
2. [pyproject.toml](../../../../pyproject.toml)：
   - **新建** `[tool.ruff]` 段：`cache-dir = "var/cache/ruff"`（ruff 相对 cache-dir 按 pyproject 所在目录解析，与 CWD 无关）
   - `[tool.pytest.ini_options]` 增配 `cache_dir = "var/cache/pytest"`（pytest 按 rootpath 解析，与 CWD 无关）
3. [tests/run.py:80](../../../../tests/run.py#L80)：`temporary_root = PROJECT_ROOT / 'var/pytest'` → `PROJECT_ROOT / 'var/tmp/pytest'`（一行；:81 mkdir 已带 parents，自动建目录）
4. 迁移后删除根级 `.ruff_cache/`、`.pytest_cache/`、`__pycache__/`

#### 4.3.2 三方依赖能力评估

- ruff `cache-dir`：官方配置项（0.16.7 支持）；相对值按 pyproject 所在目录解析（PR #7962 起），落点确定
- pytest `cache_dir`：官方配置项（9.1.1 源码 `_pytest/cacheprovider.py:141` 按 `config.rootpath` 解析），落点确定
- `PYTHONPYCACHEPREFIX`：CPython 标准环境变量，仅改变字节码缓存落点，无语义影响

#### 4.3.3 风险与验证方式

- 风险：run.sh 26 处替换遗漏（尤其无斜杠 mkdir）导致目录不存在、nohup 重定向失败或 stop 读不到 pid
- 验证：`./run.sh` 启动后 `ls var/logs/` 出现服务日志与 `.platform.pids`；`./run.sh stop` 能读到 pid 正常停服；`ruff check .` 后 `var/cache/ruff/` 生成、根目录无 `.ruff_cache`；裸跑一次 `python -m pytest tests/backend/analysis/unit/test_real_graph_factory.py` 后 `var/cache/pytest/` 生成、根目录无 `.pytest_cache`；run.sh 启动的服务运行后 `var/cache/pycache/` 生成、根目录无 `__pycache__` 再生

#### 4.3.4 文件变更清单

- **新建文件**：无
- **修改文件**：`run.sh`（22 行 26 处 + PYTHONPYCACHEPREFIX export）、`pyproject.toml`（[tool.ruff] 新建 + cache_dir）、`tests/run.py`（basetemp 一行）
- **删除文件**：`.ruff_cache/`、`.pytest_cache/`、根 `__pycache__/`

### 4.4 测试、文档与静态配置同步

#### 4.4.1 模块设计

1. 测试断言（四个文件，见三、设计概览 backend 测试表）：`test_real_graph_factory.py:101`、`test_run_log_dir.py`（50/56/62/69）、`test_state_log_events.py:64`、`test_test_selection.py:122`
2. [pyproject.toml:89](../../../../pyproject.toml#L89)：`[tool.setuptools.packages.find]` exclude 删除 `"logs*"`、`"results*"`、`"chroma_db*"`（include 已限定 `AI*/backend*/db*`，冗余；该行属 setuptools 而非 ruff）
3. [.gitignore](../../../../.gitignore)：删除 `chroma_db/`、`results/`、`logs/` 三行（`var/` 已覆盖；`*.log` 规则保留）
4. [.dockerignore:10-13](../../../../.dockerignore#L10-L13)：删除 `logs`、`results`、`chroma_db` 三行，保留 `var`
5. 文档与注释随迁（见三、设计概览"文档与注释随迁"行）：README.md 三处、板块层.md 三处、数据库表结构.md 一处、0007 迁移注释一处、docstring 类七文件九处

#### 4.4.2 三方依赖能力评估

本模块不依赖外部库/API。

#### 4.4.3 风险与验证方式

- 风险：exclude 删错导致 setuptools 打包变化（语义不变但需确认）；文档随迁漏改导致陈旧引用
- 验证：
  - 三个测试文件 `python -m pytest tests/backend/analysis/unit/test_real_graph_factory.py tests/ai/graph/unit/test_run_log_dir.py tests/ai/graph/unit/test_state_log_events.py` 通过
  - 关联回归（规范入口 `python -m tests.run --module <模块> --level unit`，具体模块实现时按测试选择器确定，覆盖 backend.analysis 与 ai.graph）
  - `ruff check .` 输出中 **不含 var/ 路径**（判定口径：var/ 相关错误计数为 0；仓库当前基线 2943 errors，不承诺全量零错）
  - 根目录纯净检查：启动服务、跑测试后 `ls` 确认根目录未再生 `logs/`、`chroma_db/`、`results/`；`git status` 无 var/ 以外的新增未跟踪项（var/ 已被 .gitignore 忽略，git status 看不到 var/ 下新文件，"var/ 无新文件"检查恒真且方向错误，不做）
  - 分析链路端到端（跑一次分析任务，验证产物落 var/runs、执行日志落 var/logs/tasks/、记忆读写 var/data/chroma_db）**仅限用户手动执行**（触发真实 LLM/toolkit 门禁）

#### 4.4.4 文件变更清单

- **新建文件**：无
- **修改文件**：`tests/backend/analysis/unit/test_real_graph_factory.py`、`tests/ai/graph/unit/test_run_log_dir.py`、`tests/ai/graph/unit/test_state_log_events.py`、`tests/backend/platform/unit/test_test_selection.py`、`pyproject.toml`、`.gitignore`、`.dockerignore`、`README.md`、`docs/knowledge/ai/板块层.md`、`docs/knowledge/backend/数据库表结构.md`、`backend/migrations/versions/0007_drop_legacy_market_tables.py`（注释）、`AI/dataflows/market_features.py`（注释）、`AI/dataflows/providers/cn/tushare.py`（注释）、`backend/modules/analysis/infrastructure/real_graph_factory.py`（docstring）、`AI/utils/llm_callbacks.py`（docstring）、`AI/utils/dataprovider_log.py`（docstring）、`AI/sectorAgents/charts.py`（docstring）、`AI/graph/trading_graph.py`（:443 注释）、`AI/eventStudy/scheduler/app_scheduler.py`（:185 消息）、`AI/utils/logs_reader.py`（:30 docstring）、`db/instrument/ingest/backfill.py`（:14 docstring）、`db/instrument/migration/migrate_legacy.py`（:8 docstring）、`AI/eventStudy/scheduler/run_daily.bat`、`AI/eventStudy/scheduler/scheduler_setup.md`、`tests/backend/analysis/contract/api/test_execution_logs_api.py`（注释）、`tests/catalog/ai/graph.json`（生成）、`tests/catalog/backend/analysis.json`（生成）、`docs/knowledge/test/测试详情/ai/graph.md`（生成）、`docs/knowledge/test/测试详情/backend/analysis.md`（生成）、`docs/experience/best-practices/ai/eventstudy-scheduler.md`（可选）、`docs/experience/pitfalls/ai/store-daily.md`（可选）
- **删除文件**：无

### 4.5 遗留垃圾清理

#### 4.5.1 模块设计

删除清单（与迁移同一次变更内执行；`git status` 曾对 `.pytest_tmp_quant` 报 Permission denied，多个 var 残留子目录同样 ACL 异常，删除与统计均用 Windows 原生命令兜底）：

| 位置 | 条目 | 依据 |
|------|------|------|
| 根 | `.codex-joint-acceptance.log`、`.codex-joint-remaining.log` | 9-27 Codex 验收一次性日志，全仓库零引用 |
| 根 | `.pytest_tmp_quant/` | 空目录、ACL 异常，全仓库零引用；用 `cmd //c rmdir /s /q` 兜底 |
| var | `uv-cache/`（51M）、`tmp/`（52M，内含 pytest 与 uv-cache 旧副本） | 当前 `uv cache dir` 实际指向 `C:\Users\qiyanqiao\AppData\Local\uv\cache`，这两份为历史配置死副本；删除 `tmp/` 后新 basetemp `var/tmp/pytest` 由 tests/run.py 自动重建 |
| var | `pytest/`、`pytest-tmp/`、`pytest-cache-ingestion/`、`pytest-factor-final/`、`pytest-factor-final-full/`、`pytest-factor-full-review/`、`pytest-factor-review/`、`pytest-review/`、`pytest-review-all/`、`pytest-review-fast/`、`pytest-review-final/`、`pytest-tmp-ingestion/`（共 12 个） | 历次测试 basetemp/任务残留；basetemp 迁 var/tmp/pytest 后此目录不再被重建，整目录删除 |
| var | `market-refresh-implementation/`（11M）、`market-refresh-review/`（2.9M）、`market-refresh-source-poc.json`、`market_refresh_source_poc.json`、`market_refresh_source_poc.py`、`test-file-index/`、`test-layout-refactor/` | 9-22~9-23 行情补采与测试选择器任务的中间产物 |
| var | `market-ingest.pid` | 过期 pid：现版 [run.sh](../../../../run.sh) 用 `logs/.platform.pids`（迁后 `var/logs/.platform.pids`），此引用只存在于待删的 `var/market-refresh-implementation` 旧 run.sh 快照中 |

#### 4.5.2 三方依赖能力评估

本模块不依赖外部库/API。

#### 4.5.3 风险与验证方式

- 风险：误删活跃数据——删除清单逐项已核实引用（`var/runs`、`var/research` 不在此清单）；ACL 异常路径（var/tmp/pytest、var/pytest/<uuid>、var/test-file-index/*、var/test-layout-refactor/*、var/market-refresh-implementation/pytest-tmp 实测 Permission denied）用 `cmd //c rmdir /s /q` 或先 `icacls` 修复再删
- 验证：删除后 `ls` 根目录仅剩清单允许的条目；`du -sh var/` 前后对比（预期释放 ~130M；ACL 异常目录的统计有误差，以实际删除结果为准）；`git status` 无意外变化；`./run.sh` 全服务正常

#### 4.5.4 文件变更清单

- **新建文件**：无
- **修改文件**：无
- **删除文件**：上述清单全部条目

## 五、已确认决策

1. **保留 `var/` 作为唯一运行时卷**（2026-10-02 对话确认）：`var` 源自 FHS `/var`（variable 可变数据），项目内已有自洽定义（.gitignore 注释"平台产物卷"、settings 两个根目录、docker 卷命名），语义合格；对比 `data/`（易与源码数据混淆）、`tmp/`（暗示可随时清空，与 var/runs 的 90 天保留审计语义冲突）后维持不变
2. **chroma 不放入 `db/`**（2026-10-02 对话确认）：`db/` 是被 git 跟踪的源码包（pyproject `include = ["db*"]`），运行期可变数据混入会造成打包污染、gitignore 例外、docker 往源码目录挂卷等冲突；chroma 属 FHS `/var/lib` 语义，落 `var/data/chroma_db/`
3. **目标布局与清理范围**：上一轮已向用户展示目标树与三类删除建议，用户未提出异议并指示"写个方案"
4. **logs/backups 与 logs/quant_history 拆分落位**（R1 修复时定）：backups 属审计备份，quant_history 属行情数据——随迁 var/logs/ 会稀释"logs"语义，拆分后引用同步成本仅 3 处，落 `var/data/backups/` 与 `var/data/quant_history/`
5. **var/ 内按性质四层分组（7 子目录）**（2026-10-02 用户拍板）：logs=纯日志、data=持久业务数据（chroma_db/quant_history/backups）、cache=可重建缓存（ruff/pytest/pycache）、tmp=临时文件（pytest basetemp）；runs/research/results 三个业务产物保持顶层；对应增量改动：tests/run.py:80 basetemp 迁 var/tmp/pytest、pytest cache_dir 改 var/cache/pytest、backups 目标改 var/data/backups
6. **启用 `PYTHONPYCACHEPREFIX`**（2026-10-02 用户拍板）：run.sh 顶部一行 export `PYTHONPYCACHEPREFIX="$SCRIPT_DIR/var/cache/pycache"`，根目录 __pycache__ 永不再生

（无未决阻塞项。）
