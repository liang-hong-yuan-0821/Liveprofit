import { memo, useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import type { LineSeriesOption } from 'echarts/charts';
import type { EChartsOption } from 'echarts';

// 多序列折线组件（趋势对比面板方案 4.3）：只接受前端 ViewModel，不接收 API DTO。
// - x 轴 type='time'（不设 category——类目轴 markLine 小数坐标坑，time 轴天然支持
//   任意日期落点）
// - 数据必须是 [日期, 值] 坐标对，不得写成 {time, value} 对象（time 轴对非 pair
//   结构静默不渲染）
// - y 轴 scale: true：归一曲线值域集中在 100 附近，从 0 起会把差异压平
// - 空序列降级：data: [] 的序列保留在 legend（用户可见"哪条线没有数据"）但不画点
// - 不启用 dataZoom（区间切换在 React 层换查询，规避 echarts-datazoom-anchors 坑）

// 序列配色：dataviz 约定 categorical 槽位固定序（2026-09-19 校验器 PASS：
// 暗色面 4 槽 CVD 相邻 ΔE 8.4 / 正常视觉 19.8，全项通过）——颜色跟随槽位，
// 不跟随展示名（序列序由后端组常量固化，槽位分配稳定）。
export const LINE_SERIES_COLORS = ['#3987e5', '#d95926', '#199e70', '#c98500'];
const NEUTRAL_LINE_COLOR = '#94a3b8'; // 超出槽位数的兜底中性色（CandlestickChart MA_FALLBACK 同款）

// 图表 chrome 色：与 CandlestickChart 暗色主题同款 token（styles.css --color-fg-muted / --color-border）
const AXIS_LABEL_COLOR = '#8b95a1';
const AXIS_LINE_COLOR = '#232a33';

export interface LineChartSeries {
  name: string;
  color?: string;                 // 不传 = 按系列序取槽位色
  data: Array<[string, number]>;  // [交易日 ISO 字符串, 值] —— ECharts time 轴的坐标对格式
}

export interface LineChartViewModel {
  series: LineChartSeries[];
}

export function buildLineOption(vm: LineChartViewModel): EChartsOption {
  const colorOf = (index: number, explicit?: string) =>
    explicit ?? LINE_SERIES_COLORS[index] ?? NEUTRAL_LINE_COLOR;
  const series: LineSeriesOption[] = vm.series.map((s, i) => ({
    name: s.name,
    type: 'line',
    data: s.data,
    showSymbol: false,
    sampling: 'lttb',     // 全历史降采样
    connectNulls: false,
    lineStyle: { width: 2, color: colorOf(i, s.color) },
    itemStyle: { color: colorOf(i, s.color) },
  }));
  return {
    backgroundColor: 'transparent',
    animation: false,
    grid: { left: 48, right: 16, top: 32, bottom: 30 },
    legend: {
      top: 0,
      itemGap: 16,
      textStyle: { color: AXIS_LABEL_COLOR, fontSize: 11 },
      // 显式列出全部序列名：空序列（data: []）也占 legend 位（降级契约）
      data: vm.series.map((s) => s.name),
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      order: 'valueDesc', // 按值降序展示（归一曲线贴近时大值在上）
    },
    xAxis: {
      type: 'time',
      axisLabel: { color: AXIS_LABEL_COLOR },
      axisLine: { lineStyle: { color: AXIS_LINE_COLOR } },
    },
    yAxis: {
      type: 'value',
      scale: true, // 关键显示参数：归一值域不设从 0 起
      axisLabel: { color: AXIS_LABEL_COLOR },
      splitLine: { lineStyle: { color: AXIS_LINE_COLOR } },
    },
    series,
  };
}

export interface LineChartProps {
  model: LineChartViewModel;
  height?: number;
}

// 图表内核（React.memo，CandlestickChart 同款模式）：option 由纯函数构建、
// 引用稳定（useMemo）→ 外层重渲染时内核拦截、无 setOption 风暴
const ChartCore = memo(function ChartCore({ option, height }: {
  option: EChartsOption;
  height: number;
}) {
  return (
    <ReactECharts option={option} style={{ height, width: '100%' }} notMerge />
  );
});

export function LineChart({ model, height = 240 }: LineChartProps) {
  const option = useMemo(() => buildLineOption(model), [model]);
  return (
    <div data-testid="line-chart">
      <ChartCore option={option} height={height} />
    </div>
  );
}
