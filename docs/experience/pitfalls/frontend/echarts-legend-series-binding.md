# ECharts 6 legend 只渲染有同名 series 的项（无同名系列的项被静默跳过）

> 一句话结论：ECharts 6 的 legend 项**必须能在 `ecModel.getSeriesByName(name)` 命中同名系列才会渲染**；`LegendModel._availableNames` 只由 series 名构成，`isSelected(name)` 对不在其中的 name 恒为 false，`LegendView.renderInner` 取不到系列时只剩 pie/funnel 的 legendVisualProvider 分支（`provider.containName(name)`，普通图不命中）→ 该项被**静默跳过**，不渲染也不报错。纯展示读数（开/高/低/收/涨幅）不能放在 legend 里，须走 DOM 覆盖层。

## 表象

指数 K 线图"图例行 1"（`option.legend[0]`，开/高/低/收/涨幅 的 label+value 读数）在页面上完全不显示——2026-10-02 用户验收："我诉求是悬浮 bar 时上方展示数据的地方展示涨跌幅"，实测画布图例只剩一个「量 5.05亿手」（成交量有同名 bar 系列），行 2/3（MA/BOLL/DIF/DEA）正常渲染。option 层数据完全正确，前端单测全绿。

## 根因（echarts 6.1.0 源码 + 运行实例实测）

- `LegendModel._updateData`：未显式给 `legend.data` 时用 `_availableNames` 兜底，而 availableNames 来自 `ecModel.eachRawSeries`（每个原始系列无条件 push 自己的 name）。
- `LegendModel.isSelected(name)`：`!this._availableNames[name] && name !== 'all'` → 直接返回 false。
- `LegendView.renderInner`：`var seriesModel = ecModel.getSeriesByName(name)[0]`；取不到时进入 legendVisualProvider 分支（pie/funnel 才注册该 provider），`containName(name)` 不命中即 `continue`。
- 本项目的 OHLC/涨幅是 candlestick 的**数组数据**（`data: [[o,c,l,h], ...]`），没有 `name`；把它们的读数写成 legend 项（2026-09-15 "行 1 开高低收+涨幅"改版）从一开始就不会渲染——同期把 tooltip 改成 `showContent: false`，页面因此没有任何地方展示单根读数。
- 浏览器实测取证实锤：实例 `getModel().getComponent('legend', i)` 的 `declared`（option 声明）含 6 项而 `available`（取自 series）为空 → 渲染只剩有系列的项；仅 mock `echarts-for-react`、断言传入 option 的单测对该差异**完全无感**。

## 正确姿势

1. 图例（ECharts legend）只放**有同名系列**的项（MA5/BOLL上轨/DIF/DEA/成交量），点击开关副图语义保留；成交量行右对齐（`right: 0`），把画布左上 readout 带让出来。
2. 纯展示读数（开/高/低/收[+涨幅]、悬浮跟随、未悬浮取可见窗口末根）走 **DOM 覆盖层**：绝对定位在画布图例带（top 0..18，与 legend 行 top 18 错开）、`pointerEvents: none` 穿透交互、`zIndex` 高于 canvas；`grid.top` 仍按"读条行恒占位 + legend 行数"预留（24/40/56）。
3. 反模式：给解析出的读数伪造同名空系列（`name: '开', data: []`）骗过 legend——会污染 tooltip/axisPointer 至少需要全链路特判，不要用。
4. 核验渲染必须"真渲染"：SSR SVG 断言文案/颜色（`echarts.init(null, null, { renderer: 'svg', ssr: true })`，jsdom 需桩 `measureText`），或浏览器实测；**只断言 option 的用例视为未覆盖渲染**。
5. DOM 读条顺带把数值与配色变成可断言事实（`data-testid="chart-readout"` 的 span 文本/`style.color`）——此前经 legend formatter 只能断字符串，颜色断言根本不成立。

（2026-10-02 用户验收"K 线图上没看到涨跌幅"发现；修复见 [CandlestickChart.tsx](../../../../frontend/src/shared/charts/CandlestickChart.tsx) 的 `buildReadoutItems`/`chart-readout`。）
