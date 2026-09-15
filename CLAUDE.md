# Python 工作区约定

## 工作流程规则

### docs/ 目录结构

```
docs/
├── index.md              ← 总导航：三层金字塔架构总览 + 三桶导航（持续演进）
├── requirements/         ← 任务桶（每任务一文件夹，8 文件骨架全建、attachments 按需）
│   ├── <任务名>/         ← 进行中任务（README/plan/tasks/log/decisions/issues/result/retrospective）
│   ├── templates/        ← 任务骨架模板（8 个 *.模板.md + 骨架说明.md）
│   └── archive/          ← 已归档（历史方案平铺 + 任务文件夹整体归档）
├── knowledge/            ← 知识沉淀（刻意使用，见下方「knowledge/ 刻意使用」）
│   ├── backend/          ← API契约.md、数据库表结构.md
│   ├── frontend/         ← 前端平台.md
│   ├── ai/               ← 市场层.md、板块层.md、个股层.md
│   └── 产品需求分析.md    ← 产品基线
├── memory/               ← 经验沉淀（见「经验沉淀规则」）
│   ├── index.md          ← pitfalls + best-practices 总索引
│   ├── pitfalls/         ← bad：踩坑记录
│   │   ├── backend/      ← 后端/数据库/平台服务踩坑
│   │   ├── frontend/     ← 前端踩坑
│   │   ├── ai/           ← AI 分析链路/dataflows/图工程踩坑
│   │   └── workspace/    ← 跨领域（流程/环境/评审机制）踩坑
│   └── best-practices/   ← good：最佳实践（已验证做法/统一约定/机制设计）
│       ├── backend/
│       ├── frontend/
│       ├── ai/
│       └── workspace/
```

**文档类型定位：**

| 类型 | 位置 | 生命周期 | 示例 |
|------|------|----------|------|
| **主干架构文档** | `docs/index.md`（总导航）+ `docs/knowledge/`（backend/frontend/ai + 产品基线） | 持续演进，随项目更新 | index.md、knowledge/backend/API契约.md、knowledge/ai/市场层.md |
| **任务文件夹** | `docs/requirements/<任务名>/`（README+plan/tasks/log/decisions/issues/result/retrospective 8 文件骨架全建，attachments 按需） | 任务驱动，完成后整体移入 archive/<任务名>/ | requirements/文档目录重构/ |
| **文档模板** | `docs/requirements/templates/`（8 个 *.模板.md + 骨架说明.md） | 稳定，新建任务文件夹时复制使用 | plan.md.模板.md、README.md.模板.md |
| **已完成方案** | `docs/requirements/archive/`（历史方案平铺 + 任务文件夹整体归档） | 归档保留，作为设计决策的历史参考 | 市场层重构方案.md |
| **经验记录** | `docs/memory/`（pitfalls=踩坑记录，best-practices=最佳实践） | 持续追加；故事与细节只放这里，CLAUDE.md 只留配套强制规则 | pitfalls/backend/dramatiq-windows.md、best-practices/frontend/markdown-render.md |

### knowledge/ 刻意使用（2026-09-14 用户拍板）

knowledge/ 是**知识沉淀桶**（领域知识库），回答"系统现在是什么样"——结构、接口、契约、架构事实。**刻意写入**：只有值得长期维护的领域知识才进，过程性内容一律不放。

- **归属规则**：
  - 后端知识 → `docs/knowledge/backend/`（API 契约、数据库表结构）
  - 前端知识 → `docs/knowledge/frontend/`（前端平台汇总）
  - AI 分析三层 → `docs/knowledge/ai/`（市场层/板块层/个股层）
  - 跨领域知识/产品基线 → `docs/knowledge/` 顶层（如 产品需求分析.md）
- **写入时机**：① 方案实施完成、架构变更落地后，把变更**整合进对应领域文档**（替代旧"整合进主干文档 docs/*.md"表述）；② 新领域知识首次出现、无归属文档时，按归属规则新建文档。
- **与 memory/ 分工**：knowledge = 事实性知识（what：结构/接口/现状）；memory = 经验教训（how：pitfalls/best-practices）。踩坑故事与做法细节进 memory/，不进 knowledge/。
- **与 requirements/ 分工**：任务过程记录（README/plan/tasks/log/decisions/issues/result/retrospective）属于 `docs/requirements/<任务名>/`；任务完成后，**可复用结论**才沉淀进 knowledge/。

### 工作流程

