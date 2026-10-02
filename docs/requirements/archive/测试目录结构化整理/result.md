# 验收证据

## T1 正式测试统一布局与安全选择入口

**结论：本次测试迁移和选择入口的软件验收 PASS；关联业务回归有 13 项存量失败。** 本结论不代表业务整体、DB 集成、端到端、部署或生产认证。未执行真实 LLM/toolkit/外部源、数据库写入或实际浏览器场景。

范围：正式测试迁移、导入/共享辅助代码/fixture/路径随迁、前端配置、模块索引、资源门禁、AGENTS 规范及 bug 脚本生命周期。按用户要求直接实施，免写技术方案与方案评审。

### 保全与资源核查

- 原 373 个文件均有明确落点；原 276 个 Python 测试文件的 2309 个测试函数、7914 条原生 assert 和 50 个前端测试文件全部保留。两个 AI 混合文件拆分 unit/integration，原函数身份合并核对；三个共享断言场景抽成 helper 后对 helper AST 核对；日期属性的等价读取规范化后核对。报告 errors 为空。新增运行门禁与 CLI 环境回归另计，不混入保全统计。
- 迁移映射：[migration.json](attachments/migration.json)、[split-map.json](attachments/split-map.json)、[helper-map.json](attachments/helper-map.json)；保全：[preservation.json](attachments/preservation.json)。原件字节及开始前 dirty 清单保留于忽略目录 var/test-layout-refactor。
- 根 conftest 默认按 fixture、目录和标记排除真实服务；真实 fixture 和已登记 DB fixture 的动态请求另设门禁。API 契约局部 fixture 指向 liveprofit_contract_test / Redis 11；量化执行局部 fixture 仍指向 liveprofit_quant_exec_test；instrument 和 event-study 保留各自专用测试库。CLI 环境新增固定隔离库名校验与显式子进程传递。
- --setup-plan 仅确认 fixture 继承与绑定，不执行 fixture，不证明 DB 实测通过。数据/量化执行/API fixture：[fixture-plan.txt](attachments/fixture-plan.txt)；AI 拆分后的真实 DB fixture：[ai-fixture-plan.txt](attachments/ai-fixture-plan.txt)。
- 原目录中剩余内容仅为 pycache；核定绝对路径均在仓库内且无源文件后清理。没有覆盖、还原或修改本任务之外的业务代码。

### 实际命令与结果

以下命令从项目根执行，前端原生命令注明工作目录。不累计重复参数化用例数。

