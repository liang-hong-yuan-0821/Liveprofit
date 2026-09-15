import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import type { BarSeriesOption, CandlestickSeriesOption, LineSeriesOption } from 'echarts/charts';
import type { DataZoomComponentOption } from 'echarts/components';
import type { CallbackDataParams } from 'echarts/types/dist/shared';
import { graphic, type EChartsType } from 'echarts';
import {
  anchorIndex,
  buildDrawingsMarks,
  DRAWING_COLOR,
  hitTestRect,
  hitTestSegment,
  newId,
  renderedSegment,
  unionExtent,
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
}

export interface CandlestickChartViewModel {
  /** x 轴日期标签 */
  xAxisData: string[];
  /** [open, close, low, high] */
  ohlc: [number, number, number, number][];
  volume?: (number | null)[];
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

// 均线配色（暗色主题友好）；未列出的周期用中性灰兜底
const MA_COLORS: Record<number, string> = {
  5: '#fbbf24',
  10: '#f472b6',
  20: '#a78bfa',
  60: '#34d399',
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

type Mode = 'view' | 'draw' | 'edit';
type DrawKind = 'hline' | 'trend' | 'ray' | 'text';

function legendColor(name: string): string {
  const ma = name.match(/^MA(\d+)$/);
  if (ma) return MA_COLORS[Number(ma[1])] ?? MA_FALLBACK_COLOR;
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

// 图例 = label + value（2026-09-15 用户改版，替代原读条）：每个图例项显示"名称 + 当前值"，
// 悬浮跟随、未悬浮显示可见窗口末根。行 1 = 开/高/低/收（+量，纯展示/可开关项），
// 行 2 = MA+BOLL、行 3 = DIF/DEA；读条 overlay 已移除。
const FIXED_OHLC_NAMES = ['开', '高', '低', '收'];
const LEGEND_DISPLAY_NAMES: Record<string, string> = {
  成交量: '量',
  BOLL上轨: 'BOLL上',
  BOLL中轨: 'BOLL中',
  BOLL下轨: 'BOLL下',
};

function legendItemColor(name: string, up: boolean): string {
  if (FIXED_OHLC_NAMES.includes(name) || name === '成交量') {
    return up ? '#ef4444' : '#22c55e'; // 与 K 线同色红涨绿跌
  }
  return legendColor(name);
}

function legendValueFor(name: string, model: CandlestickChartViewModel, idx: number): string | null {
  const fmt2 = (v: number | null | undefined) => (v === null || v === undefined ? null : v.toFixed(2));
  const row = model.ohlc[idx];
  if (name === '开') return row ? fmt2(row[0]) : null;
  if (name === '高') return row ? fmt2(row[3]) : null;
  if (name === '低') return row ? fmt2(row[2]) : null;
  if (name === '收') return row ? fmt2(row[1]) : null;
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
  const legendFor = (top: number, names: string[]) => ({
    top,
    left: 0,
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
    data: names.map((name) => ({ name, textStyle: { color: legendItemColor(name, up) } })),
  });
  const legends: Array<Record<string, unknown>> = [
    legendFor(0, hasVolume ? [...FIXED_OHLC_NAMES, '成交量'] : FIXED_OHLC_NAMES),
  ];
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
}: CandlestickChartProps) {
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
      const color = MA_COLORS[line.period] ?? MA_FALLBACK_COLOR;
      mainLegend.push(name);
      built.push({
        name,
        type: 'line',
        data: line.values,
        symbol: 'none',
        lineStyle: { width: 1, opacity: 0.5, color },
        itemStyle: { color },
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
    // 成交量不进 legend（与 MACD 柱一致，柱不进 legend）。
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
        { name: 'DIF', type: 'line', data: model.macd.dif, xAxisIndex: macdAxisIndex, yAxisIndex: macdAxisIndex, symbol: 'none', lineStyle: { width: 1, color: MACD_DIF_COLOR }, itemStyle: { color: MACD_DIF_COLOR } },
        { name: 'DEA', type: 'line', data: model.macd.dea, xAxisIndex: macdAxisIndex, yAxisIndex: macdAxisIndex, symbol: 'none', lineStyle: { width: 1, color: MACD_DEA_COLOR }, itemStyle: { color: MACD_DEA_COLOR } },
      );
    }
    return { series: built, legendMain: mainLegend, legendMacd: macdLegend, hasVolume: volumeOn, hasMacd: macdOn };
  }, [model]);

