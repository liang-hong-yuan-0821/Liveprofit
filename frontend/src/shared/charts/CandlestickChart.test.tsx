import { render } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ReactECharts from 'echarts-for-react';
import { CandlestickChart, type CandlestickChartViewModel } from './CandlestickChart';

// jsdom 无 canvas：mock echarts-for-react 捕获 option prop（不 mock 会踩 echarts 初始化坑）
vi.mock('echarts-for-react', () => ({
  default: vi.fn(() => <div data-testid="echarts" />),
}));

const mockECharts = vi.mocked(ReactECharts);

interface SeriesShape {
  name?: string;
  type?: string;
  stack?: string;
  data?: unknown[];
}

interface OptionShape {
  series: SeriesShape[];
  legend?: { data: string[] };
  dataZoom?: { type?: string }[];
}

function renderOption(): OptionShape {
  return mockECharts.mock.calls.at(-1)![0].option as OptionShape;
}

const baseModel: CandlestickChartViewModel = {
  xAxisData: ['2026-09-01', '2026-09-02', '2026-09-03'],
  ohlc: [
    [1, 2, 0.5, 1.5],
    [1.5, 2.5, 1, 2],
    [2, 2.2, 1.8, 2.1],
  ],
};

beforeEach(() => {
  mockECharts.mockClear();
});

describe('CandlestickChart', () => {
  it('无 ma/boll 时只渲染 candlestick 系列（降级纯 K 线）', () => {
    render(<CandlestickChart model={baseModel} />);
    const option = renderOption();
    expect(option.series).toHaveLength(1);
    expect(option.series[0].type).toBe('candlestick');
    expect(option.legend).toBeUndefined();
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
    expect(option.legend?.data).toEqual(['MA5', 'MA10', 'MA20', 'MA60', 'BOLL上轨', 'BOLL中轨', 'BOLL下轨']);
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
});
