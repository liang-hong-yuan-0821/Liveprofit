import { useChartTheme } from './useChartTheme';
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import type { BarSeriesOption, CandlestickSeriesOption, LineSeriesOption } from 'echarts/charts';
import type { DataZoomComponentOption } from 'echarts/components';
import type { CallbackDataParams } from 'echarts/types/dist/shared';
import type { EChartsType } from 'echarts';
import {
  anchorIndex,
  DRAWING_COLOR,
  EXTENT_EPS,
  hitTestRect,
  hitTestSegment,
  newId,
  renderedSegment,
  windowEdgeIndex,
  type Drawing,
  type RenderExtent,
} from './drawings';
import { formatVolume } from '../format/volume';

// 通用 K 线图：只接受前端 ViewModel，不接收 API DTO；
// 空数据由调用方保证不渲染（不绘制空壳/伪图表）。
// ma/boll/macd 缺失（旧后端/降级）时渲染纯 K 线，不报错。

export interface CandlestickChartProps {
  model: CandlestickChartViewModel;
  height?: number;
  /** 初始可见窗口（含端点的日期字符串）；不传 = 全量 0–100（旧行为） */
  visibleRange?: { start: string; end: string };
  /** 用户缩放/平移后上报当前可见日期区间 */
  onDataZoom?: (range: { start: string; end: string }) => void;
  /** 画线集合；仅传 onDrawingsChange 时渲染画线工具栏 */
  drawings?: Drawing[];
  onDrawingsChange?: (next: Drawing[]) => void;
  /** 参考价横线（数据驱动，如止损/止盈）：虚线贯穿可见窗口全宽，非画线工具、不可编辑 */
  referenceLines?: CandlestickReferenceLine[];
}

export interface CandlestickReferenceLine {
  /** 参考价；主图 y 轴自动扩展以包含该价（scale:true + 函数形式 min/max） */
  price: number;
  /** 线左端上方标签；缺省为 price.toFixed(2) */
  label?: string;
  /** 缺省 '#38bdf8'（与画线同色） */
  color?: string;
}

export interface CandlestickChartViewModel {
  /** x 轴日期标签 */
  xAxisData: string[];
  /** [open, close, low, high] */
  ohlc: [number, number, number, number][];
  volume?: (number | null)[];
  /** 涨跌幅 %（后端 pct_chg 原值透传，不自算）；整列缺失时读条不出现「涨幅」项 */
  pctChg?: (number | null)[];
  /** 均线（后端 ma 数组原样透传），缺失不渲染 */
  ma?: { period: number; values: (number | null)[] }[];
  /** 布林带（后端 boll 原样透传），缺失不渲染 */
  boll?: {
    period: number;
    k: number;
    mid: (number | null)[];
    upper: (number | null)[];
    lower: (number | null)[];
  };
  /** MACD 副图（后端 macd 原样透传），缺失不渲染副图 */
  macd?: {
    dif: (number | null)[];
    dea: (number | null)[];
    hist: (number | null)[];
  };
}

// 均线配色——原样式（暗色主题友好，2026-09-16 用户试参考代码效果时注释保留、回退即恢复）：
// const MA_COLORS: Record<number, string> = {
//   5: '#fbbf24',
//   10: '#f472b6',
//   20: '#a78bfa',
//   60: '#34d399',
// };
// 参考代码样式（2026-09-16 用户试效果）：ECharts 默认调色板按系列序分配——candlestick
// 恒第 0、MA 系列恒为系列序 1..4，即调色板 #91cc75/#fac858/#ee6666/#73c0de；
// 显式写死色值保证图例 label+value 与线同色（完全省略 color 时 ECharts 隐式按系列序
// 取调色板，图例侧读不到该映射）；未列出的周期用中性灰兜底。
const MA_REF_COLORS: Record<number, string> = {
  5: '#91cc75',
  10: '#fac858',
  20: '#ee6666',
  60: '#73c0de',
};
const MA_FALLBACK_COLOR = '#94a3b8';
const BOLL_LINE_COLOR = '#94a3b8';
const BOLL_BAND_FILL = 'rgba(148, 163, 184, 0.15)';
const MACD_DIF_COLOR = '#e2e8f0';
const MACD_DEA_COLOR = '#fbbf24';
const MACD_HIST_UP = '#ef4444'; // 正红
const MACD_HIST_DOWN = '#22c55e'; // 负绿
const VOLUME_UP = '#ef4444'; // 涨（红，与 K 线同色）
const VOLUME_DOWN = '#22c55e'; // 跌（绿）
const LEGEND_INACTIVE_COLOR = '#8b95a1'; // 项目 muted token（styles.css:14）

const DRAW_KIND_LABELS: Record<DrawKind, string> = {
  trend: '趋势线',
  ray: '射线',
  hline: '水平线',
  text: '标注',
};

type Mode = 'view' | 'draw' | 'edit' | 'eraser';
type DrawKind = 'hline' | 'trend' | 'ray' | 'text';
// 橡皮擦悬停高亮色（sky-300）：与选中态（同宽 3 的 DRAWING_COLOR）区分
const ERASER_HOVER_COLOR = '#7dd3fc';

function legendColor(name: string): string {
  const ma = name.match(/^MA(\d+)$/);
  if (ma) return MA_REF_COLORS[Number(ma[1])] ?? MA_FALLBACK_COLOR; // 参考样式；原 MA_COLORS 见顶部注释
  if (name.startsWith('BOLL')) return BOLL_LINE_COLOR;
  if (name === 'DIF') return MACD_DIF_COLOR;
  if (name === 'DEA') return MACD_DEA_COLOR;
  return MA_FALLBACK_COLOR;
}

// 图表内核（React.memo）：hover 索引等逐像素 state 变化时由外层拦截、不重渲染内核——
// §3.3 已知 echarts-for-react 对含内联回调的 option fast-deep-equal 恒不等，
// 内核重渲染即 notMerge 全量重建，逐 mousemove 会成 setOption 风暴。
const ChartCore = memo(function ChartCore({
  option,
  onEvents,
  onChartReady,
  height,
}: {
  option: Record<string, unknown>;
  onEvents: Record<string, Function> | undefined;
  onChartReady: (instance: EChartsType) => void;
  height: number;
}) {
  return (
    <ReactECharts
      option={option}
      style={{ height, width: '100%' }}
      notMerge
      onEvents={onEvents}
      onChartReady={onChartReady}
    />
  );
});

// 顶部读数 = label + value（2026-09-15 用户改版；2026-10-02 修正行 1 渲染方式）：
// 行 1 = 涨幅（置前）+ 开/高/低/收 由 DOM 读条承载（覆盖画布顶带 top 0..18，随悬浮跟随、
// 未悬浮显示可见窗口末根）——ECharts 6 的 legend 只渲染 `getSeriesByName(name)` 命中的项
// （LegendModel._availableNames 取自 series，LegendView 对无系列名直接跳过），OHLC 是
// candlestick 的数组数据、无同名系列，留在 legend 里永远不渲染（2026-10-02 实测：仅剩「量」）。
// 行 2 = MA+BOLL、行 3 = DIF/DEA 仍走 ECharts legend；行 1 的图例位仅保留有真实系列的
// 「量」（右对齐，仍可点击开关副图）。
// 读条项按值着色：开/高/低/收随当根涨跌（收 ≥ 开红涨），涨幅按自身正负、+ 号前缀与卡片一致。
const LEGEND_DISPLAY_NAMES: Record<string, string> = {
  成交量: '量',
  BOLL上轨: 'BOLL上',
  BOLL中轨: 'BOLL中',
  BOLL下轨: 'BOLL下',
};

function legendItemColor(name: string, up: boolean): string {
  if (name === '成交量') {
    return up ? '#ef4444' : '#22c55e'; // 与 K 线同色红涨绿跌
  }
  return legendColor(name);
}

interface ReadoutItem {
  label: string;
  text: string | null; // null = 该根无值（源未提供），只显示 label
  color: string;
}

