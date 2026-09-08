import { fireEvent, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { ApiError } from '../../../api/client';
import { renderWithRouter } from '../../../test/utils';
import { daysAgoLocalDate } from '../../../shared/format/dateTime';
import { MarketIndicesPanel } from './MarketIndicesPanel';

vi.mock('../../../api/generated/services/MarketAssetsService', () => ({
  MarketAssetsService: { listMarketAssetsApiV1MarketAssetsGet: vi.fn() },
}));
vi.mock('../../../api/generated/services/MarketDataService', () => ({
  MarketDataService: { indexBarsApiV1MarketDataIndicesSymbolBarsGet: vi.fn() },
}));
vi.mock('../../../shared/charts/CandlestickChart', () => ({
  CandlestickChart: () => <div data-testid="candlestick-chart" />,
}));

import { MarketAssetsService } from '../../../api/generated/services/MarketAssetsService';
import { MarketDataService } from '../../../api/generated/services/MarketDataService';

const assetsMock = MarketAssetsService.listMarketAssetsApiV1MarketAssetsGet as Mock;
const barsMock = MarketDataService.indexBarsApiV1MarketDataIndicesSymbolBarsGet as Mock;

function envelope(data: unknown) {
  return { data, meta: { request_id: 'r', schema_version: 'v1' } };
}

function makeAsset(overrides: Record<string, unknown> = {}) {
  return {
    market: 'CN',
    symbol: '000001.SH',
    name: '上证综指',
    currency: 'CNY',
    market_timezone: 'Asia/Shanghai',
    display_order: 10,
    enabled: true,
    supported_intervals: ['1d'],
    availability_status: 'AVAILABLE',
    ...overrides,
  };
}

const barsData = {
  asset: {}, interval: '1d', from: '2026-07-01', to: '2026-09-04',
  bars: [{ timestamp: '2026-09-03T00:00:00Z', open: 1, high: 2, low: 0.5, close: 1.5, volume: null }],
  indicators: {
    ma: [
      { period: 5, values: [null] },
      { period: 10, values: [null] },
      { period: 20, values: [null] },
      { period: 60, values: [null] },
    ],
    boll: { period: 20, k: 2, mid: [null], upper: [null], lower: [null] },
  },
  source: 'tushare', as_of: '2026-09-04', source_updated_at: null,
  freshness_status: 'FRESH', market_session_status: 'CLOSED', market_closed_reason: '已收盘',
};

beforeEach(() => {
  assetsMock.mockReset();
  barsMock.mockReset();
  assetsMock.mockResolvedValue(envelope({ items: [makeAsset()] }));
  barsMock.mockResolvedValue(envelope(barsData));
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('MarketIndicesPanel', () => {
  it('仅对 availability_status=AVAILABLE 的资产请求 bars', async () => {
    assetsMock.mockResolvedValue(
      envelope({
        items: [makeAsset(), makeAsset({ symbol: '000016.SH', name: '上证50', availability_status: 'DISABLED' })],
      }),
    );
    renderWithRouter(<MarketIndicesPanel />);

    expect(await screen.findByText('上证综指')).toBeInTheDocument();
    await screen.findByTestId('candlestick-chart');

    // 禁用资产：显示不可用说明，不请求 K 线
    expect(screen.getByText('该资产未启用')).toBeInTheDocument();
    const requestedSymbols = barsMock.mock.calls.map((call) => call[0]);
    expect(requestedSymbols).toEqual(['000001.SH']);
  });

  it('开始日期晚于结束日期：显示字段错误并阻止请求', async () => {
    renderWithRouter(<MarketIndicesPanel />);
    await screen.findByTestId('candlestick-chart');
    const callsBefore = barsMock.mock.calls.length;

    // jsdom 的 date input 不支持键盘逐字输入，用 fireEvent.change 直接赋值；
    // 开始日期取明天（结束日期默认为今天），保证任意运行日期都晚于结束日期
    fireEvent.change(screen.getByLabelText('开始日期'), { target: { value: daysAgoLocalDate(-1) } });

    expect(screen.getByText('开始日期不能晚于结束日期')).toBeInTheDocument();
    expect(barsMock.mock.calls.length).toBe(callsBefore);
  });

  it('无 bars 不绘制空壳图，显示无可展示时序', async () => {
    barsMock.mockResolvedValue(envelope({ ...barsData, bars: [] }));
    renderWithRouter(<MarketIndicesPanel />);

    expect(await screen.findByText('无可展示时序')).toBeInTheDocument();
    expect(screen.queryByTestId('candlestick-chart')).not.toBeInTheDocument();
  });

  it('STALE 显示延迟标识；闭市显示闭市原因', async () => {
    barsMock.mockResolvedValue(envelope({ ...barsData, freshness_status: 'STALE' }));
    renderWithRouter(<MarketIndicesPanel />);

    expect(await screen.findByText('数据可能延迟')).toBeInTheDocument();
    expect(screen.getByText(/闭市：已收盘/)).toBeInTheDocument();
  });

  it('旧后端无 indicators 字段：降级渲染纯 K 线', async () => {
    const { indicators: _dropped, ...barsWithoutIndicators } = barsData;
    barsMock.mockResolvedValue(envelope(barsWithoutIndicators));
    renderWithRouter(<MarketIndicesPanel />);

    expect(await screen.findByTestId('candlestick-chart')).toBeInTheDocument();
  });

  it('ASSET_DISABLED（409 不可重试）不显示重试按钮', async () => {
    barsMock.mockRejectedValue(new ApiError({ code: 'ASSET_DISABLED', message: '资产未启用', retryable: false, status: 409 }));
    renderWithRouter(<MarketIndicesPanel />);
    await screen.findByText('上证综指');

    expect(await screen.findByText('资产未启用')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '重试' })).not.toBeInTheDocument();
  });
});
