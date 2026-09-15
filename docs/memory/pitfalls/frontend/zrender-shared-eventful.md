# zrender 共享 Eventful 踩坑：裸 off 会误删 ECharts 内部监听

> 一句话结论：`zr.on/off` 与 ECharts 内部共用同一个 Handler Eventful，裸 `zr.off(event)`（不带 handler）会 `delete` 整张事件表，且 ECharts 的全局监听是 `inner(zr).initialized` 门控的**一次性注册、无法恢复**——正确姿势是按引用 `zr.off(event, handlerRef)`，且 handlerRef 为空时不得调用（否则同样落 `delete` 分支）。

## 表象

CandlestickChart 画线交互 zr handler 修复时，为防"每次重渲染新闭包累积注册"，先裸 `zr.off('mousedown')/('mousemove')/('mouseup')` 再注册。单测全绿（mock 是覆盖语义），但真实运行时：

- 十字光标与 `updateAxisPointer` 不再随鼠标移动（悬浮读条主路径失效，仅首次 mousemove 有值后冻结）
- view 模式按住拖拽平移永久失效（inside dataZoom 的 roam 监听被删且不重注册）

## 根因

- zrender `Eventful.off(event)` 不带 handler → `delete _h[eventType]`（[Eventful.js:72-74](Liveprofit/frontend/node_modules/zrender/lib/core/Eventful.js)）；`ZRender.prototype.on/off → this.handler.on/off`——与 ECharts 内部**共享同一实例**。
- ECharts 被删的监听：axisPointer/tooltip 的全局 `zr.on('mousemove', ...)`（`component/axisPointer/globalListener.js:59-81`，以 `inner(zr).initialized` 门控只注册一次匿名函数）；inside dataZoom 的 roam 监听（`RoamController.js:391-402`，`enable()` 有 `!this._enabled` 快路径不会重注册）。
- `zr.on` 只按**函数引用**去重（`=== handler`），每次重渲染的新闭包会逐渲染累积——所以"每次渲染重挂"必须 off 旧引用，但绝不能裸 off。

## 正确姿势

1. ref 存上一次注册的 handler（初始 null），effect 里仅当非空时 `zr.off(event, prevHandler)`，再注册新一份；view 模式只 off 不注册。
2. `off(event, undefined)` 同样走 `delete` 分支——必须传真实 handler 引用。
3. 测试 mock 的 `on/off` 要模拟真实语义（on 累积、off 按引用过滤、裸 off 清空），并预置"外来 handler"（模拟 ECharts 内部监听）断言其存活——这是唯一能在单测层拦住该回归的写法。
4. echarts-for-react 实例时序：临时实例 dispose 重建后才 `updateEChartsOption()`（此时 ECharts 注册内部监听）→ `onChartReady(最终实例)`——拿到最终实例时内部监听已在，任何 off 误删都不可恢复。

（2026-09-14 指数K线图交互优化 Code Review 三轮拦下；详见 [指数K线图交互优化方案](../../../requirements/archive/指数K线图交互优化/plan.md) §3.6。）