1. **复杂任务（重构/新架构/多文件变更）**
   - **每个复杂任务 = `docs/requirements/<任务名>/` 下的一个任务文件夹**（README.md 状态块 + plan.md 方案正文），该文件夹即为该任务的唯一追踪载体
   - 先写 `docs/requirements/<任务名>/plan.md`（复制 requirements/templates/plan.md.模板.md），包含：背景动机、设计思路、涉及文件清单、接口/字段变更、向后兼容、验证方法
   - **除非能确保 95% 实现无误，否则必须不断向我澄清问题**
   - **plan.md 写完（澄清完毕）后，自动进入方案评审**（见下方「方案评审机制」）：固定 3 轮（R1 全量评审 → R2 修复核验 → R3 最终核验）收敛，PASS 后修完 minor + 自检收尾；**评审通过后才请我确认**
   - **我确认后先做任务分解**：生成任务文件夹内 `tasks.md` 拆任务清单（复制 [requirements/templates/tasks.md.模板.md](docs/requirements/templates/tasks.md.模板.md)，见下方"任务文件夹约定"），把方案拆成可独立验收的开发任务；任务清单就绪后才开始实现
   - **实时状态更新**：实现过程中，每完成一个关键步骤（如：State 字段新增完毕、某个 Agent 写完、子图编译通过）或一个开发任务，**立即更新 README.md 状态块 + tasks.md 任务状态**（任务完成 → 勾选验收项 + 同步任务总览表），并在 log.md 追加时间线，记录当前进度和下一步
   - **代码写完之后，启动 subagent 做 code review**（见下方"Code Review 规则"）
   - **实现完成 + review 通过后，将方案中的架构变更整合进对应领域文档（`docs/knowledge/`），README.md 状态更新为"已完成"，result.md / retrospective.md 填写完毕，整个任务文件夹移入 `docs/requirements/archive/<任务名>/` 归档（archive 深一层，同步把文件夹内相对链接整体上移一层：`../../X` → `../../../X`）**

2. **中小改动（单文件、加函数、修 bug）**
   - 直接改代码，不需要任务文件夹
   - 如果改动改变了知识库文档中描述的结构/接口，同步更新 knowledge/ 对应文档

3. **Trivial 改动（错别字、格式化、单行修复）**
   - 直接改，无需任何文档

### 任务文件夹约定

- 每个 `docs/requirements/<任务名>/` 代表一个独立的复杂任务，8 文件骨架**全建**（attachments/ 按需）；各文件职责与模板见 [requirements/templates/骨架说明.md](docs/requirements/templates/骨架说明.md)
- **README.md** 顶部必须有状态块，格式：

  ```
  > **状态**：<当前阶段>（<最后更新时间>）
  > **进度**：<已完成>/<总步骤> 步骤
  > **下一步**：<接下来要做什么>
  ```

- 状态取值：`方案设计` → `评审中`（写完自动进入方案评审）→ `待确认` → `任务分解`（用户确认后生成任务清单）→ `实现中` → `Code Review` → `已完成`
- **实现过程中每完成一个关键步骤，必须更新 README.md 状态块的进度和下一步，并在 log.md 追加时间线**
- 任务完成后状态改为 `已完成`，**整个文件夹移入 `docs/requirements/archive/<任务名>/`** 归档（同步把文件夹内相对链接整体上移一层），架构变更同步进 knowledge/ 对应文档
- **plan.md 正文章节结构**：复制 [requirements/templates/plan.md.模板.md](docs/requirements/templates/plan.md.模板.md)，取舍规则详见模板文件末尾速查表。**详细设计章节开头必须有模块总览表（维度｜问题｜方案概览，2026-09-10 起）**：每一行与下方设计模块小节一一对应（维度名 = 小节标题），只列设计模块，三方依赖评估/验证/文件变更等过程小节不入表。**方案中所有问题陈述必须附具体例子（2026-09-10 起）**：现状与问题列用代码事实说话（文件路径/函数名/可观测现象/示例值，如"`derive_risk_gate` 正则解析 `market_regime` 文本，空串落 `normal`"），禁止抽象措辞（如"不稳定""不足"）。**待确认问题每条必须写具体并附背景、目标与推荐（2026-09-11 起）**：问题句带代码事实；背景 = 现状事实 + 该决策影响哪些实现点；目标 = 各选项对应的实现形态；推荐 = 默认选项 + 理由（用户可按推荐直接拍板）。已有历史方案不做回填改造（在审方案按新约定补齐）
- **tasks.md 拆任务清单约定**：**方案经用户确认后**，生成任务文件夹内 `tasks.md`（复制 [requirements/templates/tasks.md.模板.md](docs/requirements/templates/tasks.md.模板.md)）——把方案的实施步骤拆解为可独立验收的开发任务，是方案的实施计划载体。任务清单直接由已评审通过的方案拆出，**无需额外评审**
  - **拆分原则**：每个任务 = 一个可独立验收的实现单元（新建一个模块 / 改造一个文件 / 写一组单测）；按依赖排序，任务块标注依赖关系，无依赖任务可并行；通常 3–10 个任务；每个任务的验收标准必须是**可执行的检查项**（单测命令 / 可运行检查 / 写明观察点的人工检查），禁止"完成 XX 功能"式模糊表述
  - **状态取值**：`待开始` → `进行中` → `已完成`；被阻塞时标 `阻塞：<原因>`，解除后恢复流转
  - **实时更新**：实现过程中每完成一个任务，立即更新该任务块的状态、勾选验收项，并同步任务总览表与 README.md 的进度
  - **生命周期**：tasks.md 随任务文件夹一并归档保留（不再删除——实施记录价值保留，验收结论已固化进 result.md 与代码）。中小改动不需要任务清单
- **结构化 State 字段换代：直接替换、不搞 v1/v2 并存（2026-09-11 决策）**：存量文本字段升级为结构化 dict 时**原地改类型**（同名、同语义），派生逻辑（如 `derive_risk_gate`）原地重写；新语义字段直接用语义名，不新增 `_v2` 后缀字段、不设运行期回退开关（回滚走代码版本回退）。前提：全部消费方（含 backend 展示、JSON 落盘、测试样本）可在同一变更内同步改造；全文报告字段保留仅作展示。并存迁移（双字段+回退开关）仅在消费方不可控（跨团队接口）时采用

