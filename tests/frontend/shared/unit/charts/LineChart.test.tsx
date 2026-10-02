// test-catalog-begin
// {
//   "purpose": "公共组件 / buildLineOption：x 轴为 time 类型、y 轴 scale=true；序列 data 为 [日期, 值] 坐标对原样透传；空序列保留在 legend 且 data 为空",
//   "keywords": [
//     "公共组件",
//     "line_chart"
//   ],
//   "covers": [
//     "frontend/src/shared/charts/LineChart.tsx"
//   ],
//   "environment": [
//     "local"
//   ]
// }
// test-catalog-end

import { describe, expect, it } from 'vitest';
import { buildLineOption, LINE_SERIES_COLORS, type LineChartViewModel } from '../../../../../frontend/src/shared/charts/LineChart';

// buildLineOption 纯函数单测（趋势对比面板方案 4.3.3）：jsdom 下不渲染 ECharts，
// 断言 option 形状——① x 轴 type='time' ② y 轴 scale=true ③ 序列 data 元素为
// [string, number] 二元组 ④ 空序列仍进 legend 且 data 为空 ⑤ 单点序列不抛错。
describe('buildLineOption', () => {
  const vm: LineChartViewModel = {
    series: [
      { name: '沪深300', data: [['2026-09-01', 100], ['2026-09-02', 101.5]] },
      { name: '中证500', data: [] },
    ],
  };

  it('x 轴为 time 类型、y 轴 scale=true', () => {
    const option = buildLineOption(vm);
    expect((option.xAxis as { type: string }).type).toBe('time');
    expect((option.yAxis as { scale: boolean }).scale).toBe(true);
  });

  it('序列 data 为 [日期, 值] 坐标对原样透传', () => {
    const option = buildLineOption(vm);
    const series = option.series as Array<{ data: unknown[] }>;
    expect(series[0].data).toEqual([['2026-09-01', 100], ['2026-09-02', 101.5]]);
    for (const point of series[0].data as unknown[][]) {
      expect(point).toHaveLength(2); // 坐标对，非 {time, value} 对象
    }
  });

  it('空序列保留在 legend 且 data 为空', () => {
    const option = buildLineOption(vm);
    const legend = option.legend as { data: string[] };
    expect(legend.data).toEqual(['沪深300', '中证500']);
    const series = option.series as Array<{ name: string; data: unknown[] }>;
    expect(series.find((s) => s.name === '中证500')?.data).toEqual([]);
  });

  it('单点序列不抛错', () => {
    const option = buildLineOption({
      series: [{ name: '科创50', data: [['2019-12-31', 100]] }],
    });
    const series = option.series as Array<{ data: unknown[] }>;
    expect(series[0].data).toEqual([['2019-12-31', 100]]);
  });

  it('序列色按槽位固定序分配、显式 color 优先', () => {
    const option = buildLineOption(vm);
    const series = option.series as Array<{ lineStyle: { color: string } }>;
    expect(series[0].lineStyle.color).toBe(LINE_SERIES_COLORS[0]);
    expect(series[1].lineStyle.color).toBe(LINE_SERIES_COLORS[1]);
    const custom = buildLineOption({
      series: [{ name: 'x', color: '#ff0000', data: [['2026-09-01', 1]] }],
    });
    expect((custom.series as Array<{ lineStyle: { color: string } }>)[0].lineStyle.color).toBe('#ff0000');
  });

  it('tooltip 为 axis trigger + cross 指针、按值降序展示', () => {
    const option = buildLineOption(vm);
    const tooltip = option.tooltip as { trigger: string; order: string; axisPointer: { type: string } };
    expect(tooltip.trigger).toBe('axis');
    expect(tooltip.axisPointer.type).toBe('cross');
    expect(tooltip.order).toBe('valueDesc');
  });

  it('不启用 dataZoom（区间切换在 React 层换查询）', () => {
    expect(buildLineOption(vm).dataZoom).toBeUndefined();
  });
});