  // ---- 画线（§3.6）----
  const hasDrawings = Boolean(onDrawingsChange && drawings);
  const [mode, setMode] = useState<Mode>('view');
  const [drawKind, setDrawKind] = useState<DrawKind>('trend');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [pendingText, setPendingText] = useState<{ idx: number; price: number } | null>(null);
  const [textValue, setTextValue] = useState('');
  const textCancelledRef = useRef(false);
  const instanceRef = useRef<EChartsType | null>(null);
  const lastExtentRef = useRef<{ windowKey: string; extent: RenderExtent } | null>(null);
  const drawingRef = useRef<{ kind: Exclude<DrawKind, 'text'>; p1: { x: number; y: number } } | null>(null);
  const dragRef = useRef<{
    id: string;
    hit: 'endpoint1' | 'endpoint2' | 'body';
    start: { idx: number; price: number };
  } | null>(null);
  const previewShapeRef = useRef<graphic.Line | null>(null);

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

  // extent 两条路径一个构建函数（§3.6.1 读取时序）：windowKey 一致用 ref 里的真实 extent（精确）；
  // 不一致（缩放/平移后首帧）或首帧 → 可见窗口内全系列极值并集兜底（⊆ extent，containData 恒过）。
  // 并集仅作 containData 兜底、不作夹取精度目标。
  const cachedExtent = lastExtentRef.current;
  const extentForOption: RenderExtent | null =
    cachedExtent && cachedExtent.windowKey === windowKey
      ? cachedExtent.extent
      : unionExtent(
          windowIdx.startIdx,
          windowIdx.endIdx,
          model.ohlc,
          model.ma?.map((m) => m.values) ?? [],
          model.boll ? [model.boll.upper, model.boll.mid, model.boll.lower] : [],
        );

  const marks = useMemo(() => {
    if (!hasDrawings || !extentForOption) return { markLine: [], markPoint: [] };
    const effectiveDrawings = draggingId ? (drawings ?? []).filter((d) => d.id !== draggingId) : (drawings ?? []);
    return buildDrawingsMarks(effectiveDrawings, model.xAxisData, windowIdx, extentForOption, selectedId);
  }, [hasDrawings, extentForOption, drawings, model.xAxisData, windowIdx, selectedId, draggingId]);

  // 画线挂载（不改写 useMemo 出的 series 本体——option 的 memo 依赖需要稳定引用）
  const optionSeries = useMemo(() => {
    if (!hasDrawings) return series;
    return [
      { ...series[0], markLine: { data: marks.markLine }, markPoint: { data: marks.markPoint } },
      ...series.slice(1),
    ];
  }, [hasDrawings, series, marks]);