### 方案评审机制（3 轮收敛）

适用：任何复杂任务的 **plan.md** 写完（澄清完毕）后**自动进入本循环**，评审通过前不进入任务分解与实现。目标：**固定 3 轮内发现并修复全部问题**（R1 全量评审 → R2 修复核验 → R3 最终核验），禁止"每轮都发现新问题"的无限循环。

**评审触发规则（2026-09-14 用户拍板，防反复重审）**：**初版方案**（首次写完、澄清完毕）自动进入本循环；**之后任何修订**——含用户拍板的结构性变更、评审修复后的重写、增量模块扩充——**只有用户明确说"走评审"才启动评审**，否则不自动重审、直接进入用户确认环节。理由：结构性修订反复触发全量评审会拖慢节奏（2026-09-14 文档目录重构方案因结构 3 次调整走了 6 轮评审），修订风险由方案自带验证步骤兜底。

**0. 前置自检（主会话，不占轮次）**：进入 R1 前，按下方维度清单逐条自查初稿，声明级缺口（数据流接续、匹配规则落点、测试可构造性、现状代码事实）一次写全——初稿质量是收敛前提，自检不过不进评审（2026-08-23 曾因初稿留白走 7 轮）。方案涉及领域的经验文件（pitfalls/best-practices）是维度 1（现状代码事实）/维度 8（三方依赖能力）的核对依据，自查时对照 pitfalls 的"正确姿势"与 best-practices 的既定做法逐条过一遍（见「经验记录使用规则」）。

**评审维度清单（固定，R1 全量覆盖；发现清单缺口时扩充并更新本节）**：

| # | 维度 | 检查内容 |
|---|------|---------|
| 1 | 现状代码事实 | 所有"现状/文件路径/既有行为"描述 grep 代码核实；问题陈述附具体例子，禁抽象措辞 |
| 2 | 数据流闭环 | 每条数据：谁产生 → 传哪个参数 → 落哪个输出/表/字段，全程无断点 |
| 3 | 接口一致性 | 新增/变更接口与既有契约一致（基类签名、参数名/默认值/返回类型、命名规范） |
| 4 | 结构改造随迁 | 改元组/返回形状/字段类型时枚举全量随迁点：调用点解包、失败路径 return 个数、按 index 取值（头部插字段整体移位）、docstring；消费侧同步改造 |
| 5 | 向后兼容 | 全部消费方（展示/落盘/测试样本）可在同一变更内同步；不搞 v1/v2 并存（见任务文件夹约定） |
| 6 | 边界与失败路径 | 空输入/超限/异常/降级全覆盖；枚举取值域明确；DB 事务失败分支 rollback |
| 7 | 测试可构造性 | 每个断言 fixture 真能构造该场景；无恒空/恒真死路径；回归用例覆盖既有坑 |
| 8 | 三方依赖能力 | 端点实测（代理与官方差异）、签名匹配、降级策略；占位明确标注 TODO + 原因；**图表渲染语义边界（clip/containData/轴 extent 等）必须以真实轴 extent 实测锚定，禁止数据极值近似**（2026-09-14 画线方案 R1–R3 教训：y 裁剪夹取目标两次近似均留空带，真实 extent 一次到位） |
| 9 | 跨链路覆盖 | AI 侧与平台侧（backend/frontend）双链路；提示词真实载体（prompts.py 注册表 vs templates/md 锚点） |
| 10 | 数值推导自洽 | 示例数值可推导；窗口/阈值口径与上下游一致 |

**打分规则（2026-09-13 起）**：每轮评审对每个维度打 0–10 分，**分数由该维度 findings 按锚定规则得出**（禁止脱离 findings 凭感觉打分，保证不同 agent 之间口径可比）：

| 分数 | 锚定条件 |
|------|---------|
| 10 | 该维度无 findings，设计可直接实施 |
| 8–9 | 仅 polish 级 findings（或不影响实施一致性的 minor） |
| 6–7 | 存在 major，或影响实施一致性的 minor |
| ≤5 | 存在 blocker，或该维度大面积缺口（设计不成立） |

- **维度级门槛：任一维度 <8 分 = 该维度还有必须修的 findings**，不得以整体 verdict PASS 放过——这就是"低于 8 分要优化"的落点
- 总分 = 各维度均分，仅作**质量趋势参考**（逐轮上报给用户看收敛轨迹），放行门槛只看 verdict + 维度级门槛（均分会掩盖单维度塌方，不作门槛）

**轮次职责**（每轮换新 agent，type: `claude`，只读不改文件）：

