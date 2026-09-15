import { act, fireEvent, render, screen } from '@testing-library/react';
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
  dataZoom?: { type?: string; xAxisIndex?: number[]; startValue?: unknown; endValue?: unknown }[];
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
  mockState.fakeInstance = {
    getZr: () => mockState.zr!,
    getModel: () => ({
      getComponent: (kind: string, _idx: number) => {
        if (kind === 'grid') return { coordinateSystem: { getRect: () => ({ x: 0, y: 0, width: 600, height: 420 }) } };
        if (kind === 'yAxis') return { axis: { scale: { getExtent: () => [2900, 3200] } } };
        return null;
      },
    }),
    convertFromPixel: (_finder: unknown, [x, y]: [number, number]) => [Math.round(x / 100), 3200 - y],
    convertToPixel: (_finder: unknown, [idx, price]: [number, number]) => [idx * 100, 3200 - price],
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

// SSR 用例桩掉 canvas getContext，afterEach 还原（jsdom 无 canvas 的隔离桩）
const originalCanvasGetContext = HTMLCanvasElement.prototype.getContext;

// merge 断言用命名类型（TSX 里内联 as {…} + 泛型易被解析器误读）
type CoordLine = Array<{ coord: [number, number] }>;
type LineStyleLine = Array<{ lineStyle?: { width?: number } }>;
interface MergeArgShape {
  series: Array<{ markLine?: { data: CoordLine[] } }>;
}
interface MergeWidthArgShape {
  series: Array<{ markLine?: { data: LineStyleLine[] } }>;
}

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

  it('MA/BOLL 细线半透明：可见系列 lineStyle {width:1, opacity:0.5}，带宽隐藏系列不变', () => {
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
    const visible = option.series.filter(
      (s) => s.name && !s.stack && ['MA5', 'MA10', 'MA20', 'MA60', 'BOLL上轨', 'BOLL中轨', 'BOLL下轨'].includes(s.name),
    );
    for (const s of visible) {
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
    // 布局验算（§3.1.1 H=460 表，2026-09-15 三行图例）：主图 top 56 + bottom '50%'、成交量 '52%'/'26%'、MACD '76%'
    const grids = option.grid as GridShape[];
    expect(grids[0]).toMatchObject({ top: 56, bottom: '50%' });
    expect(grids[1]).toMatchObject({ top: '52%', bottom: '26%' });
    expect(grids[2]).toMatchObject({ top: '76%', bottom: 34 });
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

  it('画线渲染：trend 嵌套两点形态与坐标映射、hline 窗口两端、样式覆盖、text markPoint', () => {
    const hline: Drawing = { id: 'h1', kind: 'hline', p1: { date: drawDates[1], price: 3050 } };
    const text1: Drawing = { id: 'x1', kind: 'text', pos: { date: drawDates[2], price: 3030 }, text: '支撑位' };
    render(
      <CandlestickChart model={drawModel} drawings={[trend1, hline, text1]} onDrawingsChange={vi.fn()} />,
    );
    const option = renderOption();
    const markLine = option.series[0].markLine!.data as Array<Array<{
      coord: [number, number];
      symbol?: string;
      lineStyle?: { type?: string; width?: number; color?: string };
      label?: { formatter?: () => string };
    }>>;
    expect(markLine).toHaveLength(2);
    // trend：markLine.data 项本身就是两点数组（嵌套形态），坐标 = 锚点日期索引 + 价格；
    // per-item 样式挂在首元素上
    const trendItem = markLine[0];
    expect(trendItem.map((p) => p.coord)).toEqual([[3, 3000], [5, 3100]]);
    expect(trendItem[0].symbol).toBe('none');
    expect(trendItem[0].lineStyle).toMatchObject({ type: 'solid', width: 1.5, color: '#38bdf8' });
    // hline：两端 = 窗口左右缘索引（与锚点日期无关），默认标签显示价格（首元素 label）
    const hlineItem = markLine[1];
    expect(hlineItem.map((p) => p.coord)).toEqual([[0, 3050], [9, 3050]]);
    expect(hlineItem[0].label?.formatter?.()).toBe('3050.00');
    // text markPoint：circle + symbolSize 0 + 函数 formatter
    const textMark = option.series[0].markPoint!.data[0];
    expect(textMark.coord).toEqual([2, 3030]);
    expect(textMark.symbol).toBe('circle');
    expect((textMark.label!.formatter as () => string)()).toBe('支撑位');
  });

  it('画线渲染：射线右缘外推 + 跳过规则（trend 窗口同侧外/ray p1 在窗口右侧）+ y 求交收进 extent', () => {
    const ray: Drawing = {
      id: 'r1', kind: 'ray',
      p1: { date: drawDates[1], price: 3050 },
      p2: { date: drawDates[2], price: 3060 },
    };
    const trendOutside: Drawing = {
      id: 'o1', kind: 'trend',
      p1: { date: drawDates[0], price: 3000 },
      p2: { date: drawDates[1], price: 3010 },
    };
    const rayRight: Drawing = {
      id: 'o2', kind: 'ray',
      p1: { date: drawDates[8], price: 3000 },
      p2: { date: drawDates[9], price: 3010 },
    };
    const crossing: Drawing = {
      id: 'c1', kind: 'trend',
      p1: { date: drawDates[1], price: 5000 },
      p2: { date: drawDates[8], price: 1000 },
    };
    render(
      <CandlestickChart
        model={drawModel}
        drawings={[ray, trendOutside, rayRight, crossing]}
        onDrawingsChange={vi.fn()}
        visibleRange={{ start: drawDates[4], end: drawDates[7] }}
      />,
    );
    const option = renderOption();
    const markLine = option.series[0].markLine!.data as Array<Array<{ coord: [number, number] }>>;
    // trendOutside 两端点均在窗口左侧（同一侧）→ 跳过；rayRight p1 在窗口右侧 → 跳过；
    // ray + crossing 保留 → 2 条
    expect(markLine).toHaveLength(2);
    // ray：起点 = max(p1, 窗口左缘)=4、终点 = 窗口右缘外推 7；斜率 = 10/类目
    expect(markLine[0].map((p) => p.coord)).toEqual([
      [4, 3080], // 3050 + 10×(4−1)
      [7, 3110], // 3050 + 10×(7−1)
    ]);
    // crossing（5000→1000）：x 截断后两端点仍在并集 extent [2950,3150] 外、中段横穿 → y 求交收进边界。
    // x 断言（回归锚：求交参数化基准必须是起点，错取终点会算出 [4,3150],[2,2950] 的斜率反转）
    expect(markLine[1].map((p) => p.coord)).toEqual([[4, 3150], [5, 2950]]);
  });

  it('无 drawings → 无 markLine/markPoint；无 onDrawingsChange → 无工具栏（概念卡回归）', () => {
    render(<CandlestickChart model={drawModel} />);
    const option = renderOption();
    expect(option.series[0].markLine).toBeUndefined();
    expect(option.series[0].markPoint).toBeUndefined();
    expect(screen.queryByText('画线')).not.toBeInTheDocument();
  });

  it('真 echarts SSR 渲染（B1/B2/M3 回归锚）：嵌套 markLine 出图、文字 label 渲染、无异常', () => {
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

    const text1: Drawing = { id: 'x1', kind: 'text', pos: { date: drawDates[2], price: 3030 }, text: '支撑位' };
    render(<CandlestickChart model={drawModel} drawings={[trend1, text1]} onDrawingsChange={vi.fn()} />);
    const chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width: 600, height: 360 });
    chart.setOption(renderOption() as unknown as echarts.EChartsOption);
    const svg = chart.renderToSVGString();
    expect(svg).toContain('<path'); // 线段出图
    expect(svg).toContain('支撑位'); // 文字 label
  });

  // ---- 画线交互（§3.6.3 验证 5/6）----

  it('绘制 trend：mousedown→mousemove→mouseup 提交新画线；mouseup 出 grid 取消', () => {
    setupFakeInstance();
    const onDrawingsChange = vi.fn();
    render(<CandlestickChart model={drawModel} drawings={[]} onDrawingsChange={onDrawingsChange} />);
    fireChartReady();
    fireEvent.click(screen.getByText('画线')); // 进入 draw 模式（默认趋势线）

    zrFire('mousedown', 300, 200); // idx 3、price 3000
    zrFire('mousemove', 500, 100); // 预览
    expect(mockState.zr!.add).toHaveBeenCalled(); // 预览走 zr 图元
    zrFire('mouseup', 500, 100); // idx 5、price 3100
    expect(onDrawingsChange).toHaveBeenCalledTimes(1);
    const [next] = onDrawingsChange.mock.calls[0] as [Drawing[]];
    expect(next).toHaveLength(1);
    expect(next[0]).toMatchObject({
      kind: 'trend',
      p1: { date: drawDates[3], price: 3000 },
      p2: { date: drawDates[5], price: 3100 },
    });

    // mouseup 出 grid → 取消本次绘制
    zrFire('mousedown', 300, 200);
    zrFire('mouseup', 700, 100); // x=700 超出 grid 矩形 600
    expect(onDrawingsChange).toHaveBeenCalledTimes(1);
  });

  it('绘制 trend 垂直两点（同日）拒绝提交；空 text 拒绝提交', () => {
    setupFakeInstance();
    const onDrawingsChange = vi.fn();
    render(<CandlestickChart model={drawModel} drawings={[]} onDrawingsChange={onDrawingsChange} />);
    fireChartReady();
    fireEvent.click(screen.getByText('画线'));

    // 同日两点：mousedown idx3、mouseup 同 idx3（x=300 与 x=330 都取整到 3）
    zrFire('mousedown', 300, 200);
    zrFire('mouseup', 330, 100);
    expect(onDrawingsChange).not.toHaveBeenCalled();

    // 空 text：标注模式单击 → 输入框出现 → 直接 Enter 空串不提交
    fireEvent.click(screen.getByText('标注'));
    zrFire('mousedown', 200, 200);
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
    zrFire('mousedown', 200, 200); // idx 2、price 3000

    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: '压力位' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onDrawingsChange).toHaveBeenCalledTimes(1);
    const [next] = onDrawingsChange.mock.calls[0] as [Drawing[]];
    expect(next[0]).toMatchObject({ kind: 'text', pos: { date: drawDates[2], price: 3000 }, text: '压力位' });

    // Esc 取消优先于 blur：取消后 blur 不再提交
    fireEvent.click(screen.getByText('标注'));
    zrFire('mousedown', 200, 200);
    const input2 = screen.getByRole('textbox');
    fireEvent.change(input2, { target: { value: '不应提交' } });
    fireEvent.keyDown(input2, { key: 'Escape' });
    fireEvent.blur(input2);
    expect(onDrawingsChange).toHaveBeenCalledTimes(1);
  });

  it('编辑：命中端点只移该锚点、选中态 width 3（含 merge 修正后）', () => {
    setupFakeInstance();
    const onDrawingsChange = vi.fn();
    render(<CandlestickChart model={drawModel} drawings={[trend1]} onDrawingsChange={onDrawingsChange} />);
    fireChartReady();
    fireEvent.click(screen.getByText('编辑'));

    // 命中端点 2（pixel (500, 100)）：偏移 3,2 px 在 8px 阈值内
    zrFire('mousedown', 503, 102);
    zrFire('mousemove', 600, 0);
    zrFire('mouseup', 600, 0); // idx 6、price 3200
    // 提交后 selectedId 保留、draggingId 清除 → markLine 重建为选中态 width 3（样式在两点数组首元素上；
    // 拖拽中该线按 id 过滤隐藏、以 zr 预览渲染，故断言须在 mouseup 之后）
    const selectedItem = renderOption().series[0].markLine!.data[0] as Array<{ lineStyle?: { width?: number } }>;
    expect(selectedItem[0].lineStyle?.width).toBe(3);
    const [next] = onDrawingsChange.mock.calls[0] as [Drawing[]];
    expect(next[0]).toMatchObject({
      id: 't1',
      p1: { date: drawDates[3], price: 3000 }, // p1 不动
      p2: { date: drawDates[6], price: 3200 }, // 只移 p2
    });
    // 渲染后 merge 被调用（按真实 extent 修正）且选中态仍为 width 3
    expect(mockState.fakeInstance.setOption).toHaveBeenCalled();
  });

  it('编辑：拖到两锚点同日 → 回退（不更新）；线身拖拽双锚点同步平移', () => {
    setupFakeInstance();
    const onDrawingsChange = vi.fn();
    render(<CandlestickChart model={drawModel} drawings={[trend1]} onDrawingsChange={onDrawingsChange} />);
    fireChartReady();
    fireEvent.click(screen.getByText('编辑'));

    // 端点 1（pixel (300, 200)）拖到 idx5（与 p2 同日）→ 回退
    zrFire('mousedown', 303, 202);
    zrFire('mouseup', 500, 100);
    expect(onDrawingsChange).not.toHaveBeenCalled();

    // 线身命中（中点附近 (400, 150)，投影距离 ≤6px）→ 双锚点同步平移 delta(idx +1, price +102)
    zrFire('mousedown', 400, 152); // start idx 4、price 3048
    zrFire('mouseup', 500, 50); // end idx 5、price 3150
    const [next] = onDrawingsChange.mock.calls[0] as [Drawing[]];
    expect(next[0]).toMatchObject({
      p1: { date: drawDates[4], price: 3102 }, // 3000 + 102
      p2: { date: drawDates[6], price: 3202 }, // 3100 + 102
    });
  });

  it('编辑：Delete 键删除选中；Esc 退出模式并取消进行中绘制', () => {
    setupFakeInstance();
    const onDrawingsChange = vi.fn();
    render(<CandlestickChart model={drawModel} drawings={[trend1]} onDrawingsChange={onDrawingsChange} />);
    fireChartReady();
    fireEvent.click(screen.getByText('编辑'));
    zrFire('mousedown', 503, 102); // 选中 t1
    fireEvent.keyDown(document, { key: 'Delete' });
    expect(onDrawingsChange).toHaveBeenCalledWith([]);

    // Esc：退出 edit 模式（工具栏恢复、编辑提示消失）
    fireEvent.click(screen.getByText('编辑'));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByText('Esc 退出')).not.toBeInTheDocument();
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
      MA5: '#fbbf24', MA10: '#f472b6', MA20: '#a78bfa', MA60: '#34d399',
      'BOLL上轨': '#94a3b8', 'BOLL中轨': '#94a3b8', 'BOLL下轨': '#94a3b8',
      DIF: '#e2e8f0', DEA: '#fbbf24',
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
    expect(svg).toContain('#fbbf24'); // 系列色文字 fill（icon none 无可见标记，空 d path 属已知）
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

  it('merge 按真实 extent 不改写带内价格（§3.6.3 验证 4 回归锚：守护过度夹取）', () => {
    setupFakeInstance();
    // p2 价格 3180 落在并集 extent 顶（3150）与真实 extent 顶（3200）之间
    const bandTrend: Drawing = {
      id: 'b1', kind: 'trend',
      p1: { date: drawDates[1], price: 3120 },
      p2: { date: drawDates[3], price: 3180 },
    };
    render(<CandlestickChart model={drawModel} drawings={[bandTrend]} onDrawingsChange={vi.fn()} />);
    fireChartReady();
    // 首帧 option（并集兜底 [2950,3150]）：3180 被夹到 3150
    const first = renderOption().series[0].markLine!.data as Array<Array<{ coord: [number, number] }>>;
    expect(first[0].map((p) => p.coord[1])).toEqual([3120, 3150]);
    // 渲染后 merge 用真实 extent [2900,3200]：带内端点按原价渲染（不被改写）
    const fireAxis = renderProps().onEvents!.updateAxisPointer as (p: unknown) => void;
    act(() => {
      fireAxis({ axesInfo: [{ value: 1 }] });
    });
    const setOptionMock = mockState.fakeInstance.setOption as Mock;
    // 图例 merge 也走 setOption——按含 series 的调用定位 markLine merge
    const mergeArg = setOptionMock.mock.calls
      .map((call) => call[0] as { series?: unknown } & MergeArgShape)
      .find((arg) => arg.series)! as MergeArgShape;
    expect(mergeArg.series[0].markLine!.data[0].map((p) => p.coord[1])).toEqual([3120, 3180]);
  });

  it('merge 修正后选中态仍为 width 3（§3.6.3 验证 6 回归锚：构建与 merge 共用构建函数）', () => {
    setupFakeInstance();
    render(<CandlestickChart model={drawModel} drawings={[trend1]} onDrawingsChange={vi.fn()} />);
    fireChartReady();
    fireEvent.click(screen.getByText('编辑'));
    // 拖拽中该线按 id 过滤隐藏（markLine 空），选中态断言须在 mouseup 之后：
    // draggingId 清除、selectedId 保留 → 重渲染 → merge 以 selectedId 重建
    zrFire('mousedown', 503, 102);
    zrFire('mousemove', 600, 0);
    zrFire('mouseup', 600, 0);
    const setOptionMock = mockState.fakeInstance.setOption as Mock;
    // 图例 merge 也走 setOption——按含 series 的调用定位 markLine merge，取最后一次
    const seriesCalls = setOptionMock.mock.calls
      .map((call) => call[0] as { series?: unknown } & MergeWidthArgShape)
      .filter((arg) => arg.series);
    const mergeArg = seriesCalls.at(-1)! as MergeWidthArgShape;
    expect(mergeArg.series[0].markLine!.data[0][0].lineStyle?.width).toBe(3);
  });
});
