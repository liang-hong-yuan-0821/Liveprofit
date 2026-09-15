# 问题与解决

评审 findings 与实施中遇到的问题。按时间**倒序**追加，每条三要素：表象 → 根因 → 解决。

## 2026-09-14 T1 迁移中 plans 文件未跟踪导致 git mv 报错

- **表象**：`git mv docs/plans/指数K线图交互优化方案.md …` 报 `fatal: not under version control`；循环内中文文件名出现乱码导致 mv 失败。
- **根因**：plans/ 下 3 个方案文件均为未跟踪文件（与数据库表结构.md 同因）；Git Bash 循环变量拆分损坏中文文件名。
- **解决**：未跟踪文件改用 `mv`；放弃循环、逐条显式命令执行，全部迁移成功。

## 2026-09-14 R1 评审 findings（1 blocker + 8 major + 10 minor，已全部修复）

- **表象**：任务文件夹制方案 R1 verdict FAIL。
- **根因**：① plans/ 实际 4 文件漏列 Treemap（blocker）；② archive 归档深一层必断链无规则；③ 规则 D 路径算术两处错（产品需求分析 2 层 vs backend 3 层）；④ 进行中方案自身链接无规则；⑤ archive 同目录裸链接 6 处 + 死链漏 1 条；⑥ CLAUDE.md 漏 Code Review/自我更新两章节；⑦ 计数口径多处不自洽（34 vs 30 等）。
- **解决**：逐条按实测修法修复并写入方案（规则 A–K、归档上移一层、§3.4 九点）；按新评审触发规则不再走 R2/R3，验证交给 T7 集合差脚本。

## 2026-09-13 前两版评审（6 轮收敛史）

- **表象**：第一版（knowledge 三目录）R1 FAIL → R2 PASS；第二版（workflow 平铺桶）R1 FAIL → R2 FAIL → R3 PASS。
- **根因**：方案每次结构修订都触发全量评审；评审多次抓出真实问题（git mv 失败模式、25 条 ../../ 链接、memory/模板/代码注释裸路径引用）。
- **解决**：修复全部 findings；沉淀「评审触发规则」到 CLAUDE.md 防反复重审。
