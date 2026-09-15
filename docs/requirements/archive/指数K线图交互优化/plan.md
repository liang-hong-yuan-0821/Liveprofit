# 指数K线图交互优化方案

> **状态**：待确认（2026-09-13/14：适配证券市场数据库统一方案落库 + 画线工具需求扩充 §3.6（评审 4 轮收敛：R1/R2/R3 FAIL → R4 PASS）+ 图例重构与悬浮数据读条需求扩充 §3.7（评审 2 轮收敛：R1 FAIL → R2 PASS，收尾完善项已全部落地））
> **进度**：1/6 步骤（方案起草 + 3 轮评审修复完成 + 统一方案落库适配 + 画线模块扩充与 4 轮评审收敛 + 图例/读条模块扩充与 2 轮评审收敛）
> **下一步**：用户确认后任务分解
> **关联文档**：[技术指标数据源切换方案](../../archive/技术指标数据源切换方案.md)（指标数据源现状）｜[K线指标叠加方案](../../archive/K线指标叠加方案.md)（图表组件历史）｜[证券市场数据库统一方案](../../archive/证券市场数据库统一方案.md)（数据表现状：market schema 11 表；全库结构见 [数据库表结构](../../../knowledge/backend/数据库表结构.md)）｜[产品需求分析](../../../knowledge/产品需求分析.md)（§3.1.3.2 K 线交互 / §7.2 Q-01 / AC-25，实现后需同步）

---

## 一、背景与动机

