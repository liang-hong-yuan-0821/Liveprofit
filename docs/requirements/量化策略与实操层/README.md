# 量化策略与实操层

> **状态**：待确认（2026-09-16，方案已按后续评审修订量化执行职责边界，已按存量表对照修订数据表设计，完成章节结构优化，并按用户「不回测、只按条件选股」砍掉行情冻结快照表、改为执行时实时扫描；等待用户确认后进入任务分解）
> **进度**：0/0 任务（方案待确认，未拆分）
> **下一步**：用户确认方案 → 生成 tasks.md 任务清单
> **关联文档**：[plan.md](plan.md)（方案正文）｜[tasks.md](tasks.md)（拆任务清单）｜[log.md](log.md)（时间线）｜[decisions.md](decisions.md)（决策）｜[issues.md](issues.md)（问题）｜[result.md](result.md)（产出结论）｜[retrospective.md](retrospective.md)（复盘）

## 任务总览

- **目标**：新增独立于 AI 层的量化执行通道——版本化受限 `strategy(context)` 全市场扫描、组合资金/风控账户、策略/组合任务快照（行情执行时实时读取）、结构化量化报告与建议订单（需人工确认、不自动下单）；AI 层本方案不改造、不依赖，统一接入留待后续任务
- **负责人**：qiyanqiao
- **骨架文件**：[plan.md](plan.md)（方案正文）｜[tasks.md](tasks.md)（拆任务清单）｜[log.md](log.md)（时间线）｜[decisions.md](decisions.md)（决策）｜[issues.md](issues.md)（问题）｜[result.md](result.md)（产出结论）｜[retrospective.md](retrospective.md)（复盘）