  // 渲染后读真实 extent 并 merge 修正（useLayoutEffect 同帧，免闪烁）：
  // option 构建是同步纯函数、extent 渲染后才可读；extent 读取门控到实例就绪
  // （echarts-for-react mount 先建临时实例、finished 后 dispose 重建——临时实例上读 yAxis 得
  // undefined，读数校验失败待下一次渲染重试）。
  // 门控（§3.7 性能守卫）：hover 等逐像素重渲染时输入未变 → 不重读 extent、不 merge——
  // 否则每 mousemove 一次 markLine 重建（ChartCore memo 拦不住这条直连实例路径）。
  const lastMergeRef = useRef<{
    windowKey: string;
    model: CandlestickChartViewModel;
    selectedId: string | null;
    draggingId: string | null;
    drawings: Drawing[] | undefined;
  } | null>(null);
  useLayoutEffect(() => {
    const inst = instanceRef.current;
    if (!inst || !hasDrawings) return;
    const last = lastMergeRef.current;
    if (
      last &&
      last.windowKey === windowKey &&
      last.model === model &&
      last.selectedId === selectedId &&
      last.draggingId === draggingId &&
      last.drawings === drawings
    ) {
      return; // 输入未变（含数据/extent 未变）：无 merge 需要
    }
    try {
      const readExtent = (): RenderExtent | null => {
        // getModel 在类型上为私有成员，先把实例断言成公开签名再读取（运行时为公开 API）
        const getComponent = (
          inst as unknown as {
            getModel: () => { getComponent: (kind: string, idx: number) => unknown };
          }
        ).getModel().getComponent;
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
      const extent = readExtent();
      if (!extent) return; // 实例未就绪/读轴异常：待下一次渲染重试
      lastExtentRef.current = { windowKey, extent };
      lastMergeRef.current = { windowKey, model, selectedId, draggingId, drawings };
      // 直连 merge（series 按索引合并到 candlestick，不重建整图、不闪）
      const merged = buildDrawingsMarks(
        (draggingId ? (drawings ?? []).filter((d) => d.id !== draggingId) : (drawings ?? [])),
        model.xAxisData,
        windowIdx,
        extent,
        selectedId,
      );
      inst.setOption(
        { series: [{ markLine: { data: merged.markLine }, markPoint: { data: merged.markPoint } }] },
        { notMerge: false },
      );
    } catch {
      // 实例未就绪/读轴异常：待下一次渲染重试
    }
  });

  // ---- 画线交互（zr 鼠标事件，仅 draw/edit 模式挂接）----
  const toData = (x: number, y: number): { idx: number; price: number } | null => {
    const inst = instanceRef.current;
    if (!inst) return null;
    try {
      const v = inst.convertFromPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [x, y]);
      if (!Array.isArray(v) || v.length < 2 || typeof v[0] !== 'number' || typeof v[1] !== 'number') return null;
      const idx = Math.round(v[0]);
      if (idx < 0 || idx >= model.xAxisData.length) return null;
      return { idx, price: v[1] };
    } catch {
      return null;
    }
  };
  const inMainGrid = (x: number, y: number): boolean => {
    const inst = instanceRef.current;
    if (!inst) return false;
    try {
      // getModel 在类型上为私有成员，先把实例断言成公开签名再读取（运行时为公开 API）
      const grid = (
        inst as unknown as {
          getModel: () => { getComponent: (kind: string, idx: number) => unknown };
        }
      ).getModel().getComponent('grid', 0) as {
        coordinateSystem?: { getRect?: () => { x: number; y: number; width: number; height: number } };
      } | null;
      const rect = grid?.coordinateSystem?.getRect?.();
      if (!rect) return false;
      return x >= rect.x && x <= rect.x + rect.width && y >= rect.y && y <= rect.y + rect.height;
    } catch {
      return false;
    }
  };
  const toPixel = (idx: number, price: number): { x: number; y: number } | null => {
    const inst = instanceRef.current;
    if (!inst) return null;
    try {
      const v = inst.convertToPixel({ xAxisIndex: 0, yAxisIndex: 0 }, [idx, price]);
      if (!Array.isArray(v) || v.length < 2) return null;
      return { x: v[0] as number, y: v[1] as number };
    } catch {
      return null;
    }
  };
  const round2 = (v: number) => Math.round(v * 100) / 100;

