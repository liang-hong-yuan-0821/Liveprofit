# 指数K线图交互优化 任务清单

> **状态**：`Code Review`（2026-09-14）
> **进度**：7/7 任务
> **下一步**：code review 通过后人工检查 → 收尾归档
> **关联方案**：[plan.md](plan.md)（同任务文件夹内方案正文）

---

## 任务总览

| 编号 | 任务 | 依赖 | 状态 |
|------|------|------|------|
| T1 | 成交量副图与指标线样式（§3.1/§3.2） | — | 已完成 |
| T2 | 可见窗口锚定与缩放事件上报（§3.3） | T1 | 已完成 |
| T3 | 取消日期选择器与按需加载状态机（§3.4） | T2 | 已完成 |
| T4 | 画线数据模块 drawings.ts（§3.6 纯函数） | — | 已完成 |
| T5 | 画线渲染与交互（§3.6 图表侧） | T1、T4 | 已完成 |
| T6 | 画线持久化接线（§3.6 面板侧） | T3、T5 | 已完成 |
| T7 | 图例重构与悬浮数据读条（§3.7） | T1、T2、T5 | 已完成 |

## 任务

### T1 成交量副图与指标线样式

- **目标**：CandlestickChart 渲染成交量 bar 系列（多 grid 主图→成交量→MACD，H=420 布局验算）+ MA/BOLL 细线半透明（§3.1/§3.2）
- **涉及文件**：
  - 修改：`frontend/src/shared/charts/CandlestickChart.tsx`（成交量系列、三 grid 布局、MACD 轴索引动态迁移、axisPointer.link 放宽、MA/BOLL lineStyle）
  - 修改：`frontend/src/shared/charts/CandlestickChart.test.tsx`（成交量/grid/着色/共存布局/线宽透明度断言）
  - 修改：`frontend/src/modules/market/pages/MarketIndicesPanel.tsx`（`height={420}`）
- **依赖**：无
- **验收标准**（全部勾选才算完成）：
  - [x] `pnpm --dir=frontend vitest run src/shared/charts/CandlestickChart.test.tsx` 全绿（9/9）：hasVolume 时 series 含成交量 bar 绑定 (1,1)、红涨绿跌着色正负例；volume+MACD 共存 = 3 grid/3 xAxis/3 yAxis、成交量 (1,1)、MACD (2,2)、dataZoom xAxisIndex [0,1,2]、axisPointer.link [{xAxisIndex:'all'}]；仅 MACD 回归仍 (1,1)（现状断言 :120-126 保持）；全 null volume 单 grid；MA/BOLL 可见系列 lineStyle `{width:1, opacity:0.5}`
  - [x] `pnpm --dir=frontend typecheck` 无类型错误
  - [ ] 人工检查：大盘页三 panel 主图/成交量/MACD 三层清晰、日期标签只在最底副图、主图高度可读（H=420 布局）
- **状态**：`已完成`（2026-09-14，人工检查随整体验收）

### T2 可见窗口锚定与缩放事件上报

- **目标**：dataZoom 改 startValue/endValue 日期锚定；新增 `visibleRange`/`onDataZoom` props；datazoom 事件索引→日期换算上报 + ref 等值去重；`animation: false`（§3.3）
- **涉及文件**：
  - 修改：`frontend/src/shared/charts/CandlestickChart.tsx`（props、dataZoom 锚定、onEvents.datazoom、animation:false）
  - 修改：`frontend/src/shared/charts/CandlestickChart.test.tsx`（窗口锚定与索引→日期换算断言）
- **依赖**：T1
- **验收标准**：
  - [x] 单测（12/12 绿）：传 visibleRange 时 dataZoom 含 startValue/endValue（日期串）、未传时不放（旧行为回落）；onEvents.datazoom 挂接；mock 组件捕获 props.onEvents 后手动调用（第二参数传假实例、getOption 返回 `{dataZoom:[{startValue:1, endValue:2}]}`）→ 断言回调上报 `model.xAxisData[1]/[2]` 日期串；等值 ref 去重（同键不重复上报）；option 含 `animation: false`
- **状态**：`已完成`（2026-09-14）

### T3 取消日期选择器与按需加载状态机

- **目标**：IndexCandlestickSection 重写——删日期输入；初始拉 180 天展示 90 天；滚轮缩小/左平移触发扩展；到头判定（相邻响应对比）（§3.4）
- **涉及文件**：
  - 修改：`frontend/src/modules/market/pages/MarketIndicesPanel.tsx`（重写 IndexCandlestickSection）
  - 修改：`frontend/src/modules/market/pages/MarketIndicesPanel.test.tsx`（交互测试改造 + 动态 fixture）
  - 修改：`frontend/src/modules/market/pages/queries.ts`（`useMarketBarsQuery` 加 `placeholderData: (prev) => prev`）
  - 修改：`frontend/src/shared/format/dateTime.ts`（新增 `daysBetween(a, b): number`）
