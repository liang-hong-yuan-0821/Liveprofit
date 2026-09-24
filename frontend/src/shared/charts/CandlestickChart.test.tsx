import { useUiPreferenceStore } from '../../stores/uiPreferenceStore';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import * as echarts from 'echarts';
import { CandlestickChart, type CandlestickChartViewModel } from './CandlestickChart';
import type { Drawing } from './drawings';

// jsdom 无 canvas：mock echarts-for-react 捕获 option/onEvents/onChartReady props；
// forwardRef + useImperativeHandle 暴露假实例（§3.6.3 验证 5 前置：绘制/编辑交互测试
// 经 onChartReady 拿到实例、经 getZr().on 注册的 handler 手动派发鼠标事件）
const mockState = vi.hoisted(() => ({
  props: null as PropsShape | null,
  fakeInstance: {} as Record<string, unknown>,
  zrHandlers: {} as Record<string, Array<(e: { offsetX: number; offsetY: number }) => void>>,
  zr: null as null | {
    on: unknown;
    off: unknown;
    add: ReturnType<typeof vi.fn>;
    remove: ReturnType<typeof vi.fn>;
  },
  renderCount: 0,
}));

vi.mock('echarts-for-react', async () => {
  const React = await import('react');
  return {
    default: React.forwardRef((props: Record<string, unknown>, ref: React.ForwardedRef<unknown>) => {
      mockState.props = props as unknown as PropsShape;
      mockState.renderCount += 1;
      React.useImperativeHandle(ref, () => ({ getEchartsInstance: () => mockState.fakeInstance }));
      return React.createElement('div', { 'data-testid': 'echarts' });
    }),
  };
});

interface SeriesShape {
  name?: string;
  type?: string;
  stack?: string;
  data?: unknown[];
  xAxisIndex?: number;
  yAxisIndex?: number;
  itemStyle?: { color?: unknown };
  lineStyle?: { width?: number; opacity?: number; color?: string };
  smooth?: boolean;
  symbol?: string;
  markLine?: { data: Array<{ lineStyle?: { width?: number; type?: string; color?: string }; data?: unknown }> };
  markPoint?: { data: Array<{ coord?: [number, number]; label?: { formatter?: unknown }; symbol?: string }> };
}

interface GridShape {
  left?: number;
  right?: number;
  top?: number | string;
  bottom?: number | string;
}

interface LegendItemShape {
  name: string;
  textStyle?: { color?: string };
}

interface LegendShape {
  top?: number;
  left?: number;
  itemWidth?: number;
  itemGap?: number;
  icon?: string;
  inactiveColor?: string;
  selected?: Record<string, boolean>;
  formatter?: (name: string) => string;
  data: LegendItemShape[];
}

interface OptionShape {
  series: SeriesShape[];
  legend?: LegendShape[];
  dataZoom?: { type?: string; xAxisIndex?: number[]; startValue?: unknown; endValue?: unknown; zoomOnMouseWheel?: boolean; moveOnMouseMove?: boolean }[];
  grid?: unknown;
  xAxis?: unknown;
  yAxis?: unknown;
  axisPointer?: { link?: { xAxisIndex?: string }[] };
  animation?: boolean;
  tooltip?: { trigger?: string; showContent?: boolean };
}

interface PropsShape {
  option: OptionShape;
  onEvents?: Record<string, unknown>;
  onChartReady?: (instance: unknown) => void;
}

function renderProps(): PropsShape {
  return mockState.props!;
}

function renderOption(): OptionShape {
  return renderProps().option;
}

// 假实例：grid 矩形 600×420、extent [2900, 3200]、convert 像素↔数据（x/100, 3200−y——
// 价格 2900–3200 映射到 y 300–0，全部落在 grid 矩形内，保证 inMainGrid 校验通过）。
// zr 对象固定引用（组件每次 getZr() 返回同一对象，spy 断言才有意义）
function setupFakeInstance() {
  mockState.zrHandlers = {};
  mockState.zr = {
    on: (event: string, handler: unknown) => {
      // mock 刻意不做按引用去重（真实 zrender 会 === handler 去重），用于放大累积回归
      (mockState.zrHandlers[event] ??= []).push(
        handler as (e: { offsetX: number; offsetY: number }) => void,
      );
    },
    off: (event: string, handler?: unknown) => {
      // 按引用过滤（组件只允许按引用 off）；裸 off 才是清空——该路径会误删 ECharts
      // 内部监听，组件禁止使用，mock 保留真实语义供"外来 handler 存活"回归锚验证
      if (handler === undefined) {
        mockState.zrHandlers[event] = [];
      } else {
        mockState.zrHandlers[event] = (mockState.zrHandlers[event] ?? []).filter(
          (h) => h !== handler,
        );
      }
    },
    add: vi.fn(),
    remove: vi.fn(),
  };
  // 像素模型（SVG 覆盖层纯像素数学）：主图 grid {0,0,600,300}、真实 extent [2900,3200]、
  // 全窗 10 bar → band = 60、bar i 中心 = (i+0.5)×60、价格 p → y = 3200−p
  mockState.fakeInstance = {
    getZr: () => mockState.zr!,
    getModel: () => ({
      getComponent: (kind: string, _idx: number) => {
        if (kind === 'grid') return { coordinateSystem: { getRect: () => ({ x: 0, y: 0, width: 600, height: 300 }) } };
        if (kind === 'yAxis') return { axis: { scale: { getExtent: () => [2900, 3200] } } };
        return null;
      },
    }),
    setOption: vi.fn(),
    getOption: () => ({ dataZoom: [{ type: 'inside', startValue: 0, endValue: 9 }] }),
  };
}

function fireChartReady() {
  act(() => {
    renderProps().onChartReady?.(mockState.fakeInstance);
  });
}

function zrFire(event: 'mousedown' | 'mousemove' | 'mouseup', offsetX: number, offsetY: number) {
  act(() => {
    const handlers = mockState.zrHandlers[event] ?? [];
    if (handlers.length === 0) throw new Error(`zr handler ${event} not registered`);
    // off-then-on 语义下活跃 handler 恰一个；数组累积的回归会在此双触发（按设计不应发生）
    for (const handler of handlers) handler({ offsetX, offsetY });
  });
}

// SVG 覆盖层断言辅助：已提交线段（无虚线 stroke-dasharray）、预览线段（虚线）、文本
function overlayLines(): SVGLineElement[] {
  const svg = document.querySelector('svg[data-testid="drawing-overlay"]');
  return svg ? Array.from(svg.querySelectorAll('line')).filter((el) => !el.hasAttribute('stroke-dasharray')) : [];
}
function overlayPreview(): SVGLineElement | null {
  const svg = document.querySelector('svg[data-testid="drawing-overlay"]');
  return svg?.querySelector('line[stroke-dasharray]') ?? null;
}
function overlayTexts(): SVGTextElement[] {
  const svg = document.querySelector('svg[data-testid="drawing-overlay"]');
  return svg ? Array.from(svg.querySelectorAll('text')) : [];
}

// 画线提交回填 harness：onDrawingsChange 直接 setState，验证提交后覆盖层真实渲染
function DrawingHarness({ model, initial }: { model: CandlestickChartViewModel; initial: Drawing[] }) {
  const [drawings, setDrawings] = useState<Drawing[]>(initial);
  return <CandlestickChart model={model} drawings={drawings} onDrawingsChange={setDrawings} />;
}

// SSR 用例桩掉 canvas getContext，afterEach 还原（jsdom 无 canvas 的隔离桩）
const originalCanvasGetContext = HTMLCanvasElement.prototype.getContext;

const baseModel: CandlestickChartViewModel = {
  xAxisData: ['2026-09-01', '2026-09-02', '2026-09-03'],
  ohlc: [
    [1, 2, 0.5, 1.5],
    [1.5, 2.5, 1, 2],
    [2, 2.2, 1.8, 2.1],
  ],
};

// 画线测试帧：10 个类目、价格在 2950–3150 内（并集 extent [2950, 3150]）
const drawDates = Array.from({ length: 10 }, (_v, i) => `2026-09-${String(i + 1).padStart(2, '0')}`);
const drawModel: CandlestickChartViewModel = {
  xAxisData: drawDates,
  ohlc: Array.from({ length: 10 }, () => [3000, 3100, 2950, 3150] as [number, number, number, number]),
};

const trend1: Drawing = {
  id: 't1',
  kind: 'trend',
  p1: { date: drawDates[3], price: 3000 },
  p2: { date: drawDates[5], price: 3100 },
};

beforeEach(() => {
  mockState.props = null;
  mockState.fakeInstance = {};
  mockState.zrHandlers = {};
  mockState.zr = null;
  mockState.renderCount = 0;
});

afterEach(() => {
  HTMLCanvasElement.prototype.getContext = originalCanvasGetContext;
});