| 维度 | 现状 | 问题 | 目标 |
|------|------|------|------|
| 成交量 | `frontend/src/shared/charts/CandlestickChart.tsx` 只渲染 candlestick + MA/BOLL/MACD，ViewModel 里 `volume` 字段已透传但无任何系列消费（[CandlestickChart.tsx:14](Liveprofit/frontend/src/shared/charts/CandlestickChart.tsx#L14) 有 `volume?`，`toChartViewModels.ts:20` 已映射，图表未渲染）；后端 bars 响应已含 `volume`（[service.py:139](Liveprofit/backend/modules/market_data/application/service.py#L139)） | 大盘页指数 K 线看不到成交量，用户无法判断量价关系 | K 线图带成交量副图（红涨绿跌柱状，与现有 MACD 副图同一套多 grid 机制） |
| 时间范围 | [MarketIndicesPanel.tsx:80](Liveprofit/frontend/src/modules/market/pages/MarketIndicesPanel.tsx#L80)（from 默认 60 天）+ [:136-152](Liveprofit/frontend/src/modules/market/pages/MarketIndicesPanel.tsx#L136-L152)（两个日期输入），改区间仅该图重新请求；图表 dataZoom 起点固定 0–100（[CandlestickChart.tsx:134](Liveprofit/frontend/src/shared/charts/CandlestickChart.tsx#L134) `start: 0, end: 100`） | 用户想看更长历史必须先猜日期输入；滚轮缩小只能看已加载窗口，缩到头就是数据边界，无提前拉取 | 取消日期输入；默认可见近 3 个月、预拉 6 个月；滚轮缩小或左平移接近加载边界时自动预拉更早数据，直到库内数据边界 |
| 后端范围上限 | ✅ **已移除**（证券市场数据库统一方案 2026-09-13 先行落地）：[service.py:89-92](Liveprofit/backend/modules/market_data/application/service.py#L89-L92) 仅保留 from>to 校验（仍抛 `RangeTooLargeError`，错误码不变）；契约 `test_market_data.py:91-96`、集成 `test_market_flow.py:71` 已改为 from>to 用例 | （历史问题，已解决）"每个图都可以看所有的 K 线"曾被 365 天上限卡死（产品需求分析 §7.2 Q-01 的 365 表述随本方案实现收尾同步修订，见文末清单） | ✅ 已完成：请求窗口大小由前端渐进加载策略控制；本方案无后端改动（原设计见 3.5） |
| 指标线样式 | MA 线 `lineStyle: { width: 1.5 }`（[CandlestickChart.tsx:72](Liveprofit/frontend/src/shared/charts/CandlestickChart.tsx#L72)）、BOLL 三线 `width: 1` 均不透明，叠加在 K 线上偏粗偏实 | 均线与 BOLL 压住 K 线主体，视觉上抢眼 | MA/BOLL 统一细线（width 1）+ 半透明（opacity 0.5），K 线可透出（"镂空"视觉，与用户提供的 ECharts 官方示例一致） |
| 画线标注 | [CandlestickChart.tsx](Liveprofit/frontend/src/shared/charts/CandlestickChart.tsx) 全文件无 markLine/markPoint/graphic——没有任何用户标注能力；产品需求分析 §3.1.3.2 表格亦无画线条目 | 用户无法在图上标注关键价位（支撑/压力）与趋势线，复盘时只能靠外部工具或记忆 | 四线型画线工具（水平线/趋势线/射线/文字标注）+ 画/拖拽修改/删 + localStorage 按标的持久化（2026-09-13 用户拍板：仅大盘页全尺寸图，概念卡行为不变） |
| 图例与悬浮提示 | legend 单实例 `{top: 0, data: legendData}`（[CandlestickChart.tsx:146](Liveprofit/frontend/src/shared/charts/CandlestickChart.tsx#L146)）默认图标 + 灰字（SSR 实测文字色 #54555a 不随系列色）；tooltip `{trigger: 'axis'}`（[CandlestickChart.tsx:152](Liveprofit/frontend/src/shared/charts/CandlestickChart.tsx#L152)）悬浮弹气泡 | 图例带圆圈不符合视觉要求、文字无系列色辨识；悬浮气泡遮挡 K 线且数据不能常驻查看 | 双行图例（行 1 MA+BOLL、行 2 DIF+DEA）纯文字系列色、点击隐藏变灰；tooltip 移除改图例右侧两行读条（行 1 OHLC 红涨绿跌、行 2 量+指标系列色），未悬浮显示可见窗口末根（2026-09-14 用户拍板，口径见 §3.7.1） |

指标数据源不变：MA/BOLL/MACD 仍取自 `market.factor_daily` 因子宽表（读模型 = db.instrument DAO，技术指标数据源切换方案 §3.3，**不自算**），本次只改渲染样式与交互，不引入任何前端计算。

## 二、架构设计

本方案不改变现有架构：数据链路为 `market.instrument_daily + market.factor_daily（market schema，读路径 = db.instrument DAO）→ MarketDataService.get_bars → GET /api/v1/market-data/indices/{symbol}/bars → useMarketBarsQuery → CandlestickChart`。变化只发生在前端：

```
（现状）后端：from/to 任意范围（仅校验 from ≤ to；365 上限已由统一方案先行移除）→ bars + indicators
（本方案）后端：无改动（同结构响应）

（现状）前端：日期输入 → 一次查询 → 全窗口图表
（本方案）前端：loadedFrom 状态 → React Query（placeholderData 保留旧数据）
              → dataZoom startValue/endValue 锚定可见窗口
              → datazoom 事件（类目索引 → 日期映射）上报 → 缓冲不变量触发扩展请求（loadedFrom 前移）
              → 画线工具（drawings state ↔ localStorage；markLine/markPoint 日期+价格锚定，随缩放/扩展不漂移）
```

画线数据流（§3.6）：localStorage（`liveprofit.market.drawings.v1.{symbol}`）→ MarketIndicesPanel state → CandlestickChart props（drawings/onDrawingsChange）→ markLine/markPoint 渲染（日期+价格数据坐标）→ zr 鼠标交互回写 state → localStorage。纯前端链路，无后端改动。

无新表、无 DB 迁移、无 API 契约字段变更（`BarsData` 已含 `from`/`to`，bars 行已含 `volume`——`BarsDTO`，[service.py:139](Liveprofit/backend/modules/market_data/application/service.py#L139)）。

## 三、详细设计

### 3.0 模块总览

| 维度 | 问题 | 方案概览 |
|------|------|---------|
| 成交量副图 | `CandlestickChart.tsx` 消费了 ViewModel 的 `ma/boll/macd` 却从未消费 `volume`（`toChartViewModels.ts:20` 白映射） | 成交量 bar 系列挂多 grid 布局（主图→成交量→MACD 三图），红涨绿跌按 close/open 着色（itemStyle 回调，MACD 柱同款模式），MACD 轴索引随布局 1→2 动态迁移 |
| 指标线样式 | MA `width: 1.5` 不透明压住 K 线 | MA/BOLL 线统一 `width: 1` + `opacity: 0.5` |
| 可见窗口锚定与缩放事件上报 | dataZoom 恒 `start: 0, end: 100`，组件无缩放事件出口，父组件无法感知用户滚轮窗口 | dataZoom 改 startValue/endValue（日期锚定，扩展数据窗口不跳）；新增 `visibleRange`/`onDataZoom` props；datazoom 事件按"类目索引 → xAxisData 日期"换算后上报（实测 getOption 读回的是索引数字而非日期串） |
| 取消日期选择器与按需加载状态机 | `MarketIndicesPanel.tsx:80-82`（from/to/rangeError state）+ `:136-152`（两个日期输入），改区间整图重载；一次拉全窗口 | 删除日期输入；状态机：初始拉 180 天展示 90 天，缓冲不变量 `loaded ≥ 2×visible`（10% 迟滞）+ 左平移逼近边界触发（半屏内预拉一屏）；到头判定用相邻两次响应 bars 首行对比（免容差常量、长假免疫） |
| 后端范围上限移除 | ✅ 已完成（统一方案 2026-09-13 先行落地：[service.py:89-92](Liveprofit/backend/modules/market_data/application/service.py#L89-L92) 仅 from>to 校验，契约/集成测试 from>to 用例已存在） | 本方案无此模块（原设计见 3.5） |
| 画线工具 | `CandlestickChart.tsx` 无 markLine/markPoint/graphic 标注能力 | 新增 `drawings`/`onDrawingsChange` props（概念卡不传 → 不渲染工具栏，行为不变）；四线型（水平线/趋势线/射线/文字标注）挂 candlestick 系列 markLine/markPoint（日期+价格数据坐标，缩放/平移/渐进加载前插不漂移）；draw/edit 模式状态机 + zr 鼠标事件（画、端点/整线拖拽、删除、清空）；localStorage 按标的持久化（无后端改动） |
| 图例重构与悬浮数据读条 | legend 单实例默认图标+灰字；tooltip axis 气泡 | 双 legend 实例两行分组（行 1 MA+BOLL、行 2 DIF+DEA；SSR 实测 legend 数组/icon none/inactiveColor 可用、文字不继承系列色须逐项显式）；tooltip showContent:false（show:false 会连轴失效，§3.6.2 实测结论 5）；图例右侧 React overlay 两行读条（行 1 OHLC、行 2 量+指标，未悬浮显示可见窗口末根），数据源 updateAxisPointer（含离场 axesInfo 空守卫）；ChartCore React.memo 拆分防逐像素 setOption 重建；legendSelected React 化防 notMerge 重建重置；大盘页高度 360→420 |

### 3.1 成交量副图

#### 3.1.1 模块设计

- 职责：`CandlestickChart` 内新增成交量 bar 系列 + 多 grid 布局；仅在 `model.volume` 存在且含非 null 值时渲染。
- 判定：`hasVolume = (model.volume ?? []).some((v) => v !== null)`（模型驱动，与消费方无关——概念卡当前恒不渲染图表：后端 [service.py:361](Liveprofit/backend/modules/market_data/application/service.py#L361)（`get_hot_concepts` 内 hot_concepts 每 item 硬编码 `bars=[]`）→ mapper 返回 null 不挂图表，本方案不改这条链路）。
- 系列数据形态（沿示例的三元组 + 项目回调惯例）：

  ```ts
  const volumes = model.volume!.map((v, i) =>
    v === null ? null : [i, v, model.ohlc[i][1] >= model.ohlc[i][0] ? 1 : -1],
  );
  series.push({
    name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: volumes,
    itemStyle: {
      // 红涨绿跌（项目惯例，MACD 柱同款回调）：params.value = [index, volume, direction]
      color: (params: CallbackDataParams) =>
        Array.isArray(params.value) && params.value[2] === -1 ? '#22c55e' : '#ef4444',
    },
  });
  ```

- 不新增 legend 项（与 MACD 柱一致，柱不进 legend）；tooltip 内容已由 §3.7 全局移除，成交量系列不做单独 tooltip 配置。

- **grid ↔ 轴 ↔ 系列分配表**（成交量插在主图与 MACD 之间；MACD 轴索引随布局动态迁移，避免成交量与 MACD 挤同一副图）：

  | 布局 | 主图 grid/xAxis/yAxis | 成交量 grid/xAxis/yAxis | MACD grid/xAxis/yAxis | 说明 |
  |------|-----------------------|-------------------------|-----------------------|------|
  | 成交量+MACD | 0 / 0 / 0 | 1 / 1 / 1 | 2 / 2 / 2 | MACD 三系列轴绑定从现状 (1,1) 迁至 (2,2)；dataZoom/axisPointer 覆盖 [0,1,2] |
  | 仅成交量 | 0 / 0 / 0 | 1 / 1 / 1 | — | dataZoom/axisPointer 覆盖 [0,1] |
  | 仅 MACD | 0 / 0 / 0 | — | 1 / 1 / 1（现状不变） | 现状断言 `CandlestickChart.test.tsx:120-126` 保持有效 |
  | 纯 K 线 | 0 / 0 / 0 | — | — | 现状不变 |

  实现：`const macdAxisIndex = hasVolume ? 2 : 1;`，MACD 系列与轴引用统一走该变量；xAxis/yAxis 数组按上表顺序构造（每个 grid 一个轴对象，复用 `xAxisBase`/`yAxisBase` 派生）。

- grid 布局数值（`height=420` 验算——**实测纠正：grid.bottom 自底部计量、grid.top 自顶部计量，旧表把 bottom 百分比当自顶部计量，"间距 28.8px"实为重叠**（真实布局探针 grid0=[36,208.8]、grid1=[180,252] 重叠 28.8px）；420 = 36 图例 + 174 主图 + 8.4 间距 + 92.4 成交量 + 8.4 间距 + 66.8 MACD + 34（18 slider 间距 + 14 slider 高 + 2 slider 底距），主图 ≥170px 门槛达成；成交量插在主图与 MACD 之间，日期标签只在最底副图显示）：

  | 布局 | 主图 grid | 成交量 grid | MACD grid |
  |------|-----------|-------------|-----------|
  | 成交量+MACD | `{left:48, right:16, top:36, bottom:'50%'}` → 高 174px（双行图例 top 36，§3.7） | `{top:'52%', bottom:'26%'}` → 高 92.4px | `{top:'76%', bottom:34}` → 高 66.8px |
  | 仅成交量 | `{left:48, right:16, top:32, bottom:'34%'}` → 高 245.2px（单行图例 top 32） | `{top:'72%', bottom:34}` → 高 83.6px | — |
  | 仅 MACD | `{left:48, right:16, top:32, bottom:'36%'}` → 高 236.8px（单行图例，百分比不变） | — | `{top:'67%', bottom:34}` → 高 104.6px |
  | 纯 K 线 | `{left:48, right:16, top:30, bottom:30}` → 高 360px（无图例 top 24→30，§3.7 读条预算） | — | — |
  | 仅成交量+无图例（KLineDialog，`height=320`） | `{left:48, right:16, top:30, bottom:'34%'}` → 高 181.2px | `{top:'72%', bottom:34}` → 高 55.6px（间距 19.2px） | — |

  相邻 grid 间距均 ≥ 6px（H=420 真实布局探针：grid0=[36,210]、grid1=[218.4,310.8]、grid2=[319.2,386]）：主图底缘 210 与成交量顶缘 218.4 相距 8.4px，成交量底缘 310.8 与 MACD 顶缘 319.2 相距 8.4px，MACD 底缘 386 与 slider 顶缘 404 相距 18px，满足 MACD 指标副图方案 §3.3.1 的间距约束。KLineDialog（H=320，仅成交量+无图例）：主图 [30, 211.2] 高 181.2px、成交量 [230.4, 286] 高 55.6px、间距 19.2px，均满足 ≥40px 副图/≥6px 间距约束。
- xAxis：各图共用 category 数据；主图与成交量隐藏 axisLabel，最底副图（MACD 或成交量）显示日期。
- yAxis 成交量轴：`scale: true, splitNumber: 2, axisLabel: { show: false }, axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false }`（示例同款，不与价格轴共享量纲）。
- `axisPointer.link: [{xAxisIndex: 'all'}]` 条件从 `hasMacd` 放宽为 `hasMacd || hasVolume`（十字光标跨图联动）。
- 大盘页调用处 `height` 从默认 240 提到 `height={420}`（三面板布局验算见上表，主图 ≥170px 才可读）；概念卡已不渲染图表（HotConceptsPanel 只渲染 ConceptTreemap + KLineDialog）；第三消费方 KLineDialog（`height={320}`，纯 K 线+成交量布局、无指标无图例）的读条规则见 §3.7。

#### 3.1.2 三方依赖能力评估

echarts 6.1 多 grid/多 xAxis 联动为官方支持能力（官方示例 "Professional Candlestick" 即主图+成交量双 grid + dataZoom 联动 + 回调着色），与 MACD 副图既有实现同机制。本模块无新增依赖。

#### 3.1.3 风险与验证方式

- 风险：三 panel 高度分配不当导致主图过矮（验算见上表）；volume 全 null 误开副图；三图共存时 MACD 轴绑定迁移遗漏。
- 验证：`CandlestickChart.test.tsx` 新增/更新断言——
  1. hasVolume 时 series 含成交量 bar 系列且绑定轴 (1,1)、着色回调正负例断言（红涨绿跌）；
  2. **volume+MACD 共存用例**：3 grid/3 xAxis/3 yAxis、成交量=(1,1)、MACD 三系列=(2,2)、dataZoom xAxisIndex=[0,1,2]、axisPointer.link=[{xAxisIndex:'all'}]；
  3. 仅 MACD 布局回归：MACD 仍 (1,1)（现状断言 `:120-126` 保持）；
  4. 全 null volume 不开副图（单 grid）。

#### 3.1.4 文件变更清单

- 修改 `frontend/src/shared/charts/CandlestickChart.tsx`：成交量系列、多 grid 布局、MACD 轴索引动态迁移、axisPointer.link 条件放宽。
- 修改 `frontend/src/shared/charts/CandlestickChart.test.tsx`：成交量/共存/回归断言。
- 修改 `frontend/src/modules/market/pages/MarketIndicesPanel.tsx`：`height={420}`。

### 3.2 指标线样式（细线+半透明）

#### 3.2.1 模块设计

- MA 系列：`lineStyle: { width: 1, opacity: 0.5, color }`（现状 `width: 1.5` 无 opacity）。
- BOLL 上/中/下轨三条可见线：`lineStyle: { width: 1, opacity: 0.5, color: BOLL_LINE_COLOR }`（现状 `width: 1` 不透明）。
- 不动项：BOLL 带宽两条隐藏堆叠系列（lineStyle opacity 0，本就不渲染线）、MACD DIF/DEA（width 1 不透明，用户未要求改副图）、K 线实体。

#### 3.2.2 三方依赖能力评估

echarts line series `lineStyle.opacity` 为官方能力（用户示例即用此方案）。无新增依赖。

#### 3.2.3 风险与验证方式

- 风险：opacity 0.5 叠加暗色背景后对比度不足（纯视觉偏好，可后续微调）。
- 验证：单测断言 MA/BOLL 可见系列 lineStyle `{width: 1, opacity: 0.5}`；人工检查渲染效果。

#### 3.2.4 文件变更清单

- 修改 `frontend/src/shared/charts/CandlestickChart.tsx`（同上文件，见 3.1.4）。
- 修改 `frontend/src/shared/charts/CandlestickChart.test.tsx`：线宽/透明度断言。

### 3.3 可见窗口锚定与缩放事件上报

#### 3.3.1 模块设计

- 新增 props（默认值保持旧行为，旧消费方零改动）：

  ```ts
  export interface CandlestickChartProps {
    model: CandlestickChartViewModel;
    height?: number;                                          // 现状默认 240
    /** 初始可见窗口（含端点的日期字符串）；不传 = 全量 0–100（旧行为） */
    visibleRange?: { start: string; end: string };
    /** 用户缩放/平移后上报当前可见日期区间 */
    onDataZoom?: (range: { start: string; end: string }) => void;
  }
  ```

- dataZoom 从百分比模式改为日期锚定：inside 与 slider 均用 `startValue: visibleRange?.start, endValue: visibleRange?.end`（category axis 支持 startValue/endValue，字符串值由 ECharts 吸附到最近类目——实测成立）。数据扩展（前插更早 bars）时可见日期不变 → 窗口不跳。`visibleRange` 未传时不放 startValue/endValue（回落到旧行为）。
- **事件上报（实测约束）**：ECharts category 轴 dataZoom 的窗口是序号空间——本地 echarts 6.1.0 实测 `getOption().dataZoom[0].startValue/endValue` 读回的是**类目索引数字**（如 19），不是日期字符串。因此 handler 必须做索引→日期换算：

  ```ts
  const onEvents = onDataZoom
    ? {
        // echarts-for-react 3.0.6 handler 第二参数即 ECharts 实例（lib/core.js:153 `func(param, instance)`）
        datazoom: (_params: unknown, instance: EChartsType) => {
          const dz = (instance.getOption().dataZoom as DataZoomComponentOption[]) ?? [];
          const zoom = dz.find((d) => d.type === 'inside') ?? dz[0];
          const toDate = (v: unknown) => {
            if (typeof v !== 'number') return undefined;
            const idx = Math.round(v);
            return model.xAxisData[idx];
          };
          const start = toDate(zoom?.startValue);
          const end = toDate(zoom?.endValue);
          if (start && end) onDataZoom({ start, end });
        },
      }
    : undefined;
  ```

- 防抖与代价：handler 内用 ref 记录上次上报的 `start|end` 键，等值直接 return（避免每个像素的拖动事件都触发父组件 setState；父组件再渲染时 setOption 带内联回调、fast-deep-equal 深比较恒不等（实测 echarts-for-react 用 fast-deep-equal）→ 每次事件都会全量重建，属已知代价）。已知性质（评审实测）：① `setOption` 不派发 datazoom 事件，无回环死循环；② 索引→日期→索引回填有 ≤1 根 K 线的舍入漂移，视觉无感、可接受。
- `ReactECharts` 保持 `notMerge`，新增 `animation: false`——数据扩展触发的 setOption 重建不闪、不产生动画重绘；缩放窗口由父组件 state 回传（见 3.4），重建后仍落在用户当前窗口。
- 概念卡（HotConceptsPanel）不传新 props，行为与现状完全一致（图表消费方 KLineDialog 随 §3.7 的全局变更见 §3.7.1 无图例读条规则，与概念卡无关）。

#### 3.3.2 三方依赖能力评估

- echarts 6.1：category axis dataZoom `startValue/endValue` 官方支持（字符串吸附最近类目实测成立）；`datazoom` 事件与 `getOption()` 读窗口为官方 API（读回索引数字为实测结论）。
- echarts-for-react 3.0.6：`onEvents` prop 官方支持，handler 第二参数为 ECharts 实例（源码核对）。

#### 3.3.3 风险与验证方式

- 风险：扩展数据时百分比模式会跳窗（本方案用日期锚定规避）；事件风暴（ref 键等值去重 + 状态机迟滞双重防抖）；索引→日期映射边界（Math.round 取最近类目）。
- 验证：单测断言——dataZoom 含 startValue/endValue（日期串）、onEvents.datazoom 已挂接、mock 组件捕获 props.onEvents 后手动调用（单参数先例 GraphTopologyPanel.test.tsx:97 clickNode；第二参数为实例的签名依据 echarts-for-react lib/core.js:153），第二参数传假实例、getOption 返回 `{dataZoom:[{startValue: 1, endValue: 2}]}`，断言回调上报的值为**日期字符串**（model.xAxisData[1]/[2]）；MarketIndicesPanel 测试以 mock 图表组件捕获 props.onDataZoom 手动触发（测试策略见 3.4.3）。

#### 3.3.4 文件变更清单

- 修改 `frontend/src/shared/charts/CandlestickChart.tsx`：props、dataZoom 锚定、onEvents、animation:false。
- 修改 `frontend/src/shared/charts/CandlestickChart.test.tsx`：窗口锚定与事件（索引→日期换算）断言。

### 3.4 取消日期选择器与按需加载状态机

#### 3.4.1 模块设计

`MarketIndicesPanel.tsx` 的 `IndexCandlestickSection` 重写：

- **删除**：`from/to/rangeError` state、`applyRange`、两个 `<Input type="date">`、日期 Label、`daysAgoLocalDate` 的 60 天用法（该工具函数实现后仍被本状态机初值使用——`loadedFrom = daysAgoLocalDate(180)` 等，保留；HotConceptsPanel 用的是 todayLocalDate，与此无关）。
- **新增常量**（组件文件顶部）：

  ```ts
  const INITIAL_LOADED_DAYS = 180;     // 初始预拉 6 个月
  const INITIAL_VISIBLE_DAYS = 90;     // 默认展示近 3 个月
  const BUFFER_RATIO = 2;              // 缓冲不变量：loaded ≥ 2 × visible
  const HYSTERESIS = 1.1;              // 10% 迟滞，防滚轮逐像素触发重复扩展
  const PAN_EDGE_RATIO = 0.5;          // 左平移触发：可见窗口左缘距加载左界 < 半屏即预拉
  ```

- **新增 state**：

  ```ts
  const [loadedFrom, setLoadedFrom] = useState(daysAgoLocalDate(INITIAL_LOADED_DAYS));
  const [visible, setVisible] = useState({ start: daysAgoLocalDate(INITIAL_VISIBLE_DAYS), end: todayLocalDate() });
  const [exhausted, setExhausted] = useState(false);
  const prevFirstBarRef = useRef<string | null>(null);   // 上一次真实响应的首根 bar 日期
  const lastJudgedFromRef = useRef<string | null>(null); // 上次参与到头判定的响应 from
  ```

- **查询**：`useMarketBarsQuery(symbol, { market, interval, from: loadedFrom, to: todayLocalDate() }, available)`，hook 内加 `placeholderData: (prev) => prev`（3.4.4 定死落点）——扩展请求期间旧数据继续渲染（不闪 LoadingState，isPending 仅首次为 true）。
- **扩展触发**（`onDataZoom` 回调，两条触发路径 OR 关系）：

  ```ts
  function handleDataZoom(range: { start: string; end: string }) {
    const key = `${range.start}|${range.end}`;
    if (key === lastReportedRef.current) return;  // 逐像素事件去重
    lastReportedRef.current = key;
    setVisible(range);
    if (exhausted || barsQuery.isFetching) return;
    const visibleDays = daysBetween(range.start, range.end);   // 自然日差
    const loadedDays = daysBetween(loadedFrom, todayLocalDate());
    const leftBuffer = daysBetween(loadedFrom, range.start);   // 左缘距加载左界
    // 路径① 滚轮缩小破坏缓冲不变量 loaded ≥ 2×visible → 扩展至 2×visible（≥ 初始 180 天）
    if (visibleDays * BUFFER_RATIO > loadedDays * HYSTERESIS) {
      setLoadedFrom(daysAgoLocalDate(Math.max(INITIAL_LOADED_DAYS, visibleDays * BUFFER_RATIO)));
      return;
    }
    // 路径② 向左平移逼近加载左界（< 半屏）→ 预拉一屏
    if (leftBuffer < visibleDays * PAN_EDGE_RATIO) {
      setLoadedFrom(daysAgoLocalDate(Math.max(INITIAL_LOADED_DAYS, loadedDays + visibleDays)));
    }
  }
  ```

  用户示例核对：初始 loaded=180d、visible=90d → 2×90=180 ≤ 180×1.1，不触发；滚轮缩小到 visible=100d → 200 > 198 触发，loadedFrom 前移至 200d 前；继续缩到 150d（5 个月）时 loaded 已 200d，300 > 220 触发 → loadedFrom 前移至 300d 前（**10 个月，与"屏幕内展示 5 个月就拉 10 个月"一致**）。路径②核对：visible=90d、左平移到 leftBuffer=40d → 40 < 45 触发，loadedFrom 前移 90d（预拉一屏），此后 leftBuffer=130d ≥ 45d 稳定，无抖动。
- **数据到头检测（相邻响应对比，免容差常量、长假免疫；仅扩展响应参与判定）**：比较两次真实响应的首根 bar 日期——新响应首根**未前移**（≥ 上一次）即库内无更早数据，置 exhausted 停止扩展：

  ```ts
  useEffect(() => {
    const data = barsQuery.data;
    if (!data || barsQuery.isPlaceholderData) return;  // placeholder 旧数据不参与判定
    // 仅 data.from 变化（扩展响应必有新 from）才参与首行对比：查询 key 含 to=today，
    // 页面停留跨日 rollover 会重发同 from 请求，若参与判定会因首行未变误判到头
    if (data.from === lastJudgedFromRef.current) return;
    lastJudgedFromRef.current = data.from;
    const first = data.bars[0]?.timestamp.slice(0, 10) ?? null;
    if (first === null) return;
    if (prevFirstBarRef.current !== null && first >= prevFirstBarRef.current) setExhausted(true);
    prevFirstBarRef.current = first;
  }, [barsQuery.data, barsQuery.isPlaceholderData]);
  ```

  依赖只挂 `barsQuery.data` + `isPlaceholderData`（**不挂 loadedFrom**——扩展期间 data 是 placeholder 旧数据，挂 loadedFrom 会用旧 bars[0] 比新 loadedFrom 误判到头）；每次扩展响应 `data.from` 回显新请求的 from，data 引用必变、effect 必重跑。库内已是全历史（统一方案 2026-09-13 回填完成：指数日线+因子 1990 年起，~8700 行/指数）——初载 180d 返回 bars[0]=180d 前，扩展持续前移直到库内边界，最后一次扩展首根未前移 → exhausted。鲁棒性示例：若库内仅剩 ~90 天数据，初载 180d 请求返回 bars[0]≈90d 前（prevFirst 记 90d 前），首次扩展 360d 请求仍返回同一首根 → 未前移 → exhausted，正确。页面停留期间外部回填新增更早历史不自动恢复（exhausted 保持，重进页面/刷新生效，属可接受行为）。
- **UI**：保留周期标签与新鲜度徽标；来源行上方加扩展中提示 `barsQuery.isFetching && !barsQuery.isPending` 时显示"加载更多历史…"；空态/错误态/降级路径（无 indicators、无 bars）全部保留现状。
- 注意：查询 key 含 `from`，扩展产生新 key；placeholderData 保证旧 key 数据平滑过渡，无白屏。

#### 3.4.2 三方依赖能力评估

React Query v5 `placeholderData: (prev) => prev`（keepPreviousData 语义）官方支持（项目内 `useInfiniteQuery` 已用 v5 API，版本一致）；`isPlaceholderData` 为 v5 官方查询元数据。无新增依赖。

#### 3.4.3 风险与验证方式

- 风险：滚轮/平移触发频率与请求风暴（ref 键去重 + isFetching 门控 + 迟滞三重防抖）；扩展后窗口跳变（3.3 日期锚定规避）；placeholder 旧数据被误当真实数据判定到头（isPlaceholderData 门控规避）。
- 验证：`MarketIndicesPanel.test.tsx` 改造——mock 的 CandlestickChart 组件捕获 props 并暴露 `onDataZoom`，测试手动调用模拟交互。**fixture 按用例动态构造**（现 fixture bars 固定 `timestamp:'2026-09-03'`，距 `daysAgoLocalDate(180)` 超 170 天，按相邻对比规则初载不会误判到头——prevFirst 初值为 null——但扩展后首根未前移会立即 exhausted，需按用例造数据）：
  1. 初载断言：请求参数 `from = daysAgoLocalDate(180)`、图表收到 `visibleRange={start: daysAgoLocalDate(90), end: today}`；fixture bars 首行 `daysAgoLocalDate(178)`，断言初次渲染未置 exhausted；
  2. 缩小到 100 天可见（onDataZoom `{start: daysAgoLocalDate(100), end: today}`）→ 断言第二次请求 `from = daysAgoLocalDate(200)`；第二次响应首行造 `daysAgoLocalDate(198)`（前移），断言未 exhausted；
  3. 左平移逼近边界（onDataZoom `{start: daysAgoLocalDate(178), end: daysAgoLocalDate(78)}`，跨度保持 100 天——路径①不命中（200 ≤ 220），leftBuffer=22d < 50d 走路径②）→ 断言请求 `from = daysAgoLocalDate(300)`；第三次响应首行造 `daysAgoLocalDate(298)`（前移），断言未 exhausted；
  4. 继续左平移（onDataZoom `{start: daysAgoLocalDate(298), end: daysAgoLocalDate(198)}`，leftBuffer=2d < 50d）→ 断言请求 `from = daysAgoLocalDate(400)`；第四次响应首行仍造 `daysAgoLocalDate(298)`（与上次相同，未前移）→ 断言 exhausted；再触发 onDataZoom 不再产生新请求；
  5. 日期输入/范围错误测试删除（UI 已移除）。

#### 3.4.4 文件变更清单

- 修改 `frontend/src/modules/market/pages/MarketIndicesPanel.tsx`（重写 `IndexCandlestickSection`）。
- 修改 `frontend/src/modules/market/pages/MarketIndicesPanel.test.tsx`（交互测试改造 + 动态 fixture）。
- 修改 `frontend/src/modules/market/pages/queries.ts`：`useMarketBarsQuery` 内定死加一行 `placeholderData: (prev) => prev`（该 hook 全仓唯一调用方即本组件，无二义）。
- 修改 `frontend/src/shared/format/dateTime.ts`：新增 `daysBetween(a: string, b: string): number`（无则加）。

### 3.5 后端范围上限移除（✅ 已完成，本方案无此模块）

证券市场数据库统一方案（2026-09-13 归档）实施时**先行落地了本模块的全部设计**，本方案不再包含后端改动。现状核实：

- `backend/modules/market_data/application/service.py`：`MAX_RANGE_DAYS = 365` 常量与超限检查已删除，`get_bars` 仅保留 from>to 校验（[service.py:89-92](Liveprofit/backend/modules/market_data/application/service.py#L89-L92)，消息"查询范围无效：开始日期晚于结束日期"，仍抛 `RangeTooLargeError`，错误码与映射不变）。
- 契约测试 [test_market_data.py:91-96](Liveprofit/backend/tests/contract/api/test_market_data.py#L91-L96)：from>to → 422 `RANGE_TOO_LARGE` 用例已存在（原超 365 天断言已随上限移除删除）。
- 集成测试 [test_market_flow.py:71](Liveprofit/backend/tests/integration/market_data/test_market_flow.py#L71)：from>to 拒绝语义用例已存在。
- 历史数据已回填（统一方案）：指数日线+因子 1990 年起入库 `market.instrument_daily` / `market.factor_daily`，K 线缩放边界 = 库内全历史（~8700 行/指数），前端渐进加载缩到头即历史起点。
- 响应体积评估（保留，供前端渐进加载节奏参考）：全库日线（~8700 行指数 × 1 bar + 10 个指标数组：ma 4 + boll 3 + macd 3）JSON ≈ 2MB，前端渐进加载下单次请求窗口按 2× 增长，实际峰值即库内全量；唯一消费方为自家前端，可接受。

### 3.6 画线工具（2026-09-13 用户新增需求）

#### 3.6.1 模块设计

- **范围（2026-09-13 用户拍板）**：四线型（水平线/趋势线/射线/文字标注）+ 画/拖拽修改/删 + localStorage 按标的持久化 + 仅大盘页全尺寸图（概念卡不传新 props，行为不变）。
- **数据结构**（新文件 `frontend/src/shared/charts/drawings.ts`，纯函数模块）：

  ```ts
  export type DrawingAnchor = { date: string; price: number };
  export type Drawing =
    | { id: string; kind: 'hline'; p1: DrawingAnchor; label?: string }                  // 水平线：仅 p1.price 有效
    | { id: string; kind: 'trend'; p1: DrawingAnchor; p2: DrawingAnchor; label?: string }
    | { id: string; kind: 'ray'; p1: DrawingAnchor; p2: DrawingAnchor; label?: string }  // 从 p1 过 p2 向右延伸
    | { id: string; kind: 'text'; pos: DrawingAnchor; text: string };
  ```

  锚点一律存"日期 + 价格"数据坐标（非像素/索引）——与 §3.3/§3.4 日期锚定同一哲学：渲染时经 `model.xAxisData.indexOf(date)` 映射为类目索引，dataZoom 缩放平移与渐进加载前插数据（只前插不移除）时画线天然跟随、不漂移。id 用 `crypto.randomUUID()`（**非安全上下文**如 `http://局域网IP` 下该 API 为 undefined——jsdom 26 实测有——drawings.ts 内 `newId()` 带 `Math.random` fallback）。
- **Props**（在 §3.3 的 `CandlestickChartProps` 上追加两个可选字段；默认值保持旧行为，概念卡零改动）：

  ```ts
  /** 画线集合；仅传 onDrawingsChange 时渲染画线工具栏 */
  drawings?: Drawing[];
  onDrawingsChange?: (next: Drawing[]) => void;
  ```

- **渲染**（挂 candlestick 系列 markLine/markPoint，主图 grid，不动多 grid 布局）：
  - hline/trend/ray → `markLine.data` **嵌套两点形态** `[[{coord: [类目索引, 价格]}, {coord: [类目索引, 价格]}]]`（实测 echarts 6.1.0：扁平 `[{coord},{coord}]` 形态 setOption 直接抛 TypeError——`MarkLineView` 对 data 每元素走非数组分支后读 undefined.coord，必须嵌套一层）；text → `markPoint.data` `{coord: [idx, price], symbol: 'circle', symbolSize: 0, label: {show: true, formatter: () => text, color: DRAWING_COLOR}}`（函数 formatter，避模板语义串味；实测 `symbol: 'none'` 不创建 symbol 元素、label 挂在 symbol 上因此不渲染；`symbol: 'circle'` + `symbolSize: 0` label 正常渲染）。text 锚点价格超出 extent → 不渲染（**不裁剪**——点无法裁剪，与 markLine 的求交口径区别如此，实测越界 markPoint 静默不渲染）。无 drawings 时不挂 markLine/markPoint（现状 option 不变，现状断言不破）。
  - **markLine 默认样式全量显式覆盖**（实测默认 symbol `['circle','arrow']`、lineStyle 虚线、label.show true、hover emphasis 加粗——全部不符合画线语义）：每条 `symbol: 'none'`（两端点无符号）、`lineStyle: {type: 'solid', width: 1.5, color: DRAWING_COLOR}`、`emphasis: {disabled: true}`（关闭默认 hover 加粗，选中态 width 3 成为唯一加粗来源）、label 按线型且 **formatter 一律函数形式**（hline `{show: true, formatter: () => label ?? price.toFixed(2)}`；trend/ray `{show: false}`，标注文字走单独 text 线型）——字符串 formatter 走 ECharts 模板语义，用户文本含 `{a}` 等花括号会被替换串味。
  - **窗口内裁切/外推（x 与 y 双向）**：实测 markLine 无 clipPath，但 `Cartesian2D.containData` 要求 from/to 两点都在轴范围内、否则整条 item 静默丢弃——只裁 x 不裁 y 会让"两端点价格都在可视区外、中段横穿可视区"的线整条消失。先 x 后 y：
    - x 方向按当前可见窗口（dataZoom 的 startValue/endValue，即 §3.3 `visibleRange` prop，未传时全量范围）重算端点——trend 端点超窗截断到窗口边缘：`price = p1.price + (p2.price − p1.price) × (idxEdge − idx1)/(idx2 − idx1)`；ray 起点 = p1 与窗口左缘交点二者中靠右者（p1 在窗口左外时用同式在左缘求交——射线向右无限延伸，横贯窗口不跳过）、终点 = 直线在窗口右缘的求值（外推/内插同式）：`priceR = p2.price + (p2.price − p1.price) × (idxR − idx2)/(idx2 − idx1)`（idx1 ≠ idx2 由提交/载入校验保证，无除零）；hline 两点 = [窗口最左索引, price]→[窗口最右索引, price]。跳过规则按线型：trend 两端点均在窗口同一侧（与窗口区间无交集，整条在窗口外）→ 跳过；ray 仅当 p1 在窗口右侧（整条射线在窗口外）→ 跳过。
    - y 方向与真实可见价格范围求交：**夹取边界 = yAxis 真实 extent**（`instance.getModel().getComponent('yAxis', 0).axis.scale.getExtent()`）——**禁用数据极值近似**：实测 extent = 各系列（含 BOLL 上/下轨，可超 high/low）极值并集再向外 nice 取整，[min(low), max(high)] 与 extent 顶/底之间存在空带（实测 ~2% 价格 ≈ 30px），空带内画线若被夹到 max(high) 会压平渲染成错位线、若裁剪为空则提交后当场消失。**extent 内的端点一律按原价渲染，仅对超出 extent 的端点求交**（保留斜率，禁止只改 price 致斜率畸变）；**求交落位取 extent 边界值本身或向内 `clamp(v, min+ε, max−ε)`**——实测 extent 边界严格包含：恰等于边界出图、+1e-9 即整条静默丢弃，边界余量会造成"该显示的线整条消失"；两锚点价格相等 = 水平线段时跳过 y 求交。**整条在外的判定 = 线段与 [extentMin, extentMax] 无交集**（几何判定 + 极小 epsilon，hline 同此判定）——废除"5% 外扩缓冲"口径：缓冲带内"仅对超界端点求交"无有效交点，实现两种读法（压平到轴边缘/跳过）都错，必须写死几何判定。
    - **读取时序（两条路径一个构建函数）**：option 构建是同步纯函数、extent 渲染后才可读——**option 构建与渲染后 merge 修正共用 `buildDrawingsMarks(drawings, window, extent, selectedId)`**（选中态 width 3、label formatter、样式覆盖全部由它产出，否则 merge 会抹掉选中高亮）。组件持 `lastExtentRef = {windowKey, extent}`：渲染后 `useLayoutEffect`（同帧修正，免闪烁）读真实 extent、直连 `instance.setOption({series: [{markLine}]}, {notMerge: false})`（series 按索引合并到 candlestick，不重建整图）修正并更新 ref；extent 读取**门控到实例就绪**（echarts-for-react 3.0.6 mount 先建临时实例、finished 后 dispose 重建，临时实例上读 yAxis 得 undefined——用 onChartReady/finished 门控，读数校验轴存在，失败待下一次渲染重试）。option 构建时——**windowKey 与当前窗口一致 → 用 ref 里的真实 extent（精确）；不一致（缩放/平移后的首帧）→ 用当前窗口的并集兜底**：可见窗口内主图全系列（ohlc high/low + MA + BOLL 上/中/下轨）极值并集（该并集恒 ⊆ extent——nice 取整只向外扩——containData 恒通过、不丢线）。**并集仅作 containData 兜底、不作夹取精度目标**——窗口变化首帧用旧 extent 的偏差 = 两窗口 extent 之差（可远大于 nice 幅度），会整条丢线。
  - 窗口日期 → 类目索引统一走 `dateToIndex`，**两条口径分工固定**：窗口边缘日期（visibleRange 端点，可能是非交易日自然日）→ 最近类目吸附（与 ECharts startValue 吸附同语义）；**画线锚点日期 → 命中即用、未命中即跳过渲染**（不做吸附——吸附会移动画线端点致斜率错乱，并可造出 idx1===idx2 除零）。
  - **锚点日期不在当前 xAxisData 时跳过渲染不删数据**：`indexOf(date) === -1` 的场景 = 页面刷新后初始只加载 180 天，而画线是在此前扩展到更早历史时画的——线在窗口外，跳过即可；待数据扩展回来后自动恢复渲染（渐进加载只前插不移除，日期恒可回到 xAxisData）。
  - 提交校验：trend/ray 若 `p1.date === p2.date`（垂直两点）则拒绝提交（射线方向未定义）；text 空串拒绝提交（防不可见、不可命中的幽灵条目）；渲染处对 trend/ray 兜底 `idx1 === idx2 → 跳过`（覆盖 localStorage 载入路径）。
  - 颜色：`DRAWING_COLOR = '#38bdf8'`（sky-400，与 MA 五色/BOLL 灰/MACD 红绿均不撞色）。
- **交互模式状态机**（组件内 state：`'view' | 'draw' | 'edit'`，初始 'view'）：
  - 工具栏（`onDrawingsChange` 存在时渲染在图上方一行）：`[画线][编辑][清空]`；draw 模式展开线型四选一（默认趋势线）+ `[完成]`；edit 模式显示 `[删除]`（选中后可用）与"Esc 退出"提示。清空 = 一键删除全部（本地标注误清不致命，不做二次确认）。
  - 模式副作用：draw/edit 模式内 inside dataZoom 禁用滚轮缩放与鼠标平移（`zoomOnMouseWheel: false, moveOnMouseMove: false`——zr 鼠标事件与滚轮缩放互斥）；**slider 仍可拖动且照发 datazoom 事件 → §3.4 状态机照常处理**（画线数据日期锚定，不受影响）；tooltip 恒 `{showContent: false, trigger: 'axis'}`（§3.7 全局移除悬浮气泡；§3.6.2 实测结论 5：`show: false` 会连轴失效、勿用），画线模式无需额外开关；axisPointer 十字照常。
  - **绘制**（zr 鼠标事件；提交前校验事件像素位于主图 grid 矩形内——`chart.getModel().getComponent('grid', 0).coordinateSystem.getRect()` 包含性判断，副图/slider 区域事件直接忽略）：
    - trend/ray：mousedown 定 p1 → mousemove 预览 → mouseup 定 p2 提交；Esc 取消。
    - hline：mousedown 定价格 → 预览 → mouseup 提交。
    - text：单击定位 → 工具栏旁出现输入框 → 回车/失焦提交；Esc 取消——**取消优先于 blur**（Esc 置取消标记，紧随的 blur 检查标记后不再提交）。
    - **预览与拖拽预览统一走 zr 原生图元**（`import { graphic } from 'echarts'` + `new graphic.Line({shape, style})`，`instance.getZr().add(lineShape)` 像素坐标、提交/取消 `getZr().remove(lineShape)` 移除——实测与 echarts 同一 zrender 实例正常出图，frontend 无需单独声明 zrender 依赖）——不经 setOption：实测 echarts-for-react 对含内联着色回调的 option 深比较恒不等，父组件任何重渲染（如拖拽期间 bars 请求落地、isFetching 翻转）都会以 notMerge 全量重建并抹掉 graphic 式预览，zr 图元完全免疫该重建；拖拽中的画线同样隐藏其 markLine 项（按 id 过滤）、以 zr 图元预览，mouseup 才 `onDrawingsChange` 提交（避免逐 mousemove 触发父 setState 全量重建，与 §3.3 事件风暴去重同一考虑）。
    - mouseup 落在 grid 外（如拖到 slider 上松开）→ 取消本次绘制。
    - 提交 = `convertFromPixel({xAxisIndex: 0, yAxisIndex: 0}, [x, y])` → (类目索引, 价格) → `Math.round` 索引→日期（`model.xAxisData[idx]`）+ 价格 `round` 2 位 → 追加 Drawing → `onDrawingsChange`。
  - **编辑**：mousedown 命中检测（纯函数 `hitTest`，放 drawings.ts 可单测）——像素空间距离：点到线段投影距离 ≤ 6px 命中线身、距端点 ≤ 8px 命中端点（端点优先）；text 按 label 包围盒估算 ≤ 6px。命中后拖拽：端点 → 只移该锚点；线身 → 双锚点同步平移（数据坐标 delta）；hline → 只改 price；text → 整体平移。拖拽预览同绘制预览（zr 图元，见上），mouseup 才 `onDrawingsChange` 提交。**拖拽提交同走绘制提交校验**：拖到两锚点同日（`p1.date === p2.date`）不通过则回退拖拽前锚点（否则渲染兜底跳过致线当场消失、且刷新后被 loadDrawings 按 kind 校验永久丢弃）。**选中项视觉**：命中即选中，选中项以 `lineStyle.width = 3` 渲染（与渲染节选中态同一表达）。edit 模式 Delete 键删除选中；未命中点击取消选中。
  - **键盘事件落点**：Esc/Delete 挂 document 级 keydown 监听，组件卸载时清理（图表容器 div 默认不可聚焦，不能挂容器上）。
- **localStorage 持久化**：
  - key：`liveprofit.market.drawings.v1.{symbol}`（版本前缀留迁移余地；命名与既有 `liveprofit-ui-preferences` 单 key 风格不同是有意取舍——画线按标的多 key 需要前缀分组；已知简化：key 不含 interval——大盘页当前仅 1d，未来加周期时纳入 key）。
  - `loadDrawings(symbol)` / `saveDrawings(symbol, drawings)` 纯函数：读入**按 kind 逐项校验**（localStorage 是可手改的不可信输入，校验契约必须闭合）——通用项：kind 枚举合法、price `Number.isFinite`、date 匹配 `YYYY-MM-DD`、label/text 长度 ≤ 50、总条数 ≤ 100；**按 kind**：hline 需 p1；trend/ray 需 p1+p2 且 `p1.date !== p2.date`（载入路径不受提交校验覆盖，否则渲染除零出 NaN）；text 需 pos 且 text 非空。任一不过即丢弃该条目；try/catch 包裹（JSON 解析失败返回 []）。
  - MarketIndicesPanel（`IndexCandlestickSection`）接线——组件已按 symbol 作 key（[MarketIndicesPanel.tsx:68](Liveprofit/frontend/src/modules/market/pages/MarketIndicesPanel.tsx#L68)），切标的自动重挂载，无需 symbol 变化 effect：

    ```ts
    const [drawings, setDrawings] = useState<Drawing[]>(() => loadDrawings(asset.symbol));
    useEffect(() => { saveDrawings(asset.symbol, drawings); }, [asset.symbol, drawings]);
    ```

  - 图表调用处传 `drawings={drawings} onDrawingsChange={setDrawings}`（与 §3.4 的 visibleRange/onDataZoom 同处透传）。
- **与既有模块的交互**：draw/edit 模式内 inside zoom 的滚轮/平移禁用（slider 仍可拖动并照发 datazoom → §3.4 状态机照常处理，画线日期锚定不受影响）；画线提交/编辑只走 `onDrawingsChange`（父 setDrawings），不影响 barsQuery；§3.3 的 `notMerge` + `animation: false` 不动（预览走 zr 图元，不经 setOption）。

#### 3.6.2 三方依赖能力评估

- echarts 6.1：`markLine`（嵌套两点数据坐标）、`markPoint`、`convertFromPixel`/`convertToPixel`、`getZr()` 鼠标事件与 zr 原生图元均为官方能力（官方 "draggable points" 示例即 zr 事件模式）。无新增依赖。
- echarts-for-react 3.0.6：`ref.getEchartsInstance()` 获取实例官方支持（组件内挂 `ref` 取实例，供 zr 事件/坐标换算使用）。
- **已实测结论（本地 echarts 6.1.0 SSR 渲染，实现时不必再验）**：
  1. markLine 两点形态必须嵌套（`[[{coord},{coord}]]`）——扁平 `[{coord},{coord}]` 实测 `MarkLineView` 抛 TypeError、setOption 直接异常；嵌套形态在 candlestick 系列上渲染正常；
  2. markPoint `symbol: 'none'` 实测不创建 symbol 元素、label 不渲染（label 挂在 symbol 元素上）；`symbol: 'circle'` + `symbolSize: 0` 实测 label 正常渲染；
  3. markLine 无 clipPath，但 `Cartesian2D.containData` 要求 from/to 两点都在轴范围内、否则整条 item 静默丢弃（本模块以 x/y 双向求交规避，见 3.6.1）；
  4. markLine 默认样式实测为 symbol `['circle','arrow']`、lineStyle 虚线、label.show true、hover emphasis 加粗——渲染项全量显式覆盖（见 3.6.1）；
  5. tooltip 显示与 axisPointer/updateAxisPointer 联动（2026-09-14 实测修正）：**`show: false` 会在模型层短路 trigger（`modelHelper.js:103` 先判 show），axisPointer 十字与 `updateAxisPointer.axesInfo` 一并失效**（实测 axesInfo=[]、虚线 0 条）——读条数据源与画线定位十字同断，禁用该写法；**改用 `tooltip: { showContent: false, trigger: 'axis' }`**——实测 axesInfo 1 项、十字线 1 条、仅气泡内容不渲染（SSR 无法验 DOM 层，气泡框是否完全不可见实现时浏览器复核）；`setOption` 不派发 datazoom（§3.3 结论不变）。
- 待实现时实测项：`getComponent('grid', 0).coordinateSystem.getRect()` 像素矩形的读取。**已 SSR 实测（不必再验）**：extent 读取可读且 zoom 后随窗口更新；extent 边界严格包含（恰等于边界出图、+1e-9 即整条静默丢弃）；echarts-for-react 3.0.6 mount 先建临时实例、finished 后 dispose 重建（extent 读取须门控到 finished/onChartReady 后，临时实例上读 yAxis 得 undefined）。extent 读取失败兜底 = `convertToPixel` 反算主图轴范围。

#### 3.6.3 风险与验证方式

- 风险：模式切换配置残留（dataZoom 未恢复）；线密集时端点/线身命中误判；localStorage 脏数据；射线外推/截断数值；y 夹取边界 = 真实 extent 的读取时序（useLayoutEffect 同帧 merge 修正；窗口变化首帧以并集兜底——并集仅保证 containData 恒过，该帧夹取精度回落为近似）；切标的载入时序。
- 验证：
  1. `CandlestickChart.test.tsx`：传 drawings + onDrawingsChange → option 的 candlestick 系列含 markLine（**嵌套两点形态**）与 markPoint；坐标按线型分别断言——trend/ray = `xAxisData.indexOf(锚点日期)` 与价格、hline 两端 = 窗口左右缘索引（与锚点日期无关）；markLine 样式覆盖断言（symbol none/实线/宽度）；text → markPoint `symbol: 'circle', symbolSize: 0` + label 断言；**无 drawings → 无 markLine/markPoint**（现状断言不破）；无 onDrawingsChange → 无工具栏（概念卡回归）。
  2. **真 echarts 渲染断言**（防 option 形态断言永远绿）：echarts 6.1 SSR 模式（`init(null, null, {renderer: 'svg', ssr: true, width: 600, height: 360})` + `renderToSVGString()`——实测缺 width/height 出空 SVG、按原文断言必假红）渲染含 drawings 的 option——无异常抛出、SVG 含线段 path 与文字 label；**增补一例"两端点价格超区间、中段横穿可视区"并断言 SVG 含该线描边**（描边色计数法实测可行；本断言即 B1/B2/M3 的回归锚；覆盖 option 首帧的并集兜底路径，extent 精确路径由验证项 4/5 的 merge 断言覆盖）。
  3. 射线外推数值断言：p1/p2 与窗口 [start, end] 给定 → 终点 = 窗口右缘类目索引 + 线性外推价格（构造数值验算）；p1 在窗口左外 → 起点 = 左缘交点（射线横贯窗口不被跳过）。
  4. trend 端点超窗截断断言：p1 在窗口外 → 渲染端点 = 窗口左缘 + 插值价格；整条线在窗口外 → markLine 不含该条目；y 求交：两端点价格超出 extent、中段横穿 → 端点求交收进 extent（构造数值验算）；**两锚点价格均在 max(high) 与 extent 顶之间 → markLine 价格不被改写**（守护过度夹取，与"中段横穿"断言配套；本断言经渲染后 merge 路径验证——option 首帧走并集兜底路径，锚点落并集顶之上会被夹取）。
  5. 绘制交互：**echarts-for-react mock 改造为 forwardRef + useImperativeHandle 暴露假实例**（需实现的面：`getEchartsInstance`/`getZr().on`/`getZr().add/remove`/`convertFromPixel`/`convertToPixel`/`getModel().getComponent('grid', 0).coordinateSystem.getRect()`/`getModel().getComponent('yAxis', 0).axis.scale.getExtent()`/`setOption`——现有 mock 是无 forwardRef 的普通函数组件，ref 取不到实例）→ mousedown/mousemove/mouseup 序列 → onDrawingsChange 收到新 trend 且锚点日期 = `model.xAxisData[idx]`、价格 2 位小数；**另断言渲染后 merge 被调用且按真实 extent 改写/不改写**（含 windowKey 变化用例）。
  6. 编辑拖拽：mock 命中端点/线身 → 断言锚点更新数值（端点只移一端、线身双端同步平移）；选中项 markLine `lineStyle.width = 3` 断言（**含 merge 修正后选中项仍为 width 3**——构建与 merge 共用 buildDrawingsMarks 的回归锚）。
  7. 新增 `drawings.test.ts`（纯函数）：loadDrawings 按 kind 非法条目丢弃（kind 错/NaN/日期格式错/超长/text 缺 pos 或空 text/trend 两锚点同日期）；save/load 往返；hitTest 距离判定（线身/端点/未命中）。
  8. `MarketIndicesPanel.test.tsx`：localStorage mock → 初载从 `liveprofit.market.drawings.v1.{symbol}` 读取；onDrawingsChange 触发后保存；切标的（key 变化 → 重挂载）载入对应标的 key。

#### 3.6.4 文件变更清单

- 新增 `frontend/src/shared/charts/drawings.ts`：Drawing 类型、newId、load/save、校验、hitTest 纯函数。
- 修改 `frontend/src/shared/charts/CandlestickChart.tsx`：props、markLine/markPoint 渲染（窗口内裁切/外推）、工具栏、模式状态机、zr 事件（绘制/编辑/删除）。
- 新增 `frontend/src/shared/charts/drawings.test.ts`；修改 `CandlestickChart.test.tsx`（渲染/交互断言）、`MarketIndicesPanel.test.tsx`（持久化接线断言）。
- 修改 `frontend/src/modules/market/pages/MarketIndicesPanel.tsx`：drawings state + localStorage 接线 + props 透传。

### 3.7 图例重构与悬浮数据读条（2026-09-14 用户新增需求）

#### 3.7.1 模块设计

- **范围（2026-09-14 用户拍板）**：图例纯文字（无圆圈/标记）、默认展示系列色文字、点击隐藏系列且文字变灰；两行分组（行 1 = MA+BOLL、行 2 = DIF+DEA）；移除 tooltip 悬浮气泡；图例右侧两行数据读条（第一行 OHLC、第二行 量+指标），未悬浮显示最新一根（= 可见窗口末根，口径见下文数据读条节）。
- **图例（双 legend 实例，实测支撑）**：echarts 6.1.0 SSR 实测——legend 数组官方支持（各自 top 定位渲染）、`icon: 'none'` 无可见标记图形（仍产出空 `d` 的不可见 path）、`inactiveColor` 使 selected=false 的项文字变灰、**图例文字默认色 #54555a 不继承系列色**（'inherit' 在 SSR 中字面透传、解析行为不确定）→ 逐项显式 textStyle.color：
  - 行 1（top: 0）：MA5/10/20/60 + BOLL上轨/中轨/下轨，颜色复用组件常量 `MA_COLORS`/`MA_FALLBACK_COLOR`/`BOLL_LINE_COLOR`（**legend data 必须与系列名逐字一致**——实际系列名为 `BOLL上轨/中轨/下轨`，[CandlestickChart.tsx:87](Liveprofit/frontend/src/shared/charts/CandlestickChart.tsx#L87)，否则点击不联动）；
  - 行 2（top: 18）：DIF/DEA（`MACD_DIF_COLOR`/`MACD_DEA_COLOR`）；
  - 每行 `icon: 'none'`、`inactiveColor: '#8b95a1'`（灰 = 项目 muted token，[styles.css:14](Liveprofit/frontend/src/styles.css#L14)，与轴标签/dataZoom 同色）、fontSize 11、**`left: 0` + `itemWidth: 0` + `itemGap: 8`**（实测 legend 默认 `left: 'center'` 且 `icon: 'none'` 仍占 25px 图标位宽——width 544 下行 1 七项跨距 432.7px，与右侧读条必然压字；`left: 0` + `itemWidth: 0` 后跨距 282.7px 让出读条位；`itemWidth: 0` 对点击热区的影响实现时人工确认）；
  - 行按系列存在性动态构造：**双行 ⟺ MA/BOLL 与 MACD 同存**——无 MACD → 无行 2（单行）；仅 MACD（无 MA/BOLL）→ DIF/DEA 单行且**定位 top 0**（单行预算，勿沿用行 2 的 top 18——否则与主图 top 32 余量仅 ~1px）；纯 K 线 → 无图例（现状）；
  - 成交量/MACD柱不进图例（§3.1 决策保持）；点击行为原生（隐藏系列 + 文字变灰）。
  - **legendSelected React 化（防 notMerge 重建重置选中）**：组件 notMerge 全量重建时 legend 组件从新 option 重造，selected 不注入即回全选——用户点掉的系列会弹回。持 `legendSelected` state，`legendselectchanged` 事件读实例 `getOption().legend[].selected` 回填（**实测两个 legend 实例共享同一份全局 selected 映射，读任一即可**），option 构建时注入——**各 legend 只注入自己 data 中的项**（异 legend 的键不注入，避免未知名字混入）。
- **主图 grid top 联动（§3.1 表已同步改）**：图例占位——双行 32px 预算（行 1 0–14、行 2 18–32，行 2 文字底缘约 31px，top 36 留约 5px 余量）→ 主图 top 36；单行 → top 32；**无图例 → top 24→30**（读条两行 ~30px 预算，防读条压主图顶缘——纯 K 布局与 KLineDialog 消费方同规则）。大盘页高度 360→420（§3.1 表验算：三面板布局主图 174px ≥ 170px 门槛；**H=360 不升高的真实约束 = 主图预算 360−36−(360×50%) = 144px < 170px**——旧算式里的 28.8/7.2 间距是已证伪的重叠量，不沿用）。
- **tooltip 移除**：`tooltip: { showContent: false, trigger: 'axis' }`——**禁用 `show: false` 写法**（实测在模型层短路 trigger，axisPointer 十字与 `updateAxisPointer.axesInfo` 一并失效，读条数据源与画线十字线同断；§3.6.2 实测结论 5）；`showContent: false` 实测 axesInfo 1 项、十字线 1 条、仅气泡内容不渲染（气泡框 DOM 是否完全不可见实现时浏览器复核）。axisPointer 十字保留（画线模式定位参考依赖不变）；§3.6 模式副作用原"画线中隐藏气泡"开关已删（恒隐藏，无需开关）。
- **数据读条（图例右侧，React overlay）**：
  - 结构：**定位上下文 = 包住 ReactECharts 的那层 div**（`position: relative`，同 `style={{height}}`；§3.6 画线工具栏在该 div 之外，读条 `top:0 right:0` 不会落到工具栏行）；读条 = `absolute top:0 right:0` 的 overlay div，`pointer-events: none`（不挡图例点击），与图例同处顶部条带内右对齐；`max-width: 45%` + `white-space: nowrap; text-overflow: ellipsis`（图例 `left:0 + itemWidth:0` 后行 1 跨距 282.7px，与读条之和 527.7px ≤ 544px 卡宽、横向不压字；**行 1（OHLC 行）必保完整，行 2 项多时尾部 ellipsis 截断属可接受**——字号/标签缩写为可调项，实现时以 544px 卡宽实测调参）。
  - 内容两行（用户拍板"第一行放 OHCL"）：行 1 = `开 3120.50 高 3150.20 低 3100.80 收 3145.00`（四值按该根 K 线涨跌着色：收≥开 → `#ef4444`、否则 `#22c55e`，与 K 线同色）；行 2 = `量 1.23亿手 MA5 3130.10 BOLL上轨 3180.30 DIF 1.20 DEA 0.90`（量同涨跌色；指标各用系列色；该索引为 null 的项跳过）。
  - 数据源：`updateAxisPointer` 事件（echarts 官方事件；axesInfo[0].value = 类目**整数**索引——实测 Ordinal.scale 已取整、无需 round → `model.xAxisData[idx]` → 同索引取 `model.ohlc/volume/ma/boll/macd`——全部既有 ViewModel 数据，无新计算、无新接口）。**离场守卫：实测鼠标移出时会再派发一次 `axesInfo: []`——取数写法 `ev.axesInfo?.[0]`，空则 `hoverIdx = null`（回落"未悬浮"分支，不得落入越界不渲染分支致读条消失）**。
  - 未悬浮（hoverIdx = null）→ 显示**可见窗口最后一根**（`visibleRange` 末端日期对应索引——§3.4 平移/缩放后数据集末尾可能在屏幕外，取窗口末根才有意义；无 `visibleRange` 时兜底 `xAxisData.length - 1`）；bars 空/索引越界 → 不渲染读条；draw/edit 画线模式照常更新（无冲突）。
  - **无图例布局的读条规则（KLineDialog 消费方，`height={320}`，纯 K 线+成交量、无指标）**：无图例时读条仍渲染（两行右对齐，主图 grid top 30 已让位）；KLineDialog 是用户读单根数值的入口之一，tooltip 移除后读条即其替代——§3.7.3 人工检查覆盖该消费方。
  - 量格式化：按量级自适应缩写（手 → 万手/亿手），格式化函数 = `frontend/src/shared/format/volume.ts`（+ 同名 `volume.test.ts`）。
- **性能守卫（防逐像素 setOption 全量重建）**：hover 索引是逐 mousemove 的 state，不能放在渲染 ReactECharts 的层级——§3.3 已知 echarts-for-react 对含内联回调的 option fast-deep-equal 恒不等，父重渲染即 notMerge 全量重建。组件内拆两层：
  - `ChartCore`（`React.memo`）：渲染 ReactECharts，props 仅 option（`useMemo` 构建，依赖 model/visibleRange/drawings/legendSelected 等真实输入）+ 稳定回调（`useCallback`）；
  - 读条为兄弟组件；hoverIdx state 放在外层 CandlestickChart——hover 变化只重渲染读条，ChartCore memo 拦截不重渲染 → 无 setOption 风暴；
  - `updateAxisPointer`/`legendselectchanged` 事件经 ChartCore 的 echarts-for-react `onEvents`（稳定引用）上抛。

#### 3.7.2 三方依赖能力评估

- legend 数组多实例、`icon: 'none'`、`inactiveColor`、逐项 textStyle.color：**已 SSR 实测**（2026-09-14 本方案探针：legend 数组接受且各自定位渲染；icon none 无可见标记图形（仍产出空 `d` 的不可见 path）；默认文字色 #54555a 不随系列色；selected=false 的项文字变 inactiveColor #8b95a1）。
- `updateAxisPointer` 事件与 axesInfo value 为官方 API（类目索引数字，与 §3.3 datazoom 读回索引数字同口径）。
- 待实现时实测项：① `tooltip: {showContent: false}` 的气泡框 DOM 是否完全不可见（SSR 无法验 DOM；若边框残留，备选方案 = 顶层 `axisPointer: {show: true}` + yAxis 逐轴 `axisPointer: {show: false}`——实测该方案 axesInfo 2 项、x+y 双十字线，需收掉横向指针）；② `legendselectchanged` 在双 legend 实例下的事件参数与 `getOption().legend[i].selected` 结构（实测 payload = `{name, selected}` 全量映射、两 legend 共享同一份 selected）。
- 无新增依赖。

#### 3.7.3 风险与验证方式

- 风险：双 legend 与 notMerge 重建的 selected 重置（legendSelected React 化规避，见 3.7.1）；读条与图例横向争抢顶部条带（left:0 + itemWidth:0 + 读条 max-width 45%/ellipsis，见 3.7.1）；legend 点击与 `itemWidth: 0` 热区在双 legend 实例下的行为（原生机制，实现时人工确认）。
- 验证（**前置依赖：§3.6.3 验证 5 的 mock 改造——forwardRef + useImperativeHandle 假实例，且需含 `getOption` 面——与本轮一并完成**）：
  1. `CandlestickChart.test.tsx` option 断言：legend 为双实例（行 1 top 0 七项、行 2 top 18 DIF/DEA；icon none；inactiveColor #8b95a1；逐项 textStyle.color = 对应系列色常量；left 0/itemWidth 0/itemGap 8）；降级布局（无 MACD → 单 legend；仅 MACD → DIF/DEA 单行；纯 K 线 → 无 legend）；tooltip `{showContent: false, trigger: 'axis'}`；双行时主图 grid top = 36、单行 = 32、无图例 = 30。
  2. legendSelected 保留：mock `legendselectchanged` → 实例 getOption 返回 selected → 断言后续 option 注入 selected（各 legend 只注入自己 data 的项）、重渲染不回全选。
  3. 读条渲染：mock `updateAxisPointer`（axesInfo[0].value = 2）→ 读条显示 xAxisData[2] 的开高低收/量/指标值、着色断言（收≥开红否则绿、指标系列色）、null 项跳过；**show → hide 两次事件序列用例**（第二次 axesInfo 为空 → 断言回落可见窗口末根而非读条消失）；平移窗口后未悬浮 → 断言取可见窗口末根（非数据集末尾）。
  4. ChartCore memo 守卫：hoverIdx 变化时 ChartCore 重渲染计数 = 0（防逐像素 setOption 风暴的回归锚）。
  5. 真 echarts SSR 断言：双 legend + icon none 的 option 渲染无异常、SVG legend 区无可见标记图形（可留空 d path）、文字 fill 含系列色。
  6. `CandlestickChart.test.tsx` 既有断言随迁（:137-139 图例断言、:56 纯 K 无 legend、:82 单实例 legend 七项——全部随双 legend 改造）。
  7. `volume.test.ts`：量级缩写边界（null/0/9999/1e4/1e8、小数位）。
  8. 人工检查（验证总表）：窄卡图例+读条不压字；KLineDialog（`height={320}` 无图例布局）读条显示且不压主图顶缘。

#### 3.7.4 文件变更清单

- 修改 `frontend/src/shared/charts/CandlestickChart.tsx`：双 legend、tooltip showContent:false、读条 overlay、ChartCore 拆分、legendSelected state、主图 grid top 联动（含无图例 top 30）。
- 修改 `frontend/src/shared/charts/CandlestickChart.test.tsx`：图例/读条/memo/selected 断言 + mock 改造（forwardRef 假实例含 getOption，§3.6.3 验证 5 前置）+ 既有图例断言随迁。
- 新增 `frontend/src/shared/format/volume.ts` + `volume.test.ts`：量级缩写格式化。
- `MarketIndicesPanel.tsx`：本模块无改动（height 360→420 见 §3.1.4，非本模块）。

## 四、已确认决策 / 待确认问题

- 已确认决策：
  1. 后端移除 365 天范围上限（用户 2026-09-12 拍板）——✅ 已由证券市场数据库统一方案先行落地（2026-09-13，见 3.5）。
  2. MA/BOLL 线样式 = 细线（width 1）+ 半透明（opacity 0.5），对齐用户提供的 ECharts 示例。
  3. 历史数据回填已由证券市场数据库统一方案完成（2026-09-13 归档：指数日线+因子 1990 起入库 market schema），本方案只做前端代码改动。
  4. 证券市场数据库统一方案已先行完成并落地后端上限移除；本方案剩余范围 = 前端交互（§3.1–§3.4），后端无改动。
  5. 画线工具范围（2026-09-13 拍板）：四线型（水平线/趋势线/射线/文字标注）+ 画/拖拽修改/删 + localStorage 按标的持久化 + 仅大盘页全尺寸图（概念卡不传新 props 行为不变）。
  6. 图例与读条（2026-09-14 拍板）：图例纯文字无标记、系列色、点击隐藏变灰；两行分组（行 1 MA+BOLL、行 2 DIF+DEA）；移除 tooltip；读条在图例右侧两行（第一行 OHLC、第二行 量+指标），未悬浮显示最新一根（= 可见窗口末根，口径见 §3.7.1）。
- 待确认问题：无（§3.6/§3.7 范围均已拍板；图表高度 420px、迟滞 10%/半屏、命中阈值 6px/8px、画线颜色、图例/读条字号、量级缩写格式等视觉与节奏参数为实现后的可调项，不阻塞实施）。

---

## 实现收尾同步清单（产品需求分析.md）

实现完成、归档时同步 [产品需求分析](../../../knowledge/产品需求分析.md)（共六处，与本方案的交互变更同源，避免契约文档与新行为矛盾）：

1. §3.1.3.2 表格"日期范围选择"行 → 改为按需加载交互（默认近 3 个月可见、预拉 6 个月、滚轮缩小/左平移自动扩展至数据边界）；
2. §3.1.3.2 🟦 交互补充① → "近 60 个交易日默认加载"改为 6 个月预拉 / 3 个月可见；"用户修改区间后该会话内保持（切资产不重置区间）"半句删除（日期输入已移除，按需加载状态各图独立，无用户区间修改）；
3. §7.2 Q-01 两处 365 表述一并处理：建议默认值条目"K 线窗口上限：单次查询 ≤ 365 自然日"与 ✅ 已确认行"K 线窗口上限 365 自然日"——后者属历史确认记录，用修订注记"2026-09-13 更新：上限已移除（证券市场数据库统一方案先行落地）"处理；AC-25"超过服务端允许窗口提示缩小范围"改写为上限已移除、前端渐进加载，"开始晚于结束阻止提交"改为"from>to 由后端拒绝（422 RANGE_TOO_LARGE），前端已无手动范围提交路径"；
4. §3.1.3.3 契约候选段"单次查询窗口 ≤ 365 自然日"句 → 删除上限句，保留 from ≤ to 与 RANGE_TOO_LARGE 错误映射表述；**错误码总表 RANGE_TOO_LARGE 的说明（"提示缩小范围，不静默截断"）同步改为 from>to 拒绝语义**（与第 3 条 AC-25 新表述一致，避免两处矛盾）。
5. §3.1.3.2 表格"指数 K 线图"行补充画线工具描述（四线型画线 + 拖拽编辑/删除 + 本地持久化）；🟦 交互补充追加一条画线能力说明；§6.1 AC 表追加一条画线能力验收条件（四线型可画、可拖拽修改、可删除，刷新保留、切标的不串）。
6. §3.1.3.2 表格"指数 K 线图"行与 🟦 交互补充②（"K 线图数据点 tooltip 展示 开/高/低/收 + 成交量"）→ 改为图例右侧读条形态（行 1 OHLC、行 2 量+指标，未悬浮显示可见窗口末根）+ 图例纯文字系列色/两行分组的描述。

## 验证总表

| 层面 | 命令/方式 | 观察点 |
|------|----------|--------|
| 前端单测 | `pnpm --dir=frontend vitest run src/shared/charts/CandlestickChart.test.tsx src/shared/charts/drawings.test.ts src/shared/format/volume.test.ts src/modules/market/pages/MarketIndicesPanel.test.tsx` | 成交量系列/grid/着色/共存布局、线宽透明度、dataZoom 锚定与索引→日期换算、按需加载状态机（初载/缩放/平移/到头）断言；画线渲染坐标/射线外推/窗口截断、SSR 真渲染回归（markLine 嵌套形态/文字标注）、绘制与编辑交互、localStorage 校验与命中检测；图例双行/纯文字/系列色/selected 保留、读条两行值与着色（含 hide 序列回落窗口末根）、ChartCore memo 守卫、量级缩写边界 |
| 前端类型/构建 | `pnpm --dir=frontend typecheck && pnpm --dir=frontend build` | 无类型错误 |
| 后端测试 | 回归确认（本方案无后端改动）：`pytest backend/tests/contract/api/test_market_data.py backend/tests/integration/market_data/` | from>to 422 用例已由统一方案落地（test_market_data.py:91-96、test_market_flow.py:71），保持通过即可 |
| 人工检查 | 起前后端，打开大盘页 | 成交量副图显示；滚轮缩小触发 Network 面板 `from` 前移（约每 2× 一跳）；左平移近左界预拉一屏；窗口不跳；缩到头停止扩展；无日期输入框；画线工具栏出现（概念卡无）；画/拖拽/删除四线型；缩放与渐进加载后画线不漂移；刷新页面画线保留；切标的画线互不串；图例两行纯文字系列色、点击隐藏变灰（重渲染后不弹回）；悬浮无气泡、图例右侧读条显示 OHLC/量/指标、未悬浮显示可见窗口末根；窄卡图例+读条不压字；KLineDialog（无图例布局）读条显示且不压主图顶缘 |
