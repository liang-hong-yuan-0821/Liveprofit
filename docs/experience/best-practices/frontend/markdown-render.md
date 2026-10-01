# markdown 渲染约定

> 一句话结论：所有 markdown 字符串内容一律经统一组件 `MarkdownView` 渲染，禁止手写 pre/危险注入；按 kind 分流 md/json/txt；新增消费点复用 MarkdownView，勿引第二引擎。

## 统一组件 MarkdownView（2026-09-06 起）

- 位置：`frontend/src/shared/ui/markdown.tsx`。所有 markdown 字符串内容（LLM 提示词/输出、DP 出参 res.md、报告分区正文等）一律经它渲染，不得再手写 `whitespace-pre-wrap` pre 或 `dangerouslySetInnerHTML`/v-html 式注入。
- 技术栈：`react-markdown` + `remark-gfm`（GFM 表格）+ `remark-breaks`（单换行→`<br>`，对齐内核输出的换行习惯）；样式走 `@tailwindcss/typography`（styles.css 里 `@plugin` 接入）的 `prose prose-sm prose-invert max-w-none [&_table]:block [&_table]:overflow-x-auto`。
- **prose-invert 无条件加载**（应用 dark-first，`:root` 直接深色 token）；`[&_table]` 两个类兜底宽表格在窄容器的横向溢出（typography 本身不处理）。

## 内容类型分流（ExecutionLogsPanel.FileContent 与后续新增消费点遵守）

- `kind=md` → MarkdownView；`kind=json` → 格式化 pre（含 200KB 渲染保护）；`kind=txt` → 纯文本 pre；缺失/空内容显示占位。

## 安全与复用

- raw HTML 默认转义（react-markdown 无 rehype-raw），内核内容为受信 markdown 无需 DOMPurify；新增 markdown 消费点直接复用 MarkdownView，勿重复引入 marked 等第二引擎（见 docs/requirements/archive/任务详情页markdown渲染方案.md）。
