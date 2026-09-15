# 单Agent重跑与提示词编辑机制

> 一句话结论：提示词单一事实来源 = `AI/utils/prompts.py`（DEFAULT_PROMPTS）；checkpoint guard 在层构建器接线处统一注入；平台保留 key 必须在 AgentState 声明；重跑目标为环成员时入口上移到环入口。

## 提示词注册表（2026-09-10）

- `AI/utils/prompts.py` 是 23 个 LLM 节点默认提示词的单一事实来源（`DEFAULT_PROMPTS`，键 = 拓扑节点 id；US/KR 4 分析师为折叠节点仍入表、v1 不可编辑）。
- **A 类 15 工厂**（ChatPromptTemplate）走 `system_message(node_id, lambda: 模板)`——覆盖命中返回**静态 SystemMessage**（花括号原样进 LLM；langchain 元组 `("system", 文本)` 会被当模板解析，含 `{xxx}`/JSON 示例即 KeyError，实测 1.5.3）。
- **B 类 8 工厂**（f-string 纯字符串 `llm.invoke`）走 `get_system_prompt`。
- 覆盖注册表经 `init_state["prompt_overrides"]` 快照注入（propagate 入口 set_overrides），执行开始读库 → 排队/运行中任务不受后续编辑影响。

## checkpoint 存档

- `AI/utils/checkpoint.py`——guard_checkpoint 包装器在层构建器接线处统一注入（快进 + `_current_node_id` + 节点后落盘）。
- 落盘路径：`{run_dir}/checkpoints/{layer}[/{ticker}]/{Sanitized}.json` + `__init__.json` + `complete.json`（成功收尾原子写）+ `rerun.json`。
- **resolve 目录链只接受含 complete.json 的目录**——部分执行目录的环中态 checkpoint 会让被跳过环成员出口路由无限循环。

## AgentState 白名单坑

- langgraph 按 schema channels 白名单**静默丢弃**未声明输入键——`_rerun_from`/`_current_node_id`/`selected_layers` 等平台保留 key 必须在 AgentState 声明，否则快进 guard 失效/跳过落盘判定恒空（Code Review 实测）。

## 环入口上移

- 重跑目标为辩论/风险环成员（Bull/Bear/Risky/Safe/Neutral）时 entry 与 `_rerun_from` 上移环入口（`_LOOP_ENTRY`，键为完整 node_id）——环整体重演，否则被跳过环成员出口路由返回 map 外目标 KeyError 崩溃。

## 重跑触发源

- 消息级参数（outbox payload → actor kwarg），`analysis_tasks.rerun_from_node_id` 列仅展示（claim 时非 rerun 消息清列）。
- entry 查找沿 attempt 目录链回溯（worker 侧链起点 = attempt_no-1，claim 时已递增）；attempt 链构造共享 `attempt_chain_dirs`（三处消费，禁止各自内联）。

## 前端相关坑

- `RegExp.test` 不得带 `g` 标志（lastIndex 跨求值残留）；弹窗回填用显式 `userEditedRef` 标记交互（不得以 text 是否为空推断——清空后 entry refetch 会回写服务端文本）。详见 [frontend/react-query-dialog.md](../frontend/react-query-dialog.md)。