- **R1 全量评审**：按维度清单逐条全文评审，**findings 不设上限、全量收集**（禁止"留到下一轮"）。每条 finding 标注：位置（章节/行）、严重度、对应维度编号、建议修法。**按打分规则给 10 个维度各打 0–10 分（基线分）**。输出 verdict（PASS/FAIL）+ 各维度分数 + 按严重度排序的 findings。
- **主会话修复 R1**：一次性修完全部 findings，并强制两条纪律：① **同类表述同步修改**——同一概念在代码草图/决策表/契约/测试清单多处出现时全部一起改（残留未同步是下一轮 findings 的主要来源）；② **修复不得引入新矛盾**——改公式/口径前先推演与上游来源、下游消费的组合是否仍成立。
- **R2 修复核验**（delta 口径）：只核验两件事——R1 findings 的落地情况 + 修复点与周边文字的交互（同类表述残留/新引入矛盾），**只对本轮 findings 涉及的维度重新打分**（其余维度沿用上轮分数——每轮换 agent，全局重打会引入口径漂移）。verdict PASS + 无 blocker/major + **全部维度 ≥8 分** → 直接跳到收尾（快路径，2 轮完成）。
- **R3 最终核验**（R2 未通过时，仍 delta 口径）：核验 R2 残留修复的落地，重打分口径同 R2。
- **终止条件**：R2/R3 任何一轮**新发现 blocker/major（非前轮修复残留）→ 判定初稿不合格，终止循环、回到方案重写**（重写后重新进入 R1）——不靠无限开轮掩盖初稿质量问题。
- **收尾（主会话，不占轮次）**：**收尾门槛 = verdict PASS + 全部维度 ≥8 分**（<8 分维度的 findings 必须修到该维度 ≥8 才收尾）；然后按自检清单过一遍——① 数值推导自洽 ② 章节交叉引用措辞同步 ③ 编号连续 ④ 测试落点与承诺一一对应 ⑤ 新文案与既有约定一致；顺手修掉纯润色级问题（措辞/格式/示例数值）；状态更新为 `待确认`，请用户确认。

**严重度定义**：

- blocker = 方案不可实施（依赖接口不存在/机制不成立）
- major = 影响实施正确性（数据流断点/边界漏洞/测试测不到/消费方漏迁）
- minor = 影响实施一致性（描述与计划不一致，但按上下文可推断）
- polish = 纯润色（措辞/格式/示例数值），归并为一条

**评审 prompt 模板（每轮复用）**：被评审文件路径 + 修复背景（前轮 findings 清单与维度分数）+ 本轮任务（全量/delta）+ 维度清单 + 打分规则 + 输出格式（verdict + 各维度分数 + findings{位置/严重度/维度编号/建议修法}）。

**异常上报**：3 轮未收敛（R2 反复出现修复残留、维度分不升反降、R3 发现影响实施缺陷）→ 停下向用户汇报每轮发现类型的分布趋势与维度分数轨迹，判断是初稿问题还是维度清单缺口；清单缺口则扩充清单后再重审。

**评审踩坑史**（本机制每条规则的来源）：见 [docs/memory/pitfalls/workspace/评审循环踩坑.md](docs/memory/pitfalls/workspace/评审循环踩坑.md)。

### Code Review 规则

**每次复杂任务的代码写完之后，必须启动 subagent 做 code review**，review 范围以任务 plan.md 的"文件变更清单"为准。

流程：

1. 任务 README.md 状态更新为 `Code Review`
2. 启动 subagent（type: `claude`），prompt 包含：
   - 任务 plan.md 路径（含变更清单和设计意图）
   - 逐文件对照方案检查：字段命名一致性、接口签名匹配、边界条件处理、向后兼容、缺失占位
   - **代码逻辑正确性**：函数入参/出参是否与调用方匹配、条件分支是否覆盖所有情况、状态流转是否符合设计、是否存在死代码或不可达路径、异常处理是否到位
3. findings 严重度定义复用「方案评审机制」（blocker/major/minor/polish）
4. Review 发现的问题在修复后重新 review（最多 2 轮）
5. Review 通过后 README.md 状态更新为 `已完成`，执行知识库文档合并（docs/knowledge/）

> **CR 前主会话自查两类高发缺陷**（2026-09-12 踩坑，见 [docs/memory/pitfalls/workspace/评审循环踩坑.md](docs/memory/pitfalls/workspace/评审循环踩坑.md)）：① 中文枚举子串解析的否定识别做对双向（假否定/漏否定）；② 共享 PG conn 的查询循环每个 except 分支必须 `conn.rollback()`。

> 中小改动不需要 code review。

### 测试规则

- **自测禁止运行 AI 测试**：Claude 自测（自己跑 pytest 验证改动）时，一律排除依赖真实 LLM 的测试——即 tests/ 中用到 `real_llm` / `real_toolkit` fixture 的集成用例（如 `test_sector_news_analyst.py`、`test_sector_rotation_analyst.py::test_integration_*`），统一用 `-k "not integration"` 排除。这类测试**只能由用户手动调用**，Claude 不得自动运行。
- **`-k "not integration"` 排除不干净，全量自测前必须先 `grep -rl "real_llm\|real_toolkit" tests/`**：部分用真实依赖 fixture 的测试名不含 "integration"（实测 `test_sector_news_analyst.py`、`test_sector_tech_analyst.py` 的全部用例、`test_sector_rotation_analyst.py` 的单元测试以外的集成用例），按名排除不掉 → 真实 LLM 调用 + Tushare 拉取会让全量自测挂起 10 分钟以上。列出含真实依赖的文件后逐个按文件名排除，或只跑与本任务相关的测试文件 + 无真实依赖的目录（position/screening/risk_gate/graph/utils/templates/event_study/dataflows）。坑的完整故事见 [docs/memory/pitfalls/ai/testing-llm-exclusion.md](docs/memory/pitfalls/ai/testing-llm-exclusion.md)

