# 文档目录重构方案

> **状态**：待确认（2026-09-14，R1 全量评审完成：1 blocker + 8 major + 10 minor 已全部修复；用户两轮修订——requirements/ 桶名 + tasks.md 骨架文件——已全部同步；按新评审触发规则不再走 R2/R3）
> **进度**：3/6 步骤（方案起草 + 用户拍板结构 + R1 修复完成）
> **下一步**：用户确认后任务分解
> **关联文档**：[数据库表结构(../../../knowledge/backend/数据库表结构.md)（本方案将其移入 knowledge/backend/）｜[指数K线图交互优化方案](../../指数K线图交互优化/plan.md)（迁移为任务文件夹，链接随迁）

---

## 一、背景与动机

| 维度 | 现状 | 问题 | 目标 |
|------|------|------|------|
| 三桶不分 | docs/ 一级 6 个文档按"分析层"组织（市场层/板块层/个股层 + API契约/数据库表结构 + index）；过程性目录（plans/tasks/template/done）与知识文档平铺在同一层；一个任务的故事散在 3 处（plans 方案 → tasks 任务清单（实施完删）→ done 归档），时间线靠状态块串联 | 查一个领域的知识要开多个位置；过程文档与知识文档混排；任务资料不聚拢，执行记录/时间线/复盘没有固定落点 | docs/ 下三大桶（用户 2026-09-13/14 拍板）：**requirements/** 任务桶（每任务一文件夹，8 文件骨架全建、attachments 按需）+ **knowledge/** 知识沉淀（backend/frontend/ai，刻意使用约定见 CLAUDE.md）+ **memory/** 经验沉淀不动 |
| 任务骨架缺失 | 现 plans/<方案名>.md 只有状态块 + 正文；评审 findings、实施记录、时间线、复盘均无固定文件承载（评审 findings 散在会话中，任务清单实施完删除） | 过程知识（评审结论、执行步骤、关键决策、复盘）随会话丢失，沉淀不进知识库 | 每任务一文件夹：8 文件骨架**全建**（README/plan/tasks/log/decisions/issues/result/retrospective），attachments/ 按需建 |
| 链接漂移 | API契约.md:4 链接 `(plans/后端方案.md)` 指向不存在路径（实际在 done/，已断）；同批还有 index.md:203、CLAUDE.md:339 死链 | 已断链接无人修复；文件移动后断链面扩大（评审模拟实测：index 13 处 + done/ 20 文件 26 链接行 + `../../` 根链接 25 行 + 迁移文档自身 18 处 + 根级 4 处 + memory 裸路径 4 处 + 代码注释 5 处 + 模板指令 4 行） | 迁移时按 3.2 规则表 A–K 全量修复 + 11 条现存死链顺修，基线对比脚本验证（§五） |
| 约定文档滞后 | CLAUDE.md 中 docs 子目录路径提及 38 行 / 62 处（plans 6、tasks 3、template 7、done 7、memory 39），且「工作流程」「Plans 文件约定」「Tasks 文件约定」「方案评审机制」全部按平铺式两文件制撰写 | 目录结构变成任务文件夹制后，纯路径替换不够——规则文本本身（每个复杂任务 = docs/plans/ 下一个方案文件）语义失效 | CLAUDE.md 工作流规则按任务文件夹制**语义重写**（§3.4），不是机械前缀替换 |

## 二、架构设计

新目录树：

```
docs/
├── index.md                  ← 总导航（导航表改三桶分组，三层金字塔正文不动）
├── requirements/             ← 任务桶（每任务一文件夹；8 文件骨架全建，attachments 按需）
│   ├── 文档目录重构/          ← 进行中任务（示例）
│   │   ├── README.md         ← 必建：任务总览（目标/状态/负责人/链接）
│   │   ├── plan.md           ← 必建：方案正文（评审对象）
│   │   ├── tasks.md          ← 必建：拆任务清单
│   │   ├── log.md            ← 必建：时间线日志
│   │   ├── decisions.md      ← 必建：关键决策记录
│   │   ├── issues.md         ← 必建：问题与解决（评审 findings 落点）
│   │   ├── result.md         ← 必建：最终产出与结论（验证结果、Code Review 结论）
│   │   ├── retrospective.md  ← 必建：复盘
│   │   └── attachments/      ← 按需：附件、截图、数据
│   ├── templates/            ← 任务骨架模板（README/plan/tasks/log/decisions/issues/result/retrospective 8 文件全模板 + 骨架说明）
│   └── archive/              ← 已归档：30 个历史方案平铺迁入（不拆骨架）+ 未来任务文件夹整体归档
├── knowledge/                ← 知识沉淀
│   ├── backend/              ← API契约.md、数据库表结构.md（迁入）
│   ├── frontend/             ← 前端平台.md（新建汇总）
│   ├── ai/                   ← 市场层.md、板块层.md、个股层.md（迁入）
│   └── 产品需求分析.md        ← 产品基线（长期演进文档，从 plans/ 迁入；非任务，放知识桶）
└── memory/                   ← 经验沉淀（pitfalls + best-practices，位置不动）
```

骨架文件与现有流程的对应（写入 templates/ 骨架说明，CLAUDE.md 约定同步）：

| 文件 | 必建 | 对应现有载体 | 生命周期 |
|------|------|------|------|
| README.md | ✅ | 方案文件顶部状态块 | 实施中实时更新；归档时定格 |
| plan.md | ✅ | plans/<方案名>.md 正文 | 评审对象；确认后冻结，修正走 revisions 追加 |
| tasks.md | ✅ | docs/tasks/<方案名>.md 任务清单（CLAUDE.md Tasks 约定内容迁入）+ 原 execution 的执行记录职责 | 方案确认后生成；实施中演进；归档随文件夹保留（不再删除） |
| log.md | ✅ | （无，新增） | 时间线追加 |
| decisions.md | ✅ | 方案"已确认决策/待确认问题"节 | 追加 |
| issues.md | ✅ | 评审 findings / 踩坑 | 追加 |
| result.md | ✅ | 验证总表结论、Code Review 结论 | 收尾写 |
| retrospective.md | ✅ | （无，新增；与 memory/ 互补：任务级复盘 vs 跨任务主题沉淀） | 归档前写 |

**归档层级注意（写入 templates/骨架说明 + CLAUDE.md 任务文件夹约定）**：`requirements/<任务名>/`（3 层）移入 `requirements/archive/<任务名>/`（4 层）后**深一层**——归档时文件夹内相对链接整体上移一层（`../../X` → `../../../X`、`../archive/X` → `../../archive/X`），归档后跑链接检查复查。

设计原则：

1. **文件移动 = `git mv`**（保留历史；Liveprofit 为 git 仓库，按经验 git-add-hygiene 显式按路径暂存，禁 `git add -A`）。
2. **进行中方案整文件迁为 plan.md，不拆分**：现 3 个 plans/ 文档原样迁入各任务文件夹的 plan.md（顶部状态块保留——README.md 成为权威状态载体后，plan.md 顶部状态块在下次编辑时自然移除；避免机械拆分与用户并发编辑冲突）；README.md 新写（从状态块摘录）。
3. **归档区平铺不拆骨架**：30 个历史方案原样迁入 requirements/archive/（文件名不变，只改路径）；未来任务完成后**整个文件夹**移入 archive/<任务名>/（保留实际产物文件），archive 内两种形态并存（骨架说明里写明）。
4. **全仓链接同步修复**：评审模拟实测的引用清单（index 13 处、done/ 20 文件 26 链接行、`../../` 根链接 25 行、迁移文档自身 18 处、根级 4 处、memory 裸路径 4 处、代码注释 5 处、模板指令 4 行）全部按新目标路径重算修复（3.2 规则 A–K）；只改链接与活文档约定指令，不碰归档正文（archive 正文裸路径提及不改，规则 G）；顺修 11 条现存死链（规则 F）。
5. **CLAUDE.md 语义重写**（§3.4）：62 处路径 + 工作流程/Plans 文件约定/Tasks 文件约定/评审机制按任务文件夹制重写，不是纯前缀替换。
6. **index.md 正文保留**：AI 速览、三层金字塔章节不动，只改导航表、文中链接与 :35 约定句。
7. **knowledge/backend/ 不新增入口文件**；**产品需求分析.md 归 knowledge/**（产品基线属长期演进的知识文档而非任务）。

## 三、详细设计

### 3.0 模块总览

| 维度 | 问题 | 方案概览 |
|------|------|---------|
| 知识文档迁移 | 5 个知识文档 + 产品基线需要归入 knowledge/ | `git mv`：市场层/板块层/个股层 → knowledge/ai/；API契约/数据库表结构 → knowledge/backend/；产品需求分析 → knowledge/；文件名不变（3.1） |
| 任务文件夹迁移 | plans/template/done 需要转成任务文件夹制 | 进行中 3 方案（含 Treemap，以迁移前 `ls docs/plans/` 实测清单为准）整迁为 requirements/<任务名>/plan.md + 新写 README.md；done 30 文件 → requirements/archive/；template 2 文件 → requirements/templates/ 改造；旧空 docs/tasks/ 删除（3.1） |
| 链接修复 | 迁移后全仓引用断链（上版评审实测清单按新目标重算） | 按"来源位置 → 前缀替换/目标解析规则"机械修复（3.2 规则 A–K），基线对比脚本验证兜底（§五） |
| 前端汇总 | 前端领域无一级文档 | 新建 knowledge/frontend/前端平台.md（3.3） |
| 主干约定同步 | CLAUDE.md 工作流规则按平铺式两文件制撰写 | 语义重写（3.4）：目录树/定位表/工作流程/Plans+Tasks 文件约定合并为任务文件夹约定/评审机制锚定 plan.md |
| 总导航 | index.md 导航表按文档平铺，无三桶入口 | 导航表改 requirements/knowledge/memory 三桶分组 + 文中链接修正 + :35 约定句改写（3.5） |

### 3.1 文件迁移清单

| 现路径 | 新路径 | 备注 |
|------|------|------|
| docs/市场层.md | docs/knowledge/ai/市场层.md | 自身链接随迁（规则 C） |
| docs/板块层.md | docs/knowledge/ai/板块层.md | 同上 |
| docs/个股层.md | docs/knowledge/ai/个股层.md | 同上 |
| docs/API契约.md | docs/knowledge/backend/API契约.md | 顺修 :4 断链 + 自身引用前缀 |
| docs/数据库表结构.md | docs/knowledge/backend/数据库表结构.md | **未跟踪（status `??`），git mv 报错 → 用 `mv`**；`../db/`、`../AI/` 路径补层级 |
| docs/plans/产品需求分析.md | docs/knowledge/产品需求分析.md | 产品基线归知识桶；引用它的链接目标全部重算（规则 B/其他） |
| docs/plans/指数K线图交互优化方案.md | docs/requirements/指数K线图交互优化/plan.md | **整迁不拆分**（用户并发编辑该文件——只执行 `git mv` 不重写内容，迁移后 `git status` 确认旧路径未复活）；新建同夹 README.md |
| docs/plans/文档目录重构方案.md | docs/requirements/文档目录重构/plan.md | 本方案自身；新建同夹 README.md |
| docs/plans/板块概念Treemap方案.md | docs/requirements/板块概念Treemap/plan.md | 在审方案（R1 实测第 4 个 plans 文件，评审中）；新建同夹 README.md |
| docs/plans/（目录） | 删除 | 迁移后空目录删除（**以迁移前 `ls docs/plans/` 实际清单为准，逐个建任务文件夹**） |
| docs/tasks/（空目录） | 删除 | 任务清单载体 = 任务文件夹内 tasks.md（原 execution 更名，与 plan.md 同级）；CLAUDE.md Tasks 文件约定相应重写；旧目录已空，直接 rmdir |
| docs/template/开发任务模板.md | docs/requirements/templates/tasks.md.模板.md | `git mv` 后改名（保留历史）；内容微调（路径与字段名对齐新结构） |
| docs/template/技术方案文档模板.md | docs/requirements/templates/plan.md.模板.md | 同上 |
| docs/done/（整目录，30 文件 = 26 已跟踪 + 4 未跟踪） | docs/requirements/archive/ | 平铺迁入，文件名不变；目录内互链随层级重算（规则 B①） |
| docs/memory/ | 不动 | 经验沉淀留在 docs/memory/ |
| docs/index.md | 位置不动 | 总导航；内容调整见 §3.5 |

新增文件：3 任务骨架各 7 文件（README + tasks/log/decisions/issues/result/retrospective，按模板初始化）+ requirements/templates/ 下 8 文件全模板（README/plan/tasks/log/decisions/issues/result/retrospective .模板.md——plan/tasks 由现有两模板改名，其余 6 个新建）+ 骨架说明.md + knowledge/frontend/前端平台.md。

### 3.2 链接修复规则

按"引用来源位置 → 目标解析"分规则 A–K（实施时逐文件 grep 后替换；指向 plans/<方案名>.md 的链接**逐条解析目标**，不是前缀盲替）：

| 来源位置 | 替换规则 | 涉及文件（上版评审实测清单，实施时再核对） |
|------|------|------|
| A. docs/ 平级（index.md） | `](市场层.md)` → `](knowledge/ai/市场层.md)`（板块层/个股层同）；`](API契约.md)` → `](knowledge/backend/API契约.md)`（数据库表结构同）；`](plans/产品需求分析.md)` → `](knowledge/产品需求分析.md)`；`](plans/<方案名>.md)` → `](requirements/<任务名>/plan.md)`；`](done/…)` → `](requirements/archive/…)` | index.md 13 处（实测）：:13 API契约、:15 done/后端方案 + plans/产品需求分析、:203 板块层 + plans/板块层技术方案、:223、:240、:255 + 导航表 5 行（:29-33）随 §3.5 整体替换 |
| B. requirements/archive/ 内历史文档 | ① 互链 `(../archive/…)` 随层级重算：archive 平铺一层，文档间互链改为 `](<文件名>.md)`（同目录）；② 指向桶外补层级：`](../../knowledge/ai/市场层.md)` → `](../../knowledge/ai/市场层.md)`（板块层/个股层同）；`](../../knowledge/backend/API契约.md)` → `](../../knowledge/backend/API契约.md)`；`(../../../knowledge/backend/数据库表结构.md)` → `(../../../knowledge/backend/数据库表结构.md)`；`(../archive/产品需求分析.md)` → `](../../knowledge/产品需求分析.md)`；`(../archive/<方案名>.md)` → `](../../requirements/<任务名>/plan.md)`（逐条解析）；`(../templates/开发任务模板.md)` → `](../templates/tasks.md.模板.md)`；`(../templates/技术方案文档模板.md)` → `](../templates/plan.md.模板.md)`；`](index.md)` → `](../../index.md)`；`(../../memory/…)` → `](../../memory/…)`；③ **仓库根链接 `](../../X)` → `](../../../X)`**（X ∈ AI/backend/frontend 等；实测 25 行：20 行迁移后新断 + 5 行迁移前已断，全部补层级：技术指标数据源切换方案 18 行（14 新断 + 4 已断 :14/:160/:162/:169）、板块层轮动数据增强方案 :14/:21、MACD指标副图方案 :60（已断）/:99、K线指标叠加方案:14。产品需求分析.md:756/:1107 的 `../../AI/…` 随文件迁 knowledge/ 后**层级不变无需改**——plans/ 与 knowledge/ 同为 docs 下 2 层，实测复核） | done/ 20 文件共 26 链接行：市场层重构方案 3、板块层技术方案 2、板块层轮动战术与政策事件流方案 2、板块层轮动数据增强方案 2（:14/:21 + :234 ../template）、选股层与仓位管理层技术方案 / 板块轮动预测分析方案 / 板块层接口逐日数据增强方案 / 数据提供器目录重构方案 / 技术指标数据源切换方案 / 技术指标T-1交易日限制梳理方案 / 市场层证据驱动分析与三级事件路由改造方案 / 后端方案 / 任务拓扑图方案 / 任务执行调用日志方案 / 事件研究方案 / MACD指标副图方案 / K线指标叠加方案 各 1、MongoDB全面替换为PostgreSQL方案 2（:6/:246 ../index.md）、Agent输出格式外置为md文件技术方案 1（:9 ../index.md）、前端平台技术方案 1（:3 ../tasks，见规则 F⑥）；另 archive 内 `../done/` 互链与指向桶外链接实施时全量 grep 同规则处理 |
| C. knowledge/ai/ 三文档自身 | `](index.md)` → `](../../index.md)`；`](done/…)` → `](../../requirements/archive/…)`；`](plans/…)` → `](../../knowledge/…)` 或 `](../../requirements/<任务名>/plan.md)`（逐条解析）；互链同目录不变 | 市场层.md 4 处（:5 index + done×2、:101 正文 done×1）；板块层.md 5 处（:5 index、:167 正文 done×4）；个股层.md 1 处（:5 index）；plans/ 链接以 grep 为准 |
| D. knowledge/ 内文档自身（按层数分两套） | **3 层（backend 两文档）**：`](done/…)` → `](../../requirements/archive/…)`；`](plans/…)` → `../../requirements/<任务名>/plan.md`（逐条解析）；`](../db/…)` → `](../../../db/…)`；`](../AI/…)` → `](../../../AI/…)`。**2 层（knowledge/产品需求分析.md）**：`(../archive/…)` → `../requirements/archive/…`；指向同级知识文档逐条解析；`](../../AI/…)`、`](../../backend/…)` 层级不变**无需改**（plans/ 与 knowledge/ 同为 2 层，:756/:1107 实测复核） | API契约.md 3 处（:3 done、:4 plans×2——后端方案见规则 F、产品需求分析改同级 `../产品需求分析.md`）；数据库表结构.md 5 处（:4、:37、:97 ../db、:4 ../AI、:93 done）；产品需求分析.md 自身链接（:620 `(../archive/…)` → `../requirements/archive/…` 等）实施时全量 grep |
| E. 根级文件 | `docs/API契约.md` → `docs/knowledge/backend/API契约.md`；`docs/done/前端平台技术方案.md` → `docs/requirements/archive/前端平台技术方案.md` | README.md 2 处（:84、:145）、run.sh 2 处（:173、:374 注释）；CLAUDE.md 的 docs/… 链接与其 62 处路径提及一并由 §3.4 处理 |
| F. 顺修现存死链（11 条，与迁移无关同批修复） | ① API契约.md:4 `(plans/后端方案.md)` → `(../../requirements/archive/后端方案.md)`；② index.md:203 `(plans/板块层技术方案.md)` → `(requirements/archive/板块层技术方案.md)`；③ CLAUDE.md:339 `docs/plans/证券市场数据库统一方案.md` → `docs/requirements/archive/证券市场数据库统一方案.md`；④ memory/pitfalls/ai/store-daily.md:32 同目标死链 → `docs/requirements/archive/…`；⑤ **archive 同目录裸链接 6 处（现状即断，迁入后仍断）**：K线指标叠加方案.md:6、任务拓扑图方案.md:12、任务详情页markdown渲染方案.md:6、单Agent重跑与提示词编辑方案.md:6、前端平台技术方案.md:6 的 `](../../knowledge/产品需求分析.md)` → `](../../knowledge/产品需求分析.md)`；证券市场数据库统一方案.md:6 的 `(../指数K线图交互优化/plan.md)` → `](../指数K线图交互优化/plan.md)`；⑥ **已删任务清单引用（现状即断）**：前端平台技术方案.md:3 `](../tasks/前端平台技术方案.md)` → **删除该链接**（任务清单为过程性文档，该方案实施完成后清单已删，无替代目标） | API契约.md、index.md、CLAUDE.md、memory/pitfalls/ai/store-daily.md + archive 内 6 文件 |
| G. archive 正文裸路径提及（不改） | 不改——归档文档正文属历史记录，只修链接不碰正文；裸路径提及以 grep 实测清单为准（含 `docs/done|docs/plans` 字样的行实测 18 文件 37 行；示例：MongoDB全面替换为PostgreSQL方案.md:286、证券市场数据库统一方案.md:427、日志查看器UI重构方案.md:237、前端平台技术方案.md:465、事件研究独立Tab与审核自动拉取方案.md:331），完整清单实施时 `grep -rn 'docs/(done|plans)' docs/requirements/archive` 落盘存档 | — |
| H. memory/ 裸路径引用 | memory/ 位置不动、无指向 plans/done 的 markdown 链接（grep 实测 0）；改 3 文件 4 处裸路径：store-daily.md:32（`docs/done/全市场日线本地库方案.md` 与 `docs/plans/证券市场数据库统一方案.md`——后者即 F④）、tushare-endpoints.md:52（`docs/done/技术指标数据源切换方案.md`）、markdown-render.md:17（`docs/done/任务详情页markdown渲染方案.md`）→ `docs/requirements/archive/…` | 上述 3 文件 |
| I. 代码注释（5 处，源文本实测，目标解析后逐条改） | 不做前缀盲替，按"源文本 → 新目标"两段式：① AI/eventStudy/db/schema.sql:3 `docs/plans/事件研究.md` → `docs/requirements/archive/事件研究方案.md`；② schema.sql:53 `docs/plans/市场层证据驱动分析与三级事件路由改造方案.md` → `docs/requirements/archive/市场层证据驱动分析与三级事件路由改造方案.md`；③ AI/eventStudy/__init__.py:1 `docs/plans/事件研究.md` → `docs/requirements/archive/事件研究方案.md`；④ backend/modules/analysis/application/graph_topology.py:3 `docs/plans/任务拓扑图方案.md` → `docs/requirements/archive/任务拓扑图方案.md`；⑤ backend/tests/unit/analysis/test_graph_topology.py:3 同④ | 上述 4 文件（.py/.ts/.sh/.sql 全仓 grep 排除 .venv、node_modules、liveprofit.egg-info） |
| J. templates/ 模板正文指令（活文档约定，须改） | 迁入 requirements/templates/ 后：① 开发任务模板（→tasks.md.模板.md）：:56 `docs/done/` → `docs/requirements/archive/`；:59 `docs/tasks/<方案名>.md` → 「任务文件夹内 tasks.md」（拆解结构与验收标准沿用现约定）；② 技术方案文档模板（→plan.md.模板.md）:93 `docs/plans/<方案名>.md` → `docs/requirements/<任务名>/plan.md`、:95 `docs/plans/`、`docs/done/` → 新路径 | requirements/templates/ 下 2 个模板文件 |
| K. 进行中方案自身（plans/<方案名>.md → requirements/<任务名>/plan.md，2→3 层） | ① `(../archive/…)` → `](../archive/…)`；② 同级 `](../../knowledge/产品需求分析.md)` → `](../../knowledge/产品需求分析.md)`；③ 同级 `(../../../knowledge/backend/数据库表结构.md)` → `(../../../knowledge/backend/数据库表结构.md)`；④ 同级其他方案 `(../指数K线图交互优化/plan.md)` → `](../指数K线图交互优化/plan.md)`（逐条解析） | K线方案 5 处（:6 ../done×3、../数据库表结构、产品需求分析）；本方案 2 处（:6 ../数据库表结构、指数K线图交互优化方案）；Treemap 4 处（:6 ../done/证券市场数据库统一方案、产品需求分析、指数K线图交互优化方案、:14，实施时 grep 复核） |

排除项：memory/ 对五个知识文档无引用（已核实）；.venv/、node_modules/、liveprofit.egg-info/ 为依赖/生成物噪声，不处理。

### 3.3 前端汇总文件大纲（knowledge/frontend/前端平台.md）

内容来源与落点（实施时先读来源再写，只写结构与指向，不复制细节）：

| 章节 | 内容 | 来源 |
|------|------|------|
| 状态块 + 关联文档 | 持续演进；链接 ../../index.md、../backend/API契约.md、../../requirements/archive/前端平台技术方案.md | — |
| 一、技术栈与构建 | React 19 / Vite 7 / TS 5.8 / pnpm / Tailwind 4 / @tanstack/react-query v5 / echarts 6.1 / zustand 5；pnpm 为唯一包管理器（npm install 会报错） | frontend/package.json + CLAUDE.md 前端包管理器节 |
| 二、目录结构 | src/api（generated，禁手改）/ app / modules（analysis、event-study、market、watchlist）/ routes / shared（charts、feedback、format、ui）/ stores | frontend/src 实况 |
| 三、API client 约定 | openapi-typescript-codegen@0.29.0 codegen 流程；backend 改路由后必须先 export_openapi + pnpm run generate:api 再动消费代码；不得手写后端领域类型；契约参照 ../backend/API契约.md（冲突以 OpenAPI 为准） | API契约.md 说明 + CLAUDE.md codegen 节 |
| 四、页面与模块 | 大盘页（market）、AI 看板（analysis）、事件研究（event-study）、自选（watchlist）各自的入口与职责一句话 | API契约.md 领域清单 + src/modules 实况 |
| 五、共享组件体系 | charts（仅 CandlestickChart：多 grid K线/MACD/成交量）、ui（MarkdownView 统一 markdown 渲染，kind 分流 md/json/txt）、feedback、format；拓扑图组件在 modules/analysis（components/topologyChartOption.ts、pages/task-detail/GraphTopologyPanel.tsx、pages/agents/AgentTopologyPage.tsx） | src/shared 实况 + CLAUDE.md markdown 渲染约定 |
| 六、状态与数据约定 | React Query v5 hooks 按模块收敛于 queries.ts、placeholderData 渐进加载模式（指数K线图交互优化方案**计划项**，现状代码尚无此模式——写入时标注"计划中"）、zustand stores 用途 | src/modules 实况 + 指数K线图交互优化方案 |
| 七、约定与坑索引 | 逐条链接 CLAUDE.md 前端规则节 + ../../memory/pitfalls/frontend/（pnpm-openapi-codegen、react-query-dialog）+ ../../memory/best-practices/frontend/markdown-render，一行一句摘要 | memory/index.md |

### 3.4 CLAUDE.md 同步点（语义重写，不是纯替换）

1. **「docs/ 目录结构」代码块**：按 §二 新树重写（三桶 + 任务文件夹骨架示意）；注释用稳态描述。
2. **「文档类型定位」表**：主干架构文档 → `docs/index.md`（总导航）+ `docs/knowledge/`（backend/frontend/ai + 产品基线）；任务文件夹 → `docs/requirements/<任务名>/`（README+plan 必建，其余按需）；文档模板 → `docs/requirements/templates/`；已完成方案 → `docs/requirements/archive/`；经验记录行不变（docs/memory/）。原"进行中方案/开发任务清单"两行合并为任务文件夹行。
3. **「工作流程」**：复杂任务流程重写——每个复杂任务 = `docs/requirements/<任务名>/` 文件夹（README.md 状态块 + plan.md 方案正文）；方案评审锚定 plan.md；用户确认后任务分解写入 tasks.md；实施中实时更新 README.md 状态块并追加 log.md；收尾产物写 result.md；归档 = 文件夹移入 `docs/requirements/archive/<任务名>/`。中小改动/trivial 规则不变。
4. **「Plans 文件约定」+「Tasks 文件约定」合并重写为「任务文件夹约定」**：README.md 状态块格式（状态/进度/下一步/关联文档）；plan.md 正文结构沿用现模板约定（模块总览表、具体例子、待确认问题三要素）；tasks.md（拆任务清单，沿用现任务清单的拆分原则与验收标准）+ log/decisions/issues/result/retrospective（8 文件全建、各司其职，attachments 按需）；归档 = 文件夹整体移入 `docs/requirements/archive/<任务名>/` **并同步把文件夹内相对链接上移一层**（archive 深一层），任务清单（tasks.md）随文件夹保留不再删除。
5. **「方案评审机制」**：评审对象路径改为任务文件夹 plan.md；其余机制（3 轮、维度、打分）不变。
6. **62 处路径**：docs/plans → 按目标解析（产品需求分析 → docs/knowledge/产品需求分析.md；方案名 → docs/requirements/<任务名>/plan.md；泛指规则文本 → docs/requirements/<任务名>/ 表述）；docs/tasks → 任务文件夹内 tasks.md 表述；docs/template → docs/requirements/templates；docs/done → docs/requirements/archive；docs/memory 39 处不动。具体行实施时逐条改（38 行）。
7. **:339 死链顺修**（规则 F③）。
8. **「Code Review 规则」**：「review 范围以方案文件的"文件变更清单"为准」「方案文件状态更新为 Code Review」→ 改指 `docs/requirements/<任务名>/plan.md`（变更清单）与任务 README.md（状态块）。
9. **「CLAUDE.md 自我更新规则」**：「任务本身的状态与进度（属于方案文件状态块）」→「（属于任务 README.md 状态块）」。

### 3.5 index.md 同步点

- 「文档导航」表改为三桶分组：

  | 文档 | 类型 | 内容 |
  |------|------|------|
  | [tasks/](tasks/) | 任务桶 | 进行中任务（每任务一文件夹：README+plan+按需骨架）｜[templates](requirements/templates/) 任务骨架模板｜[archive](requirements/archive/) 已归档方案 |
  | [knowledge/](knowledge/) | 知识沉淀 | [backend](knowledge/backend/)（[API 契约](knowledge/backend/API契约.md) + [数据库表结构](knowledge/backend/数据库表结构.md)）｜[frontend](knowledge/frontend/)（[前端平台](knowledge/frontend/前端平台.md)）｜[ai](knowledge/ai/)（[市场层](knowledge/ai/市场层.md) / [板块层](knowledge/ai/板块层.md) / [个股层](knowledge/ai/个股层.md)）｜[产品需求分析](knowledge/产品需求分析.md)（产品基线） |
  | [memory/](memory/) | 经验沉淀 | [index](memory/index.md)（pitfalls + best-practices 总索引） |

- 「后端平台」节 API契约 链接（:13）、文中 plans/done 链接（:15、:203、:223、:240、:255）按规则 A/F 修正。
- **:35 约定句改写**：「`plans/` 下的方案文档实施完成后整合进主干并归档 `done/`」→「`requirements/<任务名>/` 下的任务实施完成后整合进主干并归档 `requirements/archive/`」。

## 四、已确认决策 / 待确认问题

- 已确认决策（用户拍板）：
  1. 三桶结构：requirements/（任务桶，每任务一文件夹，骨架含 tasks.md）+ knowledge/（知识沉淀）+ memory/（不动）（2026-09-13/14）。
  2. 任务桶 = 每任务一文件夹 + 8 文件骨架**全建**（attachments/ 按需）（2026-09-14，用户从"按需建"改回全建——8 文件各司其职，轻任务容忍空壳）。
  3. 归档区平铺历史方案不拆骨架（2026-09-14，方案推荐随评审确认）。
  4. 结构形态 = 目录式；前端知识库 = 新建汇总文件。
- 方案内决策（默认推荐，随评审确认）：
  1. 进行中方案**整迁不拆分**：原样迁为 plan.md（顶部状态块保留至下次编辑移除），README.md 新写——避免机械拆分与用户并发编辑冲突。
  2. 产品需求分析.md（产品基线）归 knowledge/ 顶层，不作任务文件夹。
  3. archive 内两种形态并存：历史平铺文件 + 未来任务文件夹整体归档。
  4. 任务清单 = 任务文件夹内 tasks.md（原 execution 更名，与 plan.md 同级，2026-09-14 用户拍板）；旧 docs/tasks/<方案名>.md 目录约定废弃；tasks.md 随文件夹归档保留（不再删除）。
  5. 前端汇总文件名为「前端平台.md」；knowledge/backend/ 不新增入口文件。
- 待确认问题：无。

## 五、验证方式

| 层面 | 命令/方式 | 观察点 |
|------|----------|--------|
| 链接完整性（基线对比） | 迁移前跑 Python 脚本：遍历 docs/**/*.md 解析全部 `](相对路径)` 链接（排除 http/#锚点/模板目录占位，相对本文件解析），**把解析失败清单落盘为基线文件**（脚本按分类打印计数，以迁移前落盘为准，不写死数字；`Liveprofit/…` 写法约 48 处：K线方案 15 + 板块概念Treemap 30 + 证券市场数据库统一方案 12 + memory 1，与本次迁移无关，不修；Treemap 为并发新增、计数以落盘为准）；迁移后重跑**取集合差** | 集合差**为空**（无新增断链）；指向迁移文档的链接全部可达新路径；11 条现存死链（规则 F）已修复 |
| 裸路径与根级检查 | ① `grep -nE 'docs/(市场层|板块层|个股层|API契约|数据库表结构)\.md' CLAUDE.md README.md run.sh`；② `grep -rnE 'docs/(done|plans|template)(/|\b)' CLAUDE.md README.md run.sh docs/memory docs/requirements/templates` | ①② 均 0 命中（memory 4 处、templates 指令、run.sh:374、CLAUDE.md 重写处全部改完；`(/|\b)` 边界防误匹配新桶 docs/requirements/templates；旧 docs/tasks/ 引用由集合差兜住） |
| 代码注释检查 | 全仓 `grep -rnE 'docs/(plans|done)'` 于 .py/.ts/.sh/.sql（排除 .venv、node_modules、liveprofit.egg-info） | 0 命中（5 处代码注释全部改完） |
| 归档层级演练 | 建一个示例任务文件夹（含 `../../knowledge/…` 链接）→ 按约定移入 `requirements/archive/<示例>/` → 链接检查脚本复查 | 归档后无新增断链（验证"归档上移一层"规则可执行、无遗漏） |
| 变更范围 | 迁移前 `git status --porcelain > 基线快照`（整仓保存；现状整仓已有用户并发改动），迁移后 diff 对比 | 增量仅含预期文件（迁移 41 + 新建 29：3 任务 × 7 骨架 + 模板新建 6 + 骨架说明 + 前端平台.md；逐文件口径见 §六）；无用户并发文件的意外改动 |
| 导航可用 | 人工点开 index.md 导航链接 | requirements/knowledge/memory 三行均可到达对应文档 |
| 主干一致 | 重读 CLAUDE.md docs 节 | 目录树/定位表/工作流程/任务文件夹约定/评审机制与新结构一致 |

## 六、文件变更清单

**计数口径（逐文件）**：迁移 41 个文件（知识文档 6 + 进行中方案 3 + archive 30 + 模板 2）+ 新建 29 个文件（3 任务 × 7 骨架文件 + 模板新建 6 + 骨架说明 + 前端平台.md）。

1. 建目录：`mkdir -p docs/requirements/templates docs/requirements/archive docs/knowledge/backend docs/knowledge/frontend docs/knowledge/ai docs/requirements/文档目录重构 docs/requirements/指数K线图交互优化 docs/requirements/板块概念Treemap`，并 `touch docs/requirements/templates/骨架说明.md`（骨架说明 + 模板文件本身即有内容，无需 .gitkeep）。
2. `git mv` 知识文档 ×5 + 产品基线 ×1：市场层/板块层/个股层 → knowledge/ai/；API契约 → knowledge/backend/；产品需求分析 → knowledge/；**数据库表结构.md 未跟踪，用 `mv` 移动**（git mv 对未跟踪文件报 `not under version control`，实测确认）。
3. 进行中方案迁移：`git mv` ×3（指数K线图交互优化方案.md → tasks/指数K线图交互优化/plan.md、本方案 → tasks/文档目录重构/plan.md、板块概念Treemap方案.md → tasks/板块概念Treemap/plan.md；**以迁移前 `ls docs/plans/` 实际清单为准逐个建任务文件夹**；K线方案正被用户并发编辑——只执行 `git mv` 不重写文件内容，迁移后 `git status` 确认旧路径未复活）+ 按模板建齐 3 个任务的骨架（README + tasks/log/decisions/issues/result/retrospective 各 7 个，模板头 + 空状态初始化）。
4. 前置 `git add -u docs/plans`（暂存 docs/plans 内已删除的跟踪文件（现状 ` D` 的 MACD指标副图方案.md），保持基线快照干净、避免 git mv 目录时对已删跟踪文件报错）→ `git mv` 流程目录：docs/done → docs/requirements/archive（整目录）、docs/template → docs/requirements/templates（整目录）；删除已空的 docs/plans/（旧 docs/tasks/ 空目录直接 rmdir）。
5. 模板改造：`git mv` 改名 ×2（开发任务模板.md → tasks.md.模板.md、技术方案文档模板.md → plan.md.模板.md，保留历史）+ 内容微调（规则 J）+ 新建 6 个模板（README/log/decisions/issues/result/retrospective .模板.md，各放对应文件的结构头与填写说明）+ 骨架说明.md（§3.0 骨架表 + 归档层级注意）。
6. 新建 `docs/knowledge/frontend/前端平台.md`（按 3.3 大纲写，写前先读各来源文件）。
7. 链接修复：index.md（规则 A + F②）、archive 历史文档（规则 B + F⑤⑥）、knowledge 文档自身（规则 C/D）、进行中方案自身（规则 K）、README/run.sh（规则 E）、memory 3 文件 4 处（规则 H + F④）、代码注释 5 处（规则 I）、templates 指令（规则 J）；archive 正文提及不动（规则 G）。
8. CLAUDE.md 语义重写：§3.4 九点（目录树/定位表/工作流程/任务文件夹约定（含归档上移一层）/评审机制/62 处路径/:339/Code Review 规则/自我更新规则）。
9. 验证：§五 基线对比脚本 + 裸路径/代码注释 grep + 归档层级演练 + git status 前后快照 diff。
10. 暂存：按路径显式 `git add`（含新建文件），禁 `git add -A`（git-add-hygiene 约定）。
