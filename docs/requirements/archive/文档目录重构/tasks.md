# 拆任务清单

| # | 任务 | 状态 | 依赖 |
|---|------|------|------|
| T0 | 链接基线脚本落盘（迁移前） | 已完成 | — |
| T1 | mkdir + git mv 迁移（知识文档 6 + 方案 3 + archive 30 + 模板 2） | 已完成 | T0 |
| T2 | 3 任务骨架（×7 文件）+ 8 文件模板 + 骨架说明 + 模板路径指令修正 | 已完成 | T1 |
| T3 | 规则 A–K 链接修复（docs 内全部文档） | 已完成 | T1 |
| T4 | CLAUDE.md 语义重写（§3.4 九点） | 已完成 | T1 |
| T5 | index.md 三桶导航 + 新建 knowledge/frontend/前端平台.md | 已完成 | T1 |
| T6 | README/run.sh（规则 E）+ 代码注释 5 处（规则 I）+ 11 条死链（规则 F）+ memory 3 文件（规则 H） | 已完成 | T1 |
| T7 | 集合差验证 + 归档层级演练 + 按路径显式暂存（禁 git add -A） | 已完成 | T2–T6 |

## T0 链接基线脚本落盘

- **内容**：Python 脚本遍历 docs/**/*.md 解析 `](相对路径)`（排除 http/#/锚点），解析失败清单落盘。
- **验收**：`python /tmp/linkcheck.py save /tmp/link_baseline.txt` 输出基线（实测 214 条存量断链）。✅ 已完成

## T1 目录与 git 迁移

- **内容**：mkdir 三桶目录；`git add -u docs/plans`；git mv ×6 知识文档（数据库表结构.md 未跟踪用 mv）、×3 方案（未跟踪用 mv，K线方案只移动不重写内容）、docs/done → requirements/archive、docs/template → requirements/templates（含 2 个改名）；rmdir docs/plans、docs/tasks。
- **验收**：`ls docs/` = index.md + knowledge/ + memory/ + requirements/；git status 无意外文件。✅ 已完成

## T2 任务骨架与模板

- **内容**：3 任务 × 7 骨架文件（README/tasks/log/decisions/issues/result/retrospective，按模板初始化）；新建 6 个模板（README/log/decisions/issues/result/retrospective）+ 骨架说明.md；修正 2 个改名模板的路径指令（规则 J）。
- **验收**：templates/ 下 8 个 *.模板.md + 骨架说明.md 齐全；3 任务文件夹各含 plan.md + 7 骨架文件；本清单（tasks.md）已按模板填写。

## T3 规则 A–K 链接修复（docs 内）

- **内容**：按 plan.md §3.2 规则表执行：A（index.md 13 处）、B（archive 20 文件 26 链接行 + 25 行 ../../ 根链接 + ../template 2 处）、C（knowledge/ai 三文档）、D（knowledge/backend 两文档 + 产品需求分析.md 按 3 层/2 层两套）、K（3 个进行中方案自身链接）、J（模板已随 T2 完成）。
- **验收**：linkcheck diff 无新增断链；grep 旧路径（`docs/(done|plans|template)`）于 docs/requirements、docs/knowledge 为 0 命中。

## T4 CLAUDE.md 语义重写

- **内容**：plan.md §3.4 九点：目录树、定位表、工作流程、Plans+Tasks 约定合并为任务文件夹约定（含归档上移一层）、评审机制锚定 plan.md、62 处路径、:339 死链、Code Review 规则、自我更新规则。
- **验收**：`grep -rnE 'docs/(done|plans|template)(/|\b)' CLAUDE.md` 0 命中；重读 docs 节与新结构一致。

## T5 index.md 三桶导航 + 前端平台.md

- **内容**：index.md 导航表改 requirements/knowledge/memory 三桶分组（knowledge 行保留 4 文档直达链接）、文中链接（规则 A+F②）、:35 约定句改写；新建 knowledge/frontend/前端平台.md（按 plan.md §3.3 大纲）。
- **验收**：三桶导航可点；前端平台.md 七章齐全且来源链接可达。

## T6 根级与代码注释、死链、memory

- **内容**：README.md 2 处 + run.sh 2 处（规则 E）；代码注释 5 处两段式改写（规则 I，.py/.ts/.sh/.sql 全仓 grep 兜底）；11 条现存死链（规则 F）；memory 3 文件 4 处裸路径（规则 H）。
- **验收**：`grep -nE 'docs/(市场层|板块层|个股层|API契约|数据库表结构)\.md' CLAUDE.md README.md run.sh` 0 命中；`grep -rnE 'docs/(plans|done)'` 于 .py/.sql 0 命中；`grep -rnE 'docs/(done|plans|template)(/|\b)' docs/memory` 0 命中。

## T7 全量验证与暂存

- **内容**：linkcheck diff 取集合差（无新增断链、11 条死链已修复）；归档层级演练（示例任务文件夹 → archive/ → 复查）；`git status --porcelain` 与迁移前快照 diff；按路径显式 `git add`（含新建文件），禁 `git add -A`。
- **验收**：集合差为空（除预期修复项）；归档演练无新增断链；暂存清单仅含预期文件。