### 经验沉淀规则（pitfalls + best-practices）

**踩坑记录与最佳实践一律不写进 CLAUDE.md，沉淀到 `docs/memory/`，按 good/bad 两类 + 领域分类存储**：

```
docs/memory/
├── index.md                  ← 两类文件的总索引（新增文件后同步更新）
├── pitfalls/                 ← bad：踩坑记录（"XX 踩坑/实测/曾导致/表象为"类内容）
│   ├── backend/{主题}.md     ← 后端/数据库/平台服务（FastAPI、Dramatiq、PG、Redis、alembic…）
│   ├── frontend/{主题}.md    ← 前端（React Query、openapi codegen…）
│   ├── ai/{主题}.md          ← AI 分析链路（dataflows、Tushare、checkpoint、提示词、store…）
│   └── workspace/{主题}.md   ← 跨领域（流程、环境、评审机制自身）
└── best-practices/           ← good：最佳实践（已验证有效的做法/统一约定/机制设计）
    ├── backend/{主题}.md
    ├── frontend/{主题}.md
    ├── ai/{主题}.md
    └── workspace/{主题}.md
```

**写入规则**：

- **一个主题一个文件**（主题 = 模块/领域，如 `pitfalls/backend/dramatiq-windows.md`）；同一主题的新内容**追加到既有文件**（按时间倒序），不新建散文件
- **分类判据**：**"做错了会坏"的教训 → pitfalls**（文件格式：顶部一句话结论，正文按"表象 → 根因 → 正确姿势"分条，附代码位置引用）；**"照着做能对"的做法 → best-practices**（已验证的机制设计、统一约定、可复用技巧，写入时注明验证方式或实测结论）
- **CLAUDE.md 只保留配套的强制规则**（一行式"必须/禁止"），故事与细节一律在 memory 文件里
- 新增经验三步：① 写入/追加对应 memory 文件 ② 若衍生出新强制规则，在 CLAUDE.md 对应章节加一行 ③ 更新 [docs/memory/index.md](docs/memory/index.md)
- 出现两类装不下的主题时，新建类别目录并同步更新本规则与 index.md

### 经验记录使用规则

经验文件**不会随会话自动加载**——不主动查就会重踩或重造轮子。以下时机必须先读对应文件：

1. **动手改代码前**：按改动领域先读 [docs/memory/index.md](docs/memory/index.md) 定位主题文件，再读全文——**pitfalls 告诉你别踩什么，best-practices 告诉你照着做什么**。领域对照：backend（路由/迁移/DB/Redis/异步任务）→ `backend/`；frontend（codegen 消费/弹窗/缓存/markdown 渲染）→ `frontend/`；AI 链路（dataflows/Tushare/checkpoint/提示词/store）→ `ai/`；跨领域流程/环境 → `workspace/`。CLAUDE.md 各领域章节的指针带关键词，可按词快速定位
2. **写方案时**：方案涉及领域的经验文件是「方案评审机制」维度 1（现状代码事实）与维度 8（三方依赖能力）的核对依据——前置自检时对照 pitfalls 的"正确姿势"与 best-practices 的既定做法逐条过一遍
3. **实现收尾时**：Code Review 前，对照方案涉及领域的经验文件过一遍——pitfalls 的"正确姿势"逐条确认没重蹈覆辙（如 tushare 消费点升序归一、共享 PG conn 的 except 分支 rollback），best-practices 的约定确认已遵守（如 markdown 一律走 MarkdownView）
4. **排查异常现象时**：挂起/静默失败/行为异常，先按表象到 pitfalls 对应类别文件中找匹配的坑（多数坑文件按"表象 → 根因 → 正确姿势"组织，可从表象反查）；命中则按正确姿势处理，未命中且确认为新坑 → 按「经验沉淀规则」回写
5. **设计新机制/新模块时**：先扫 best-practices 同类主题——已验证的机制设计与统一约定直接复用，避免重新发明（如每日批处理可直接参考补跑三层触发设计）
6. **新经验回写后**：按「经验沉淀规则」把衍生的强制规则同步到 CLAUDE.md 对应章节，让后续会话不经查文件也能规避/遵守

### CLAUDE.md 自我更新规则