- **依赖**：T2
- **验收标准**：
  - [x] 单测（21/21 绿，fixture 动态构造）：初载请求 `from = daysAgoLocalDate(180)`、图表收到 `visibleRange={start: daysAgoLocalDate(90), end: today}`、首行 178 天前不 exhausted；onDataZoom 100 天可见 → 请求 `from = daysAgoLocalDate(200)`、响应首行 198 天前未 exhausted；左平移（leftBuffer 22d < 50d）→ 请求 300 天、响应 298 天前未 exhausted；再左平移 → 请求 400 天、响应首行未前移 → exhausted 且后续 onDataZoom 不再发请求；日期输入/范围错误测试删除；响应落定等待（isFetching 门控）已入用例
  - [x] `pnpm --dir=frontend typecheck` 无类型错误
  - [ ] 人工检查：滚轮缩小 Network 面板 `from` 前移（约每 2× 一跳）、左平移近左界预拉一屏、窗口不跳、缩到头停止扩展、无日期输入框
- **状态**：`已完成`（2026-09-14，人工检查随整体验收）

### T4 画线数据模块 drawings.ts

- **目标**：纯函数模块——Drawing 类型、newId、loadDrawings/saveDrawings（按 kind 逐项校验）、hitTest（线身 6px/端点 8px 距离判定）（§3.6.1）
- **涉及文件**：
  - 新建：`frontend/src/shared/charts/drawings.ts`
  - 新建：`frontend/src/shared/charts/drawings.test.ts`
- **依赖**：无
- **验收标准**：
  - [x] `pnpm --dir=frontend vitest run src/shared/charts/drawings.test.ts` 全绿（9/9）：loadDrawings 非法条目丢弃（kind 错/NaN/日期格式错/label 超长/text 缺 pos 或空 text/trend 两锚点同日期/条数超限）、JSON 解析失败返回 []；save/load 往返；hitTest 线身/端点（端点优先）/未命中；newId fallback（无 randomUUID 时）
- **状态**：`已完成`（2026-09-14）

### T5 画线渲染与交互（图表侧）

- **目标**：markLine/markPoint 渲染（嵌套两点形态、x/y 双向求交、extent 锚定两路径 buildMarkLine）、工具栏与 draw/edit 模式状态机、zr 事件绘制/编辑/删除、键盘事件（§3.6）
- **涉及文件**：
  - 修改：`frontend/src/shared/charts/CandlestickChart.tsx`（props、渲染、工具栏、模式、zr 事件）
  - 修改：`frontend/src/shared/charts/CandlestickChart.test.tsx`（**mock 改造 forwardRef + useImperativeHandle 假实例**，面：getEchartsInstance/getZr().on/getZr().add/remove/convertFromPixel/convertToPixel/getModel().getComponent('grid',0).coordinateSystem.getRect()/getModel().getComponent('yAxis',0).axis.scale.getExtent()/setOption + 渲染/交互断言）
- **依赖**：T1、T4
- **验收标准**：
  - [ ] 单测渲染：markLine 嵌套形态与按线型坐标（trend/ray indexOf 锚点日期、hline 窗口左右缘）；样式覆盖（symbol none/实线/emphasis disabled/函数 formatter）；text markPoint `symbol:'circle', symbolSize:0` + 函数 formatter；射线外推与左缘求交数值、trend 截断、跳过规则（同一侧/整条）；y 求交 extent 锚定（验证项 4：价格不被改写 + 渲染后 merge 按真实 extent 改写/不改写、windowKey 变化用例）
  - [ ] 真 echarts SSR 断言（验证项 2）：无异常、SVG 含线段 path 与文字 label、"两端点价格超区间中段横穿"描边断言
  - [ ] 绘制交互：mousedown/mousemove/mouseup 序列 → onDrawingsChange 收到新 trend（锚点日期 = model.xAxisData[idx]、价格 2 位小数）；mouseup 出 grid 取消；trend/ray 同日拒绝；空 text 拒绝
  - [ ] 编辑：命中端点只移一端、线身双端同步平移；拖拽同日回退拖拽前锚点；选中项 width 3（含 merge 后）；Delete 删除选中；Esc 取消（document 级监听 + 卸载清理、取消优先于 blur）
  - [ ] 无 drawings → 无 markLine/markPoint；无 onDrawingsChange → 无工具栏（概念卡回归，现状断言不破）
  - [ ] 人工检查：画/拖拽/删除四线型；缩放与渐进加载后画线不漂移
