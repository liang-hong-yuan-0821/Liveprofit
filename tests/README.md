# 测试目录与运行约定

正式测试采用 **领域 → 业务模块 → 测试层级 → 功能文件**。模块清单、被测代码范围和显式关联回归保存在 [suites.json](suites.json)。

```text
tests/
├── backend/<业务模块>/{unit,integration,contract}/
├── frontend/<业务模块>/{unit,integration,contract}/
├── ai/<业务模块>/{unit,integration,contract}/
├── data/<业务模块>/{unit,integration,contract}/
├── e2e/<业务流程>/{api,ui}/
├── support/{python,frontend}/
├── conftest.py
├── suites.json
├── catalog.json          # 文件级摘要与模块详情引用
├── catalog/<领域>/<模块>.json  # 自动生成的模块详情
├── index.py              # 静态生成、查询及一致性检查
└── run.py
```

只建立实际需要的目录。backend 的 API/router 和业务 Worker 跟随所属模块，目录较大时在测试层级内再分 `api/`、`workers/` 或稳定子功能。通用启动/配置/Broker 归 `backend/platform`，平台迁移归 `backend/migrations`。公共数据生产、转换、存储归 `data`，backend/AI 的数据消费行为仍归各自领域。

- `unit`：独立逻辑、算法、状态或组件；使用 fake/mock 隔离资源。
- `integration`：模块协作或实际隔离的数据库、Redis、子进程；目录不代表允许访问生产资源。
- `contract`：API、事件、结构化输出等稳定边界。
- `e2e`：完整业务流程，`api` 为 Python 链路测试，`ui` 为 Playwright 浏览器测试。
- `support`：共享 fixture/fake/builder；模块专用辅助代码就近放在模块的 `support/`，专用样本放 `fixtures/`。测试文件之间不导入对方的辅助函数或 fixture。

## 选择与运行

先按需求或代码查 [文件级索引](../docs/knowledge/test/测试索引.md)，读取候选文件的断言和 fixture，再决定修改与运行范围：

```text
python -m tests.index --query "行情刷新 复用" --module backend.market_data
python -m tests.index --source backend/modules/market_data/application/refresh_service.py
python -m tests.index --source frontend/src/modules/market --json --limit 0
python -m tests.index --module backend.market_data --details
```

`--query` 用空格分隔关键词，全部匹配；也搜索场景名称和说明。`--source` 支持仓库内现存代码文件或目录，反查维护者登记的 `covers`。`--module` 是精确模块名；默认展示前 10 个候选，`--limit 0` 展示全部。查询无结果返回退出码 1，无效输入返回 2；继续查所属模块源码，不能把无结果当成没有测试。默认文本和 JSON 只输出文件用途、环境、被测文件和详情入口；加 `--details` 展开场景名称、源码行号及显式关联回归。查询仍搜索完整场景内容，摘要输出不缩小匹配范围。

索引解析源码和注释，不导入或执行测试、连接数据库、启动浏览器或调用模型。定位结果不证明断言覆盖充分，也不授权执行真实依赖。

在项目根、使用项目 Python 环境运行：

```text
python -m tests.run --list
python -m tests.run --module backend.analysis --related --list
python -m tests.run --module backend.analysis --level unit
python -m tests.run --module frontend.market
python -m tests.run --module data.providers --related --list
```

`--related` 加入索引中明确登记的关联模块；它不会自行证明整个调用链已覆盖。任务仍须根据共享 schema、触发器、权限、锁序、摘要算法等变化补齐必要门禁。`--level` 仅是筛选，不免除必验项。新增或调整模块时同步维护索引。

Python 默认排除需 DB/Redis、真实 LLM/toolkit/memory、外部源及 E2E 的用例，并报告 deselected；全被排除返回 pytest 的非成功退出码。动态请求真实 fixture 也有额外门禁。隔离资源用例须先读具体测试和 fixture，再显式使用 `--allow-db`；标记或目录不能替代连接隔离核查。真实 LLM/toolkit 及外部源只允许用户手动通过 `--allow-live` / `--allow-external` 启用。真实依赖与单元/集成层级正交，不建立 `live/` 平行目录。

