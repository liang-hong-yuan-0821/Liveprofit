# 复盘

## 做对了什么（可复用）

- **依赖排序先推演**：tasks 拆解时把方案 4.2/4.3/4.4 的顺序按删除依赖重组（T2 平移先行是 T3 删 step_gate、T4 删 logviewer 的硬前提），并把「中间态验收必然不全绿」（T2 后 smoke 测试必挂、T4 删前 grep 必残）预置进验收口径——实现全程零顺序返工
- **验收 grep 口径预先校准**：开工前先实测 grep 命中的真实分布（如 `AI.logviewer` 模式因正则 `.` 匹配斜杠会命中 `AI/logviewer` 路径字面），验收标准按「剩余命中全部位于后续任务范围内」而非盲写「归零」，避免任务间互相卡死
- **并发工作区纪律**：全程按路径精确 Edit、禁整文件覆盖、禁 `git add -A`——用户并发改动（quant_strategy/trends 等）零误伤，Code Review 也确认本任务文件 diff 仅含方案指定改动

## 踩了什么坑（教训）

- **评审 agent 路径核验陷阱**：R1 评审把 `backend/modules/analysis/infrastructure/execution_control.py` 误写成 `application/` 前缀，`git status --porcelain <不存在路径>` 静默返回空输出（exit 0），被误读为「文件未修改」并写进报告结论。主会话用正确路径复验才纠正。教训已沉淀 docs/experience/pitfalls/workspace/git-status-porcelain-empty-path.md
- **尾注日期语义**：方案 4.6.1 指定尾注日期为拍板日（2026-09-16），实施后尾注表达的是「移除日」语义，两者不一致被 R1 评审点出——文案写日期时应先明确是决策日还是事件发生日
- **「全仓零 streamlit」字面口径**：T5/T7 验收用 `import streamlit` 口径通过后，logs_reader.py docstring 的「无 streamlit 依赖」仍是全仓唯一 streamlit 字面残留（R1 polish 1）——归零类任务的顶层目标要按「字面归零」而非「功能归零」设最终口径

## 下次改进

- 归零类任务的终验直接上「目标词全仓字面 grep（含大小写不敏感变体）」，不依赖各子任务的分项口径
- Code Review prompt 里对「核验文件是否被修改」类检查附上正确的全路径清单，避免 agent 自拼路径