describe('CandlestickChart', () => {
  it('无 ma/boll 时只渲染 candlestick 系列（降级纯 K 线；图例行 1 开高低收恒在）', () => {
    render(<CandlestickChart model={baseModel} />);
    const option = renderOption();
    expect(option.series).toHaveLength(1);
    expect(option.series[0].type).toBe('candlestick');
    expect(option.legend).toHaveLength(1);
    expect(option.legend?.[0].data.map((d) => d.name)).toEqual(['开', '高', '低', '收']);
  });

  it('含 ma/boll 时叠加 4 条均线 + BOLL 五系列与 legend', () => {
    const model: CandlestickChartViewModel = {
      ...baseModel,
      ma: [
        { period: 5, values: [null, null, 1.5] },
        { period: 10, values: [null, null, null] },
        { period: 20, values: [null, null, null] },
        { period: 60, values: [null, null, null] },
      ],
      boll: {
        period: 20,
        k: 2,
        mid: [null, null, 2],
        upper: [null, null, 2.4],
        lower: [null, null, 1.6],
      },
    };
    render(<CandlestickChart model={model} />);
    const option = renderOption();
    expect(option.series).toHaveLength(10); // 1 candlestick + 4 MA + 5 BOLL
    expect(option.series.map((s) => s.type)).toEqual([
      'candlestick', 'line', 'line', 'line', 'line', 'line', 'line', 'line', 'line', 'line',
    ]);
    // 图例 = label+value 三行（2026-09-15）：行 1 开高低收、行 2 MA+BOLL、行 3 DIF/DEA
    expect(option.legend?.[0].data.map((d) => d.name)).toEqual(['开', '高', '低', '收']);
    expect(option.legend?.[1].data.map((d) => d.name)).toEqual(['MA5', 'MA10', 'MA20', 'MA60', 'BOLL上轨', 'BOLL中轨', 'BOLL下轨']);
    // Confidence Band：2 条隐藏堆叠系列——下轨垫底 → 带宽差值，保证填充落在 [lower, upper]
    const stacked = option.series.filter((s) => s.stack === 'boll-band');
    expect(stacked).toHaveLength(2);
    expect(stacked[0].data).toEqual([null, null, 1.6]); // 下轨垫底
    expect(stacked[1].data).toEqual([null, null, expect.closeTo(0.8, 5)]); // 带宽 = upper − lower
  });

  it('dataZoom：inside 滚轮缩放 + slider 底部滑条联动', () => {
    render(<CandlestickChart model={baseModel} />);
    const option = renderOption();
    expect(option.dataZoom?.map((d) => d.type)).toEqual(['inside', 'slider']);
  });

  it('含 macd 时挂双 grid 副图：2 grid/2 yAxis、副图三系列绑定轴 1、dataZoom 联动、axisPointer.link 顶层', () => {
    const model: CandlestickChartViewModel = {
      ...baseModel,
      ma: [
        { period: 5, values: [null, null, 1.5] },
        { period: 10, values: [null, null, null] },
        { period: 20, values: [null, null, null] },
        { period: 60, values: [null, null, null] },
      ],
      boll: {
        period: 20,
        k: 2,
        mid: [null, null, 2],
        upper: [null, null, 2.4],
        lower: [null, null, 1.6],
      },
      macd: { dif: [null, null, 0.3], dea: [null, null, 0.2], hist: [null, null, 0.2] },
    };
    render(<CandlestickChart model={model} />);
    const option = renderOption();
    // 双 grid + 双 xAxis/yAxis
    expect(option.grid).toHaveLength(2);
    expect(option.xAxis).toHaveLength(2);
    expect(option.yAxis).toHaveLength(2);
    // 副图三系列：MACD柱 bar + DIF/DEA 线，显式绑定 grid 1
    const macdSeries = option.series.slice(-3);
    expect(macdSeries.map((s) => [s.type, s.xAxisIndex, s.yAxisIndex])).toEqual([
      ['bar', 1, 1],
      ['line', 1, 1],
      ['line', 1, 1],
    ]);
    // MACD 柱正红负绿：回调按值着色
    const histBar = macdSeries[0];
    const colorFn = (histBar.itemStyle?.color ?? (() => '')) as (p: { value?: unknown }) => string;
    expect(colorFn({ value: 0.2 })).toBe('#ef4444');
    expect(colorFn({ value: -0.1 })).toBe('#22c55e');
    expect(colorFn({ value: null })).toBe('#22c55e');
    // dataZoom 覆盖两图 + 顶层 axisPointer.link
    expect(option.dataZoom?.map((d) => d.xAxisIndex)).toEqual([[0, 1], [0, 1]]);
    expect(option.axisPointer?.link).toEqual([{ xAxisIndex: 'all' }]);
    // legend 三行分组（2026-09-15 label+value）：行 2 MA+BOLL、行 3 DIF/DEA（MACD 柱不进 legend）
    expect(option.legend?.[1].data.map((d) => d.name)).toEqual([
      'MA5', 'MA10', 'MA20', 'MA60', 'BOLL上轨', 'BOLL中轨', 'BOLL下轨',
    ]);
    expect(option.legend?.[2].data.map((d) => d.name)).toEqual(['DIF', 'DEA']);
  });

  it('无 macd 时回归单 grid：grid 非数组、series 数量回归 10（含 ma/boll）', () => {
    const model: CandlestickChartViewModel = {
      ...baseModel,
      ma: [
        { period: 5, values: [null, null, 1.5] },
        { period: 10, values: [null, null, null] },
        { period: 20, values: [null, null, null] },
        { period: 60, values: [null, null, null] },
      ],
      boll: {
        period: 20,
        k: 2,
        mid: [null, null, 2],
        upper: [null, null, 2.4],
        lower: [null, null, 1.6],
      },
    };
    render(<CandlestickChart model={model} />);
    const option = renderOption();
    expect(option.series).toHaveLength(10);
    expect(Array.isArray(option.grid)).toBe(false);
    expect(option.axisPointer).toBeUndefined();
  });

  it('MA 参考样式（smooth+调色板色+symbol none）、BOLL 细线半透明：带宽隐藏系列不变', () => {
    const model: CandlestickChartViewModel = {
      ...baseModel,
      ma: [
        { period: 5, values: [null, null, 1.5] },
        { period: 10, values: [null, null, null] },
        { period: 20, values: [null, null, null] },
        { period: 60, values: [null, null, null] },
      ],
      boll: {
        period: 20,
        k: 2,
        mid: [null, null, 2],
        upper: [null, null, 2.4],
        lower: [null, null, 1.6],
      },
    };
    render(<CandlestickChart model={model} />);
    const option = renderOption();
    // MA（2026-09-16 参考代码样式）：smooth、symbol none（用户拍板：不喜欢默认空心圆点）、
    // 半透明 + 调色板按系列序色值（MA5/10/20/60 = 系列序 1..4）
    const maSeries = option.series.filter((s) => s.name && /^MA\d+$/.test(s.name));
    expect(maSeries.map((s) => [s.smooth, s.symbol])).toEqual([
      [true, 'none'],
      [true, 'none'],
      [true, 'none'],
      [true, 'none'],
    ]);
    expect(maSeries.map((s) => [s.name, s.lineStyle])).toEqual([
      ['MA5', { opacity: 0.7, color: '#91cc75' }],
      ['MA10', { opacity: 0.7, color: '#fac858' }],
      ['MA20', { opacity: 0.7, color: '#ee6666' }],
      ['MA60', { opacity: 0.7, color: '#73c0de' }],
    ]);
    // BOLL 可见系列：细线半透明（不变）
    const bollVisible = option.series.filter(
      (s) => s.name && !s.stack && ['BOLL上轨', 'BOLL中轨', 'BOLL下轨'].includes(s.name),
    );
    for (const s of bollVisible) {
      expect(s.lineStyle).toMatchObject({ width: 1, opacity: 0.5 });
    }
    // 带宽两条隐藏堆叠系列不透明
    const hidden = option.series.filter((s) => s.stack === 'boll-band');
    for (const s of hidden) {
      expect(s.lineStyle).toMatchObject({ opacity: 0 });
    }
  });

  it('hasVolume：成交量 bar 系列绑定 (1,1)、红涨绿跌回调着色、null 值透传、不进 legend', () => {
    const model: CandlestickChartViewModel = {
      ...baseModel,
      volume: [100, null, 300],
      ma: [{ period: 5, values: [null, null, 1.5] }],
    };
    render(<CandlestickChart model={model} />);
    const option = renderOption();
    const volumeSeries = option.series.find((s) => s.name === '成交量');
    expect(volumeSeries).toBeDefined();
    expect([volumeSeries!.type, volumeSeries!.xAxisIndex, volumeSeries!.yAxisIndex]).toEqual(['bar', 1, 1]);
    expect(volumeSeries!.data).toEqual([[0, 100, 1], null, [2, 300, 1]]);
    const colorFn = (volumeSeries!.itemStyle?.color ?? (() => '')) as (p: { value?: unknown }) => string;
    expect(colorFn({ value: [0, 100, 1] })).toBe('#ef4444'); // 涨（收≥开）红
    expect(colorFn({ value: [0, 100, -1] })).toBe('#22c55e'); // 跌绿
    // 量入图例（2026-09-15）：行 1 = 开高低收+量
    expect(option.legend?.[0].data.map((d) => d.name)).toEqual(['开', '高', '低', '收', '成交量']);
    expect(option.legend?.[1].data.map((d) => d.name)).toEqual(['MA5']);
    // 仅成交量布局：2 grid、dataZoom 覆盖 [0,1]、axisPointer.link 顶层
    expect(option.grid).toHaveLength(2);
    expect(option.dataZoom?.map((d) => d.xAxisIndex)).toEqual([[0, 1], [0, 1]]);
    expect(option.axisPointer?.link).toEqual([{ xAxisIndex: 'all' }]);
  });

  it('volume+MACD 共存：3 grid/3 xAxis/3 yAxis、成交量 (1,1)、MACD 迁至 (2,2)、dataZoom [0,1,2]', () => {
    const model: CandlestickChartViewModel = {
      ...baseModel,
      volume: [100, 200, 300],
      ma: [{ period: 5, values: [null, null, 1.5] }],
      boll: {
        period: 20,
        k: 2,
        mid: [null, null, 2],
        upper: [null, null, 2.4],
        lower: [null, null, 1.6],
      },
      macd: { dif: [null, null, 0.3], dea: [null, null, 0.2], hist: [null, null, 0.2] },
    };
    render(<CandlestickChart model={model} />);
    const option = renderOption();
    expect(option.grid).toHaveLength(3);
    expect(option.xAxis).toHaveLength(3);
    expect(option.yAxis).toHaveLength(3);
    const volumeSeries = option.series.find((s) => s.name === '成交量');
    expect([volumeSeries!.xAxisIndex, volumeSeries!.yAxisIndex]).toEqual([1, 1]);
    const macdSeries = option.series.slice(-3);
    expect(macdSeries.map((s) => [s.type, s.xAxisIndex, s.yAxisIndex])).toEqual([
      ['bar', 2, 2],
      ['line', 2, 2],
      ['line', 2, 2],
    ]);
    expect(option.dataZoom?.map((d) => d.xAxisIndex)).toEqual([[0, 1, 2], [0, 1, 2]]);
    expect(option.axisPointer?.link).toEqual([{ xAxisIndex: 'all' }]);
    // 布局验算（§3.1.1 H=460 表，2026-09-16 间距加宽 3.5%）：主图 top 56 + bottom '49%'、成交量 '54.5%'/'26%'、MACD '77.5%'
    const grids = option.grid as GridShape[];
    expect(grids[0]).toMatchObject({ top: 56, bottom: '49%' });
    expect(grids[1]).toMatchObject({ top: '54.5%', bottom: '26%' });
    expect(grids[2]).toMatchObject({ top: '77.5%', bottom: 34 });
  });

  it('volume 全 null：不开副图（单 grid、无成交量系列）', () => {
    const model: CandlestickChartViewModel = {
      ...baseModel,
      volume: [null, null, null],
    };
    render(<CandlestickChart model={model} />);
    const option = renderOption();
    expect(option.series.find((s) => s.name === '成交量')).toBeUndefined();
    expect(Array.isArray(option.grid)).toBe(false);
    expect(option.axisPointer).toBeUndefined();
  });

  it('visibleRange 传入时 dataZoom 以日期锚定（startValue/endValue），未传时回落旧行为；两种锚互斥不混写', () => {
    const { rerender } = render(
      <CandlestickChart model={baseModel} visibleRange={{ start: '2026-09-01', end: '2026-09-03' }} />,
    );
    const option = renderOption();
    expect(option.dataZoom?.[0].startValue).toBe('2026-09-01');
    expect(option.dataZoom?.[0].endValue).toBe('2026-09-03');
    // 回归锚（实测混写时百分比优先、日期锚被忽略、窗口锁死全量 → 放大失效）
    expect((option.dataZoom?.[0] as { start?: number }).start).toBeUndefined();
    expect((option.dataZoom?.[0] as { end?: number }).end).toBeUndefined();
    rerender(<CandlestickChart model={baseModel} />);
    const option2 = renderOption();
    expect(option2.dataZoom?.[0].startValue).toBeUndefined();
    expect(option2.dataZoom?.[0].endValue).toBeUndefined();
    expect((option2.dataZoom?.[0] as { start?: number }).start).toBe(0);
    expect((option2.dataZoom?.[0] as { end?: number }).end).toBe(100);
  });

  it('成交量 y 轴可见：标签量级缩写（万/亿手）、splitNumber 2、保留暗色轴样式', () => {
    const model: CandlestickChartViewModel = {
      ...drawModel,
      volume: drawDates.map(() => 1e6),
      ma: [{ period: 5, values: drawDates.map(() => 3050) }],
    };
    render(<CandlestickChart model={model} />);
    const yAxes = renderOption().yAxis as Array<{
      axisLabel?: { show?: boolean; formatter?: (v: number) => string };
      splitNumber?: number;
      splitLine?: unknown;
    }>;
    const volumeAxis = yAxes[1];
    expect(volumeAxis.axisLabel?.show).toBeUndefined(); // 不再隐藏
    expect(volumeAxis.axisLabel?.formatter?.(1e6)).toBe('100.00万手');
    expect(volumeAxis.splitNumber).toBe(2);
    expect(volumeAxis.splitLine).toBeDefined();
  });

  it('onDataZoom 传入时挂 datazoom 事件、索引→日期换算上报、等值去重；未传时不挂 onEvents', () => {
    const onDataZoom = vi.fn();
    const { rerender } = render(<CandlestickChart model={baseModel} onDataZoom={onDataZoom} />);
    const props = renderProps();
    expect(props.onEvents?.datazoom).toBeTypeOf('function');
    // 第二参数假实例：getOption 返回索引数字（实测 category 轴读回的是索引而非日期串）
    const fakeInstance = { getOption: () => ({ dataZoom: [{ type: 'inside', startValue: 1, endValue: 2 }] }) };
    const handler = props.onEvents!.datazoom as (p: unknown, i: unknown) => void;
    handler(undefined, fakeInstance);
    expect(onDataZoom).toHaveBeenCalledWith({ start: '2026-09-02', end: '2026-09-03' });
    // 等值去重：同键不再上报
    handler(undefined, fakeInstance);
    expect(onDataZoom).toHaveBeenCalledTimes(1);
    // 新窗口再上报
    handler(undefined, { getOption: () => ({ dataZoom: [{ type: 'inside', startValue: 0, endValue: 2 }] }) });
    expect(onDataZoom).toHaveBeenCalledTimes(2);
    expect(onDataZoom).toHaveBeenLastCalledWith({ start: '2026-09-01', end: '2026-09-03' });
    // 未传 onDataZoom → 不挂 datazoom 事件（updateAxisPointer/legendselectchanged 为 §3.7 恒挂事件）
    rerender(<CandlestickChart model={baseModel} />);
    expect(renderProps().onEvents?.datazoom).toBeUndefined();
  });

  it('option 含 animation:false（数据扩展重建不闪、不产生动画重绘）', () => {
    render(<CandlestickChart model={baseModel} />);
    expect(renderOption().animation).toBe(false);
  });

  // ---- 画线渲染（§3.6.3 验证 1/3/4）----

  it('画线覆盖层渲染：trend/hline 线段像素与样式、hline 价格标签、text 文本', () => {
    setupFakeInstance();
    const hline: Drawing = { id: 'h1', kind: 'hline', p1: { date: drawDates[1], price: 3050 } };
    const text1: Drawing = { id: 'x1', kind: 'text', pos: { date: drawDates[2], price: 3030 }, text: '支撑位' };
    render(
      <CandlestickChart model={drawModel} drawings={[trend1, hline, text1]} onDrawingsChange={vi.fn()} />,
    );
    fireChartReady(); // 实例就绪 → tick 重渲染 → layout effect 读视口 → 覆盖层出图
    const lines = overlayLines();
    const texts = overlayTexts();
    expect(lines).toHaveLength(2);
    // trend (3,3000)-(5,3100)：像素 ((3+0.5)×60, 200)-((5+0.5)×60, 100) = (210,200)-(330,100)
    expect(lines[0]).toHaveAttribute('x1', '210');
    expect(lines[0]).toHaveAttribute('y1', '200');
    expect(lines[0]).toHaveAttribute('x2', '330');
    expect(lines[0]).toHaveAttribute('y2', '100');
    expect(lines[0]).toHaveAttribute('stroke', '#38bdf8');
    expect(lines[0]).toHaveAttribute('stroke-width', '1.5');
    // hline 3050：窗口半开带两端 −0.5/9.5 → 像素 (0,150)-(600,150)；价格标签在左端上方
    expect(lines[1]).toHaveAttribute('x1', '0');
    expect(lines[1]).toHaveAttribute('y1', '150');
    expect(lines[1]).toHaveAttribute('x2', '600');
    expect(lines[1]).toHaveAttribute('y2', '150');
    expect(texts[0]).toHaveTextContent('3050.00');
    expect(texts[0]).toHaveAttribute('x', '0');
    expect(texts[0]).toHaveAttribute('y', '146');
    // text 标注：锚点 (2, 3030) → 像素 ((2+0.5)×60, 170) = (150, 170)，文本在锚点上方 12px
    expect(texts[1]).toHaveTextContent('支撑位');
    expect(texts[1]).toHaveAttribute('x', '150');
    expect(texts[1]).toHaveAttribute('y', '158');
  });

  it('小数锚点 x 渲染：线段精确落在按点像素（回归锚：线的起点 = 鼠标的起点）', () => {
    setupFakeInstance();
    const frac: Drawing = {
      id: 'f1', kind: 'trend',
      p1: { date: drawDates[3], price: 3000, x: 3.2 },
      p2: { date: drawDates[5], price: 3100, x: 5.4 },
    };
    render(<CandlestickChart model={drawModel} drawings={[frac]} onDrawingsChange={vi.fn()} />);
    fireChartReady();
    const lines = overlayLines();
    expect(lines).toHaveLength(1);
    // 小数 x 直算像素：(3.2+0.5)×60 = 222、(5.4+0.5)×60 = 354（非 bar 中心 210/330）
    expect(lines[0]).toHaveAttribute('x1', '222');
    expect(lines[0]).toHaveAttribute('x2', '354');
  });

  it('真 echarts 实例回归锚：viewport 读取 → 覆盖层出图（拆引用裸调 getComponent 会丢 this 抛 TypeError）', () => {
    setupFakeInstance();
    // jsdom 无 canvas：zrender 布局 measureText 走 getContext——桩一个极简 2d 上下文
    const dummyCtx = new Proxy({}, {
      get: (_target, prop) => {
        if (prop === 'measureText') return (text: string) => ({ width: String(text).length * 7 });
        if (typeof prop !== 'string') return undefined;
        return () => {};
      },
      set: () => true,
    }) as unknown as CanvasRenderingContext2D;
    HTMLCanvasElement.prototype.getContext = vi.fn(() => dummyCtx) as never;

    render(<CandlestickChart model={drawModel} drawings={[trend1]} onDrawingsChange={vi.fn()} />);
    // 用真实 echarts SSR 实例替换 fake：覆盖层 viewport 读取（grid rect + yAxis extent）走真 API。
    // 2026-09-16 实锤：假实例的 getComponent 是箭头函数，拆引用裸调不炸；真实例是类方法，
    // 裸调读 this._componentsMap 抛 TypeError——viewport 永远 null、画线功能整体静默失效
    const chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width: 600, height: 460 });
    chart.setOption(renderOption() as unknown as echarts.EChartsOption);
    act(() => {
      renderProps().onChartReady?.(chart);
    });
    const lines = overlayLines();
    expect(lines).toHaveLength(1); // trend1 出图
    expect(Number(lines[0].getAttribute('x1'))).toBeGreaterThan(0);
    expect(Number(lines[0].getAttribute('y1'))).toBeGreaterThan(0);
  });

  it('无 drawings 覆盖层无线段；无 onDrawingsChange → 无工具栏（概念卡回归）', () => {
    setupFakeInstance();
    render(<CandlestickChart model={drawModel} />);
    const option = renderOption();
    expect(option.series[0].markLine).toBeUndefined();
    expect(option.series[0].markPoint).toBeUndefined();
    expect(screen.queryByText('画线')).not.toBeInTheDocument();
    expect(document.querySelector('svg[data-testid="drawing-overlay"]')).toBeNull();
  });

  // ---- 参考价横线（止损/止盈，数据驱动非画线工具）----

  it('参考价横线：无画线工具也渲染覆盖层——虚线贯穿窗口全宽 + 左端标签与配色', () => {
    setupFakeInstance();
    render(
      <CandlestickChart
        model={drawModel}
        referenceLines={[
          { price: 2950, label: '止损 2950.00', color: '#22c55e' },
          { price: 3150, label: '止盈 3150.00', color: '#ef4444' },
        ]}
      />,
    );
    fireChartReady();
    const svg = document.querySelector('svg[data-testid="drawing-overlay"]');
    expect(svg).not.toBeNull();
    const dashed = Array.from(svg!.querySelectorAll('line[stroke-dasharray]'));
    expect(dashed).toHaveLength(2);
    // 2950 → y = 3200−2950 = 250；全窗宽 0–600（半开带 −0.5/9.5）
    expect(dashed[0]).toHaveAttribute('x1', '0');
    expect(dashed[0]).toHaveAttribute('y1', '250');
    expect(dashed[0]).toHaveAttribute('x2', '600');
    expect(dashed[0]).toHaveAttribute('y2', '250');
    expect(dashed[0]).toHaveAttribute('stroke', '#22c55e');
    expect(dashed[0]).toHaveAttribute('stroke-dasharray', '4 4');
    // 3150 → y = 3200−3150 = 50
    expect(dashed[1]).toHaveAttribute('y1', '50');
    expect(dashed[1]).toHaveAttribute('stroke', '#ef4444');
    const texts = Array.from(svg!.querySelectorAll('text'));
    expect(texts.map((t) => t.textContent)).toEqual(['止损 2950.00', '止盈 3150.00']);
    expect(texts[0]).toHaveAttribute('x', '0');
    expect(texts[0]).toHaveAttribute('y', '246');
  });

  it('参考价越出真实 extent 不渲染；y 轴注入函数形式 min/max（扩展自动 extent）', () => {
    setupFakeInstance();
    render(
      <CandlestickChart model={drawModel} referenceLines={[{ price: 3300, label: '止盈' }]} />,
    );
    fireChartReady();
    // 3300 > 假实例真实 extent 3200 → 覆盖层越界不渲染（假实例 extent 不受 option 影响）
    const svg = document.querySelector('svg[data-testid="drawing-overlay"]')!;
    expect(svg.querySelectorAll('line[stroke-dasharray]')).toHaveLength(0);
    // option 主图 y 轴注入 min/max 回调
    const yAxis = renderOption().yAxis as { min?: unknown; max?: unknown } | Array<{ min?: unknown; max?: unknown }>;
    const main = Array.isArray(yAxis) ? yAxis[0] : yAxis;
    const min = main.min as (v: { min: number }) => number;
    const max = main.max as (v: { max: number }) => number;
    expect(min({ min: 2950 })).toBe(2950); // 数据域更小 → 保留
    expect(max({ max: 3150 })).toBe(3300); // 参考价更大 → 扩展
  });

  it('真 echarts SSR：参考价扩展主图 y 轴 extent（scale:true + 函数形式 min/max 生效）', () => {
    // jsdom 无 canvas：zrender 布局 measureText 走 getContext——桩一个极简 2d 上下文
    const dummyCtx = new Proxy({}, {
      get: (_target, prop) => {
        if (prop === 'measureText') return (text: string) => ({ width: String(text).length * 7 });
        if (typeof prop !== 'string') return undefined;
        return () => {};
      },
      set: () => true,
    }) as unknown as CanvasRenderingContext2D;
    HTMLCanvasElement.prototype.getContext = vi.fn(() => dummyCtx) as never;

    render(<CandlestickChart model={drawModel} referenceLines={[{ price: 4000, label: '止盈' }]} />);
    const chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width: 600, height: 460 });
    chart.setOption(renderOption() as unknown as echarts.EChartsOption);
    const model = (
      chart as unknown as { getModel: () => { getComponent: (k: string, i: number) => unknown } }
    ).getModel();
    const axis = model.getComponent('yAxis', 0) as {
      axis?: { scale?: { getExtent?: () => number[] } };
    } | null;
    const [lo, hi] = axis?.axis?.scale?.getExtent?.() ?? [NaN, NaN];
    expect(hi).toBeGreaterThanOrEqual(4000); // 参考价扩展轴域
    expect(lo).toBeLessThanOrEqual(2950); // 数据域保留（drawModel 低点 2950）
  });

  // ---- 画线交互（§3.6.3 验证 5/6）----

  it('绘制 trend：mousedown→mousemove→mouseup 提交新画线；起点精确落点；mouseup 出 grid 取消', () => {
    setupFakeInstance();
    render(<DrawingHarness model={drawModel} initial={[]} />);
    fireChartReady();
    fireEvent.click(screen.getByText('画线')); // 进入 draw 模式（默认趋势线）

    zrFire('mousedown', 222, 200); // 非 bar 中心像素：锚点小数 x = 3.2、price 3000
    // 同 bar 内拖拽（回归锚：按下后立即出线——锚点精确像素→鼠标原始像素，连续跟手不空窗）
    zrFire('mousemove', 240, 220);
    expect(overlayPreview()).toHaveAttribute('x1', '222');
    expect(overlayPreview()).toHaveAttribute('y1', '200');
    expect(overlayPreview()).toHaveAttribute('x2', '240');
    expect(overlayPreview()).toHaveAttribute('y2', '220');
    zrFire('mousemove', 330, 100); // 跨 bar 拖拽：终点跟随鼠标原始像素
    // 起点精确落点回归锚（核心）：预览起点 = 按点像素 (222,200) 自身、钉死不飘——
    // 不再是 bar 中心 (210,200)；mouseup 提交后渲染即此点
    expect(overlayPreview()).toHaveAttribute('x1', '222');
    expect(overlayPreview()).toHaveAttribute('x2', '330');
    zrFire('mouseup', 330, 100); // idx 5、price 3100、小数 x 5
    // 提交后覆盖层渲染（harness 回填 drawings）：起点 = 按点像素 (222,200)——线的起点就是鼠标的起点
    const committed = overlayLines();
    expect(committed).toHaveLength(1);
    expect(committed[0]).toHaveAttribute('x1', '222');
    expect(committed[0]).toHaveAttribute('y1', '200');
    expect(committed[0]).toHaveAttribute('x2', '330');
    expect(committed[0]).toHaveAttribute('y2', '100');
    expect(overlayPreview()).toBeNull(); // 提交后预览清除

    // mouseup 出 grid → 取消本次绘制
    zrFire('mousedown', 210, 200);
    zrFire('mouseup', 700, 100); // x=700 超出 grid 矩形 600
    expect(overlayLines()).toHaveLength(1); // 不追加
  });

  it('绘制预览按线型：ray 锚点→主图右缘（斜率跟手）、hline 锚点价格全窗宽；起点恒钉锚点', () => {
    setupFakeInstance();
    render(<CandlestickChart model={drawModel} drawings={[]} onDrawingsChange={vi.fn()} />);
    fireChartReady();
    fireEvent.click(screen.getByText('画线'));
    fireEvent.click(screen.getByText('射线'));
    zrFire('mousedown', 210, 200); // 锚点 fx 3.0、price 3000 → 像素 (210,200)
    zrFire('mousemove', 330, 100); // 斜率 (100−200)/(330−210) = −5/6
    // 主图 rect 右缘 x=600：y = 200 − (5/6)×390 = −125 → 截进 grid 顶缘 0
    expect(overlayPreview()).toHaveAttribute('x1', '210');
    expect(overlayPreview()).toHaveAttribute('y1', '200');
    expect(overlayPreview()).toHaveAttribute('x2', '600');
    expect(overlayPreview()).toHaveAttribute('y2', '0');

    fireEvent.click(screen.getByText('水平线'));
    zrFire('mousedown', 270, 200); // 锚点 fx 4.0、price 3000 → 像素 (270,200)
    zrFire('mousemove', 330, 100); // 鼠标位置不影响 hline 预览（提交只取锚点价格）
    expect(overlayPreview()).toHaveAttribute('x1', '0');
    expect(overlayPreview()).toHaveAttribute('y1', '200');
    expect(overlayPreview()).toHaveAttribute('x2', '600');
    expect(overlayPreview()).toHaveAttribute('y2', '200');
  });

  it('draw 模式滚轮缩放不锁（滚轮与 zr 绘制事件不冲突）、拖拽平移保持锁定；退出恢复平移', () => {
    render(<CandlestickChart model={drawModel} drawings={[]} onDrawingsChange={vi.fn()} />);
    expect(renderOption().dataZoom?.[0]).toMatchObject({ zoomOnMouseWheel: true, moveOnMouseMove: true });
    fireEvent.click(screen.getByText('画线')); // 进入 draw 模式
    expect(renderOption().dataZoom?.[0]).toMatchObject({ zoomOnMouseWheel: true, moveOnMouseMove: false });
    fireEvent.click(screen.getByText('完成')); // 退出 → view 恢复拖拽平移
    expect(renderOption().dataZoom?.[0]).toMatchObject({ zoomOnMouseWheel: true, moveOnMouseMove: true });
  });

  it('绘制 trend 垂直两点（同日）拒绝提交；空 text 拒绝提交', () => {
    setupFakeInstance();
    const onDrawingsChange = vi.fn();
    render(<CandlestickChart model={drawModel} drawings={[]} onDrawingsChange={onDrawingsChange} />);
    fireChartReady();
    fireEvent.click(screen.getByText('画线'));

    // 同日两点：mousedown idx3、mouseup 同 idx3（fx 3.0 与 fx 3.2 都取整到 3）
    zrFire('mousedown', 210, 200);
    zrFire('mouseup', 222, 100);
    expect(onDrawingsChange).not.toHaveBeenCalled();

    // 空 text：标注模式单击 → 输入框出现 → 直接 Enter 空串不提交
    fireEvent.click(screen.getByText('标注'));
    zrFire('mousedown', 150, 200);
    const input = screen.getByRole('textbox');
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onDrawingsChange).not.toHaveBeenCalled();
  });

  it('text 标注：定位 + 输入 + 回车提交；Esc 取消优先于 blur', () => {
    setupFakeInstance();
    const onDrawingsChange = vi.fn();
    render(<CandlestickChart model={drawModel} drawings={[]} onDrawingsChange={onDrawingsChange} />);
    fireChartReady();
    fireEvent.click(screen.getByText('画线'));
    fireEvent.click(screen.getByText('标注'));
    zrFire('mousedown', 150, 200); // fx 2.0、price 3000

    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: '压力位' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onDrawingsChange).toHaveBeenCalledTimes(1);
    const [next] = onDrawingsChange.mock.calls[0] as [Drawing[]];
    expect(next[0]).toMatchObject({ kind: 'text', pos: { date: drawDates[2], price: 3000 }, text: '压力位' });

    // Esc 取消优先于 blur：取消后 blur 不再提交
    fireEvent.click(screen.getByText('标注'));
    zrFire('mousedown', 150, 200);
    const input2 = screen.getByRole('textbox');
    fireEvent.change(input2, { target: { value: '不应提交' } });
    fireEvent.keyDown(input2, { key: 'Escape' });
    fireEvent.blur(input2);
    expect(onDrawingsChange).toHaveBeenCalledTimes(1);
  });

  it('draw 模式命中线身 → 拖动平移、不新建线（画错不用重画，拖一下即到位）', () => {
    setupFakeInstance();
    const onDrawingsChange = vi.fn();
    render(<CandlestickChart model={drawModel} drawings={[trend1]} onDrawingsChange={onDrawingsChange} />);
    fireChartReady();
    fireEvent.click(screen.getByText('画线')); // 进入 draw 模式（默认趋势线）

    // 命中线身（中点 (270,150)，恰在线段上）→ 拖拽预览（虚线）而非开新线
    zrFire('mousedown', 270, 150); // start fx 4.0、price 3050
    zrFire('mousemove', 330, 50);
    expect(overlayPreview()).not.toBeNull(); // 拖拽预览 = 假设提交后的渲染线段
    zrFire('mouseup', 330, 50); // end fx 5.0、price 3150 → delta idx +1、price +100
    // 提交 = 更新 t1（双锚点同步平移），不追加新线
    expect(onDrawingsChange).toHaveBeenCalledTimes(1);
    const [next] = onDrawingsChange.mock.calls[0] as [Drawing[]];
    expect(next).toHaveLength(1);
    expect(next[0]).toMatchObject({
      id: 't1',
      p1: { date: drawDates[4], price: 3100 }, // 3000 + 100
      p2: { date: drawDates[6], price: 3200 }, // 3100 + 100
    });
  });

  it('draw 模式命中端点 → 只移该锚点', () => {
    setupFakeInstance();
    const onDrawingsChange = vi.fn();
    render(<CandlestickChart model={drawModel} drawings={[trend1]} onDrawingsChange={onDrawingsChange} />);
    fireChartReady();
    fireEvent.click(screen.getByText('画线'));

    // 命中端点 1（pixel (210,200)，偏移 3,2 px 在 8px 阈值内）→ 只移 p1
    zrFire('mousedown', 213, 202);
    zrFire('mouseup', 270, 50); // fx 4.0、price 3150
    const [next] = onDrawingsChange.mock.calls[0] as [Drawing[]];
    expect(next).toHaveLength(1);
    expect(next[0]).toMatchObject({
      id: 't1',
      p1: { date: drawDates[4], price: 3150 }, // 只移 p1（落点回 bar 中心）
      p2: { date: drawDates[5], price: 3100 }, // p2 不动
    });
  });

  it('draw 模式拖拽提交后仍在 draw 模式，可继续画第二条', () => {
    setupFakeInstance();
    render(<DrawingHarness model={drawModel} initial={[trend1]} />);
    fireChartReady();
    fireEvent.click(screen.getByText('画线'));

    // 先拖走 t1：线身命中 → 平移（p1 → bar4/3100 像素 (270,100)，p2 → bar6/3200 像素 (390,0)）
    zrFire('mousedown', 270, 150);
    zrFire('mouseup', 330, 50);
    expect(overlayLines()).toHaveLength(1);

    // 点空处（远离已移走的线）→ 正常开新线并提交
    zrFire('mousedown', 150, 200); // fx 2.0、price 3000
    zrFire('mouseup', 210, 100); // fx 3.0、price 3100 → 新 trend 线
    expect(overlayLines()).toHaveLength(2);
    expect(screen.getByText('完成')).toBeInTheDocument(); // 未退出 draw 模式
  });

  it('draw 模式 text 工具命中已有线 → 拖动而非弹标注框', () => {
    setupFakeInstance();
    const onDrawingsChange = vi.fn();
    render(<CandlestickChart model={drawModel} drawings={[trend1]} onDrawingsChange={onDrawingsChange} />);
    fireChartReady();
    fireEvent.click(screen.getByText('画线'));
    fireEvent.click(screen.getByText('标注'));

    zrFire('mousedown', 213, 202); // 命中端点 1 → 拖动优先于标注定位
    expect(screen.queryByRole('textbox')).toBeNull();
    zrFire('mouseup', 270, 50);
    const [next] = onDrawingsChange.mock.calls[0] as [Drawing[]];
    expect(next).toHaveLength(1);
    expect(next[0]).toMatchObject({
      id: 't1',
      kind: 'trend',
      p1: { date: drawDates[4], price: 3150 },
      p2: { date: drawDates[5], price: 3100 },
    });
  });

  it('draw 模式工具栏提示可拖动已有线', () => {
    render(<CandlestickChart model={drawModel} drawings={[]} onDrawingsChange={vi.fn()} />);
    fireEvent.click(screen.getByText('画线'));
    expect(screen.getByText('按住已有线拖动调整 · Esc 退出')).toBeInTheDocument();
  });

  it('编辑：命中端点只移该锚点、选中态 width 3', () => {
    setupFakeInstance();
    const onDrawingsChange = vi.fn();
    render(<CandlestickChart model={drawModel} drawings={[trend1]} onDrawingsChange={onDrawingsChange} />);
    fireChartReady();
    fireEvent.click(screen.getByText('编辑'));

    // 命中端点 2（pixel (330, 100)）：偏移 3,2 px 在 8px 阈值内
    zrFire('mousedown', 333, 102);
    zrFire('mousemove', 390, 0);
    zrFire('mouseup', 390, 0); // fx 6.0、price 3200
    // 提交后 selectedId 保留、draggingId 清除 → 覆盖层重渲染为选中态 width 3（拖拽中该线按
    // id 过滤隐藏、以虚线预览呈现，故断言须在 mouseup 之后）
    const lines = overlayLines();
    expect(lines).toHaveLength(1);
    expect(lines[0]).toHaveAttribute('stroke-width', '3');
    const [next] = onDrawingsChange.mock.calls[0] as [Drawing[]];
    expect(next[0]).toMatchObject({
      id: 't1',
      p1: { date: drawDates[3], price: 3000 }, // p1 不动
      p2: { date: drawDates[6], price: 3200 }, // 只移 p2（拖拽落点回 bar 中心）
    });
  });

  it('编辑：拖到两锚点同日 → 回退（不更新）；线身拖拽双锚点同步平移', () => {
    setupFakeInstance();
    const onDrawingsChange = vi.fn();
    render(<CandlestickChart model={drawModel} drawings={[trend1]} onDrawingsChange={onDrawingsChange} />);
    fireChartReady();
    fireEvent.click(screen.getByText('编辑'));

    // 端点 1（pixel (210, 200)）拖到 fx 5（与 p2 同日）→ 回退
    zrFire('mousedown', 213, 202);
    zrFire('mouseup', 330, 100);
    expect(onDrawingsChange).not.toHaveBeenCalled();

    // 线身命中（中点 (270, 150)，在线段上）→ 双锚点同步平移 delta(idx +1, price +100)
    zrFire('mousedown', 270, 150); // start fx 4.0、price 3050
    zrFire('mouseup', 330, 50); // end fx 5.0、price 3150
    const [next] = onDrawingsChange.mock.calls[0] as [Drawing[]];
    expect(next[0]).toMatchObject({
      p1: { date: drawDates[4], price: 3100 }, // 3000 + 100
      p2: { date: drawDates[6], price: 3200 }, // 3100 + 100
    });
  });

  it('编辑：Delete 键删除选中；Esc 退出模式并取消进行中绘制', () => {
    setupFakeInstance();
    const onDrawingsChange = vi.fn();
    render(<CandlestickChart model={drawModel} drawings={[trend1]} onDrawingsChange={onDrawingsChange} />);
    fireChartReady();
    fireEvent.click(screen.getByText('编辑'));
    zrFire('mousedown', 333, 102); // 选中 t1
    fireEvent.keyDown(document, { key: 'Delete' });
    expect(onDrawingsChange).toHaveBeenCalledWith([]);

    // Esc：退出 edit 模式（工具栏恢复、编辑提示消失）
    fireEvent.click(screen.getByText('编辑'));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByText('Esc 退出')).not.toBeInTheDocument();
  });

  it('橡皮擦：悬停高亮（sky-300/宽3）+ 单击删除命中线 + 空处不删 + Esc 退出', () => {
    setupFakeInstance();
    const onDrawingsChange = vi.fn();
    render(<CandlestickChart model={drawModel} drawings={[trend1]} onDrawingsChange={onDrawingsChange} />);
    fireChartReady();
    fireEvent.click(screen.getByText('橡皮擦'));
    expect(screen.getByText('点击线删除 · Esc 退出')).toBeInTheDocument();

    // 悬停命中线身（trend1 线段上 (270,150)）→ 高亮 sky-300 + 宽 3
    zrFire('mousemove', 270, 150);
    let lines = overlayLines();
    expect(lines[0]).toHaveAttribute('stroke', '#7dd3fc');
    expect(lines[0]).toHaveAttribute('stroke-width', '3');
    // 移开 → 恢复默认
    zrFire('mousemove', 30, 30);
    lines = overlayLines();
    expect(lines[0]).toHaveAttribute('stroke', '#38bdf8');
    expect(lines[0]).toHaveAttribute('stroke-width', '1.5');

    // 单击命中 → 立即删除该线
    zrFire('mousedown', 270, 150);
    expect(onDrawingsChange).toHaveBeenCalledTimes(1);
    expect((onDrawingsChange.mock.calls[0] as [Drawing[]])[0]).toHaveLength(0);

    // 空处点击 → 不删除
    zrFire('mousedown', 30, 30);
    expect(onDrawingsChange).toHaveBeenCalledTimes(1);

    // Esc 退出 eraser 模式
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByText('点击线删除 · Esc 退出')).not.toBeInTheDocument();
  });

  it('橡皮擦删除文字标注（harness 回填 → 覆盖层同步消失）', () => {
    setupFakeInstance();
    const text1: Drawing = { id: 'x1', kind: 'text', pos: { date: drawDates[2], price: 3030 }, text: '支撑位' };
    render(<DrawingHarness model={drawModel} initial={[text1]} />);
    fireChartReady();
    expect(overlayTexts()).toHaveLength(1);
    fireEvent.click(screen.getByText('橡皮擦'));
    zrFire('mousedown', 150, 160); // text 包围盒内（锚点 (150,170)）
    expect(overlayTexts()).toHaveLength(0); // 删除后覆盖层同步消失
  });

  // ---- 图例与读条（§3.7.3 验证 1/2/3/4/5）----

  const fullModel: CandlestickChartViewModel = {
    ...drawModel,
    ma: [5, 10, 20, 60].map((period) => ({ period, values: drawDates.map(() => 3050) })),
    boll: {
      period: 20,
      k: 2,
      mid: drawDates.map(() => 3050),
      upper: drawDates.map(() => 3150),
      lower: drawDates.map(() => 2950),
    },
    macd: { dif: drawDates.map(() => 0.3), dea: drawDates.map(() => 0.2), hist: drawDates.map(() => 0.2) },
  };

  it('图例三行分组（label+value 版）：行 1 开高低收 top 0、行 2 MA+BOLL top 18、行 3 DIF/DEA top 36、icon none、逐项系列色、inactiveColor、left 0/itemWidth 0', () => {
    render(<CandlestickChart model={fullModel} />);
    const legends = renderOption().legend!;
    expect(legends).toHaveLength(3);
    expect(legends[0]).toMatchObject({
      top: 0, left: 0, itemWidth: 0, itemGap: 6, icon: 'none', inactiveColor: '#8b95a1',
    });
    expect(legends[0].data.map((d) => d.name)).toEqual(['开', '高', '低', '收']);
    expect(legends[1].data.map((d) => d.name)).toEqual([
      'MA5', 'MA10', 'MA20', 'MA60', 'BOLL上轨', 'BOLL中轨', 'BOLL下轨',
    ]);
    expect(legends[1].top).toBe(18);
    expect(legends[2].data.map((d) => d.name)).toEqual(['DIF', 'DEA']);
    expect(legends[2].top).toBe(36);
    // 逐项系列色（实测图例文字默认不继承系列色，须显式指定；开高低收按涨跌色）
    const byName = Object.fromEntries(legends.flatMap((l) => l.data.map((d) => [d.name, d.textStyle?.color])));
    expect(byName).toEqual({
      开: '#ef4444', 高: '#ef4444', 低: '#ef4444', 收: '#ef4444', // drawModel 收≥开 → 红
      MA5: '#91cc75', MA10: '#fac858', MA20: '#ee6666', MA60: '#73c0de', // 参考调色板色（2026-09-16）
      'BOLL上轨': '#94a3b8', 'BOLL中轨': '#94a3b8', 'BOLL下轨': '#94a3b8',
      DIF: '#eaf0f8', DEA: '#fbbf24',
    });
  });

  it('图例降级布局：无 MACD 2 行、仅 MACD 2 行（MACD 行提至 top 18）、纯 K 仅行 1；tooltip showContent:false；grid top 24/40/56', () => {
    const { rerender } = render(<CandlestickChart model={fullModel} />);
    expect(renderOption().tooltip).toEqual({ showContent: false, trigger: 'axis' });
    // 无 MACD → 2 行（行 1 + MA/BOLL）
    const { macd: _macd, ...noMacd } = fullModel;
    rerender(<CandlestickChart model={noMacd} />);
    expect(renderOption().legend).toHaveLength(2);
    // 仅 MACD（无 MA/BOLL）→ 2 行：MACD 行提至 top 18（行 2 缺失时不空位）
    rerender(<CandlestickChart model={{ ...drawModel, macd: fullModel.macd }} />);
    expect(renderOption().legend).toHaveLength(2);
    expect(renderOption().legend![1].top).toBe(18);
    // 纯 K → 仅行 1（开高低收）；grid top 24（1 行预算）
    rerender(<CandlestickChart model={drawModel} />);
    expect(renderOption().legend).toHaveLength(1);
    expect(renderOption().legend![0].data.map((d) => d.name)).toEqual(['开', '高', '低', '收']);
    expect((renderOption().grid as GridShape).top).toBe(24);
    // 仅成交量+MA → 2 行 → grid top 40（多 grid 数组取 [0]）
    rerender(<CandlestickChart model={{ ...drawModel, ma: fullModel.ma, volume: drawDates.map(() => 100) }} />);
    expect((renderOption().grid as GridShape[])[0].top).toBe(40);
  });

  it('legendSelected：legendselectchanged 回填并注入 option（各 legend 只注入自己 data 的项），重渲染不回全选；固定展示项点击无效', () => {
    setupFakeInstance();
    render(<CandlestickChart model={fullModel} />);
    fireChartReady();
    // legend 共享同一份全局 selected（实测）；开为纯展示固定项（无系列），点击后必须过滤（不变灰）
    act(() => {
      (renderProps().onEvents!.legendselectchanged as (p: unknown, i: unknown) => void)(undefined, {
        getOption: () => ({
          legend: [
            { selected: { 开: false, MA5: false, DIF: false } },
            { selected: { 开: false, MA5: false, DIF: false } },
            { selected: { 开: false, MA5: false, DIF: false } },
          ],
        }),
      });
    });
    const legends = renderOption().legend!;
    expect(legends[0].selected).toEqual({}); // 开 被过滤（点击无效、不变灰）
    expect(legends[1].selected).toEqual({ MA5: false }); // 行 2 只注入自己 data 的项
    expect(legends[2].selected).toEqual({ DIF: false });
  });

  it('图例 label+value 与 ChartCore memo：updateAxisPointer 驱动、hide 回落窗口末根、hover 不重渲染内核', () => {
    setupFakeInstance();
    const readoutModel: CandlestickChartViewModel = {
      xAxisData: drawDates,
      ohlc: drawDates.map((_d, i) =>
        (i === 2 ? [3002, 2998, 2950, 3150] : [3000 + i, 3100 + i, 2950 + i, 3150 + i]) as [number, number, number, number],
      ),
      volume: drawDates.map(() => 1e6),
      ma: [{ period: 5, values: drawDates.map((_d, i) => 3005 + i) }],
      macd: { dif: drawDates.map(() => 0.3), dea: drawDates.map(() => 0.2), hist: drawDates.map(() => 0.2) },
    };
    render(<CandlestickChart model={readoutModel} visibleRange={{ start: drawDates[4], end: drawDates[7] }} />);
    fireChartReady();
    // 未悬浮：图例值 = 可见窗口末根（drawDates[7]），非数据集末尾（drawDates[9]）
    const legends = renderOption().legend!;
    const row1Formatter = legends[0].formatter as (n: string) => string;
    const row2Formatter = legends[1].formatter as (n: string) => string;
    const row3Formatter = legends[2].formatter as (n: string) => string;
    expect(row1Formatter('开')).toBe('开 3007.00');
    expect(row1Formatter('成交量')).toBe('量 100.00万手'); // formatter 入参 = data 名（成交量）
    expect(row2Formatter('MA5')).toBe('MA5 3012.00'); // 3005 + 7
    expect(row3Formatter('DIF')).toBe('DIF 0.30');
    const rendersBefore = mockState.renderCount;
    // updateAxisPointer → hoverIdx = 2（该根收<开 → 绿），图例经 merge 刷新（不重建 option）
    const fireAxis = renderProps().onEvents!.updateAxisPointer as (p: unknown) => void;
    act(() => {
      fireAxis({ axesInfo: [{ value: 2 }] });
    });
    const setOptionMock = mockState.fakeInstance.setOption as Mock;
    const legendMerge = setOptionMock.mock.calls
      .map((call) => call[0] as { legend?: Array<{ formatter?: (n: string) => string }> })
      .find((arg) => arg.legend)!;
    const mergedRow1 = legendMerge.legend![0].formatter!;
    const mergedRow2 = legendMerge.legend![1].formatter!;
    expect(mergedRow1('开')).toBe('开 3002.00');
    expect(mergedRow1('收')).toBe('收 2998.00');
    expect(mergedRow2('MA5')).toBe('MA5 3007.00'); // 3005 + 2
    // ChartCore memo 守卫：hover 变化未重渲染内核（renderCount 不变 → 无 option 重建风暴；
    // 图例值经定向 merge 更新）
    expect(mockState.renderCount).toBe(rendersBefore);
    // hide（实测 mouseleave 再派发 axesInfo: []）→ 回落窗口末根
    act(() => {
      fireAxis({ axesInfo: [] });
    });
    const legendCalls2 = setOptionMock.mock.calls
      .map((call) => call[0] as { legend?: Array<{ formatter?: (n: string) => string }> })
      .filter((arg) => arg.legend);
    const legendMerge2 = legendCalls2.at(-1)!;
    expect(legendMerge2.legend![0].formatter!('开')).toBe('开 3007.00');
  });

  it('真 echarts SSR：双 legend 渲染无异常、图例文字 fill 含系列色（无可见标记图形）', () => {
    // jsdom 无 canvas：zrender 布局 measureText 走 getContext——桩一个极简 2d 上下文
    const dummyCtx = new Proxy({}, {
      get: (_target, prop) => {
        if (prop === 'measureText') return (text: string) => ({ width: String(text).length * 7 });
        if (typeof prop !== 'string') return undefined;
        return () => {};
      },
      set: () => true,
    }) as unknown as CanvasRenderingContext2D;
    HTMLCanvasElement.prototype.getContext = vi.fn(() => dummyCtx) as never;

    render(<CandlestickChart model={fullModel} />);
    const chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width: 600, height: 420 });
    chart.setOption(renderOption() as unknown as echarts.EChartsOption);
    const svg = chart.renderToSVGString();
    expect(svg).toContain('MA5');
    expect(svg).toContain('BOLL上'); // formatter 展示缩写名（data 名为 BOLL上轨）
    expect(svg).toContain('DIF');
    expect(svg).toContain('#91cc75'); // MA5 系列色文字 fill（参考调色板；icon none 无可见标记，空 d path 属已知）
    expect(svg).toContain('#fbbf24'); // DEA 系列色
  });

  // ---- Code Review 回归（2026-09-14 CR 一轮 findings）----

  it('zr handler 不累积（F1 回归）：draw 模式多次重渲染后每事件仍恰一个活跃 handler；退出后清空', () => {
    setupFakeInstance();
    render(<CandlestickChart model={drawModel} drawings={[]} onDrawingsChange={vi.fn()} />);
    fireChartReady();
    fireEvent.click(screen.getByText('画线'));
    expect(mockState.zrHandlers.mousedown).toHaveLength(1);
    // 多次重渲染（hover 逐像素）后活跃 handler 仍恰一个（zrender 仅按函数引用去重、
    // 新闭包会累积——mock 数组长度捕获该回归：不做 off 清空会涨到 4）
    const fireAxis = renderProps().onEvents!.updateAxisPointer as (p: unknown) => void;
    act(() => { fireAxis({ axesInfo: [{ value: 1 }] }); });
    act(() => { fireAxis({ axesInfo: [{ value: 2 }] }); });
    act(() => { fireAxis({ axesInfo: [{ value: 3 }] }); });
    expect(mockState.zrHandlers.mousedown).toHaveLength(1);
    // 退出 draw → 按引用 off：view 模式 mousedown 无自有 handler（旧闭包不得残留误建画线）
    fireEvent.click(screen.getByText('完成'));
    expect(mockState.zrHandlers.mousedown ?? []).toHaveLength(0);
  });

  it('按引用精确 off 不误删外来 handler（R2-1 回归：ECharts 内部监听存活）', () => {
    setupFakeInstance();
    // 模拟 ECharts 内部挂在同一 zr Eventful 上的全局监听（axisPointer/tooltip mousemove、
    // inside dataZoom roam）——裸 zr.off(event) 会把它们连同自有 handler 一起永久删除。
    // 三个事件都预置（若将来只把某事件改回裸 off，对应断言即失败）
    const foreigners = { mousedown: vi.fn(), mousemove: vi.fn(), mouseup: vi.fn() };
    for (const event of ['mousedown', 'mousemove', 'mouseup'] as const) {
      mockState.zrHandlers[event] = [foreigners[event]];
    }
    render(<CandlestickChart model={drawModel} drawings={[]} onDrawingsChange={vi.fn()} />);
    fireChartReady();
    fireEvent.click(screen.getByText('画线'));
    // 多轮重渲染后：三事件外来 handler 仍在、自有 handler 恰一个
    const fireAxis = renderProps().onEvents!.updateAxisPointer as (p: unknown) => void;
    act(() => { fireAxis({ axesInfo: [{ value: 1 }] }); });
    act(() => { fireAxis({ axesInfo: [{ value: 2 }] }); });
    for (const event of ['mousedown', 'mousemove', 'mouseup'] as const) {
      const handlers = mockState.zrHandlers[event] ?? [];
      expect(handlers.filter((h) => h === foreigners[event])).toHaveLength(1); // 外来存活
      expect(handlers.filter((h) => h !== foreigners[event])).toHaveLength(1); // 自有恰一个
    }
    // 退出 draw：自有移除、外来仍在
    fireEvent.click(screen.getByText('完成'));
    for (const event of ['mousedown', 'mousemove', 'mouseup'] as const) {
      const handlers = mockState.zrHandlers[event] ?? [];
      expect(handlers).toHaveLength(1);
      expect(handlers[0]).toBe(foreigners[event]);
    }
  });

  it('仅 MACD 布局：MACD 副图 yAxis 保持价格轴样式（F3 回归：不被成交量轴样式误套）', () => {
    const model: CandlestickChartViewModel = {
      ...baseModel,
      ma: [{ period: 5, values: [null, null, 1.5] }],
      boll: {
        period: 20, k: 2,
        mid: [null, null, 2], upper: [null, null, 2.4], lower: [null, null, 1.6],
      },
      macd: { dif: [null, null, 0.3], dea: [null, null, 0.2], hist: [null, null, 0.2] },
    };
    render(<CandlestickChart model={model} />);
    const yAxes = renderOption().yAxis as Array<{
      axisLabel?: { show?: boolean; color?: string };
      splitLine?: unknown;
    }>;
    expect(yAxes).toHaveLength(2);
    // 索引 1 = MACD grid（无成交量）：价格轴样式——刻度可见、分割线存在（volumeYAxis 才隐藏）
    expect(yAxes[1].axisLabel?.show).toBeUndefined();
    expect(yAxes[1].splitLine).toBeDefined();
  });

  it('MA/BOLL + MACD 无成交量：图例三行 → 主图 top 56（F4 回归：按图例行数而非副图数）', () => {
    const model: CandlestickChartViewModel = {
      ...drawModel,
      ma: fullModel.ma,
      boll: fullModel.boll,
      macd: fullModel.macd,
    };
    render(<CandlestickChart model={model} />);
    const grids = renderOption().grid as GridShape[];
    expect(grids).toHaveLength(2); // 主图 + MACD
    expect(grids[0].top).toBe(56); // 三行图例（行 1 开高低收恒在）→ 56
  });

  it('渲染用真实 extent 不夹带内价格（§3.6.3 验证 4 回归锚：守护过度夹取）', () => {
    setupFakeInstance();
    // p2 价格 3180：并集 extent 顶（3150）外、真实 extent [2900,3200] 内 → 渲染 y = 3200−3180 = 20
    const bandTrend: Drawing = {
      id: 'b1', kind: 'trend',
      p1: { date: drawDates[1], price: 3120 },
      p2: { date: drawDates[3], price: 3180 },
    };
    render(<CandlestickChart model={drawModel} drawings={[bandTrend]} onDrawingsChange={vi.fn()} />);
    fireChartReady(); // 实例就绪 → tick 重渲染 → 覆盖层以真实 extent 出图
    const lines = overlayLines();
    expect(lines).toHaveLength(1);
    // 带内端点按原价渲染（不被并集 extent [2950,3150] 夹到 3150）：y2 = 3200−3180 = 20
    expect(lines[0]).toHaveAttribute('x1', '90'); // (1+0.5)×60
    expect(lines[0]).toHaveAttribute('y1', '80'); // 3200−3120
    expect(lines[0]).toHaveAttribute('x2', '210');
    expect(lines[0]).toHaveAttribute('y2', '20');
  });

  it('编辑拖拽提交后覆盖层重渲染为选中态 width 3（§3.6.3 验证 6 回归锚：渲染与命中同源几何）', () => {
    setupFakeInstance();
    render(<CandlestickChart model={drawModel} drawings={[trend1]} onDrawingsChange={vi.fn()} />);
    fireChartReady();
    fireEvent.click(screen.getByText('编辑'));
    // 拖拽中该线按 id 过滤隐藏（覆盖层不渲染、以虚线预览呈现），选中态断言须在 mouseup 之后：
    // draggingId 清除、selectedId 保留 → 覆盖层以 selectedId 重渲染
    zrFire('mousedown', 333, 102);
    zrFire('mousemove', 390, 0);
    zrFire('mouseup', 390, 0);
    const lines = overlayLines();
    expect(lines).toHaveLength(1);
    expect(lines[0]).toHaveAttribute('stroke-width', '3');
  });
});

it('theme toggling merges colors without replaying dataZoom or removing drawings', () => {
  act(() => useUiPreferenceStore.setState({ themeMode: 'dark' }));
  render(<CandlestickChart model={baseModel} drawings={[]} onDrawingsChange={() => {}} />);
  setupFakeInstance();
  fireChartReady();
  const option = renderOption();
  const setOption = mockState.fakeInstance.setOption as Mock;
  setOption.mockClear();
  act(() => useUiPreferenceStore.setState({ themeMode: 'light' }));
  expect(renderOption()).toBe(option); // Full notMerge option is unchanged.
  expect(setOption).toHaveBeenCalled();
  for (const [patch, options] of setOption.mock.calls) { expect(patch.dataZoom).toBeUndefined(); expect(options.notMerge).toBe(false); }
  const colorPatch = setOption.mock.calls.find(([patch]) => patch.xAxis)?.[0];
  expect(colorPatch.xAxis[0].axisLabel.color).toBe('#636978');
  act(() => useUiPreferenceStore.setState({ themeMode: 'dark' }));
});