前端依赖保留在 `frontend/node_modules`，测试文件集中在此目录；以下命令继续有效：

```text
pnpm --dir frontend test
pnpm --dir frontend test:watch
pnpm --dir frontend typecheck
pnpm --dir frontend build
pnpm --dir frontend e2e --list
```

前端测试启动器固定以项目根为工作目录。Vitest 独立配置在 `frontend/vitest.config.ts`，不改变应用的 Vite 根目录。Playwright 默认排除会创建真实分析任务的 `analysis_flow/ui/task-flow.spec.ts`；只有用户手动设置 `LIVEPROFIT_ALLOW_LIVE_E2E=1` 才会包含。浏览器的真实应用、账户、LLM及数据环境仍须独立认证；`--list` 只收集，不执行。

## 功能维护与 bug 闭环

正式文件顶部说明是文件级索引的唯一维护来源。Python 用 `#`，TypeScript 用 `//` 注释同样的 JSON，放在模块 docstring 或 import 之前：

```python
# test-catalog-begin
# {
#   "purpose": "行情刷新任务复用与并发领取",
#   "keywords": ["行情刷新", "复用", "并发", "领取"],
#   "covers": ["backend/modules/market_data/application/refresh_service.py"],
#   "environment": ["db", "redis"]
# }
# test-catalog-end
```

- `purpose`：准确说明该文件验证的功能；`keywords` 包含业务说法和必要技术名称。
- `covers`：维护者阅读调用和断言后登记实际被测文件，包括相关 SQL、schema、接口或测试工具；使用仓库相对路径和 `/`，不得用整个模块目录代替文件级登记。引用存在不等于已验证全部行为，新增间接消费路径须按实际场景补充。
- `environment`：允许 `local`、`db`、`redis`、`real_llm`、`external_data`、`browser`、`app`。混合文件登记所需环境的并集；标签提示依赖和执行门禁，不改变 conftest/Playwright 的实际许可规则，仍须核查具体 fixture。
- 可选 `related_tests`：显式关联的其他正式测试文件；模块级关联从 `suites.json` 生成，避免重复维护。

场景从 Python 测试函数/方法及其 docstring、TS 静态测试标题或模板提取，不展开参数化组合；动态标题或工具无法提取的声明须阅读源码。新增、删除或修改测试后执行：

```text
python -m tests.index
python -m tests.index --check
```

生成 `tests/catalog.json` 文件级摘要、`tests/catalog/<领域>/<模块>.json` 模块详情、`docs/knowledge/test/测试索引.md` 模块导航及 `docs/knowledge/test/测试详情/<领域>/<模块>.md`。agent 先查询摘要，按候选模块读取详情，再检查实际断言和 fixture，不需通读全部详情。各模块详情保存完整场景，根摘要不重复存储场景。

`--check` 验证全部正式文件的说明、模块归属、被测文件、关联文件与生成物一致性，失败返回非零退出码，可用于本地收尾或 CI。生成物不手工修改。模块删除或改名时，检查会报告遗留生成文件，重新生成清理带生成标记的旧详情；同目录手写文件不自动清理。本次已有文件的初始登记结合本地引用、函数/场景说明核对建立，后续维护者按实际变化修订。

存量功能修改优先更新、补充已有功能测试；新增独立功能才新增文件。回归用例按行为命名并永久保留。复杂 bug 的临时复现/诊断脚本可放 `backend/bugs/<问题标识>/`；确认正式测试能检测修复前错误、修复后通过，必要边界及关联回归完成，且样本已纳入正式 fixture、测试不再依赖临时目录后，清理临时脚本。数据修复或迁移脚本按执行/回滚/审计要求保留。

历史数据源基准输出保存在 `data/providers/fixtures/benchmarks`；手动实测工具位于 `support/python/manual`，不由 pytest 自动收集。用户手动调用 `python -m tests.support.python.manual.run_agent` 或 `python -m tests.support.python.manual.benchmark_tushare_provider`；新的基准输出写入 `var/test-benchmarks`。任务验收证据仍保存在对应任务的 `result.md`。
