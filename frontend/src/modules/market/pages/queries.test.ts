import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { queryKeys } from '../../../api/queryKeys';
import { createTestQueryClient, withQueryClient } from '../../../test/utils';
import {
  CONCEPT_TREE_LIMIT,
  useConceptBarsQuery,
  useConceptTreeQuery,
  useStockBarsQuery,
} from './queries';

vi.mock('../../../api/generated/services/MarketDataService', () => ({
  MarketDataService: {
    conceptTreeApiV1MarketDataConceptsTreeGet: vi.fn(),
    sectorBarsApiV1MarketDataConceptsSectorCodeBarsGet: vi.fn(),
    stockBarsApiV1MarketDataStocksSymbolBarsGet: vi.fn(),
  },
}));

import { MarketDataService } from '../../../api/generated/services/MarketDataService';

const treeMock = MarketDataService.conceptTreeApiV1MarketDataConceptsTreeGet as Mock;
const conceptBarsMock = MarketDataService.sectorBarsApiV1MarketDataConceptsSectorCodeBarsGet as Mock;
const stockBarsMock = MarketDataService.stockBarsApiV1MarketDataStocksSymbolBarsGet as Mock;

const treeFixture = {
  as_of: '2026-09-11', algorithm_version: 'heat_v1', result_status: 'OK',
  items: [], source: 'dc', source_updated_at: null, freshness_status: 'FRESH',
};

function envelope(data: unknown) {
  return { data, meta: { request_id: 'r', schema_version: 'v1' } };
}

const barsFixture = {
  asset: { market: 'CN', symbol: 'X', name: 'X' }, interval: '1d',
  from: '2026-03-18', to: '2026-09-11',
  bars: [{ timestamp: '2026-09-11T00:00:00Z', open: 1, high: 2, low: 0.5, close: 1.5, volume: null }],
  indicators: null, source: 'dc', as_of: '2026-09-11', source_updated_at: null,
  freshness_status: 'FRESH', market_session_status: 'CLOSED', market_closed_reason: '已收盘',
};

beforeEach(() => {
  treeMock.mockReset();
  conceptBarsMock.mockReset();
  stockBarsMock.mockReset();
  treeMock.mockResolvedValue(envelope(treeFixture));
  conceptBarsMock.mockResolvedValue(envelope(barsFixture));
  stockBarsMock.mockResolvedValue(envelope(barsFixture));
});

describe('useConceptTreeQuery', () => {
  it('请求参数与 queryKey（from→as_of 透传、limit 恒 30）', async () => {
    const queryClient = createTestQueryClient();
    const filters = { market: 'CN', interval: '1d', from: '2026-09-11', to: '2026-09-11' };
    const { result } = renderHook(() => useConceptTreeQuery(filters), {
      wrapper: withQueryClient(queryClient),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(treeMock).toHaveBeenCalledWith(
      'CN', '1d', '2026-09-11', '2026-09-11', CONCEPT_TREE_LIMIT, '2026-09-11',
    );
    expect(queryClient.getQueryCache().findAll()[0]?.queryKey).toEqual(
      queryKeys.conceptTree.list({ ...filters, limit: CONCEPT_TREE_LIMIT }),
    );
    expect(result.current.data).toEqual(treeFixture);
  });
});

describe('useConceptBarsQuery', () => {
  it('enabled=true 发起请求（source=dc、180 天窗口）', async () => {
    const queryClient = createTestQueryClient();
    const filters = { market: 'CN', source: 'dc', interval: '1d', from: '2026-03-18', to: '2026-09-11' };
    const { result } = renderHook(() => useConceptBarsQuery('BK1753', filters, true), {
      wrapper: withQueryClient(queryClient),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(conceptBarsMock).toHaveBeenCalledWith(
      'BK1753', 'CN', '1d', '2026-03-18', '2026-09-11', 'dc',
    );
    expect(result.current.data?.bars).toHaveLength(1);
  });

  it('enabled=false 不发起请求', () => {
    const queryClient = createTestQueryClient();
    const filters = { market: 'CN', source: 'dc', interval: '1d', from: '2026-03-18', to: '2026-09-11' };
    renderHook(() => useConceptBarsQuery('BK1753', filters, false), {
      wrapper: withQueryClient(queryClient),
    });
    expect(conceptBarsMock).not.toHaveBeenCalled();
  });
});

describe('useStockBarsQuery', () => {
  it('请求参数与 queryKey', async () => {
    const queryClient = createTestQueryClient();
    const filters = { market: 'CN', interval: '1d', from: '2026-03-18', to: '2026-09-11' };
    const { result } = renderHook(() => useStockBarsQuery('600050.SH', filters, true), {
      wrapper: withQueryClient(queryClient),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(stockBarsMock).toHaveBeenCalledWith('600050.SH', 'CN', '1d', '2026-03-18', '2026-09-11');
    expect(queryClient.getQueryCache().findAll()[0]?.queryKey).toEqual(
      queryKeys.stockBars.list({ symbol: '600050.SH', ...filters }),
    );
  });

  it('enabled=false 不发起请求', () => {
    const queryClient = createTestQueryClient();
    const filters = { market: 'CN', interval: '1d', from: '2026-03-18', to: '2026-09-11' };
    renderHook(() => useStockBarsQuery('600050.SH', filters, false), {
      wrapper: withQueryClient(queryClient),
    });
    expect(stockBarsMock).not.toHaveBeenCalled();
  });
});
