# ECharts dataZoom 锚定互斥踩坑：百分比与日期锚混写锁死窗口

> 一句话结论：dataZoom 的 `start/end`（百分比）与 `startValue/endValue`（类目值/日期锚）**互斥，禁止同时写**——实测混写时百分比优先、日期锚被忽略，窗口锁死全量；每次 setOption 重建都带 `start:0/end:100` 会把用户缩放弹回，表现为"只能缩小、不能放大"。

## 表象

大盘页 K 线图滚轮只能缩小（看更多历史），放大（聚焦更少 K 线）完全失效；初始可见窗口不是方案设计的 90 天而是全量 180 天。

## 根因

- 组件为"无 visibleRange 时回落旧行为"把两种锚同时写进 dataZoom：`{ start: 0, end: 100, ...visibleWindow }`（visibleWindow = startValue/endValue）。
- 实测（echarts 6.1.0 + jsdom 真实实例 + wheel 事件派发探针）：混写时读回窗口恒为全量（startValue 解析自百分比），日期锚被忽略。
- 叠加 report→rebuild 闭环（datazoom 事件 → 父 setState → 重建），每次重建都带 `start: 0, end: 100`，任何缩放瞬间被弹回全量——放大全废；"缩小"看似可用只是因为 §3.4 按需加载让全量窗口随扩展变大。
- 单测只断言"startValue 存在"，没断言"start/end 不存在"——半混写形态全绿。

## 正确姿势

1. 两种锚二选一：有 visibleRange → 只写 `startValue/endValue`；无 → 只写 `start:0/end:100`。
2. 回归断言必须同时断两侧：有锚时 `start/end` 为 undefined、无锚时 `startValue/endValue` 为 undefined。
3. 交互类 echarts 行为（wheel 缩放、事件时序）可写 jsdom 探针验证：真实 `echarts.init(container)` + 桩 canvas 2d context（measureText 返回宽度）+ `canvas.dispatchEvent(new WheelEvent('wheel', {...}))`，wheel 事件间隔需 >100ms 越过 roam 节流。

（2026-09-15 用户验收发现，探针定位；修复见 CandlestickChart.tsx dataZoom 构造。）