- **每次完成一个任务/分析（方案评审收尾、实现完成、Code Review 通过、踩坑解决）后，检查是否有值得沉淀的内容**，有则直接更新，无需用户提醒
- 值得写入 **CLAUDE.md**：新确认的约定或决策、流程规则变更、经验衍生的强制规则（一行式）、用户明确要求"记住"的内容
- 值得写入 **docs/memory/pitfalls/**：踩坑的故事与细节、技术细节参考（新数据源/新端点用法细节、库机制坑）
- 值得写入 **docs/memory/best-practices/**：已验证有效的做法、统一约定、机制设计（非坑的正向沉淀，如"这个方案实测无差距，后续可直接复用"）
- 不写入任何地方：任务本身的状态与进度（属于任务 README.md 状态块）、一次性命令与临时信息、可由代码/git 推导的事实

### Data Provider 接口约定

**接口契约以 `BaseStockDataProvider` 基类为准**（[base_provider.py](AI/dataflows/providers/base_provider.py)）。
所有 Provider（AKShare、Tushare、未来新增）继承该基类。

**目录结构（2026-09-01 起）**：CN 市场提供器统一放在 `AI/dataflows/providers/cn/` 子包
（`cn/tushare.py`、`cn/akshare.py` 及仅被二者使用的纯函数模块 `cn/daily_matrix_utils.py`、
`cn/limit_ladder_utils.py`）；`base_provider.py` 留在 `providers/` 层作跨市场契约。
模块名不含 `_provider` 后缀，类名仍为 `TushareProvider`/`AKShareProvider`；引用一律走
`AI.dataflows.providers.cn.<模块>`，不留旧模块名兼容 shim（见 docs/requirements/archive/数据提供器目录重构方案.md）。

**设计原则：**
- 基类定义完整接口 + 默认"不支持"返回 → 子类按需覆写
- `interface.py` 通过 `hasattr(prov, 'method_name')` 动态检测可用方法
- 新增数据能力时，**先在基类加方法签名 → 优先在 TushareProvider 覆写**（AKShare 仅在 Tushare 无法覆盖时补充，见强制规则 2）

**强制规则：**

1. **新增方法必须先加到基类** `BaseStockDataProvider`，提供默认 `_not_supported()` 返回
2. **优先 Tushare 实现**：新数据能力默认只在 TushareProvider 覆写；仅当 Tushare 无对应接口/权限、且 AKShare 有对应能力时才在 AKShareProvider 覆写（如 AKShare 独有接口）。两 provider 都覆写时签名完全一致（参数名、默认值、返回类型）
3. **无法提供数据时**，不覆写基类方法即可（自动返回 `"数据不可用：{provider_name} 不支持 <功能>。"`）
4. **仅 `get_stock_data` 和 `get_stock_info` 为抽象方法**（`@abstractmethod`），子类必须实现
5. **返回格式统一为 `str`**（格式化 Markdown），仅 `get_stock_info` 返回 `dict`
6. **类属性** `GLOBAL_TECH_INDICES`、`AI_INDUSTRY_CHAIN`、`A_SHARE_CONCEPT_MAP` 在基类中定义为空 `dict`，子类覆写
7. **多数据块可用性门控契约**（板块轮动预测分析师引入，2026-08）：node 内多块数据独立判定可用性时，各块正常输出统一以 `# ` 开头（代码用 `startswith("#")` 判定），所有不可用/异常返回串（含空串/None）一律**不得以 `#` 开头**——新增数据函数返回不可用/异常串时必须遵守，防止门控误判（见 docs/requirements/archive/板块层轮动数据增强方案.md 3.3.1）
8. **结构化接口例外**（2026-08-24 板块层热力图引入）：新增**结构化返回**（dict/DataFrame 消费方）的接口方法，基类默认返回 `None`（不返回 `_not_supported()` 的 str，避免破坏 dict/DataFrame 消费方）；不支持/失败时返回 `None`。已有 3 例：`get_industry_daily_returns_matrix`、`get_concept_daily_returns_matrix`（见 docs/requirements/archive/板块层轮动战术与政策事件流方案.md 2.1）

### 三方依赖能力评估

- **技术方案文档必须包含三方依赖能力评估**：逐项确认方案所依赖的第三方库（AKShare、LangGraph、LangChain 等）是否有足够能力实现诉求
- 评估要点：
  - **不仅看端点是否存在，更要看能否拿到所需数据**：单个 API 能否直接满足？还是需要多个 API 组合（如 IPO 日历 = 申购 + 上市两个接口；资金流向 = 北向 + 主力两个接口）？组合后的覆盖是否完整？
  - 每个新增数据函数的入参和出参与 AKShare/Tushare 端点的实际签名是否匹配（参数名、日期格式、返回字段）
  - LangGraph 的 subgraph / StateGraph 机制是否支持方案的图谱架构
  - 需占位的工具明确标注"占位 + TODO + 缺失原因"，不假装可实现
- 如果某个依赖能力不确定（端点覆盖不全、字段缺失、需要降级），必须在方案中标记为风险并给出降级策略
- 此评估写入方案文档的独立章节（如"三方依赖能力评估"）

## 项目配置

### pyproject.toml 优先
- **所有 Python 项目使用 `pyproject.toml` 管理依赖**，不使用 `requirements.txt` 作为主要依赖声明
- 构建后端：`setuptools.build_meta`（`setuptools>=61.0`）
- 安装命令：`pip install -e .` 或 `uv pip install -e .`

### LangGraph 图结构提取约定

- **确定性拓扑必须用 `compiled.builder`**（`builder.nodes` 声明序、`builder.branches[src][router_key].ends` 保留条件目标声明序）——`get_graph()` 的 edges 是 set 无序，不能用于确定性顺序提取。实现见 AI/graph/topology.py
- 正确姿势详见 [docs/memory/best-practices/ai/langgraph-topology.md](docs/memory/best-practices/ai/langgraph-topology.md)

### 单Agent重跑与提示词编辑

- **提示词单一事实来源 = `AI/utils/prompts.py`**（`DEFAULT_PROMPTS`，键 = 拓扑节点 id）；checkpoint guard 在层构建器接线处统一注入（`AI/utils/checkpoint.py`）
- 关键不变量：平台保留 key（`_rerun_from` 等）必须在 AgentState 声明；重跑目标为环成员时入口上移到环入口（`_LOOP_ENTRY`）；resolve 目录链只接受含 complete.json 的目录
- 完整机制与坑见 [docs/memory/pitfalls/ai/prompts-checkpoint-rerun.md](docs/memory/pitfalls/ai/prompts-checkpoint-rerun.md)

### 市场层证据驱动与事件路由（T6）

- **templates md 含 JSON 结论块保留单花括号**，工厂一律 `prompt.partial(output_format=...)` 注入；**纯代码节点**必须在 `AI/utils/llm_callbacks._NODE_LAYER` 登记 layer 前缀；**结构化 State 字段**（market_regime 等）消费方一律经 `format_*_summary` 渲染，`risk_gate` 枚举 fail-closed → caution
- 完整约定见 [docs/memory/best-practices/ai/market-t6.md](docs/memory/best-practices/ai/market-t6.md)

### 前端包管理器（pnpm）

- **frontend 是 pnpm 布局**，依赖操作一律 `pnpm add` / `pnpm install` / `pnpm run`——`npm install` 会直接报错
- 前端 API client 由 `pnpm run generate:api` 生成；**backend 改路由后必须先 `python -m backend.scripts.export_openapi` + `pnpm run generate:api` 再动前端消费代码**（并发编辑下未重导出会让他人 typecheck 失败）
- 坑与细节（codegen tags 分组、Literal 必选/可选两种形态）见 [docs/memory/pitfalls/frontend/pnpm-openapi-codegen.md](docs/memory/pitfalls/frontend/pnpm-openapi-codegen.md)

### 前端 markdown 渲染约定

- **所有 markdown 字符串内容一律经统一组件 `MarkdownView`**（`frontend/src/shared/ui/markdown.tsx`）渲染，不得手写 `whitespace-pre-wrap` pre 或 dangerouslySetInnerHTML 式注入；内容按 kind 分流（md/json/txt）
- 统一渲染约定详见 [docs/memory/best-practices/frontend/markdown-render.md](docs/memory/best-practices/frontend/markdown-render.md)；React Query/弹窗交互坑见 [docs/memory/pitfalls/frontend/react-query-dialog.md](docs/memory/pitfalls/frontend/react-query-dialog.md)

### 每日批处理触发方式（2026-08-31 起）

- **方案 B（推荐）：常驻自调度**——APScheduler 挂在 eventStudy FastAPI lifespan，每天 08:30 以子进程触发 `python -m AI.eventStudy.scheduler.daily_job`；睡眠/宕机靠三层补跑（cron 触发、服务启动自检、每 15 分钟周期自检）补救
- 运行约束：uvicorn **单 worker、禁用 --reload**（否则调度器重复启动）；Windows 守护用 NSSM
- 完整机制细节（防重复标记、完成标记、方案 A schtasks）见 [docs/memory/best-practices/ai/eventstudy-scheduler.md](docs/memory/best-practices/ai/eventstudy-scheduler.md)

### 调试步进模式（Debug Step Mode）

- 开启：运行分析前设 `LIVEPROFIT_DEBUG_STEP=true`，分析进程在 DP 响应 / LLM 调用前 / 节点 res 三检查点暂停；`streamlit run AI/logviewer/app.py` 的「调用时序」tab 点【✅ 下一步】/【⏭ 跳过全部】继续
- 协调机制详见 [docs/memory/best-practices/ai/debug-step-mode.md](docs/memory/best-practices/ai/debug-step-mode.md)

### 后端平台踩坑（backend/）

- 全部沉淀在 [docs/memory/pitfalls/backend/](docs/memory/pitfalls/backend/)（完整清单见 [docs/memory/index.md](docs/memory/index.md)）——包括但不限于：Windows PG/redis 循环与 host 归一化、alembic ASCII/JSONB 归一/pydantic alias、Dramatiq 进程内模型、Thread/Future 桥接、FastAPI OpenAPI、DB 测试隔离与 Redis 数据安全、PG 参数上限分批
- **动 backend 涉及上述领域前，先读对应文件**

### 证券市场数据库（db.instrument 包）

- `AI/dataflows/store/` 六表（public schema）旧实现已删除——统一实现为 `db/instrument/`（market schema，见下方命名规范）；存量事务约定/回填断点续跑/写入约定已迁入 [db/instrument/ingest/](db/instrument/ingest/)（经验见 [docs/memory/pitfalls/ai/store-daily.md](docs/memory/pitfalls/ai/store-daily.md)）

## 项目结构约定

```
项目根/
├── pyproject.toml          # 项目配置（必需）
├── README.md
├── main.py                 # 入口（被 pyproject.toml 的 scripts 引用）
└── 包名/                   # Python 包（与 pyproject.toml name 对应）
```

## 环境配置
- 使用 `.env` 或 `.bash` 文件管理环境变量
- `python-dotenv` 用于加载 `.env` 文件
- 敏感信息（API Key）不提交到 Git

### Git Bash 运行脚本注意（Windows）

- `cmd.exe /c "start ..."` 在 Git Bash 下的 `/c` 会被 MSYS 路径转换破坏，可能让 cmd 进入**交互式会话挂起脚本**（终端出现 cmd 横幅后卡住，后续步骤不再执行）。打开浏览器/外部程序改用 `powershell.exe -NoProfile -Command "Start-Process '...'"`，并加 `</dev/null` 兜底防交互式挂起（见 run.sh）

### Tushare 代理端点与数据接口（关键不变量）

- **日线消费必须先 `_sort_asc_by_trade_date` 升序归一**（端点返回降序，直接 tail 取到最旧数据）；**全市场拉取禁区间查询**（静默截断，必须 trade_date 单日 + 截断降级分批）；**概念成分参数硬约束**（ths 用 ts_code、dc 用 ts_code+trade_date）；**技术指标不自算**（指数走 idx_factor_pro、个股走 stk_factor_pro，stk_factor 旧端点已废）
- 代理端点 URL 覆写、端点子日志与其余细节见 [docs/memory/pitfalls/ai/tushare-endpoints.md](docs/memory/pitfalls/ai/tushare-endpoints.md)

### 证券市场数据库命名规范（2026-09-13 起，与用户共同制定）

market schema（liveprofit 库）内所有表/列/代码命名以此为准；旧表（store 六表 / market_bars_daily / market_index_factors 等）不回溯改名（随迁移废弃）。实现见 docs/requirements/archive/证券市场数据库统一方案.md。

| 事物 | 命名 | 说明 |
|------|------|------|
| 标的 | `market.instrument` | 主表：股票/基金/指数统一目录，一代码一行；通用列 ts_code/name/instrument_type/list_date/delist_date/list_status/data_source/updated_at（**无 market 列**——US/KR 原 symbol 与 CN 代码格式天然不冲突，ts_code 全局唯一；**exchange 属股票差异列，放 stock_info**）；**差异数据一律进信息表，不塞主表** |
| 日线 | `market.instrument_daily` | 交易日行情（股票/基金/指数同构一张表，store 决策 1 已证无差距），主键 (ts_code, trade_date) |
| 复权因子 | `market.adj_factor` | 主键 (ts_code, trade_date) |
| 技术因子 | `market.factor_daily` | 指数∪个股列并集宽表，与日线同频对齐 |
| 板块 | `market.sector` + `market.sector_member` | 同花顺板块体系字典 + 成分关系：行业/概念/特色三类板块（**type 区分 N/I/S**，与 source（ths/dc）正交）；首期存量仅概念板块（恒 'N'），'I'/'S' 增量采集后续阶段；PK (source, sector_code) |
| 板块日线 | `market.sector_daily` | 板块指数日行情（ths/dc 口径），PK (source, sector_code, trade_date)；热度现场计算的数据底座（heat_v1 热度 = pct×0.6 + vol×0.4，backend 读本表现场算，不落快照表不进 Redis——concept_hotness_snapshots 已删）；**dc 源每日增量积累**（dc_daily 窗口型端点，历史自采集启动日积累不回溯——ths_daily 全历史能力已实测、暂缓；板块概念Treemap方案 2026-09）——热度口径 = dc（原口径不变） |
| 行业 | `market.industry` + `market.industry_member` | 申万 SW2021 字典 + 成分关系（**与同花顺板块是两套体系：申万=互斥完备分类，sector=多对多标签**），**与 sector 完全对称：列 source / industry_code / name / count；主键含 source（industry PK (source, industry_code)、industry_member PK (source, industry_code, ts_code)），多来源共存（SW2021 等）；industry_code 为 tushare index_classify 原名，名称列统一 name（与 sector 对齐）** |
| 信息表 | `market.{instrument_type}_info` | fund_info / stock_info；主表差异数据按资产类拆信息表，命名后缀 `_info`（不用 `_extension`）；**列名沿用 tushare 原名（含跨表同名不同口径的 market：stock_info.market=上市板块、fund_info.market=场内/场外 E/O，与 tushare stock_basic/fund_basic 现状一致）** |

通用规则：

- **表名单数**；列名 snake_case；主键含时间序列的表一律 `(ts_code, trade_date)` 复合主键 + `(trade_date, ts_code)` 反向索引
- 时间列：`trade_date`（DATE）；行写入时间**全表统一** `updated_at`（TIMESTAMPTZ）——原 instrument_daily/factor_daily 的 source_updated_at 例外废除（2026-09-13 拍板：覆盖式写入下"来源入库时间"与"行更新时间"无实质区别，来源维度已由 source 列承担）
- 单位约定：价格元、vol 手、amount 千元（上游 tushare 原值口径，不换算）
- 取值域：instrument_type ∈ index/stock/fund；data_source ∈ tushare/akshare
- 代码格式：CN 资产 ts_code = 6 位数字 + `.SH`/`.SZ`/`.BJ` 后缀；US/KR 用原 symbol（如 `.INX`）——两种格式天然不冲突，ts_code 全局唯一
- db.instrument 包 DAO 模块名 = 表名（instrument.py / instrument_daily.py / adj_factor.py / factor_daily.py / sector.py / sector_daily.py / industry.py / fund_info.py / stock_info.py）；**成分表豁免**：sector_member/industry_member 写入并入 dao/sector.py / dao/industry.py，不单设模块