  const showPreview = (x1: number, y1: number, x2: number, y2: number) => {
    const inst = instanceRef.current;
    if (!inst) return;
    clearPreview();
    const line = new graphic.Line({
      shape: { x1, y1, x2, y2 },
      style: { stroke: DRAWING_COLOR, lineWidth: 1.5, lineDash: [4, 4] },
    });
    previewShapeRef.current = line;
    inst.getZr().add(line);
  };
  const clearPreview = () => {
    const inst = instanceRef.current;
    if (previewShapeRef.current && inst) {
      inst.getZr().remove(previewShapeRef.current);
    }
    previewShapeRef.current = null;
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
    const onMouseDown = (event: { offsetX: number; offsetY: number }) => {
      if (!inMainGrid(event.offsetX, event.offsetY)) return; // 副图/slider 区域忽略
      if (mode === 'draw') {
        if (drawKind === 'text') {
          const pos = toData(event.offsetX, event.offsetY);
          if (pos) {
            setPendingText(pos);
            setTextValue('');
            textCancelledRef.current = false;
          }
          return;
        }
        drawingRef.current = { kind: drawKind, p1: { x: event.offsetX, y: event.offsetY } };
        return;
      }
      // edit：命中检测（像素空间：端点 8px 优先、线身 6px、text 包围盒 6px）。
      // 线段几何与渲染同源（renderedSegment）：射线外推段可命中、截断后不可见的段不可命中；
      // 拖拽中按 id 过滤隐藏的线不在命中集合。
      const mouse = { x: event.offsetX, y: event.offsetY };
      let hit: { id: string; part: 'endpoint1' | 'endpoint2' | 'body' } | null = null;
      for (const d of drawings ?? []) {
        if (d.id === draggingId) continue;
        if (d.kind === 'text') {
          const idx = anchorIndex(model.xAxisData, d.pos.date);
          if (idx < 0) continue;
          const pix = toPixel(idx, d.pos.price);
          if (!pix) continue;
          const rect = { x1: pix.x - 2, y1: pix.y - 12, x2: pix.x + d.text.length * 6.6 + 2, y2: pix.y + 2 };
          if (hitTestRect(mouse, rect)) {
            hit = { id: d.id, part: 'body' };
            break;
          }
          continue;
        }
        const seg = extentForOption
          ? renderedSegment(d, model.xAxisData, windowIdx, extentForOption)
          : null;
        if (!seg) continue;
        const a = toPixel(seg.i1, seg.p1);
        const b = toPixel(seg.i2, seg.p2);
        if (!a || !b) continue;
        const part = hitTestSegment(mouse, a, b);
        if (part) {
          hit = { id: d.id, part };
          break;
        }
      }
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
        showPreview(drawingRef.current.p1.x, drawingRef.current.p1.y, event.offsetX, event.offsetY);
        return;
      }
      const dr = dragRef.current;
      if (dr && draggingId && extentForOption) {
        // 拖拽预览 = 假设提交后的渲染线段（与渲染几何同源）：原线已按 id 过滤隐藏，
        // 预览画平移/变形后的真实位置；校验不通过（如同日）则预览清除
        const target = (drawings ?? []).find((d) => d.id === dr.id);
        const end = toData(event.offsetX, event.offsetY);
        if (target && end) {
          const updated = applyDrag(target, dr.hit, dr.start, end, model.xAxisData);
          if (updated) {
            const seg = renderedSegment(updated, model.xAxisData, windowIdx, extentForOption);
            const a = seg ? toPixel(seg.i1, seg.p1) : null;
            const b = seg ? toPixel(seg.i2, seg.p2) : null;
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
        const end = toData(event.offsetX, event.offsetY);
        const startData = toData(start.p1.x, start.p1.y);
        if (!end || !startData) return;
        const date = (i: number) => model.xAxisData[i];
        if (start.kind === 'hline') {
          onDrawingsChange?.([...(drawings ?? []), {
            id: newId(), kind: 'hline',
            p1: { date: date(startData.idx), price: round2(startData.price) },
          }]);
          return;
        }
        // trend/ray：垂直两点拒绝提交（射线方向未定义）
        if (startData.idx === end.idx) return;
        onDrawingsChange?.([...(drawings ?? []), {
          id: newId(), kind: start.kind,
          p1: { date: date(startData.idx), price: round2(startData.price) },
          p2: { date: date(end.idx), price: round2(end.price) },
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
  // 多 grid 布局（H=420 验算，§3.1.1 表）：主图→成交量→MACD；grid.bottom 自底部计量、
  // grid.top 自顶部计量；相邻间距 8.4px/8.4px + slider 余量 18px，主图 ≥170px 门槛。
  // top：双行图例预算 36、单行 32、无图例 30（读条预算）。
  const zoomLocked = mode !== 'view';

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
    // 图例选中回填（防 notMerge 重建重置选中）；开/高/低/收为纯展示固定项，
    // 点击切换对其无系列可联动——过滤掉，保持"点击无效"（不变灰）
    events.legendselectchanged = (_params: unknown, instance: EChartsType) => {
      const legendOption = (instance.getOption().legend as Array<{ selected?: Record<string, boolean> }>) ?? [];
      const merged: Record<string, boolean> = {};
      for (const l of legendOption) Object.assign(merged, l.selected ?? {});
      for (const n of FIXED_OHLC_NAMES) delete merged[n];
      setLegendSelected(merged);
    };
    return events;
  }, [onDataZoom, model.xAxisData]);

  const onChartReady = useCallback((instance: EChartsType) => {
    instanceRef.current = instance;
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
  }, [hoverIdx, fallbackIdx, model, hasVolume, legendMain, legendMacd, legendSelected]);

  // option 全量构建收敛进 useMemo：grid/xAxis/yAxis/dataZoom/legend 的对象引用必须稳定——
  // 外层 hover 等逐像素 state 变化时不重建 → ChartCore memo 拦截、无 setOption 风暴。
  const option = useMemo(() => {
    // 图例行数（行 1 开高低收+量恒在）：3 行 56 / 2 行 40 / 1 行 24（读条已移除，
    // 无"无图例"布局——行 1 恒渲染）
    const legendRows = 1 + (legendMain.length > 0 ? 1 : 0) + (legendMacd.length > 0 ? 1 : 0);
    const mainGrid = {
      left: 48,
      right: 16,
      top: legendRows >= 3 ? 56 : legendRows === 2 ? 40 : legendRows === 1 ? 24 : 30,
      bottom: hasVolume && hasMacd ? '50%' : hasVolume ? '34%' : hasMacd ? '36%' : 30,
    };
    const volumeGrid = hasVolume
      ? { left: 48, right: 16, top: hasMacd ? '52%' : '72%', bottom: hasMacd ? '26%' : 34 }
      : undefined;
    const macdGrid = hasMacd
      ? { left: 48, right: 16, top: hasVolume ? '76%' : '67%', bottom: 34 }
      : undefined;
    const grids = [mainGrid, volumeGrid, macdGrid].filter((g): g is NonNullable<typeof g> => g !== undefined);
    const grid = grids.length > 1 ? grids : mainGrid;

    // 轴数组按 grid 顺序构造（每个 grid 一个轴对象）：主图 0、成交量 1、MACD 2。
    // 日期标签只在最底副图显示（主图与成交量隐藏 axisLabel）。
    const xAxisBase = { type: 'category' as const, data: model.xAxisData, axisLabel: { color: '#8b95a1' }, axisLine: { lineStyle: { color: '#232a33' } } };
    const yAxisBase = { scale: true, axisLabel: { color: '#8b95a1' }, splitLine: { lineStyle: { color: '#232a33' } } };
    // 成交量 y 轴独立量纲：可见纵轴（标签量级缩写 万/亿手，splitNumber 2；
    // 与价格轴共用暗色轴样式，不与价格轴共享量纲）
    const volumeYAxis = {
      ...yAxisBase,
      gridIndex: 1,
      splitNumber: 2,
      axisLabel: { color: '#8b95a1', formatter: (value: number) => formatVolume(value) },
    };
    const xAxis = grids.length > 1
      ? grids.map((_g, i) => ({
          ...xAxisBase,
          gridIndex: i,
          axisLabel: i === grids.length - 1 ? { color: '#8b95a1' } : { show: false, color: '#8b95a1' },
        }))
      : xAxisBase;
    // 成交量轴只挂在它实际存在的 grid 上（hasVolume=false 时索引 1 是 MACD grid——
    // 写死 i===1 会把 MACD 副图套上成交量轴样式，刻度/分割线全隐）
    const volumeAxisIndex = hasVolume ? 1 : -1;
    const yAxis = grids.length > 1
      ? grids.map((_g, i) => (i === volumeAxisIndex ? volumeYAxis : { ...yAxisBase, gridIndex: i }))
      : yAxisBase;

    // dataZoom 覆盖全部副图（[0..gridCount-1]）；单 grid 不设 xAxisIndex（现状行为）。
    // 日期锚定（§3.3）：visibleRange 传入时用 startValue/endValue（category 轴字符串吸附最近类目），
    // 数据扩展（前插更早 bars）时可见日期不变 → 窗口不跳；未传时回落 start/end 百分比。
    // **两种锚互斥，禁止混写**（实测混写时百分比优先、日期锚被忽略，窗口锁死全量且
    // 每次重建弹回——放大永远失效，缩小仅因 §3.4 扩展让全量窗口变大看似可用）。
    // draw/edit 模式禁用 inside 滚轮缩放与鼠标平移（zr 事件与缩放互斥；slider 仍可拖动照发 datazoom）。
    const zoomAxes = grids.length > 1 ? grids.map((_g, i) => i) : undefined;
    const zoomWindow = visibleRange
      ? { startValue: visibleRange.start, endValue: visibleRange.end }
      : { start: 0, end: 100 };
    const dataZoom = [
      { type: 'inside', xAxisIndex: zoomAxes, zoomOnMouseWheel: !zoomLocked, moveOnMouseMove: !zoomLocked, moveOnMouseWheel: false, ...zoomWindow },
      {
        type: 'slider', xAxisIndex: zoomAxes, height: 14, bottom: 2, showDetail: false,
        borderColor: 'transparent', backgroundColor: 'rgba(35, 42, 51, 0.6)',
        fillerColor: 'rgba(148, 163, 184, 0.25)', handleStyle: { color: '#8b95a1' },
        textStyle: { color: '#8b95a1', fontSize: 10 },
        ...zoomWindow,
      },
    ];

    // 图例 = label + value（buildLegendOptions，行 1 开高低收+量恒在、行 2 MA+BOLL、行 3 DIF/DEA）；
    // 纯文字（icon none）、逐项系列色（实测默认文字色 #54555a 不继承系列色）、
    // 点击隐藏变灰（inactiveColor）、left 0 + itemWidth 0 + 紧凑 gap（值为本随行显示，无右侧读条）
    const legends = buildLegendOptions({
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
      series: optionSeries,
    };
  }, [
    hasVolume, hasMacd, model.xAxisData, visibleRange?.start, visibleRange?.end,
    zoomLocked, legendMain, legendMacd, legendSelected, optionSeries, fallbackIdx,
  ]);

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
      <div style={{ position: 'relative' }}>
        <ChartCore option={option} onEvents={onEvents} onChartReady={onChartReady} height={height} />
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
    const p1 = { date: xAxisData[i1 + shift], price: round2(d.p1.price + deltaPrice) };
    const p2 = { date: xAxisData[i2 + shift], price: round2(d.p2.price + deltaPrice) };
    if (p1.date === p2.date) return null;
    return { ...d, p1, p2 };
  }
  const moved =
    hit === 'endpoint1'
      ? { p1: { date: date(end.idx), price: round2(end.price) }, p2: d.p2 }
      : { p1: d.p1, p2: { date: date(end.idx), price: round2(end.price) } };
  if (moved.p1.date === moved.p2.date) return null; // 同日 → 回退
  return { ...d, ...moved };
}