function buildReadoutItems(model: CandlestickChartViewModel, idx: number): ReadoutItem[] {
  const row = model.ohlc[idx];
  if (!row) return [];
  const fmt2 = (v: number | null | undefined) => (v === null || v === undefined ? null : v.toFixed(2));
  const ohlcColor = row[1] >= row[0] ? '#ef4444' : '#22c55e'; // 收 ≥ 开（与图例/蜡烛同口径）
  const items: ReadoutItem[] = [];
  // 涨幅排在最前（2026-10-02 用户要求，置于开之前）；整列缺失时 mapper 不设 pctChg
  // （不占位），单根缺失只出 label
  if (model.pctChg) {
    const pct = model.pctChg[idx];
    items.push({
      label: '涨幅',
      text: pct === null || pct === undefined ? null : `${pct > 0 ? '+' : ''}${pct.toFixed(2)}%`,
      color: pct == null || pct === 0 ? LEGEND_INACTIVE_COLOR : pct > 0 ? '#ef4444' : '#22c55e',
    });
  }
  items.push(
    { label: '开', text: fmt2(row[0]), color: ohlcColor },
    { label: '高', text: fmt2(row[3]), color: ohlcColor },
    { label: '低', text: fmt2(row[2]), color: ohlcColor },
    { label: '收', text: fmt2(row[1]), color: ohlcColor },
  );
  return items;
}

function legendValueFor(name: string, model: CandlestickChartViewModel, idx: number): string | null {
  const fmt2 = (v: number | null | undefined) => (v === null || v === undefined ? null : v.toFixed(2));
  if (name === '成交量') {
    const v = model.volume?.[idx];
    return v === null || v === undefined ? null : formatVolume(v);
  }
  const ma = name.match(/^MA(\d+)$/);
  if (ma) {
    const v = model.ma?.find((l) => l.period === Number(ma[1]))?.values[idx];
    return v === null || v === undefined ? null : v.toFixed(2);
  }
  if (name === 'BOLL上轨' || name === 'BOLL中轨' || name === 'BOLL下轨') {
    const key = name === 'BOLL上轨' ? 'upper' : name === 'BOLL中轨' ? 'mid' : 'lower';
    const v = model.boll?.[key]?.[idx];
    return v === null || v === undefined ? null : v.toFixed(2);
  }
  if (name === 'DIF') return fmt2(model.macd?.dif[idx]);
  if (name === 'DEA') return fmt2(model.macd?.dea[idx]);
  return null;
}

function buildLegendOptions(params: {
  model: CandlestickChartViewModel;
  hasVolume: boolean;
  legendMain: string[];
  legendMacd: string[];
  idx: number;
  legendSelected: Record<string, boolean> | undefined;
  neutralColor?: string;
}): Array<Record<string, unknown>> {
  const { model, hasVolume, legendMain, legendMacd, idx, legendSelected } = params;
  const row = model.ohlc[idx];
  const up = row ? row[1] >= row[0] : true; // 收 ≥ 开（图例项颜色随悬浮值走）
  const selectedSubset = (names: string[]): Record<string, boolean> => {
    const out: Record<string, boolean> = {};
    for (const n of names) {
      if (legendSelected?.[n] === false) out[n] = false;
    }
    return out;
  };
  const legendFor = (top: number, names: string[], rightAlign = false) => ({
    top,
    // 行 1（量）右对齐：左上留给 DOM 读条（开/高/低/收/涨幅），避免同带重合
    ...(rightAlign ? { right: 0 } : { left: 0 }),
    itemWidth: 0,
    itemGap: 6,
    icon: 'none',
    inactiveColor: LEGEND_INACTIVE_COLOR,
    textStyle: { fontSize: 10.5 },
    // 图例直接当 label + value：formatter 读取当前悬浮索引（hoverIdxRef 直读，见组件内）
    formatter: (name: string) => {
      const v = legendValueFor(name, model, idx);
      const label = LEGEND_DISPLAY_NAMES[name] ?? name;
      return v === null ? label : `${label} ${v}`;
    },
    selected: selectedSubset(names),
    data: names.map((name) => ({ name, textStyle: { color: name === 'DIF' ? (params.neutralColor ?? MACD_DIF_COLOR) : legendItemColor(name, up) } })),
  });
  // 行 1 图例只剩「量」（开高低收/涨幅由 DOM 读条承载，见 buildReadoutItems 注释）；
  // 无成交量时不建行 1 图例（读条行的顶距仍由 grid.top 预留）
  const legends: Array<Record<string, unknown>> = [];
  if (hasVolume) legends.push(legendFor(0, ['成交量'], true));
  if (legendMain.length > 0) legends.push(legendFor(18, legendMain));
  if (legendMacd.length > 0) legends.push(legendFor(legendMain.length > 0 ? 36 : 18, legendMacd));
  return legends;
}

