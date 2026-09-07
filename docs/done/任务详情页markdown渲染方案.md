# 任务详情页 markdown 渲染方案

> **状态**：已完成（2026-09-06，实现完成 + Code Review PASS（无 blocker/major，2 条 minor 已修复，delta 复评无新问题）+ 归档）
> **进度**：5/5 步骤（初稿完成 → 评审循环 → 用户确认 → 任务分解 → 实现）
> **下一步**：无。遗留：手工验收项待用户执行（真实任务详情页 markdown 观感 / 深色底可读性 / 窄窗口表格滚动 / 空白折叠观感，见 3.1.3 测试表）
> **关联文档**：[任务执行调用日志方案](../done/任务执行调用日志方案.md)（本方案改动其 3.3.1 的"不需要 markdown 库"决策）｜[前端平台技术方案](../done/前端平台技术方案.md)｜[产品需求分析](产品需求分析.md)

---

## 一、背景与动机

### 1.1、现状

- 任务详情页（`frontend/src/modules/analysis/pages/task-detail/`）有两处承载 markdown 内容的位置目前按**纯文本**渲染：
  1. **执行调用日志面板**：[ExecutionLogsPanel.tsx:377](frontend/src/modules/analysis/pages/task-detail/ExecutionLogsPanel.tsx#L377) 的 `FileContent`——`kind=md` 文件（LLM 提示词 req.md、LLM 输出 res.md、DP 出参 res.md）走 `<pre className="whitespace-pre-wrap break-words text-sm">`；
  2. **报告区**：[ReportContent.tsx:130](frontend/src/modules/analysis/pages/task-detail/ReportContent.tsx#L130) 的 `ReportSectionCard`——AVAILABLE 分区展开态正文走 `<p className="whitespace-pre-wrap text-sm leading-6">`。
- 项目**无任何 markdown 依赖**（`frontend/package.json` 确认）；执行日志方案 3.3.1 当时明确决策"渲染内容不需要 markdown 库"。
- 内容本身是重度 GFM：内核契约规定 Data Provider 返回**格式化 Markdown**（CLAUDE.md「Data Provider 接口约定」第 5 条），实测 `logs/2026-08-24_002449/sector/001_Sector_News_Analyst/001_get_industry_sector_performance/res.md` 含 `# / ##` 标题与 31 行表格；LLM 输出、报告分区正文同为 markdown 文档。纯文本渲染下表格、标题、列表全部不可读。
- 参考实现：TradingAgents-CN（Vue 3）用 `marked` ^16.2.0，`marked.setOptions({ breaks: true, gfm: true })` → `v-html` 注入 `.markdown-content` 容器，仅手写 h1–h3 样式（TaskResultDialog.vue / TaskReportDialog.vue / Queue/index.vue / ReportDetail.vue 共 4 处）。其 mermaid、vue3-markdown-it 依赖在 src 内无使用点。

### 1.2、目标

1. 任务详情页全部 `kind=md` 内容（LLM 提示词/输出、DP 出参、报告分区正文）按 **GFM**（表格/标题/列表/代码块/删除线）渲染为排版良好的文档；
2. 换行观感与现状对齐：单换行保留为断行（现状 `whitespace-pre-wrap` 保留单换行；GFM 规范下裸单换行会合并，需 breaks 语义对齐）；**连续多空格/制表符/行首缩进将按 markdown 语义折叠**（现状 pre-wrap 原样保留）——属预期变化，实测 DP 出参与 req.md 为规范 GFM 不受影响，验收时仅核对 LLM 输出无异常观感；
3. 非 markdown 内容渲染不变：`kind=txt`（工具 res.txt）保持纯文本 pre，JSON 保持格式化 pre（含 200KB 渲染保护）；
4. 既有交互不变：截断"查看完整内容"（content 端点）、自动展开最新 node、报告三态（UNAVAILABLE/NOT_REQUESTED/AVAILABLE）与折叠预览；
5. 不引入后端/内核改动；前端在 React 生态内实现，参考 TradingAgents-CN 的"GFM + breaks"语义而非其 v-html 注入方式。

## 二、架构设计

本方案**不改变现有架构**，纯前端展示层改动：新增一个共享渲染组件，两处消费方替换渲染分支。

```
MarkdownView（frontend/src/shared/ui/markdown.tsx，新）
  ├─ react-markdown@^10.1.0 + remark-gfm@^4.0.1 + remark-breaks@^4.0.0
  └─ 容器类：prose prose-sm prose-invert max-w-none（@tailwindcss/typography@^0.5.20，
     styles.css 以 @plugin 指令接入）
消费方：
  ├─ ExecutionLogsPanel.FileContent：kind=md → MarkdownView（内嵌内容与 content 端点
  │   全量内容同路径，FileBody 的 fetchFull 复用 FileContent）
  └─ ReportContent.ReportSectionCard：AVAILABLE 展开态正文 → MarkdownView
styles.css：@import "tailwindcss" 后新增 @plugin "@tailwindcss/typography"
```

### 2.1 数据模型设计

本方案不涉及共享状态或跨模块接口变更，无数据模型设计。

## 三、详细设计

### 3.1 前端 markdown 渲染

#### 3.1.1 模块设计

**新组件** `frontend/src/shared/ui/markdown.tsx`（模式同 collapsible.tsx：React 组件 + `cn` 工具）：

```tsx
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import { cn } from './utils';

// 任务详情页 markdown 内容的统一渲染组件：GFM + 单换行断行，深色 prose 样式。
// react-markdown 默认转义原始 HTML（无 rehype-raw），内容为内核生成的受信 markdown，无需净化。
export function MarkdownView({ content, className }: { content: string; className?: string }) {
  return (
    <div className={cn('prose prose-sm prose-invert max-w-none', className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{content}</ReactMarkdown>
    </div>
  );
}
```

- `prose-invert` 无条件加载：应用为 dark-first（[styles.css:6-14](frontend/src/styles.css#L6-L14) `:root` 直接写深色 token + `color-scheme: dark`，`.dark` 变体当前未用于主题切换），prose 默认浅色文字在深色底上不可读。未来引入亮色主题时再按 `.dark` 门控（加 `dark:prose-invert` 并去掉无条件 invert）。
- `max-w-none`：prose 默认 65ch 限宽，在面板 Card 内会产生不必要留白，统一取消限宽由容器约束。
- `prose-sm`：面板内容密度高；报告正文也用同规格，保持全页字号一致（模板取舍：不引入 size prop，见「四、自行决定的工程取舍」第 2 条）。

**ExecutionLogsPanel.tsx 的 `FileContent` 改造**（第 357~378 行）：kind 三分支显式化：

```tsx
if (kind === ExecutionFileDTO.kind.JSON) {
  // ……现有 JSON 分支不变（含 JSON_RENDER_MAX_CHARS 200KB 保护）
}
if (kind === ExecutionFileDTO.kind.MD) {
  return <MarkdownView content={String(content)} />;
}
return <pre className="whitespace-pre-wrap break-words text-sm">{String(content)}</pre>; // txt
```

- 语义差异说明（与参考实现 marked 对齐映射）：marked 的 `gfm: true` ↔ `remark-gfm`；`breaks: true` ↔ `remark-breaks`；raw HTML 透出（v-html）↔ react-markdown 默认**转义**——内核输出契约是格式化 Markdown 不含 raw HTML，转义是更安全的选择，无回退需求。
- `FileBody`（第 309~355 行）不动：truncated/parse_error 的"查看完整内容"流程拉回全量后仍走 `FileContent`，md 全量内容自动获得 markdown 渲染。
- LLM 提示词 req.md 同为 `kind=md` → 同样 markdown 渲染（统一规则，不特殊化；提示词中的 `#`/列表渲染后更可读）。

**ReportContent.tsx 的 `ReportSectionCard` 改造**（第 130 行，仅 AVAILABLE 展开态）：

```tsx
{section.content && <MarkdownView content={section.content} />}
```

- 折叠态预览（`summary ?? truncateContentPreview(section.content)`，第 119~120 行）**保持纯文本**：summary 是服务端生成的纯文本摘要，预览是经空白折叠的单段截断串（`truncateContentPreview` 定义于 toReportViewModels.ts:58-64），不是 markdown 文档，渲染 markdown 无意义。
- 三态其他分支（UNAVAILABLE / NOT_REQUESTED）与 dataSources / riskNote 均不动。

**styles.css**：第 1 行 `@import "tailwindcss";` 之后新增：

```css
@plugin "@tailwindcss/typography";
```

#### 3.1.2 三方依赖能力评估

| 依赖 | 版本 | 能力确认 |
|------|------|----------|
| react-markdown | ^10.1.0（npm 实测最新 10.1.0） | peer 依赖 react >=18、@types/react >=18——项目 React 19 满足。渲染为 React 元素（无 dangerouslySetInnerHTML），默认转义原始 HTML；v9 起不再内置 gfm 插件，表格必须靠 remark-gfm |
| remark-gfm | ^4.0.1（实测最新） | GFM 扩展：表格、删除线、任务列表、自动链接——DP 出参表格（实测 res.md 31 行表格）与 LLM 输出的核心诉求 |
| remark-breaks | ^4.0.0（实测最新） | 单换行 → `<br>`，对齐参考实现 marked `breaks: true`；不引入则 LLM 输出的裸单换行被 GFM 规范合并、观感回退 |
| @tailwindcss/typography | ^0.5.20（实测最新） | Tailwind v4 兼容（v0.5.16 起支持 `@plugin` 指令接入）；项目 styles.css 已是 v4 CSS-first 配置（`@import "tailwindcss"` + `@custom-variant dark`），无 tailwind.config，接入方式匹配。prose-invert 适配深色底 |
| 测试环境 | — | react-markdown 为纯 React 渲染，jsdom/vitest 可用；MarkdownView 不 import CSS，测试无需样式处理 |

无后端/内核/API 改动；不引入 mermaid 与语法高亮（TradingAgents-CN 的 mermaid 依赖在 src 内无使用点，YAGNI）。

#### 3.1.3 风险与验证方式

- **风险 1：prose 默认样式与深色主题冲突** → 无条件 `prose-invert`（3.1.1 已述）；人工验收观察标题/表格/代码块在深色底下可读。
- **风险 2：宽表格在窄容器横向溢出** → 实现时实测 typography 0.5.20 生成的 table 样式是否含溢出处理；若溢出，在 MarkdownView 容器类追加表格溢出工具类（如 `[&_table]:block [&_table]:overflow-x-auto`）。验证：人工检查窄窗口下 DP 出参 31 行表格横向可滚动。
- **风险 3：大内容渲染性能** → md 分支无 JSON 分支的 200KB 截断保护（原方案决策，truncated 已挡 >100KB 内嵌内容）；content 端点全量（≤10MB）仅在用户点击"查看完整内容"后渲染，v1 接受与现状 pre 同量级的极端场景。
- **风险 4：既有测试断言依赖纯文本渲染** → `ExecutionLogsPanel.test.tsx:103`（`findByText(/# 输出全文/)`）与 `:124`（`findByText(/# 完整内容/)`）在 markdown 渲染后 `# 输出全文` 变为 `<h1>输出全文</h1>`，`#` 不再是文本内容，两断言须改为**精确字符串匹配** `findByText('输出全文')` / `findByText('完整内容')`（testing-library 字符串默认全等匹配）——注意第 124 行场景按钮「**查看**完整内容」与 h1 同屏，若用 `/完整内容/` 正则会因双匹配抛错。其余断言（`A 结果`、`B 结果`、`决策正文` 等纯文本）渲染为段落文本节点，逐字匹配不受影响；全库扫描确认无第三处 `#` 断言。
- 验证方式（单测落点与命令）：

| 层 | 用例 | 落点/命令 |
|----|------|-----------|
| 前端 | MarkdownView：`# 标题` 渲染为 heading（getByRole）、GFM 表格单元格文本可见、单换行产生 `<br>`（相邻文本节点分行）、raw HTML 转义（`<script>alert(1)</script>` 以文本呈现不执行）、空串渲染无异常 | `frontend/src/shared/ui/markdown.test.tsx`（新建） |
| 前端 | ExecutionLogsPanel：既有 2 处 `#` 断言修正 + 新增用例（makeFile content 含 `## 二级` 与表格 → heading 与单元格文本可见） | `ExecutionLogsPanel.test.tsx`（修改） |
| 前端 | ReportContent：新增用例（decision content 含 `# 决策` → getByRole heading）；既有纯文本断言不变 | `ReportContent.test.tsx`（修改） |
| 前端 | 全量回归 + 构建 | `pnpm test`；`pnpm build`（tsc + vite，验证 @plugin 编译） |
| 手工 | 真实任务详情页：DP 出参表格/LLM 输出/提示词/报告正文的 markdown 观感、深色底可读性、窄窗口表格滚动、"查看完整内容"全量内容同样 markdown 渲染；另核对 LLM 输出无因空白折叠（连续空格/缩进）产生的异常观感（见目标 2） | 用户验收 |

#### 3.1.4 文件变更清单

- **新建文件**：
  - `frontend/src/shared/ui/markdown.tsx`：MarkdownView 组件（3.1.1 草案）；
  - `frontend/src/shared/ui/markdown.test.tsx`：组件单测（3.1.3 测试表第 1 行）。
- **修改文件**：
  - `frontend/package.json`：dependencies 新增 `react-markdown`、`remark-gfm`、`remark-breaks`；devDependencies 新增 `@tailwindcss/typography`（构建期插件，随 devDeps；安装命令 `pnpm add react-markdown remark-gfm remark-breaks && pnpm add -D @tailwindcss/typography`）；
  - `frontend/pnpm-lock.yaml`：随 pnpm add 自动更新（不手改）；
  - `frontend/src/styles.css`：`@import "tailwindcss"` 后加 `@plugin "@tailwindcss/typography";`；
  - `frontend/src/modules/analysis/pages/task-detail/ExecutionLogsPanel.tsx`：FileContent kind 三分支（md → MarkdownView）；
  - `frontend/src/modules/analysis/pages/task-detail/ExecutionLogsPanel.test.tsx`：2 处 `#` 断言修正（见风险 4）+ 新增 markdown 渲染用例；
  - `frontend/src/modules/analysis/pages/task-detail/ReportContent.tsx`：AVAILABLE 展开态正文 → MarkdownView；
  - `frontend/src/modules/analysis/pages/task-detail/ReportContent.test.tsx`：新增 heading 渲染用例。
- **删除文件**：无。

## 四、已确认决策 / 待确认问题

- **已确认决策**（2026-09-06，用户拍板）：
  1. 渲染引擎 = **react-markdown + remark-gfm**（不用 marked + DOMPurify 的 v-html 路线）；
  2. 范围 = **执行日志面板 + 报告区**（任务详情页两处纯文本渲染点）；
  3. 样式 = **@tailwindcss/typography prose**（不手写 markdown CSS）。
- **本方案自行决定的工程取舍**（如有异议请指出）：
  1. 一并引入 `remark-breaks`——对齐参考实现 TradingAgents-CN 的 `marked breaks: true` 语义，且现状 `whitespace-pre-wrap` 保留单换行的观感不因换库而回退；
  2. 统一 `prose-sm + max-w-none`、不设 size prop——面板与报告同规格、容器约束宽度，组件保持最小 API；
  3. `prose-invert` 无条件加载（应用当前 dark-first，无亮色主题）；
  4. LLM 提示词（req.md）同样 markdown 渲染——统一"kind=md → markdown"规则，不特殊化；
  5. 不引入 mermaid / 语法高亮 / DOMPurify——内核内容为受信 markdown，react-markdown 默认转义 raw HTML 已覆盖安全面。
