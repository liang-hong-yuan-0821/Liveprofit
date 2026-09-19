# git status --porcelain 对不存在路径静默返回空

**一句话结论**：`git status --porcelain <路径>` 在路径不存在时**静默返回空输出（exit 0）**，与「文件未修改」的输出完全一样——用它核验文件是否被改前，必须先确认路径真实存在。

- **表象**：2026-09-19 移除 Streamlit 任务 Code Review R1，评审 agent 核验 `backend/modules/analysis/infrastructure/execution_control.py` 是否被并发修改，报告「相对 HEAD 均未修改（git status --porcelain 空）」；主会话实测同一文件为 ` M`（+17/-5）。两轮才纠正。
- **根因**：查询时路径拼错为 `backend/modules/analysis/application/execution_control.py`（目录应为 `infrastructure`）；git 对不存在的路径**不报错**，只输出空串，被误读为「无改动」。同一批命令的 `git diff --stat` 输出里其实已出现正确路径（diffstat 按目录汇总列出 `infrastructure/`），agent 未做比对。
- **正确姿势**：
  1. 核验「某文件是否被修改」前，先用 `ls` / `git ls-files <路径>` 确认路径存在；`git ls-files` 对不存在路径同样静默空，但可与「路径存在但未跟踪」区分不开——最稳的是先 `test -f` 或 `ls` 再看 porcelain。
  2. `git status --porcelain` 结果为空 ≠ 目标文件未改：先证明**查对了路径**再下结论。
  3. 涉及目录级核验时优先 `git diff --stat HEAD -- <目录>`（目录存在性由 diff 输出可见），比单文件 porcelain 更不易误读。
