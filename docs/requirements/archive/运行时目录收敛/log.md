# 时间线日志

按时间**倒序**追加（最新在上）。每条 = 日期 + 动作 + 结果/备注。

| 日期 | 动作 | 结果/备注 |
|------|------|----------|
| 2026-10-02 | 根目录一级目录/文件用途分析 | 12 个运行时/缓存条目分三类：业务数据（var/logs/chroma_db/results）、工具缓存（ruff/pytest/pycache/egg-info/node_modules）、遗留垃圾（codex 日志/.pytest_tmp_quant/var 残留） |
| 2026-10-02 | 建立任务文件夹骨架并完成 plan.md 初版 | 8 文件全建；方案含 5 个设计模块与 1 个待确认问题（PYTHONPYCACHEPREFIX） |
| 2026-10-02 | R1 全量评审返回 FAIL | 10 major / 12 minor / 1 polish（核心：3 个 logs 代码写入方漏迁、相对路径未兑现 CWD 无关原则、验证命令不可执行） |
| 2026-10-02 | R1 findings 一次性修复 | plan.md 重写修复全部 22 条；新增 9 处代码写入方随迁、resolver 设计、backups/quant_history 拆分决策 |
| 2026-10-02 | R2 delta 核验返回 FAIL | 20/22 条落地；1 major（test_state_log_events 处方与 F5 绝对路径矛盾）+ C 组同类残留 + mkdir 边界；不触发重写条款 |
| 2026-10-02 | R2 findings 就地修正 | A/B/C/D/E + polish 全部修正；随迁清单补齐 14 文件 |
| 2026-10-02 | R3 最终核验 PASS | 全维度 ≥8 满足收尾门槛；N1-N4 新发现（minor/polish）收尾修正；方案进入待用户确认 |
| 2026-10-02 | 用户确认方案 | 拍板：var 内性质分层 7 子目录 + PYTHONPYCACHEPREFIX 启用；logs/runs/results 合并分析结论不合并 |
| 2026-10-02 | 任务分解完成 | 5 任务：T1 代码收敛 → T2 run.sh/缓存 → T3 文档同步 → T4 迁移+启动 → T5 清理；开始 T1 |
| 2026-10-02 | T1 验收完成 | 定向 20 passed；CWD 无关验证通过；backend.analysis 121 passed；ai.graph 79 passed + 1 存量失败（test_prompts 计数漂移，登记 issues） |
| 2026-10-02 | T2 验收完成 | run.sh 启动/停止 + 三缓存落位 var/cache 验证通过 |
| 2026-10-02 | T3 验收完成 | 14 文件随迁 + catalog 再生成 + exclude/gitignore/dockerignore；var/tmp ACL 阻塞已获授权解除 |
| 2026-10-02 | T4 验收完成 | 三目录数据迁入 var/（backups/quant_history 嵌套当场修正），启动 + 执行日志页历史可读 |
| 2026-10-02 | T5 验收完成 | 垃圾清理 + 17 个 ACL 异常目录移出仓库隔离；根/var 纯度达标；整体 code review 中 |
