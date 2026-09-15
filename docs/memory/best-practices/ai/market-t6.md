# 市场层证据驱动与事件路由约定（T6，2026-09-11）

> 一句话结论：templates md 含 JSON 结论块保留单花括号、工厂一律 `prompt.partial` 注入；纯代码节点必须登记 `_NODE_LAYER`；结构化 State 消费一律经 `format_*_summary` 渲染。

## 输出格式模板的花括号约定

- `AI/templates/**/*.md` 含 JSON 结论块时保留**单**花括号（禁 `{{` 转义），工厂一律 `prompt.partial(output_format=...)` 注入。
- **根因**：partial 值不参与 langchain 模板解析（文本替换注入会把模板内容并入模板再解析 → JSON 单花括号 KeyError，实测）；backend 展示走 `render_default_prompt` 的纯文本 `.replace`，与运行一致。
- 已落地：`market/cn_tech_analyst`、`market/cn_news_analyst`、`market/international_news_analyst`。

## 纯代码节点接入约定（`market:Risk Gate` 先例）

- 无 LLM/无工具/无提示词（不入 `DEFAULT_PROMPTS`），但**必须在 `AI/utils/llm_callbacks._NODE_LAYER` 登记 layer 前缀**（否则 `guard_checkpoint` 包装 KeyError）。
- 节点不产生日志目录 → 后端拓扑状态视图显示 `not_executed`（预期，状态判定以日志目录为准）。

## 结构化 State 字段消费约定

- `market_regime`/`market_event_calendar`/`global_risk_assessment` 为 dict，消费方一律经 `AI/dataflows/market_features` 的 `format_*_summary` 渲染为紧凑 Markdown 再注入（禁止 `len(str)>N` 判定与直接 str 拼接）。
- `risk_gate` 为 `normal/caution/block` 枚举（关键数据不足 fail-closed → caution），仓位管理层零改动。
