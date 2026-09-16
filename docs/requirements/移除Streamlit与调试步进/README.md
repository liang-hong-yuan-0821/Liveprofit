# 移除 Streamlit 与调试步进模式

> **状态**：待确认（2026-09-16）
> **进度**：2/7 步骤（现状盘点、方案评审 3 轮收敛 PASS）
> **下一步**：请用户确认方案 → 任务分解（tasks.md）→ 实现
> **关联文档**：[plan.md](plan.md)（方案正文）｜archive/事件研究审核界面平台集成方案.md｜archive/调试步进模式方案.md

## 任务总览

- **目标**：仓库零 streamlit 代码与依赖——删审核页（review_app）、日志查看器 UI（logviewer/app.py）、调试步进机制（step_gate 及三处接线）、streamlit 依赖与全部文档引用；logs_reader 数据层平移至 AI/utils/（平台执行日志依赖）
- **负责人**：qiyanqiao
- **骨架文件**：[plan.md](plan.md)（方案正文）｜[tasks.md](tasks.md)（拆任务清单）｜[log.md](log.md)（时间线）｜[decisions.md](decisions.md)（决策）｜[issues.md](issues.md)（问题）｜[result.md](result.md)（产出结论）｜[retrospective.md](retrospective.md)（复盘）
