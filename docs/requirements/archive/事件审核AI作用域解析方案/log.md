# 时间线日志

按时间**倒序**追加（最新在上）。每条 = 日期 + 动作 + 结果/备注；一句话能说清就不写长段。

| 日期 | 动作 | 结果/备注 |
|------|------|----------|
| 2026-09-16 | 现状排查 | 定位标题塌缩根因（08e9c34f 加两列后 minmax(0,1.2fr) 塌 0px）；实测 Redis 328 草稿：145 缺 scope / 162 market / 21 sector+stock；实测字典表可用性（instrument 5564 / industry 31 / sector dc 概念） |
| 2026-09-16 | 用户拍板两个决策 | ① 自动回填 + 强制按钮；② 美股先做 A 股概念映射（美股个股另立任务） |
| 2026-09-16 | plan.md 初稿 + 前置自检 | 自检发现 _open_existence 2→3 元组随迁点，补入 4.7 |
| 2026-09-16 | R1 全量评审 | verdict FAIL：B1 blocker（force 循环不收敛）+ M1/M2 major + 8 minor + 6 polish |
| 2026-09-16 | 修复 R1 findings | B1 改采纳评审选项 b（前端 draft_ids 分片驱动，弃版本标记方案）；M1 补 rollback；M2 随迁点全量枚举；minor/polish 一并修完 |
| 2026-09-16 | R2 修复核验 | verdict FAIL：15/15 R1 findings 落地，无 blocker/major；新发现 N1（两处既有用例 fixture 未随新谓词同步）、N2（force 字段无行为残留）、N3（7 项 polish） |
| 2026-09-16 | 修复 R2 findings | N1 两用例 fixture/断言同步写法写明；N2 删请求级 force 字段（覆写语义完全由 draft_ids 表达）；N3 七项润色全修 |
| 2026-09-16 | R3 最终核验 | verdict PASS：N1/N2/N3 全落地，维度 3 = 10、维度 7 = 9，全维度 ≥8；仅 3 项 polish（NP1/NP2/NP3）收尾修完 |
| 2026-09-16 | 评审收尾 | 状态更新为「待确认」，请用户确认方案 |
| 2026-09-16 | 用户澄清后修订 | 用户确认「LLM 不补代码」：新规则 8 改为「代码仅原文出现时输出，原文只有名称时只输出中文名称」；NameResolver 为名称→代码唯一映射源；方案正文/概览/测试断言同步更新（按评审规则不自动重审，直接进入用户确认环节） |
| 2026-09-16 | 用户确认方案，任务分解 | tasks.md 8 任务（T5/T7 可并行），README 转实现中 |
| 2026-09-16 | T1 完成 | NameResolver + 单测 17 passed |
| 2026-09-16 | T2 完成 | ai_prelabel 提示词/sanitize/回填谓词/force/TTL；tests/event_study 196 passed |
| 2026-09-16 | T3 完成 | backend schema/router/service/adapter/contracts；单测 35 passed |
| 2026-09-16 | T4 完成 | export_openapi + generate:api；契约门禁 2 passed + 契约 API 39 passed |
| 2026-09-16 | T5 完成 | 两处 GRID 标题列 minmax 下界修复（人工视觉检查留待用户） |
| 2026-09-16 | T6 完成 | 强制重填按钮分片循环 + 详情弹窗警示；vitest 59 passed + typecheck 干净 |
| 2026-09-16 | T7 完成 | Streamlit force checkbox 分片进度 + unresolved 警示（人工检查留待用户） |
| 2026-09-16 | T8 验证部分 | 全量验证 278 passed（后端侧）+ 59 passed（前端）；API契约.md 预填口径随迁完成；启动 Code Review |
| 2026-09-16 | Code Review 第 1 轮 | verdict PASS（无 blocker/major），6 条 minor/polish（F1 层级顺序/F2 包含未批量/F3 契约注释/F4 缺断言/F5 Streamlit 护栏/F6 口径确认） |
| 2026-09-16 | 修复 F1-F6 | F1 两层循环（全局精确优先）；F2 ILIKE ANY 批量化；F3 注释随迁；F4 补断言；F5 Streamlit prelabeled==0 提前终止；F6 契约文档补口径 |
| 2026-09-16 | Code Review 第 2 轮 | verdict PASS：F1-F6 全落地；新发现 N1（重复名称破坏唯一包含计数）、N2（大小写归属不等价）→ 收尾修复（dict.fromkeys + casefold）+ 补两用例 |
| 2026-09-16 | 收尾 | 全量验证 281 passed（后端侧）+ 59 passed（前端）；typecheck 报错确认全部位于用户并发开发的 analysis 模块（与本任务无关，未改动）；result/retrospective 填写；经验沉淀 docs/experience/pitfalls/frontend/grid-minmax-collapse.md + index 更新；任务文件夹归档 |
| 2026-09-16 | 用户拍板撤销 Streamlit 改动 | 「不要再改 streamlit 审核页了，不是已经全部改到 frontend 的页面了吗」——review_app.py 的 T7 UI 改动（force checkbox 分片 + unresolved 警示，31 行）全部撤销恢复 HEAD；AI 侧回填谓词对 Streamlit 自动生效无需 UI 改动；decisions/result/tasks 同步更新 |

> **使用方式**：复制本文件到 `docs/requirements/<任务名>/log.md`，随实施过程逐条追加。归档时定格。