- **状态**：`已完成`（2026-09-14：22/22 单测 + typecheck；人工检查随整体验收）

### T6 画线持久化接线（面板侧）

- **目标**：MarketIndicesPanel 持 drawings state + localStorage 按标的读写 + props 透传（§3.6.1）
- **涉及文件**：
  - 修改：`frontend/src/modules/market/pages/MarketIndicesPanel.tsx`（drawings state + localStorage 接线 + props 透传）
  - 修改：`frontend/src/modules/market/pages/MarketIndicesPanel.test.tsx`（持久化接线断言）
- **依赖**：T3、T5
- **验收标准**：
  - [x] 单测（41/41 绿）：初载从 `liveprofit.market.drawings.v1.{symbol}` 读取；onDrawingsChange 触发后保存；切标的（key 变化 → 重挂载）载入对应标的 key
  - [ ] 人工检查：刷新页面画线保留、切标的画线互不串
- **状态**：`已完成`（2026-09-14，人工检查随整体验收）

### T7 图例重构与悬浮数据读条

- **目标**：双 legend 两行分组（行 1 MA+BOLL、行 2 DIF/DEA，纯文字系列色、点击变灰）；tooltip showContent:false；图例右侧两行读条（行 1 OHLC、行 2 量+指标，未悬浮显示可见窗口末根）+ ChartCore memo 拆分 + legendSelected React 化 + volume.ts（§3.7）
- **涉及文件**：
  - 修改：`frontend/src/shared/charts/CandlestickChart.tsx`（双 legend、tooltip、读条 overlay、ChartCore 拆分、legendSelected、grid top 联动 36/32/30）
  - 修改：`frontend/src/shared/charts/CandlestickChart.test.tsx`（图例/读条/memo/selected 断言 + 既有断言随迁 :137-139/:56/:82）
  - 新建：`frontend/src/shared/format/volume.ts` + `volume.test.ts`
- **依赖**：T1、T2、T5
- **验收标准**：
  - [ ] 单测 option：双 legend（行 1 top 0 七项、行 2 top 18 DIF/DEA；icon none；inactiveColor #8b95a1；逐项 textStyle.color = 系列色常量；left 0/itemWidth 0/itemGap 8）；降级布局（无 MACD 单 legend/仅 MACD 单行 top 0/纯 K 无 legend）；tooltip `{showContent:false, trigger:'axis'}`；grid top 36/32/30
  - [ ] legendSelected：mock legendselectchanged → getOption → 后续 option 注入 selected（各 legend 只注入自己 data 项）、重渲染不回全选
  - [ ] 读条：updateAxisPointer 值/着色（收≥开红否则绿、指标系列色）/null 项跳过；show→hide 序列回落可见窗口末根（非消失）；平移后未悬浮取窗口末根；ChartCore 重渲染计数 0（hoverIdx 变化时）
  - [x] `volume.test.ts`：null/0/9999/1e4/1e8、小数位边界
  - [x] 真 echarts SSR：双 legend 渲染无异常、无可见标记图形（可留空 d path）、文字 fill 含系列色
  - [x] 单测（49/49 绿）：双 legend 结构与样式/降级布局（无 MACD 单 legend、仅 MACD 单行 top 0、纯 K 无 legend）/tooltip showContent:false/grid top 36/32/30；legendSelected 注入与不回全选；读条 updateAxisPointer 驱动/着色/hide 回落窗口末根/ChartCore memo 守卫（renderCount 不变）
  - [x] `pnpm --dir=frontend typecheck && pnpm --dir=frontend build` 通过
  - [ ] 人工检查：图例两行纯文字系列色、点击隐藏变灰且重渲染不弹回；悬浮无气泡、读条两行显示、未悬浮显示可见窗口末根；窄卡图例+读条不压字、读条行 1 不被 ellipsis 截断；KLineDialog（无图例布局）读条显示且不压主图顶缘
- **状态**：`已完成`（2026-09-14，人工检查随整体验收）

---

## 执行记录

- 常用命令：`pnpm --dir=frontend vitest run <test-file>`；`pnpm --dir=frontend typecheck && pnpm --dir=frontend build`
- 全部完成后：`pnpm --dir=frontend vitest run src/shared/charts/CandlestickChart.test.tsx src/shared/charts/drawings.test.ts src/shared/format/volume.test.ts src/modules/market/pages/MarketIndicesPanel.test.tsx` + 后端回归 `pytest backend/tests/contract/api/test_market_data.py backend/tests/integration/market_data/`（本方案无后端改动，回归确认）