export function CandlestickChart({
  model,
  height = 240,
  visibleRange,
  onDataZoom,
  drawings,
  onDrawingsChange,
  referenceLines,
}: CandlestickChartProps) {
  const chartTheme = useChartTheme();
  const initialTheme = useRef(chartTheme).current;
  const { series, legendMain, legendMacd, hasVolume, hasMacd } = useMemo(() => {
    const built: Array<CandlestickSeriesOption | LineSeriesOption | BarSeriesOption> = [
      {
        type: 'candlestick',
        data: model.ohlc,
        itemStyle: {
          color: '#ef4444', // 涨（红）
          color0: '#22c55e', // 跌（绿）
          borderColor: '#ef4444',
          borderColor0: '#22c55e',
        },
      },
    ];
    const mainLegend: string[] = [];
    const macdLegend: string[] = [];

    for (const line of model.ma ?? []) {
      const name = `MA${line.period}`;
      mainLegend.push(name);
      // 参考代码样式（2026-09-16 用户试效果）：smooth + 默认调色板色；数据点经用户
      // 拍板改回 symbol: 'none'（不喜欢默认空心圆点），透明度经用户调至 0.7（0.5 太淡），
      // 其余保留。原样式注释保留便于回退：
      //   const color = MA_COLORS[line.period] ?? MA_FALLBACK_COLOR;
      //   lineStyle: { width: 1, opacity: 0.5, color },
      //   itemStyle: { color },
      built.push({
        name,
        type: 'line',
        data: line.values,
        symbol: 'none',
        smooth: true,
        lineStyle: { opacity: 0.7, color: MA_REF_COLORS[line.period] ?? MA_FALLBACK_COLOR },
        itemStyle: { color: MA_REF_COLORS[line.period] ?? MA_FALLBACK_COLOR },
      });
    }

    if (model.boll) {
      const { mid, upper, lower } = model.boll;
      // Confidence Band（ECharts 官方带宽填充模式）：同 stack 系列自底向上累计，
      // 顺序必须是"下轨垫底 → 带宽差值"——带宽 areaStyle 才落在 [lower, upper] 之间；
      // 任一轨为 null 时带宽保持 null（堆叠跳过该点）。
      const band = mid.map((value, i) =>
        value === null || upper[i] === null || lower[i] === null
          ? null
          : (upper[i] as number) - (lower[i] as number),
      );
      mainLegend.push('BOLL上轨', 'BOLL中轨', 'BOLL下轨');
      built.push(
        { name: 'BOLL上轨', type: 'line', data: upper, symbol: 'none', lineStyle: { width: 1, opacity: 0.5, color: BOLL_LINE_COLOR } },
        { name: 'BOLL中轨', type: 'line', data: mid, symbol: 'none', lineStyle: { width: 1, opacity: 0.5, color: BOLL_LINE_COLOR } },
        { name: 'BOLL下轨', type: 'line', data: lower, symbol: 'none', lineStyle: { width: 1, opacity: 0.5, color: BOLL_LINE_COLOR } },
        { name: 'BOLL带-下轨', type: 'line', data: lower, stack: 'boll-band', symbol: 'none', lineStyle: { opacity: 0 }, tooltip: { show: false } },
        { name: 'BOLL带-填充', type: 'line', data: band, stack: 'boll-band', symbol: 'none', lineStyle: { opacity: 0 }, areaStyle: { color: BOLL_BAND_FILL }, tooltip: { show: false } },
      );
    }

    // 成交量副图（§3.1）：volume 存在且含非 null 值才挂 bar 系列（全 null 不开副图）；
    // 三元组 [类目索引, 成交量, 方向]——方向 1 涨（收≥开）、-1 跌，红涨绿跌回调着色。
    // 成交量进图例行 1（右对齐，左侧让位 DOM 读条），点击可开关副图；MACD 柱不进 legend。
    const volumeOn = (model.volume ?? []).some((v) => v !== null);
    if (volumeOn && model.volume) {
      const volumes = model.volume.map((v, i) =>
        v === null ? null : [i, v, model.ohlc[i][1] >= model.ohlc[i][0] ? 1 : -1],
      );
      built.push({
        name: '成交量',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: volumes,
        barWidth: '60%',
        itemStyle: {
          // 红涨绿跌（国内惯例，MACD 柱同款模式）：回调按三元组方向着色
          color: (params: CallbackDataParams) =>
            Array.isArray(params.value) && params.value[2] === -1 ? VOLUME_DOWN : VOLUME_UP,
        },
      });
    }

    // MACD 副图（震荡指标不放主图）：macd 存在时挂副图，副图三系列显式绑定轴；
    // 轴索引随布局动态迁移（§3.1 分配表）：有成交量时 MACD 迁至 (2,2)，否则 (1,1) 现状。
    const macdOn = Boolean(model.macd);
    const macdAxisIndex = volumeOn ? 2 : 1;
    if (macdOn && model.macd) {
      macdLegend.push('DIF', 'DEA');
      built.push(
        {
          name: 'MACD柱', type: 'bar', data: model.macd.hist, xAxisIndex: macdAxisIndex, yAxisIndex: macdAxisIndex,
          barWidth: '60%',
          itemStyle: {
            // 正红负绿（国内惯例）：回调按单值着色
            color: (params: CallbackDataParams) =>
              typeof params.value === 'number' && params.value >= 0 ? MACD_HIST_UP : MACD_HIST_DOWN,
          },
        },
        { name: 'DIF', type: 'line', data: model.macd.dif, xAxisIndex: macdAxisIndex, yAxisIndex: macdAxisIndex, symbol: 'none', lineStyle: { width: 1, color: initialTheme.neutral }, itemStyle: { color: initialTheme.neutral } },
        { name: 'DEA', type: 'line', data: model.macd.dea, xAxisIndex: macdAxisIndex, yAxisIndex: macdAxisIndex, symbol: 'none', lineStyle: { width: 1, color: MACD_DEA_COLOR }, itemStyle: { color: MACD_DEA_COLOR } },
      );
    }
    return { series: built, legendMain: mainLegend, legendMacd: macdLegend, hasVolume: volumeOn, hasMacd: macdOn };
  }, [model, initialTheme.neutral]);

  // ---- 画线（§3.6）----
  const hasDrawings = Boolean(onDrawingsChange && drawings);
  const [mode, setMode] = useState<Mode>('view');
  const [drawKind, setDrawKind] = useState<DrawKind>('trend');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draggingId, setDraggingId] = useState<string | null>(null);
  // 橡皮擦模式悬停命中的画线 id（逐像素 state：ChartCore memo 拦截，仅覆盖层重绘）
  const [eraserHoverId, setEraserHoverId] = useState<string | null>(null);
  const [pendingText, setPendingText] = useState<{ idx: number; price: number } | null>(null);
  const [textValue, setTextValue] = useState('');
  const textCancelledRef = useRef(false);
  const instanceRef = useRef<EChartsType | null>(null);
  // 渲染视口（grid 矩形 + 真实 y 轴 extent）：渲染后由 layout effect 读取存 state，
  // 覆盖层与交互换算全部走它（纯像素数学，不依赖 convertToPixel/轴 band API——实测
  // 类目轴 convertToPixel 对小数索引取整、convertFromPixel 不返回连续值）
  const [viewport, setViewport] = useState<{
    rect: { x: number; y: number; width: number; height: number };
    extent: RenderExtent;
    windowKey: string;
  } | null>(null);
  // onChartReady 只写 ref 不触发渲染——置位一次让 layout effect 重跑读视口
  // （echarts-for-react mount 先建临时实例再重建，可能多次 ready，tick 累加无副作用）
  const [, setChartTick] = useState(0);
  // 绘制/拖拽预览线段（SVG 覆盖层渲染，纯 state 声明式）
  const [previewLine, setPreviewLine] = useState<{ x1: number; y1: number; x2: number; y2: number } | null>(null);
  // 绘制起点在 mousedown 时即转数据坐标存储（非像素）：绘制中滚轮缩放/滑条改轴域后
  // 锚点仍钉在原数据位置不漂移；x = 小数类目索引（连续位置）——渲染精确落在按点像素
  const drawingRef = useRef<{ kind: Exclude<DrawKind, 'text'>; p1: { idx: number; price: number; x: number } } | null>(null);
  const dragRef = useRef<{
    id: string;
    hit: 'endpoint1' | 'endpoint2' | 'body';
    start: { idx: number; price: number };
  } | null>(null);

  // ---- 图例与读条（§3.7）----
  // legendSelected React 化（防 notMerge 重建重置选中）：legendselectchanged 读实例 selected
  // 回填 state（实测两个 legend 实例共享同一份全局 selected 映射，读任一即可合并），
  // option 构建时注入——各 legend 只注入自己 data 中的项。
  const [legendSelected, setLegendSelected] = useState<Record<string, boolean> | undefined>(undefined);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  // 图例 formatter 在 option 构建时闭包捕获 idx 会随 hover 过期——经 ref 直读最新悬浮索引，
  // hover 变化走图例 merge 刷新文本（不重建 option，保住 ChartCore memo 守卫）
  const hoverIdxRef = useRef<number | null>(null);
  hoverIdxRef.current = hoverIdx;

  // 可见窗口（类目索引）：窗口边缘日期最近类目吸附；锚点日期命中即用（口径分工见 §3.6.1）
  const windowIdx = useMemo(() => {
    if (visibleRange) {
      return {
        startIdx: windowEdgeIndex(model.xAxisData, visibleRange.start),
        endIdx: windowEdgeIndex(model.xAxisData, visibleRange.end),
      };
    }
    return { startIdx: 0, endIdx: model.xAxisData.length - 1 };
  }, [model.xAxisData, visibleRange?.start, visibleRange?.end]);
  const windowKey = `${windowIdx.startIdx}|${windowIdx.endIdx}`;

  // ---- 画线渲染（SVG 覆盖层，§3.6）----
  // 渲染后读真实 extent 与主图网格矩形 → viewport state（useLayoutEffect 同帧，免闪烁）。
  // 覆盖层渲染与交互换算全部走纯像素数学：类目轴 bar 带线性填满网格、y 轴 extent 线性
  // 映射网格高度——不依赖 convertToPixel/convertFromPixel（实测类目轴两者对小数索引
  // 取整、不返回连续值）与轴 band API。读数门控到实例就绪（echarts-for-react mount
  // 先建临时实例、finished 后 dispose 重建——临时实例上读 yAxis 得 undefined，重试）。
  const readExtent = (): RenderExtent | null => {
    const inst = instanceRef.current;
    if (!inst) return null;
    // getModel 在类型上为私有成员，先把实例断言成公开签名再读取（运行时为公开 API）。
    // 注意：getComponent 必须经 model 以方法方式调用（bind 保 this）——拆引用裸调在真实
    // 实例上是类方法，内部读 this._componentsMap 直接 TypeError（假实例箭头函数测不出来，
    // 2026-09-16 诊断用例实锤）
    const model = (
      inst as unknown as {
        getModel: () => { getComponent: (kind: string, idx: number) => unknown };
      }
    ).getModel();
    const getComponent = model.getComponent.bind(model);
    const axis = getComponent('yAxis', 0) as {
      axis?: { scale?: { getExtent?: () => number[] } };
    } | null;
    const ext = axis?.axis?.scale?.getExtent?.();
    if (ext && ext.length === 2 && Number.isFinite(ext[0]) && Number.isFinite(ext[1])) {
      return { min: ext[0], max: ext[1] };
    }
    // extent 读取失败兜底 = convertToPixel 反算主图轴范围（§3.6.2）
    const grid = getComponent('grid', 0) as {
      coordinateSystem?: { getRect?: () => { x: number; y: number; width: number; height: number } };
    } | null;
    const rect = grid?.coordinateSystem?.getRect?.();
    if (!rect) return null;
    const top = inst.convertFromPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [rect.x, rect.y]);
    const bottom = inst.convertFromPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [rect.x, rect.y + rect.height]);
    if (
      !Array.isArray(top) || !Array.isArray(bottom) ||
      typeof top[1] !== 'number' || typeof bottom[1] !== 'number'
    ) {
      return null;
    }
    return { min: Math.min(top[1], bottom[1]), max: Math.max(top[1], bottom[1]) };
  };
  const mainGridRect = (): { x: number; y: number; width: number; height: number } | null => {
    const inst = instanceRef.current;
    if (!inst) return null;
    try {
      // getModel 在类型上为私有成员，先把实例断言成公开签名再读取（运行时为公开 API）
      const grid = (
        inst as unknown as {
          getModel: () => { getComponent: (kind: string, idx: number) => unknown };
        }
      ).getModel().getComponent('grid', 0) as {
        coordinateSystem?: { getRect?: () => { x: number; y: number; width: number; height: number } };
      } | null;
      return grid?.coordinateSystem?.getRect?.() ?? null;
    } catch {
      return null;
    }
  };
  const inMainGrid = (x: number, y: number): boolean => {
    const rect = mainGridRect();
    if (!rect) return false;
    return x >= rect.x && x <= rect.x + rect.width && y >= rect.y && y <= rect.y + rect.height;
  };
  useLayoutEffect(() => {
    const inst = instanceRef.current;
    if (!inst) return;
    try {
      const rect = mainGridRect();
      const extent = readExtent();
      if (!rect || !extent || rect.width <= 0 || rect.height <= 0 || extent.max <= extent.min) return;
      setViewport((prev) =>
        prev &&
        prev.windowKey === windowKey &&
        prev.extent.min === extent.min &&
        prev.extent.max === extent.max &&
        prev.rect.x === rect.x &&
        prev.rect.y === rect.y &&
        prev.rect.width === rect.width &&
        prev.rect.height === rect.height
          ? prev // 视口未变（hover 等逐像素重渲染）→ 不触发覆盖层重绘
          : { rect, extent, windowKey },
      );
    } catch {
      // 实例未就绪/读轴异常：待下一次渲染重试
    }
  });

  // ---- 画线交互（zr 鼠标事件，仅 draw/edit 模式挂接；几何换算全部经 viewport 纯像素数学）----
  const dataOf = (px: number, py: number): { idx: number; price: number; x: number } | null => {
    const vp = viewport;
    if (!vp) return null;
    const band = vp.rect.width / (windowIdx.endIdx - windowIdx.startIdx + 1);
    // 小数类目索引 = 窗口左缘 − 0.5 + 像素偏移/带宽（按点像素直算，不经 convertFromPixel——
    // 实测其只返回所在 bar 整数索引，小数位置会丢）
    const fx = windowIdx.startIdx - 0.5 + (px - vp.rect.x) / band;
    const idx = Math.round(fx);
    if (idx < 0 || idx >= model.xAxisData.length) return null;
    const price = vp.extent.max - ((py - vp.rect.y) / vp.rect.height) * (vp.extent.max - vp.extent.min);
    return { idx, price, x: fx };
  };
  const toData = (px: number, py: number): { idx: number; price: number } | null => {
    const d = dataOf(px, py);
    return d ? { idx: d.idx, price: d.price } : null;
  };
  // 数据坐标（小数类目索引 + 价格）→ 容器像素（与 dataOf 严格互逆）
  const pxOf = (fx: number, price: number): { x: number; y: number } | null => {
    const vp = viewport;
    if (!vp) return null;
    const band = vp.rect.width / (windowIdx.endIdx - windowIdx.startIdx + 1);
    return {
      x: vp.rect.x + (fx - windowIdx.startIdx + 0.5) * band,
      y: vp.rect.y + ((vp.extent.max - price) / (vp.extent.max - vp.extent.min)) * vp.rect.height,
    };
  };
  const round2 = (v: number) => Math.round(v * 100) / 100;

  const showPreview = (x1: number, y1: number, x2: number, y2: number) => {
    setPreviewLine({ x1, y1, x2, y2 });
  };
  const clearPreview = () => {
    setPreviewLine(null);
  };
  const cancelInteraction = () => {
    drawingRef.current = null;
    dragRef.current = null;
    setDraggingId(null);
    clearPreview();
  };

  // 上次注册的三个 handler（按引用精确 off 用；裸 zr.off(event) 会 delete 整张事件表——
  // zr 与 ECharts 内部共用同一 Handler Eventful，axisPointer/tooltip 的 mousemove 全局监听
  // 与 inside dataZoom 的 roam 监听都挂在上面，裸 off 会永久删掉它们）
  const zrHandlersRef = useRef<{
    mousedown: ((e: { offsetX: number; offsetY: number }) => void) | null;
    mousemove: ((e: { offsetX: number; offsetY: number }) => void) | null;
    mouseup: ((e: { offsetX: number; offsetY: number }) => void) | null;
  }>({ mousedown: null, mousemove: null, mouseup: null });

  useEffect(() => {
    const inst = instanceRef.current;
    if (!inst) return;
    const zr = inst.getZr();
    // zrender 的 on 仅按函数引用去重（Eventful.on 里 === handler），新闭包会逐渲染累积——
    // 必须按引用精确 off 上一次注册的 handler（不得裸 off，见 zrHandlersRef 注释）
    const prev = zrHandlersRef.current;
    if (prev.mousedown) zr.off('mousedown', prev.mousedown);
    if (prev.mousemove) zr.off('mousemove', prev.mousemove);
    if (prev.mouseup) zr.off('mouseup', prev.mouseup);
    zrHandlersRef.current = { mousedown: null, mousemove: null, mouseup: null };
    if (mode === 'view') return;
    // 命中检测（像素空间：端点 8px 优先、线身 6px、text 包围盒 6px）——edit 拖拽、橡皮擦
    // 悬停/删除共用。线段几何与渲染同源（renderedSegment）：射线外推段可命中、截断后
    // 不可见的段不可命中；拖拽中按 id 过滤隐藏的线不在命中集合。
    const hitTestDrawings = (mouse: { x: number; y: number }): { id: string; part: 'endpoint1' | 'endpoint2' | 'body' } | null => {
      for (const d of drawings ?? []) {
        if (d.id === draggingId) continue;
        if (d.kind === 'text') {
          const idx = anchorIndex(model.xAxisData, d.pos.date);
          if (idx < 0) continue;
          const pix = pxOf(idx, d.pos.price);
          if (!pix) continue;
          const rect = { x1: pix.x - 2, y1: pix.y - 12, x2: pix.x + d.text.length * 6.6 + 2, y2: pix.y + 2 };
          if (hitTestRect(mouse, rect)) return { id: d.id, part: 'body' };
          continue;
        }
        const seg = viewport
          ? renderedSegment(d, model.xAxisData, windowIdx, viewport.extent)
          : null;
        if (!seg) continue;
        const a = pxOf(seg.i1, seg.p1);
        const b = pxOf(seg.i2, seg.p2);
        if (!a || !b) continue;
        const part = hitTestSegment(mouse, a, b);
        if (part) return { id: d.id, part };
      }
      return null;
    };
    const onMouseDown = (event: { offsetX: number; offsetY: number }) => {
      if (!inMainGrid(event.offsetX, event.offsetY)) return; // 副图/slider 区域忽略
      const hit = hitTestDrawings({ x: event.offsetX, y: event.offsetY });
      if (mode === 'draw') {
        // 命中已有画线 → 选中并直接进入拖拽（与 edit 同路径）：画错了不用重画，拖一下即到位；
        // 未命中 → 开新线。命中优先于开新线（TradingView 同款语义），text 工具同样命中优先。
        if (hit) {
          setSelectedId(hit.id);
          setDraggingId(hit.id);
          const pos = toData(event.offsetX, event.offsetY);
          if (pos) dragRef.current = { id: hit.id, hit: hit.part, start: pos };
          return;
        }
        if (drawKind === 'text') {
          const pos = toData(event.offsetX, event.offsetY);
          if (pos) {
            setPendingText(pos);
            setTextValue('');
            textCancelledRef.current = false;
          }
          return;
        }
        const pos = dataOf(event.offsetX, event.offsetY);
        if (pos) drawingRef.current = { kind: drawKind, p1: pos };
        return;
      }
      if (mode === 'eraser') {
        // 橡皮擦：单击命中的画线立即删除（悬停已高亮，所见即所删）；空处点击无操作
        if (hit) {
          onDrawingsChange?.((drawings ?? []).filter((d) => d.id !== hit.id));
          setSelectedId(null);
          setEraserHoverId(null);
        }
        return;
      }
      // edit：命中 → 选中并进入拖拽
      if (hit) {
        setSelectedId(hit.id);
        setDraggingId(hit.id);
        const pos = toData(event.offsetX, event.offsetY);
        if (pos) dragRef.current = { id: hit.id, hit: hit.part, start: pos };
      } else {
        setSelectedId(null);
      }
    };
    const onMouseMove = (event: { offsetX: number; offsetY: number }) => {
      if (drawingRef.current) {
        // 绘制预览：起点 = mousedown 时锚点的精确像素（小数 x 直算，提交后渲染即此点——
        // 起点所见即所得、钉死不飘）；终点跟随鼠标原始像素连续移动（流畅跟手，提交同一几何）。
        // trend 预览 = 锚点→鼠标；ray 预览 = 完整射线（锚点→主图右缘、斜率跟手，与提交渲染同语义）；
        // hline 预览 = 锚点价格全窗宽水平线（提交即此几何，静态不跟手）。
        const dr = drawingRef.current;
        const a = pxOf(dr.p1.x, dr.p1.price);
        if (!a) {
          clearPreview();
          return;
        }
        if (dr.kind === 'hline') {
          const rect = mainGridRect();
          if (rect) {
            showPreview(rect.x, a.y, rect.x + rect.width, a.y);
            return;
          }
          clearPreview();
          return;
        }
        if (dr.kind === 'ray' && event.offsetX !== a.x) {
          const slope = (event.offsetY - a.y) / (event.offsetX - a.x);
          if (Number.isFinite(slope)) {
            const rect = mainGridRect();
            if (rect) {
              const edgeX = rect.x + rect.width;
              // 与主图右缘交点；y 截进 grid（提交后线段按 extent 求交，预览仅像素级近似）
              const yEdge = Math.max(rect.y, Math.min(rect.y + rect.height, a.y + slope * (edgeX - a.x)));
              showPreview(a.x, a.y, edgeX, yEdge);
              return;
            }
          }
        }
        showPreview(a.x, a.y, event.offsetX, event.offsetY);
        return;
      }
      if (mode === 'eraser') {
        // 橡皮擦悬停高亮：mousemove 逐像素命中检测（≤100 条画线，开销可忽略），
        // 同值 setState React 直接 bail——覆盖层重绘、ChartCore memo 拦截图表重建
        const hit = hitTestDrawings({ x: event.offsetX, y: event.offsetY });
        setEraserHoverId(hit?.id ?? null);
        return;
      }
      const dr = dragRef.current;
      if (dr && draggingId && viewport) {
        // 拖拽预览 = 假设提交后的渲染线段（与渲染几何同源）：原线已按 id 过滤隐藏，
        // 预览画平移/变形后的真实位置；校验不通过（如同日）则预览清除
        const target = (drawings ?? []).find((d) => d.id === dr.id);
        const end = toData(event.offsetX, event.offsetY);
        if (target && end) {
          const updated = applyDrag(target, dr.hit, dr.start, end, model.xAxisData);
          if (updated) {
            const seg = renderedSegment(updated, model.xAxisData, windowIdx, viewport.extent);
            const a = seg ? pxOf(seg.i1, seg.p1) : null;
            const b = seg ? pxOf(seg.i2, seg.p2) : null;
            if (a && b) {
              showPreview(a.x, a.y, b.x, b.y);
              return;
            }
          }
        }
        clearPreview();
      }
    };
    const onMouseUp = (event: { offsetX: number; offsetY: number }) => {
      if (drawingRef.current) {
        const start = drawingRef.current;
        drawingRef.current = null;
        clearPreview();
        if (!inMainGrid(event.offsetX, event.offsetY)) return; // mouseup 出 grid → 取消
        const end = dataOf(event.offsetX, event.offsetY);
        if (!end) return;
        const startData = start.p1; // mousedown 时已转数据坐标（含小数 x）
        const date = (i: number) => model.xAxisData[i];
        if (start.kind === 'hline') {
          onDrawingsChange?.([...(drawings ?? []), {
            id: newId(), kind: 'hline',
            p1: { date: date(startData.idx), price: round2(startData.price) },
          }]);
          return;
        }
        // trend/ray：两锚点同日拒绝提交（数据模型按日期锚点，同 bar 两锚点不可区分）
        if (startData.idx === end.idx) return;
        onDrawingsChange?.([...(drawings ?? []), {
          id: newId(), kind: start.kind,
          // x 存小数类目索引：渲染精确落在 mousedown/mouseup 的按点像素（§3.6）
          p1: { date: date(startData.idx), price: round2(startData.price), x: startData.x },
          p2: { date: date(end.idx), price: round2(end.price), x: end.x },
        }]);
        return;
      }
      if (dragRef.current) {
        const dr = dragRef.current;
        dragRef.current = null;
        clearPreview();
        const end = toData(event.offsetX, event.offsetY);
        if (!end) {
          setDraggingId(null);
          return;
        }
        const target = (drawings ?? []).find((d) => d.id === dr.id);
        if (target) {
          const updated = applyDrag(target, dr.hit, dr.start, end, model.xAxisData);
          if (updated) {
            onDrawingsChange?.((drawings ?? []).map((d) => (d.id === updated.id ? updated : d)));
          }
          // 校验不通过（如拖到两锚点同日）→ 回退拖拽前锚点（不更新）
        }
        setDraggingId(null);
      }
    };
    zrHandlersRef.current = { mousedown: onMouseDown, mousemove: onMouseMove, mouseup: onMouseUp };
    zr.on('mousedown', onMouseDown);
    zr.on('mousemove', onMouseMove);
    zr.on('mouseup', onMouseUp);
    // 每次重渲染按引用替换 handler（off 上一份再注册新一份，闭包取最新 state/mode）；
    // 进行中的交互状态不可在重渲染时清除（否则父组件重渲染会打断拖拽）
  });

  // 键盘事件落点：Esc/Delete 挂 document 级（图表容器 div 默认不可聚焦），组件卸载时清理
  useEffect(() => {
    if (mode === 'view' && !pendingText) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (pendingText) {
          textCancelledRef.current = true; // 取消优先于 blur（紧随的 blur 检查标记不再提交）
          setPendingText(null);
          return; // 只取消标注，不退出画线模式（input 内 Esc 冒泡到此）
        }
        cancelInteraction();
        if (mode !== 'view') setMode('view');
      }
      if (event.key === 'Delete' && mode === 'edit' && selectedId) {
        onDrawingsChange?.((drawings ?? []).filter((d) => d.id !== selectedId));
        setSelectedId(null);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  });

  const commitText = () => {
    const pos = pendingText;
    if (!pos || textCancelledRef.current) {
      setPendingText(null);
      return;
    }
    const trimmed = textValue.trim();
    setPendingText(null);
    if (!trimmed) return; // 空 text 拒绝提交（防不可见、不可命中的幽灵条目）
    onDrawingsChange?.([...(drawings ?? []), {
      id: newId(),
      kind: 'text',
      pos: { date: model.xAxisData[pos.idx], price: round2(pos.price) },
      text: trimmed,
    }]);
  };

  // ---- 图例/布局/dataZoom（§3.1/§3.3/§3.7）----
  // 多 grid 布局（H=460 验算，2026-09-16 间距加宽）：主图→成交量→MACD；grid.bottom 自底部计量、
  // grid.top 自顶部计量；相邻间距 16.1px/16.1px（3.5%）+ slider 余量 18px，主图 ≥170px 门槛：
  // 56 图例 + 178.6 主图 + 16.1 间距 + 89.7 成交量 + 16.1 间距 + 69.5 MACD + 34（x 标签 + slider）= 460。
  // top：三行图例 56、双行 40、单行 24。
  const panLocked = mode !== 'view'; // draw/edit 只锁鼠标拖拽平移（与绘制/拖拽的 mousedown 互斥）；滚轮缩放不锁

  // 事件出口（§3.3 datazoom 上报 + §3.7 读条/图例）：引用保持稳定（useMemo），
  // 外层 hover 变化不重建 → ChartCore memo 拦截、无 setOption 风暴。
  const lastReportedRef = useRef<string | null>(null);
  const onEvents = useMemo(() => {
    const events: Record<string, Function> = {};
    if (onDataZoom) {
      // echarts-for-react 3.0.6 handler 第二参数即 ECharts 实例（lib/core.js:153 func(param, instance)）
      events.datazoom = (_params: unknown, instance: EChartsType) => {
        const dz = (instance.getOption().dataZoom as DataZoomComponentOption[]) ?? [];
        const zoom = dz.find((d) => d.type === 'inside') ?? dz[0];
        const toDate = (v: unknown) => {
          if (typeof v !== 'number') return undefined;
          const idx = Math.round(v);
          return model.xAxisData[idx];
        };
        const start = toDate(zoom?.startValue);
        const end = toDate(zoom?.endValue);
        if (start && end) {
          const key = `${start}|${end}`;
          if (key === lastReportedRef.current) return;
          lastReportedRef.current = key;
          onDataZoom({ start, end });
        }
      };
    }
    // 图例 label+value 数据源：updateAxisPointer 的 axesInfo[0].value = 类目整数索引（实测
    // Ordinal.scale 已取整、无需 round）；离场守卫——实测鼠标移出时会再派发一次
    // axesInfo: []，空则回落 null（未悬浮分支显示窗口末根）。
    events.updateAxisPointer = (params: unknown) => {
      const axesInfo = (params as { axesInfo?: Array<{ value?: unknown }> } | undefined)?.axesInfo;
      const info = axesInfo?.[0];
      const next = info && typeof info.value === 'number' ? Math.round(info.value) : null;
      hoverIdxRef.current = next;
      setHoverIdx(next);
    };
    // 图例选中回填（防 notMerge 重建重置选中）；图例项全部为真实系列
    // （开/高/低/收/涨幅走 DOM 读条、非图例项，无点击态）
    events.legendselectchanged = (_params: unknown, instance: EChartsType) => {
      const legendOption = (instance.getOption().legend as Array<{ selected?: Record<string, boolean> }>) ?? [];
      const merged: Record<string, boolean> = {};
      for (const l of legendOption) Object.assign(merged, l.selected ?? {});
      setLegendSelected(merged);
    };
    return events;
  }, [onDataZoom, model.xAxisData]);

  const onChartReady = useCallback((instance: EChartsType) => {
    instanceRef.current = instance;
    setChartTick((t) => t + 1); // 触发一次重渲染 → layout effect 读视口 → 覆盖层出图
  }, []);

  // 未悬浮 → 可见窗口最后一根（平移/缩放后数据集末尾可能在屏幕外，取窗口末根才有意义）
  const fallbackIdx = visibleRange ? windowIdx.endIdx : model.xAxisData.length - 1;

  // hover 变化走图例 merge 刷新 label+value（不重建 option，保住 ChartCore memo 守卫）；
  // legendSelected 等图例输入变化时同源重建，保证 merge 与 option 的图例一致
  useEffect(() => {
    const inst = instanceRef.current;
    if (!inst) return;
    inst.setOption(
      {
        legend: buildLegendOptions({
          neutralColor: chartTheme.neutral,
          model,
          hasVolume,
          legendMain,
          legendMacd,
          idx: hoverIdx ?? fallbackIdx,
          legendSelected,
        }),
      },
      { notMerge: false },
    );
  }, [chartTheme.neutral, hoverIdx, fallbackIdx, model, hasVolume, legendMain, legendMacd, legendSelected]);

  // option 全量构建收敛进 useMemo：grid/xAxis/yAxis/dataZoom/legend 的对象引用必须稳定——
  // 外层 hover 等逐像素 state 变化时不重建 → ChartCore memo 拦截、无 setOption 风暴。
  const option = useMemo(() => {
    // 图例行数（行 1 = DOM 读条恒在，画布顶带 top 0..18 让位）：3 行 56 / 2 行 40 /
    // 1 行 24（行 1 恒占位，无"无图例"布局）
    const legendRows = 1 + (legendMain.length > 0 ? 1 : 0) + (legendMacd.length > 0 ? 1 : 0);
    const mainGrid = {
      left: 48,
      right: 16,
      top: legendRows >= 3 ? 56 : legendRows === 2 ? 40 : legendRows === 1 ? 24 : 30,
      bottom: hasVolume && hasMacd ? '49%' : hasVolume ? '34%' : hasMacd ? '35%' : 30,
    };
    const volumeGrid = hasVolume
      ? { left: 48, right: 16, top: hasMacd ? '54.5%' : '72%', bottom: hasMacd ? '26%' : 34 }
      : undefined;
    const macdGrid = hasMacd
      ? { left: 48, right: 16, top: hasVolume ? '77.5%' : '70%', bottom: 34 }
      : undefined;
    const grids = [mainGrid, volumeGrid, macdGrid].filter((g): g is NonNullable<typeof g> => g !== undefined);
    const grid = grids.length > 1 ? grids : mainGrid;

    // 轴数组按 grid 顺序构造（每个 grid 一个轴对象）：主图 0、成交量 1、MACD 2。
    // 日期标签只在最底副图显示（主图与成交量隐藏 axisLabel）。
    const xAxisBase = { type: 'category' as const, data: model.xAxisData, axisLabel: { color: initialTheme.text }, axisLine: { lineStyle: { color: initialTheme.grid } } };
    const yAxisBase = { scale: true, axisLabel: { color: initialTheme.text }, splitLine: { lineStyle: { color: initialTheme.grid } } };
    // 成交量 y 轴独立量纲：可见纵轴（标签量级缩写 万/亿手，splitNumber 2；
    // 与价格轴共用暗色轴样式，不与价格轴共享量纲）
    const volumeYAxis = {
      ...yAxisBase,
      gridIndex: 1,
      splitNumber: 2,
      axisLabel: { color: initialTheme.text, formatter: (value: number) => formatVolume(value) },
    };
    const xAxis = grids.length > 1
      ? grids.map((_g, i) => ({
          ...xAxisBase,
          gridIndex: i,
          axisLabel: i === grids.length - 1 ? { color: initialTheme.text } : { show: false, color: initialTheme.text },
        }))
      : xAxisBase;
    // 成交量轴只挂在它实际存在的 grid 上（hasVolume=false 时索引 1 是 MACD grid——
    // 写死 i===1 会把 MACD 副图套上成交量轴样式，刻度/分割线全隐）；
    // MACD 副图矮（69.5px）：splitNumber 3 防默认 5 分度刻度标签逐刻度挤压
    const volumeAxisIndex = hasVolume ? 1 : -1;
    const macdAxisIndex = hasMacd ? (hasVolume ? 2 : 1) : -1;
    // 参考价横线（止损/止盈）：主图 y 轴以函数形式 min/max 在自动 extent 基础上
    // 再扩展——scale:true 下回调仍生效（echarts 6 实测），保证参考价恒在轴域内；
    // 未传不注入，保持既有 extent 行为
    const refMin = referenceLines && referenceLines.length > 0 ? Math.min(...referenceLines.map((r) => r.price)) : null;
    const refMax = referenceLines && referenceLines.length > 0 ? Math.max(...referenceLines.map((r) => r.price)) : null;
    const mainYAxis = {
      ...yAxisBase,
      ...(refMin !== null ? { min: (value: { min: number }) => Math.min(value.min, refMin) } : {}),
      ...(refMax !== null ? { max: (value: { max: number }) => Math.max(value.max, refMax) } : {}),
    };
    const yAxis = grids.length > 1
      ? grids.map((_g, i) => (
          i === volumeAxisIndex
            ? volumeYAxis
            : i === macdAxisIndex
              ? { ...yAxisBase, gridIndex: i, splitNumber: 3 }
              : { ...mainYAxis, gridIndex: i }
        ))
      : mainYAxis;

    // dataZoom 覆盖全部副图（[0..gridCount-1]）；单 grid 不设 xAxisIndex（现状行为）。
    // 日期锚定（§3.3）：visibleRange 传入时用 startValue/endValue（category 轴字符串吸附最近类目），
    // 数据扩展（前插更早 bars）时可见日期不变 → 窗口不跳；未传时回落 start/end 百分比。
    // **两种锚互斥，禁止混写**（实测混写时百分比优先、日期锚被忽略，窗口锁死全量且
    // 每次重建弹回——放大永远失效，缩小仅因 §3.4 扩展让全量窗口变大看似可用）。
    // draw/edit 模式只禁用鼠标拖拽平移（moveOnMouseMove 与绘制/拖拽的 mousedown 互斥）；
    // 滚轮缩放保留——wheel 不走 mousedown/mousemove，与 zr 绘制事件不冲突；
    // 绘制起点已按数据坐标存储，绘制中缩放锚点不漂移。slider 仍可拖动照发 datazoom。
    const zoomAxes = grids.length > 1 ? grids.map((_g, i) => i) : undefined;
    const zoomWindow = visibleRange
      ? { startValue: visibleRange.start, endValue: visibleRange.end }
      : { start: 0, end: 100 };
    const dataZoom = [
      { type: 'inside', xAxisIndex: zoomAxes, zoomOnMouseWheel: true, moveOnMouseMove: !panLocked, moveOnMouseWheel: false, ...zoomWindow },
      {
        type: 'slider', xAxisIndex: zoomAxes, height: 14, bottom: 2, showDetail: false,
        borderColor: 'transparent', backgroundColor: 'rgba(35, 42, 51, 0.6)',
        fillerColor: 'rgba(148, 163, 184, 0.25)', handleStyle: { color: initialTheme.text },
        textStyle: { color: initialTheme.text, fontSize: 10 },
        ...zoomWindow,
      },
    ];

    // 图例 = label + value（buildLegendOptions，行 1 量（右对齐）、行 2 MA+BOLL、行 3 DIF/DEA；
    // 开高低收/涨幅不在图例——见 buildReadoutItems 的 ECharts 6 图例限制注释）；
    // 纯文字（icon none）、逐项系列色（实测默认文字色 #54555a 不继承系列色）、
    // 点击隐藏变灰（inactiveColor）、itemWidth 0 + 紧凑 gap
    const legends = buildLegendOptions({
          neutralColor: initialTheme.neutral,
      model,
      hasVolume,
      legendMain,
      legendMacd,
      idx: hoverIdxRef.current ?? fallbackIdx,
      legendSelected,
    });

    return {
      backgroundColor: 'transparent',
      animation: false, // 数据扩展触发的 setOption 重建不闪、不产生动画重绘
      grid,
      legend: legends.length > 0 ? legends : undefined,
      dataZoom,
      // axisPointer.link 必须放顶层（写在 xAxis 对象内会被忽略）：十字光标跨图联动
      axisPointer: grids.length > 1 ? { link: [{ xAxisIndex: 'all' }] } : undefined,
      xAxis,
      yAxis,
      // tooltip 内容已移除（§3.7 读条替代）：禁用 show:false 写法——实测会在模型层短路
      // trigger，axisPointer 与 updateAxisPointer.axesInfo 一并失效（§3.6.2 实测结论 5）
      tooltip: { showContent: false, trigger: 'axis' },
      series,
    };
  }, [
    initialTheme, hasVolume, hasMacd, model.xAxisData, visibleRange?.start, visibleRange?.end,
    panLocked, legendMain, legendMacd, legendSelected, series, fallbackIdx, referenceLines,
  ]);

  // Theme changes merge presentation only: never replay dataZoom or recreate the chart.
  // Also runs after an ordinary full option update so current theme stays authoritative.
  useEffect(() => {
    const inst = instanceRef.current;
    if (!inst) return;
    const axisCount = 1 + Number(hasVolume) + Number(hasMacd);
    inst.setOption({
      xAxis: Array.from({ length: axisCount }, () => ({ axisLabel: { color: chartTheme.text }, axisLine: { lineStyle: { color: chartTheme.grid } } })),
      yAxis: Array.from({ length: axisCount }, () => ({ axisLabel: { color: chartTheme.text }, splitLine: { lineStyle: { color: chartTheme.grid } } })),
      ...(hasMacd ? { series: [{ name: 'DIF', lineStyle: { color: chartTheme.neutral }, itemStyle: { color: chartTheme.neutral } }] } : {}),
      legend: buildLegendOptions({ model, hasVolume, legendMain, legendMacd, idx: hoverIdxRef.current ?? fallbackIdx, legendSelected, neutralColor: chartTheme.neutral }),
    }, { notMerge: false });
  }, [chartTheme, option]);

  // 已提交画线的覆盖层元素（SVG，§3.6）：线段走 renderedSegment（与编辑命中检测同源几何，
  // §3.6.1），像素经 pxOf 纯数学换算——小数锚点精确落在按点像素；渲染即覆盖层，
  // 不依赖 zr 生命周期（临时实例/重建时序/图层序一律无关）
  const overlay = useMemo(() => {
    const lines: Array<{ id: string; x1: number; y1: number; x2: number; y2: number; width: number; color: string }> = [];
    const texts: Array<{ id: string; x: number; y: number; content: string; color: string }> = [];
    if (!hasDrawings || !viewport) return { lines, texts };
    const extent = viewport.extent;
    for (const d of drawings ?? []) {
      if (draggingId === d.id) continue; // 拖拽中的线按 id 过滤隐藏，以拖拽预览呈现
      // 橡皮擦悬停：高亮色 + 宽 3（与选中态同宽、颜色区分）
      const hovered = mode === 'eraser' && eraserHoverId === d.id;
      const color = hovered ? ERASER_HOVER_COLOR : DRAWING_COLOR;
      if (d.kind === 'text') {
        const idx = anchorIndex(model.xAxisData, d.pos.date);
        if (idx < 0) continue; // 锚点未命中 → 跳过渲染不删数据（§3.6.1）
        // 锚点价格超出 extent → 不渲染（不裁剪——点无法裁剪，与线段求交口径不同）
        if (d.pos.price < extent.min - EXTENT_EPS || d.pos.price > extent.max + EXTENT_EPS) continue;
        const pix = pxOf(idx, d.pos.price);
        if (!pix) continue;
        texts.push({ id: d.id, x: pix.x, y: pix.y - 12, content: d.text, color });
        continue;
      }
      const seg = renderedSegment(d, model.xAxisData, windowIdx, extent);
      if (!seg) continue;
      const a = pxOf(seg.i1, seg.p1);
      const b = pxOf(seg.i2, seg.p2);
      if (!a || !b) continue;
      lines.push({ id: d.id, x1: a.x, y1: a.y, x2: b.x, y2: b.y, width: selectedId === d.id || hovered ? 3 : 1.5, color });
      if (d.kind === 'hline') {
        // hline 价格标签：线左端上方
        texts.push({ id: `${d.id}-label`, x: a.x, y: a.y - 4, content: d.label ?? d.p1.price.toFixed(2), color });
      }
    }
    return { lines, texts };
  }, [hasDrawings, viewport, drawings, model.xAxisData, windowIdx, selectedId, draggingId, mode, eraserHoverId]);

  // 参考价横线覆盖层（虚线，数据驱动、非画线工具）：与画线同几何——pxOf 纯像素换算、
  // 真实 extent 判定；extent 已由 option 主图 y 轴 min/max 扩展保证包含参考价
  const refOverlay = useMemo(() => {
    const lines: Array<{ id: string; x1: number; y1: number; x2: number; y2: number; color: string }> = [];
    const texts: Array<{ id: string; x: number; y: number; content: string; color: string }> = [];
    if (!viewport || !referenceLines || referenceLines.length === 0) return { lines, texts };
    for (const r of referenceLines) {
      if (!Number.isFinite(r.price)) continue;
      if (r.price < viewport.extent.min - EXTENT_EPS || r.price > viewport.extent.max + EXTENT_EPS) continue;
      const a = pxOf(windowIdx.startIdx - 0.5, r.price);
      const b = pxOf(windowIdx.endIdx + 0.5, r.price);
      if (!a || !b) continue;
      const color = r.color ?? DRAWING_COLOR;
      const key = `ref-${r.price}`;
      lines.push({ id: `${key}-line`, x1: a.x, y1: a.y, x2: b.x, y2: b.y, color });
      texts.push({ id: `${key}-label`, x: a.x, y: a.y - 4, content: r.label ?? r.price.toFixed(2), color });
    }
    return { lines, texts };
  }, [viewport, referenceLines, windowIdx]);

  // 行 1 读条（DOM 覆盖层，见 buildReadoutItems 注释）：悬浮跟随，未悬浮取可见窗口末根
  const readoutItems = buildReadoutItems(model, hoverIdx ?? fallbackIdx);

  return (
    <div data-testid="candlestick-chart">
      {onDrawingsChange && (
        <div className="mb-1 flex flex-wrap items-center gap-1">
          <button
            type="button"
            className="rounded border border-[var(--color-fg-muted)] px-1.5 py-0.5 text-xs"
            style={mode === 'draw' ? { color: DRAWING_COLOR } : undefined}
            onClick={() => setMode(mode === 'draw' ? 'view' : 'draw')}
          >
            画线
          </button>
          <button
            type="button"
            className="rounded border border-[var(--color-fg-muted)] px-1.5 py-0.5 text-xs"
            style={mode === 'edit' ? { color: DRAWING_COLOR } : undefined}
            onClick={() => setMode(mode === 'edit' ? 'view' : 'edit')}
          >
            编辑
          </button>
          <button
            type="button"
            className="rounded border border-[var(--color-fg-muted)] px-1.5 py-0.5 text-xs"
            style={mode === 'eraser' ? { color: DRAWING_COLOR } : undefined}
            onClick={() => setMode(mode === 'eraser' ? 'view' : 'eraser')}
          >
            橡皮擦
          </button>
          <button
            type="button"
            className="rounded border border-[var(--color-fg-muted)] px-1.5 py-0.5 text-xs"
            onClick={() => onDrawingsChange([])}
          >
            清空
          </button>
          {mode === 'draw' && (
            <>
              {(Object.keys(DRAW_KIND_LABELS) as DrawKind[]).map((kind) => (
                <button
                  key={kind}
                  type="button"
                  className="rounded border border-[var(--color-fg-muted)] px-1.5 py-0.5 text-xs"
                  style={kind === drawKind ? { color: DRAWING_COLOR } : undefined}
                  onClick={() => setDrawKind(kind)}
                >
                  {DRAW_KIND_LABELS[kind]}
                </button>
              ))}
              <button
                type="button"
                className="rounded border border-[var(--color-fg-muted)] px-1.5 py-0.5 text-xs"
                onClick={() => setMode('view')}
              >
                完成
              </button>
              <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                按住已有线拖动调整 · Esc 退出
              </span>
            </>
          )}
          {mode === 'edit' && (
            <>
              <button
                type="button"
                disabled={!selectedId}
                className="rounded border border-[var(--color-fg-muted)] px-1.5 py-0.5 text-xs disabled:opacity-40"
                onClick={() => {
                  if (selectedId) onDrawingsChange((drawings ?? []).filter((d) => d.id !== selectedId));
                  setSelectedId(null);
                }}
              >
                删除
              </button>
              <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
                Esc 退出
              </span>
            </>
          )}
          {mode === 'eraser' && (
            <span className="text-xs" style={{ color: 'var(--color-fg-muted)' }}>
              点击线删除 · Esc 退出
            </span>
          )}
          {pendingText && (
            <input
              autoFocus
              className="rounded border border-[var(--color-fg-muted)] px-1.5 py-0.5 text-xs"
              value={textValue}
              onChange={(event) => setTextValue(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') commitText();
                if (event.key === 'Escape') {
                  textCancelledRef.current = true;
                  setPendingText(null);
                }
              }}
              onBlur={commitText}
            />
          )}
        </div>
      )}
      <div style={{ position: 'relative', cursor: mode === 'eraser' ? 'pointer' : undefined }}>
        <ChartCore option={option} onEvents={onEvents} onChartReady={onChartReady} height={height} />
        {readoutItems.length > 0 && (
          // 行 1 数值读条（开/高/低/收/涨幅）：占据画布图例行 1 的顶带（top 0..18，
          // 与图例行 2 top 18 错开）；pointerEvents none——滚轮/悬浮/点击穿透给画布；
          // 有量时右侧 maxWidth 让位于右对齐的「量」图例项（无量时行 1 无图例、全宽）
          <div
            data-testid="chart-readout"
            style={{
              position: 'absolute',
              top: 0,
              left: 0,
              zIndex: 2,
              display: 'flex',
              columnGap: 6,
              maxWidth: hasVolume ? 'calc(100% - 84px)' : '100%',
              overflow: 'hidden',
              whiteSpace: 'nowrap',
              pointerEvents: 'none',
              fontSize: 10.5,
              lineHeight: '14px',
            }}
          >
            {readoutItems.map((item) => (
              <span key={item.label} style={{ color: item.color }}>
                {item.text === null ? item.label : `${item.label} ${item.text}`}
              </span>
            ))}
          </div>
        )}
        {(hasDrawings || (referenceLines && referenceLines.length > 0)) && viewport && (
          // 画线/参考价覆盖层：pointerEvents none——滚轮/悬浮/点击全部穿透给 ECharts 画布
          <svg
            data-testid="drawing-overlay"
            style={{
              position: 'absolute',
              inset: 0,
              width: '100%',
              height: '100%',
              pointerEvents: 'none',
              zIndex: 1,
              overflow: 'visible',
            }}
          >
            {overlay.lines.map((l) => (
              <line
                key={l.id}
                x1={l.x1}
                y1={l.y1}
                x2={l.x2}
                y2={l.y2}
                stroke={l.color}
                strokeWidth={l.width}
              />
            ))}
            {refOverlay.lines.map((l) => (
              <line
                key={l.id}
                x1={l.x1}
                y1={l.y1}
                x2={l.x2}
                y2={l.y2}
                stroke={l.color}
                strokeWidth={1.5}
                strokeDasharray="4 4"
              />
            ))}
            {overlay.texts.map((t) => (
              <text key={t.id} x={t.x} y={t.y} fill={t.color} fontSize={10}>
                {t.content}
              </text>
            ))}
            {refOverlay.texts.map((t) => (
              <text key={t.id} x={t.x} y={t.y} fill={t.color} fontSize={10}>
                {t.content}
              </text>
            ))}
            {previewLine && (
              <line
                x1={previewLine.x1}
                y1={previewLine.y1}
                x2={previewLine.x2}
                y2={previewLine.y2}
                stroke={DRAWING_COLOR}
                strokeWidth={1.5}
                strokeDasharray="4 4"
              />
            )}
          </svg>
        )}
      </div>
    </div>
  );
}

