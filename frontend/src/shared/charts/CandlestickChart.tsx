import ReactECharts from 'echarts-for-react';
import type { CandlestickSeriesOption, LineSeriesOption } from 'echarts/charts';

// 通用 K 线图：只接受前端 ViewModel，不接收 API DTO；
// 空数据由调用方保证不渲染（不绘制空壳/伪图表）。
// ma/boll 缺失（旧后端/降级）时渲染纯 K 线，不报错。

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

export function CandlestickChart({ model, height = 240 }: { model: CandlestickChartViewModel; height?: number }) {
  const series: Array<CandlestickSeriesOption | LineSeriesOption> = [
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
  const legendData: string[] = [];

  for (const line of model.ma ?? []) {
    const name = `MA${line.period}`;
    const color = MA_COLORS[line.period] ?? MA_FALLBACK_COLOR;
    legendData.push(name);
    series.push({
      name,
      type: 'line',
      data: line.values,
      symbol: 'none',
      lineStyle: { width: 1.5, color },
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
    legendData.push('BOLL上轨', 'BOLL中轨', 'BOLL下轨');
    series.push(
      { name: 'BOLL上轨', type: 'line', data: upper, symbol: 'none', lineStyle: { width: 1, color: BOLL_LINE_COLOR } },
      { name: 'BOLL中轨', type: 'line', data: mid, symbol: 'none', lineStyle: { width: 1, color: BOLL_LINE_COLOR } },
      { name: 'BOLL下轨', type: 'line', data: lower, symbol: 'none', lineStyle: { width: 1, color: BOLL_LINE_COLOR } },
      { name: 'BOLL带-下轨', type: 'line', data: lower, stack: 'boll-band', symbol: 'none', lineStyle: { opacity: 0 }, tooltip: { show: false } },
      { name: 'BOLL带-填充', type: 'line', data: band, stack: 'boll-band', symbol: 'none', lineStyle: { opacity: 0 }, areaStyle: { color: BOLL_BAND_FILL }, tooltip: { show: false } },
    );
  }

  const option = {
    backgroundColor: 'transparent',
    grid: { left: 48, right: 16, top: legendData.length > 0 ? 32 : 24, bottom: 30 },
    legend: legendData.length > 0 ? { top: 0, data: legendData } : undefined,
    // 缩放交互：inside（滚轮缩放/拖拽平移）+ slider（底部滑条），默认全量显示，两者联动
    dataZoom: [
      { type: 'inside', start: 0, end: 100, zoomOnMouseWheel: true, moveOnMouseMove: true, moveOnMouseWheel: false },
      {
        type: 'slider', start: 0, end: 100, height: 14, bottom: 2, showDetail: false,
        borderColor: 'transparent', backgroundColor: 'rgba(35, 42, 51, 0.6)',
        fillerColor: 'rgba(148, 163, 184, 0.25)', handleStyle: { color: '#8b95a1' },
        textStyle: { color: '#8b95a1', fontSize: 10 },
      },
    ],
    xAxis: { type: 'category', data: model.xAxisData, axisLabel: { color: '#8b95a1' }, axisLine: { lineStyle: { color: '#232a33' } } },
    yAxis: { scale: true, axisLabel: { color: '#8b95a1' }, splitLine: { lineStyle: { color: '#232a33' } } },
    tooltip: { trigger: 'axis' },
    series,
  };

  return (
    <div data-testid="candlestick-chart">
      <ReactECharts option={option} style={{ height, width: '100%' }} notMerge />
    </div>
  );
}
