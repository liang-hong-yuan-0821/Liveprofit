import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { ApiError } from '../../../api/client';
import { renderWithRouter } from '../../../test/utils';
import MarketOverviewPage from './MarketOverviewPage';

vi.mock('../../../api/generated/services/MarketAssetsService', () => ({
  MarketAssetsService: { listMarketAssetsApiV1MarketAssetsGet: vi.fn() },
}));
vi.mock('../../../api/generated/services/MarketDataService', () => ({
  MarketDataService: {
    indexBarsApiV1MarketDataIndicesSymbolBarsGet: vi.fn(),
    hotConceptsApiV1MarketDataConceptsHotGet: vi.fn(),
  },
}));
vi.mock('../../../api/generated/services/MacroInformationService', () => ({
  MacroInformationService: { listMacroInformationApiV1MacroInformationGet: vi.fn() },
}));
vi.mock('../../../shared/charts/CandlestickChart', () => ({
  CandlestickChart: ({ model }: { model: unknown }) => (
    <div data-testid="candlestick-chart" data-bars={JSON.stringify(model)} />
  ),
}));

import { MarketAssetsService } from '../../../api/generated/services/MarketAssetsService';
import { MarketDataService } from '../../../api/generated/services/MarketDataService';
import { MacroInformationService } from '../../../api/generated/services/MacroInformationService';

const assetsMock = MarketAssetsService.listMarketAssetsApiV1MarketAssetsGet as Mock;
const barsMock = MarketDataService.indexBarsApiV1MarketDataIndicesSymbolBarsGet as Mock;
const hotMock = MarketDataService.hotConceptsApiV1MarketDataConceptsHotGet as Mock;
const macroMock = MacroInformationService.listMacroInformationApiV1MacroInformationGet as Mock;

function envelope(data: unknown, meta: Record<string, unknown> = {}) {
  return { data, meta: { request_id: 'r', schema_version: 'v1', ...meta } };
}

const cnAsset = {
  market: 'CN', symbol: '000001.SH', name: '上证综指', currency: 'CNY',
  market_timezone: 'Asia/Shanghai', display_order: 10, enabled: true,
  supported_intervals: ['1d'], availability_status: 'AVAILABLE',
};

beforeEach(() => {
  assetsMock.mockReset();
  barsMock.mockReset();
  hotMock.mockReset();
  macroMock.mockReset();

  assetsMock.mockResolvedValue(envelope({ items: [cnAsset] }));
  barsMock.mockResolvedValue(
    envelope({
      asset: {}, interval: '1d', from: '2026-07-01', to: '2026-09-04',
      bars: [{ timestamp: '2026-09-03T00:00:00Z', open: 1, high: 2, low: 0.5, close: 1.5, volume: null }],
      source: 'tushare', as_of: '2026-09-04', source_updated_at: null,
      freshness_status: 'FRESH', market_session_status: 'CLOSED', market_closed_reason: '已收盘',
    }),
  );
  hotMock.mockRejectedValue(
    new ApiError({ code: 'HOT_CONCEPTS_UPSTREAM_UNAVAILABLE', message: '热点上游不可用', retryable: true, status: 503 }),
  );
  macroMock.mockResolvedValue(envelope({ items: [] }));
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('MarketOverviewPage', () => {
  it('三个 Panel 并行独立：热点失败仅重试自身，市场与信息区块正常展示', async () => {
    renderWithRouter(<MarketOverviewPage />);

    // 市场区块：资产与 K 线
    expect(await screen.findByText('上证综指')).toBeInTheDocument();
    expect(await screen.findByTestId('candlestick-chart')).toBeInTheDocument();
    // 信息区块：正常空态
    expect(screen.getByText('暂无可展示的事件研究宏观信息')).toBeInTheDocument();
    // 板块区块：独立错误态 + 重试
    expect(screen.getByText('热点上游不可用')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();

    const barsCallsBefore = barsMock.mock.calls.length;
    hotMock.mockResolvedValue(
      envelope({
        as_of: '2026-09-04', algorithm_version: 'heat_v1', result_status: 'OK',
        items: [], source: null, source_updated_at: null, freshness_status: 'FRESH',
      }),
    );
    await userEvent.setup().click(screen.getByRole('button', { name: '重试' }));

    await waitFor(() => expect(screen.getByText('当前条件下暂无热点概念')).toBeInTheDocument());
    // 只重试热点区块：市场 bars 未重新请求
    expect(barsMock.mock.calls.length).toBe(barsCallsBefore);
  });

  it('无页内 Tab、无本地指数名单：仅渲染三个固定区块标题', async () => {
    renderWithRouter(<MarketOverviewPage />);
    await screen.findByText('上证综指');

    expect(screen.getByRole('heading', { name: '市场' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '板块' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '信息' })).toBeInTheDocument();
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
  });
});