| 检查 | 实际命令 | 结果与证据 |
|---|---|---|
| 保全 | .venv/Scripts/python.exe var/test-layout-refactor/catalog.py | 原身份及断言保留，errors=[]；上述 JSON |
| Python 收集 | .venv/Scripts/python.exe -m pytest --collect-only -q -k "not integration" -p no:cacheprovider | 该检查时 2134/3072 collected，938 deselected，无导入错误；其后新增一个 launcher 用例由下方定向测试覆盖。[日志](attachments/python-collect-final.txt) |
| 广泛关联 mock 回归 | .venv/Scripts/python.exe -m pytest tests/backend/analysis/unit tests/backend/market_data/unit tests/backend/quant_strategy/unit tests/backend/quant_research/unit tests/backend/investment_workspace/unit tests/ai/graph/unit tests/ai/screening/unit tests/ai/position/unit tests/data/providers/unit tests/data/instrument/unit tests/data/ingest/unit tests/data/transforms/unit -q -k "not integration" -p no:cacheprovider --basetemp var/test-layout-refactor/pytest_run_01 | 1606 passed，11 failed；原件基线同 11 项失败。[回归](attachments/python-regression.txt)、[原件基线](attachments/python-baseline.txt) |
| 基线重放 | .venv/Scripts/python.exe var/test-layout-refactor/baseline.py | 只选上述失败节点，以迁移前原件运行；11 failed，与迁移后一致 |
| 复核修正／事件研究 mock | .venv/Scripts/python.exe -m pytest tests/backend/platform/unit/test_test_selection.py tests/data/instrument/unit/test_test_environment.py tests/ai/event_study/unit -q -k "not integration" -p no:cacheprovider --basetemp var/test-layout-refactor/review_delta_01 | 196 passed，1 个调度器失败；原件同节点失败。[定向](attachments/review-delta.txt)、[原件](attachments/scheduler-baseline.txt) |
| 最终运行门禁／CLI 环境 | .venv/Scripts/python.exe -m pytest tests/backend/platform/unit/test_test_selection.py tests/data/instrument/unit/test_test_environment.py -q -k "not integration" -p no:cacheprovider --basetemp var/test-layout-refactor/policy_final | 18 passed，不访问 DB。[日志](attachments/policy-final.txt) |
| Python 模块入口 | .venv/Scripts/python.exe -m tests.run --module backend.analysis --level unit | 121 passed；首次缺少临时父目录的问题已修复并复验。[日志](attachments/selector-python-final.txt) |
| 前端模块入口 | .venv/Scripts/python.exe -m tests.run --module frontend.market --level unit | 4 文件、32 passed。[日志](attachments/selector-frontend.txt) |
| 前端全量 | pnpm test（cwd=frontend） | 50 文件，380 passed、2 failed；其中迁移引起的静态源路径问题已修复，另一个为存量行情参数契约失败。[日志](attachments/frontend-tests.txt) |
| 前端修正复验 | node frontend/test-runner.mjs vitest run tests/frontend/analysis/integration/pages/tasks/AiTasksPage.test.tsx | 9 passed；修正不涉及共享配置，复用其余通过证据。[日志](attachments/frontend-delta.txt) |
| 前端原件基线 | .venv/Scripts/python.exe var/test-layout-refactor/extra_baseline.py（最终前端探针）；原 QuantExecutionPanel.test.tsx 与 utils.tsx 在原路径临时恢复，node frontend/node_modules/vitest/vitest.mjs run --config frontend/baseline-vitest.config.ts，随后清理临时文件 | 3 passed、同一行情参数用例 failed；原件断言五参数，当前实际多出 cache_only。[日志](attachments/frontend-baseline.txt) |
| 调度器原件基线 | .venv/Scripts/python.exe -m pytest var/test-layout-refactor/baseline-tests/test_app_scheduler.py::test_start_registers_jobs_and_startup_check_thread -q -p no:cacheprovider --basetemp var/test-layout-refactor/scheduler_baseline | 1 failed；原件期待注册旧任务，当前默认禁用旧调度器。[日志](attachments/scheduler-baseline.txt) |
| 类型／构建 | pnpm typecheck；pnpm build（cwd=frontend） | 均 PASS，保留既有大包体警告。[类型](attachments/frontend-typecheck.txt)、[构建](attachments/frontend-build.txt) |
| 浏览器发现 | node frontend/test-runner.mjs playwright test --list | 21 tests / 3 files；默认排除创建真实分析任务的 task-flow 2 场景，不执行浏览器。[日志](attachments/playwright-collect.txt) |
| 资源 fixture 绑定 | .venv/Scripts/python.exe -m pytest tests/data/instrument/integration/test_db.py tests/backend/quant_strategy/integration/execution/test_execution.py tests/backend/analysis/contract/api/test_analysis_tasks.py --allow-db --setup-plan -q -p no:cacheprovider；同命令检查 tests/ai/event_study/integration/test_predictor.py 与 test_event_routing.py | 均 no tests ran；仅 setup-plan，未执行任何 DB fixture |

### 复核与后续门禁

独立 Code Review R1 修复后，R2 delta 及收尾定向补充均 PASS，无待修 finding，详见 [复核记录](attachments/code-review.md)。发现的迁移新增失败均修复，存量失败已逐项核对，不据此放行相关业务；具体治理组见 [issues.md](issues.md)。

DB/Redis 集成、真实 LLM/外部源和实际 E2E 保持未执行，使用前仍须按测试规则读取 fixture、核隔离/认证与用户授权。目录整理不能替代其后续验收。

任务代码及文档已完成并归档。**未自动提交**：开始前已有大量未提交业务修改，部分迁移测试也是存量修改或未跟踪文件，且依赖未提交业务实现；将这些测试一并暂存会混入其他任务内容，只提交迁移子集则会形成不完整版本。保留全部工作区变更及迁移证据，提交留待现有业务改动边界整理完成，未执行 git add -A、还原或业务修补。