// 编辑拖拽提交：端点命中只移该锚点；线身命中双锚点同步平移（数据坐标 delta）；
// hline 只改 price；text 整体平移。拖到两锚点同日不通过则返回 null（回退拖拽前锚点——
// 否则渲染兜底跳过致线当场消失、且刷新后被 loadDrawings 按 kind 校验永久丢弃）。
// 拖拽后端点若在窗口外由下次渲染按 §3.6.1 裁切处理。
function applyDrag(
  d: Drawing,
  hit: 'endpoint1' | 'endpoint2' | 'body',
  start: { idx: number; price: number },
  end: { idx: number; price: number },
  xAxisData: string[],
): Drawing | null {
  const clampIdx = (i: number) => Math.max(0, Math.min(xAxisData.length - 1, i));
  const date = (i: number) => xAxisData[clampIdx(i)];
  const round2 = (v: number) => Math.round(v * 100) / 100;
  if (d.kind === 'hline') {
    return { ...d, p1: { date: d.p1.date, price: round2(end.price) } };
  }
  if (d.kind === 'text') {
    return { ...d, pos: { date: date(end.idx), price: round2(end.price) } };
  }
  // trend / ray
  if (hit === 'body') {
    const deltaIdx = end.idx - start.idx;
    const deltaPrice = end.price - start.price;
    const i1 = anchorIndex(xAxisData, d.p1.date);
    const i2 = anchorIndex(xAxisData, d.p2.date);
    if (i1 < 0 || i2 < 0) return null;
    const minDelta = -Math.min(i1, i2);
    const maxDelta = xAxisData.length - 1 - Math.max(i1, i2);
    const shift = Math.max(minDelta, Math.min(maxDelta, deltaIdx));
    // 双锚点同步平移：小数 x 随锚点保留（偏移不变）
    const p1 = { date: xAxisData[i1 + shift], price: round2(d.p1.price + deltaPrice), x: d.p1.x };
    const p2 = { date: xAxisData[i2 + shift], price: round2(d.p2.price + deltaPrice), x: d.p2.x };
    if (p1.date === p2.date) return null;
    return { ...d, p1, p2 };
  }
  // 端点拖拽：落点按 bar 中心锚定（拖拽取整索引），小数 x 重置
  const moved =
    hit === 'endpoint1'
      ? { p1: { date: date(end.idx), price: round2(end.price) }, p2: d.p2 }
      : { p1: d.p1, p2: { date: date(end.idx), price: round2(end.price) } };
  if (moved.p1.date === moved.p2.date) return null; // 同日 → 回退
  return { ...d, ...moved };
}
