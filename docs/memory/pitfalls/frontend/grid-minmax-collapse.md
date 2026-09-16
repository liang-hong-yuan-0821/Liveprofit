# Tailwind grid-cols 任意值 + minmax(0, Xfr) 轨道塌缩

> 一句话结论：grid 模板中固定轨道总宽超过容器时，`minmax(0, Xfr)` 的弹性轨道按 0 下界收缩为 **0px**——整列（含表头文字）不可见且无任何报错；标题类弹性列必须给正下界（`minmax(14rem, 1.2fr)`）+ 容器 `overflow-x-auto`。

## 表象（2026-09-16 实测）

- 事件研究审核表格（`PendingEventRow.tsx` 的 `PENDING_ROW_GRID`）在 08e9c34f 提交新增「作用域(8rem)+目标(12rem)」两列后，用户反馈「标题没有展示出来」——标题列（`minmax(0,1.2fr)`）连同表头「标题」二字整列消失，无控制台报错、无测试失败。
- 14 列固定轨道总和 93rem + 13×0.5rem 间隙 ≈ **1592px**；屏幕内容区 < 1592px 时即触发。

## 根因

CSS Grid 的 fr 轨道只分配**剩余空间**：固定轨道（rem 任意值）总和超出容器宽度时剩余空间为 0，fr 轨道按 `minmax` 下界收缩——下界写 0 就直接塌缩成 0px。`truncate`（overflow-hidden）掩盖了溢出，所以不是"文字溢出"而是"整列消失"。

## 正确姿势

1. 表格/列表的弹性列一律 `minmax(正下界, Xfr)`（下界按内容估：中文 16px/字，14rem ≈ 14 字），配合容器 `overflow-x-auto` 横向滚动；
2. 新增固定宽列前先估算固定轨道总和与目标屏宽（本坑本可在加列时避免）；
3. 同类模式全局 grep：`minmax(0,` 出现在表格网格里都按此姿势复核（本次同时修了 `ImpactConfirmTab.tsx` 的 `IMPACT_ROW_GRID` 两处）。

## 关联

- 修复任务：docs/requirements/archive/事件审核AI作用域解析方案/（2026-09-16）
