// test-catalog-begin
// {
//   "purpose": "行情界面 / TrendComparisonPanel：默认市值分层 Tab、近1年区间：请求参数（from=今天减12月）、无 role=\"tab\"；Tab 切换：aria-pressed 翻转、图表切到板组序列（序列名取响应 name）；区间切换换查询 key：全部 → from=1990-01-01、近3月 → from=今天减3月",
//   "keywords": [
//     "行情界面",
//     "市场分析",
//     "权限角色",
//     "trend_comparison_panel",
//     "role"
//   ],
//   "covers": [
//     "frontend/src/api/generated/services/MarketDataService.ts",
//     "frontend/src/modules/market/pages/TrendComparisonPanel.tsx",
//     "frontend/src/shared/charts/LineChart.tsx",
//     "frontend/src/shared/format/dateTime.ts"
//   ],
//   "environment": [
//     "local"
//   ]
// }
// test-catalog-end

import { fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { MarketDataService } from '../../../../../frontend/src/api/generated/services/MarketDataService';
import { renderWithRouter } from '../../../../support/frontend/utils';
import { addMonthsClamped, TrendComparisonPanel, trendRangeFrom } from '../../../../../frontend/src/modules/market/pages/TrendComparisonPanel';

// 趋势对比面板单测（趋势对比面板方案 4.4.3）：Tab 按钮 aria-pressed 翻转 + 页面无
// role="tab"、区间切换换查询 key、区间 from 口径（近1年=今天减12月 / 全部=1990-01-01
// / addMonths 日溢出夹取）、as_of/freshness 消费（FRESH/STALE/UNAVAILABLE/as_of=null）、
// 空序列角标、请求参数断言。
vi.mock('../../../../../frontend/src/api/generated/services/MarketDataService', () => ({
  MarketDataService: {
    capTierTrendsApiV1MarketDataTrendsCapTiersGet: vi.fn(),
    boardTrendsApiV1MarketDataTrendsBoardsGet: vi.fn(),
  },
}));
vi.mock('../../../../../frontend/src/shared/charts/LineChart', () => ({
  LineChart: ({ model }: { model: unknown }) => (
    <div data-testid="line-chart" data-model={JSON.stringify(model)} />
  ),
}));
vi.mock('../../../../../frontend/src/shared/format/dateTime', () => ({
  todayLocalDate: () => '2026-09-19',
}));

const capMock = MarketDataService.capTierTrendsApiV1MarketDataTrendsCapTiersGet as Mock;
const boardMock = MarketDataService.boardTrendsApiV1MarketDataTrendsBoardsGet as Mock;

function envelope(data: unknown) {
  return { data, meta: { request_id: 'r', schema_version: 'v1' } };
}

function seriesOf(name: string, ...points: Array<[string, number]>) {
  return { symbol: name, name, points: points.map(([date, close]) => ({ date, close })) };
}

function trendsFixture(
  series: Array<{ symbol: string; name: string; points: Array<{ date: string; close: number }> }>,
  freshness: string,
  asOf: string | null,
) {
  return {
    from: '2025-09-19', to: '2026-09-19',
    series,
    as_of: asOf,
    freshness_status: freshness,
  };
}

const capSeries = [
  seriesOf('沪深300', ['2014-01-02', 2200], ['2014-01-03', 2211]),
  seriesOf('中证500'),
  seriesOf('中证1000', ['2014-01-02', 4000]),
  seriesOf('中证2000'),
];
const boardSeries = [
  seriesOf('上证综指', ['2019-12-31', 3000]),
  seriesOf('创业板指', ['2019-12-31', 1800]),
  seriesOf('科创50', ['2019-12-31', 1000]),
];

beforeEach(() => {
  capMock.mockReset();
  boardMock.mockReset();
  capMock.mockResolvedValue(envelope(trendsFixture(capSeries, 'FRESH', '2026-09-18')));
  boardMock.mockResolvedValue(envelope(trendsFixture(boardSeries, 'FRESH', '2026-09-18')));
});

describe('TrendComparisonPanel', () => {
  it('默认市值分层 Tab、近1年区间：请求参数（from=今天减12月）、无 role="tab"', async () => {
    renderWithRouter(<TrendComparisonPanel />);

    await waitFor(() => expect(capMock).toHaveBeenCalledWith('2025-09-19', '2026-09-19'));
    // 两 Tab 端点无条件并行挂载 = 有意设计（Tab 切换零等待、缓存直读；区间切换才换
    // key 重拉，「全部」区间两组合计 ≈1.3MB 成本已实测并记录于 plan 4.4.3）
    expect(boardMock).toHaveBeenCalledWith('2025-09-19', '2026-09-19');
    expect(screen.getByRole('button', { name: '市值分层' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '市场板' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: '近1年' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
    expect(await screen.findByText('数据截至 2026-09-18')).toBeInTheDocument();
  });

  it('Tab 切换：aria-pressed 翻转、图表切到板组序列（序列名取响应 name）', async () => {
    renderWithRouter(<TrendComparisonPanel />);
    await waitFor(() => expect(capMock).toHaveBeenCalled());

    fireEvent.click(screen.getByRole('button', { name: '市场板' }));

    expect(screen.getByRole('button', { name: '市场板' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '市值分层' })).toHaveAttribute('aria-pressed', 'false');
    const chart = await screen.findByTestId('line-chart');
    const model = JSON.parse(chart.getAttribute('data-model') ?? '{}') as {
      series: Array<{ name: string }>;
    };
    expect(model.series.map((s) => s.name)).toEqual(['上证综指', '创业板指', '科创50']);
  });

  it('区间切换换查询 key：全部 → from=1990-01-01、近3月 → from=今天减3月', async () => {
    renderWithRouter(<TrendComparisonPanel />);
    await waitFor(() => expect(capMock).toHaveBeenCalledWith('2025-09-19', '2026-09-19'));

    fireEvent.click(screen.getByRole('button', { name: '全部' }));
    await waitFor(() => expect(capMock).toHaveBeenLastCalledWith('1990-01-01', '2026-09-19'));
    expect(boardMock).toHaveBeenLastCalledWith('1990-01-01', '2026-09-19');

    fireEvent.click(screen.getByRole('button', { name: '近3月' }));
    await waitFor(() => expect(capMock).toHaveBeenLastCalledWith('2026-06-19', '2026-09-19'));
  });

  it('空序列 → "无数据：<name>"角标（仅空序列产生角标）', async () => {
    renderWithRouter(<TrendComparisonPanel />);

    expect(await screen.findByText('无数据：中证500')).toBeInTheDocument();
    expect(screen.getByText('无数据：中证2000')).toBeInTheDocument();
    expect(screen.queryByText('无数据：沪深300')).not.toBeInTheDocument();
    expect(screen.queryByText('无数据：中证1000')).not.toBeInTheDocument();
  });

  it('STALE → 「数据滞后」角标（数据截至仍展示）', async () => {
    capMock.mockResolvedValue(envelope(trendsFixture(capSeries, 'STALE', '2026-09-17')));
    renderWithRouter(<TrendComparisonPanel />);

    expect(await screen.findByText('数据滞后')).toBeInTheDocument();
    expect(screen.getByText('数据截至 2026-09-17')).toBeInTheDocument();
    expect(screen.queryByText('暂无数据')).not.toBeInTheDocument();
  });

  it('UNAVAILABLE → 「暂无数据」角标且不渲染"数据截至"（as_of=null 整条省略）', async () => {
    capMock.mockResolvedValue(envelope(trendsFixture(capSeries, 'UNAVAILABLE', null)));
    renderWithRouter(<TrendComparisonPanel />);

    expect(await screen.findByText('暂无数据')).toBeInTheDocument();
    expect(screen.queryByText(/数据截至/)).not.toBeInTheDocument();
    expect(screen.queryByText('数据滞后')).not.toBeInTheDocument();
  });
});

describe('区间 from 口径（addMonths 日溢出夹取）', () => {
  it('addMonthsClamped：目标月同日，日溢出夹取到目标月最后一天', () => {
    expect(addMonthsClamped('2026-09-19', -12)).toBe('2025-09-19');
    expect(addMonthsClamped('2026-09-19', -3)).toBe('2026-06-19');
    expect(addMonthsClamped('2026-03-31', -1)).toBe('2026-02-28'); // 夹取，不得滚成 3/3
    expect(addMonthsClamped('2026-01-31', -1)).toBe('2025-12-31');
    expect(addMonthsClamped('2026-03-31', 1)).toBe('2026-04-30');
  });

  it('trendRangeFrom：「全部」= 1990-01-01（早于任何库内数据）', () => {
    expect(trendRangeFrom('all', '2026-09-19')).toBe('1990-01-01');
    expect(trendRangeFrom('1y', '2026-09-19')).toBe('2025-09-19');
  });
});
