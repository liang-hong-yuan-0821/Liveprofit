import { act, cleanup, renderHook } from '@testing-library/react';
import { QueryObserver } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Resource, RefreshGroup, RefreshJob, type RefreshStatusData } from '../../../api/generated';
import { queryKeys } from '../../../api/queryKeys';
import { createTestQueryClient, withQueryClient } from '../../../test/utils';
import { resourceAffectsQuery, useMarketRefresh } from './refreshQueries';

const mocks = vi.hoisted(() => ({ status: vi.fn(), post: vi.fn() }));
vi.mock('../../../api/generated/services/MarketDataService', () => ({ MarketDataService: {
  refreshStatusApiV1MarketDataRefreshStatusGet: mocks.status,
  refreshApiV1MarketDataRefreshPost: mocks.post,
} }));
const envelope = (data: unknown) => ({ data, meta: { request_id: 'test', schema_version: 'v1' } });
function group(overrides: Partial<RefreshGroup> = {}): RefreshGroup {
  return {
    resource: Resource.CN_INDEX_BARS, market: RefreshGroup.market.CN, market_date: '2026-09-23',
    calendar_status: RefreshGroup.calendar_status.OK, supported_through: '2026-12-31',
    expected_trade_date: '2026-09-22', next_ready_at: null, latest_observed_date: '2026-09-22', complete_through_date: '2026-09-22',
    freshness: RefreshGroup.freshness.FRESH, expected_count: 11, available_count: 11, exempt_count: 0, missing_count: 0,
    window_coverage: { from: '2026-09-18', to: '2026-09-22', expected_count: 33, available_count: 33, exempt_count: 0, missing_count: 0 },
    data_version: 'v1', auto_eligibility: { allowed: false, reason: 'fresh', next_retry_at: null },
    manual_eligibility: { allowed: false, reason: 'fresh', next_retry_at: null }, job: null, ...overrides,
  };
}
function status(groups = [group()]): RefreshStatusData {
  return { server_time: '2026-09-23T00:00:00Z', refresh_available: true, worker_online: true, concept_display_date: '2026-09-22', groups };
}
async function tick(ms = 1) { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); }
function visibility(value: DocumentVisibilityState) {
  Object.defineProperty(document, 'visibilityState', { configurable: true, value });
  act(() => document.dispatchEvent(new Event('visibilitychange')));
}
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-09-23T00:00:00Z'));
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  mocks.status.mockReset().mockResolvedValue(envelope(status()));
  mocks.post.mockReset().mockResolvedValue(envelope({ decisions: [] }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe('page refresh coordinator', () => {
  it('market page submits only index/sector resources; stock page submits only stock', async () => {
    const index = group({ auto_eligibility: { allowed: true, reason: 'missing', next_retry_at: null } });
    const stock = group({ resource: Resource.CN_STOCK_DAILY, auto_eligibility: { allowed: true, reason: 'missing', next_retry_at: null } });
    mocks.status.mockResolvedValue(envelope(status([index, stock])));
    const market = renderHook(() => useMarketRefresh('market'), { wrapper: withQueryClient(createTestQueryClient()) });
    await tick(20);
    expect(mocks.post).toHaveBeenCalledExactlyOnceWith({ resources: [Resource.CN_INDEX_BARS], mode: 'auto' });
    market.unmount();
    mocks.post.mockClear();
    const quant = renderHook(() => useMarketRefresh('stock'), { wrapper: withQueryClient(createTestQueryClient()) });
    await tick(20);
    expect(mocks.post).toHaveBeenCalledExactlyOnceWith({ resources: [Resource.CN_STOCK_DAILY], mode: 'auto' });
    quant.unmount();
  });

  it('market page refreshes concept trees and open stock K-lines when stock version changes', async () => {
    const client = createTestQueryClient();
    const conceptKey = queryKeys.conceptTree.all;
    const stockKey = queryKeys.stockBars.all;
    const stock = group({ resource: Resource.CN_STOCK_DAILY, data_version: 'stock-v2' });
    mocks.status.mockResolvedValue(envelope(status([stock])));
    client.setQueryData(conceptKey, 'old-concept');
    client.setQueryData(stockKey, 'old-stock');
    const conceptFetch = vi.fn().mockResolvedValue('new-concept');
    const stockFetch = vi.fn().mockResolvedValue('new-stock');
    const conceptObserver = new QueryObserver(client, { queryKey: conceptKey, queryFn: conceptFetch, staleTime: Infinity });
    const stockObserver = new QueryObserver(client, { queryKey: stockKey, queryFn: stockFetch, staleTime: Infinity });
    const closeConcept = conceptObserver.subscribe(() => {});
    const closeStock = stockObserver.subscribe(() => {});
    renderHook(() => useMarketRefresh('market'), { wrapper: withQueryClient(client) });
    await tick(20);
    expect(conceptFetch).toHaveBeenCalledTimes(1);
    expect(stockFetch).toHaveBeenCalledTimes(1);
    expect(mocks.post).not.toHaveBeenCalled();
    closeConcept(); closeStock(); client.clear();
  });

  it('coalesces focus within 30 seconds; hidden tabs pause and resume immediately', async () => {
    renderHook(useMarketRefresh, { wrapper: withQueryClient(createTestQueryClient()) });
    await tick();
    act(() => window.dispatchEvent(new Event('focus')));
    await tick(29_000);
    expect(mocks.status).toHaveBeenCalledTimes(1);
    await tick(1_000);
    act(() => window.dispatchEvent(new Event('focus')));
    await tick();
    expect(mocks.status).toHaveBeenCalledTimes(2);
    visibility('hidden');
    await tick(600_000);
    expect(mocks.status).toHaveBeenCalledTimes(2);
    visibility('visible');
    await tick();
    expect(mocks.status).toHaveBeenCalledTimes(3);
    act(() => window.dispatchEvent(new Event('focus')));
    await tick();
    expect(mocks.status).toHaveBeenCalledTimes(3);
  });

  it('submits one allowed auto trigger and polls an active job every ten seconds', async () => {
    const allowed = group({ auto_eligibility: { allowed: true, reason: 'missing', next_retry_at: null } });
    mocks.status.mockResolvedValue(envelope(status([allowed])));
    const { result } = renderHook(useMarketRefresh, { wrapper: withQueryClient(createTestQueryClient()) });
    await tick(20);
    expect(mocks.post).toHaveBeenCalledExactlyOnceWith({ resources: [Resource.CN_INDEX_BARS], mode: 'auto' });
    const running = { ...allowed, job: { id: 'j', resource: Resource.CN_INDEX_BARS, target_trade_date: '2026-09-22', status: RefreshJob.status.RUNNING, attempt: 1, processed: 1, total: 11, started_at: null, heartbeat_at: null, error_code: null, error_summary: null } };
    mocks.status.mockResolvedValue(envelope(status([running])));
    await tick(30_000);
    act(() => window.dispatchEvent(new Event('focus')));
    await tick(20);
    const count = mocks.status.mock.calls.length;
    await tick(20_000);
    expect(mocks.status.mock.calls.length).toBeGreaterThan(count);
    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(result.current.status?.groups[0].job?.id).toBe('j');
  });

  it('manual eligibility works after the automatic attempt budget is exhausted', async () => {
    mocks.status.mockResolvedValue(envelope(status([group({ manual_eligibility: { allowed: true, reason: 'retry', next_retry_at: null } })])));
    const { result } = renderHook(useMarketRefresh, { wrapper: withQueryClient(createTestQueryClient()) });
    await tick();
    expect(mocks.post).not.toHaveBeenCalled();
    act(() => result.current.retry(Resource.CN_INDEX_BARS));
    await tick(10);
    expect(mocks.post).toHaveBeenCalledExactlyOnceWith({ resources: [Resource.CN_INDEX_BARS], mode: 'retry' });
  });

  it('network failure does not retry a mutation, and prevents GET/POST for at least 60 seconds', async () => {
    mocks.status.mockResolvedValue(envelope(status([group({ auto_eligibility: { allowed: true, reason: 'missing', next_retry_at: null } })])));
    mocks.post.mockRejectedValueOnce(new Error('offline')).mockResolvedValue(envelope({ decisions: [] }));
    const { result } = renderHook(useMarketRefresh, { wrapper: withQueryClient(createTestQueryClient()) });
    await tick(20);
    expect(mocks.post).toHaveBeenCalledTimes(1);
    visibility('hidden'); visibility('visible');
    await tick(59_000);
    act(() => window.dispatchEvent(new Event('focus')));
    await tick();
    expect(mocks.status).toHaveBeenCalledTimes(1);
    expect(mocks.post).toHaveBeenCalledTimes(1);
    await tick(11_000);
    expect(mocks.status.mock.calls.length).toBeGreaterThan(1);
    expect(mocks.post).toHaveBeenCalledTimes(2);
    expect(result.current.error).toBe(false);
  });

  it('Redis unavailability retains data and never submits', async () => {
    mocks.status.mockResolvedValue(envelope({ ...status([group({ auto_eligibility: { allowed: true, reason: 'missing', next_retry_at: null } })]), refresh_available: false }));
    const { result } = renderHook(useMarketRefresh, { wrapper: withQueryClient(createTestQueryClient()) });
    await tick(310_000);
    expect(result.current.status?.groups[0].available_count).toBe(11);
    expect(mocks.post).not.toHaveBeenCalled();
  });

  it('invalidates existing active cache once; versions batch within ten seconds and inactive history only becomes stale', async () => {
    const client = createTestQueryClient();
    client.setQueryDefaults(queryKeys.marketBars.all, { gcTime: Infinity });
    const cnKey = queryKeys.marketBars.list({ market: 'CN', symbol: '000001.SH' });
    const usKey = queryKeys.marketBars.list({ market: 'US', symbol: '.INX' });
    const historyKey = queryKeys.marketBars.list({ market: 'CN', symbol: '000001.SH', from: '2020-01-01' });
    for (const key of [cnKey, usKey, historyKey]) client.setQueryData(key, 'cached');
    const cnFetch = vi.fn().mockResolvedValue('new'); const usFetch = vi.fn().mockResolvedValue('new');
    const cnObserver = new QueryObserver(client, { queryKey: cnKey, queryFn: cnFetch, staleTime: Infinity });
    const usObserver = new QueryObserver(client, { queryKey: usKey, queryFn: usFetch, staleTime: Infinity });
    const closeCn = cnObserver.subscribe(() => {}); const closeUs = usObserver.subscribe(() => {});
    renderHook(useMarketRefresh, { wrapper: withQueryClient(client) });
    await tick(10);
    expect(cnFetch).toHaveBeenCalledTimes(1); expect(usFetch).not.toHaveBeenCalled();
    expect(client.getQueryState(historyKey)?.isInvalidated).toBe(true);
    act(() => client.setQueryData(queryKeys.marketRefresh.all, status([group({ data_version: 'v2' })])));
    await tick(5_000);
    act(() => client.setQueryData(queryKeys.marketRefresh.all, status([group({ data_version: 'v3' })])));
    await tick(5_000);
    expect(cnFetch).toHaveBeenCalledTimes(2);
    act(() => client.setQueryData(queryKeys.marketRefresh.all, status([group({ data_version: 'v3' })])));
    await tick(10_000);
    expect(cnFetch).toHaveBeenCalledTimes(2);
    act(() => client.setQueryData(queryKeys.marketRefresh.all, status([group({ data_version: 'v3', expected_trade_date: '2026-09-23', freshness: RefreshGroup.freshness.STALE })])));
    await tick(10);
    expect(cnFetch).toHaveBeenCalledTimes(3);
    await tick(300_000);
    expect(usFetch).toHaveBeenCalledTimes(1);
    expect(client.getQueryData(historyKey)).toBe('cached');
    closeCn(); closeUs(); client.clear();
  });

  it('updates the server market date without manufacturing a date in the browser timezone', async () => {
    const client = createTestQueryClient();
    const { result } = renderHook(useMarketRefresh, { wrapper: withQueryClient(client) });
    await tick();
    expect(result.current.dates.CN).toBe('2026-09-23');
    act(() => client.setQueryData(queryKeys.marketRefresh.all, status([group({ market_date: '2026-09-24' })])));
    await tick();
    expect(result.current.dates.CN).toBe('2026-09-24');
  });

  it('maps each resource to its affected data domains only', () => {
    const q = (key: readonly unknown[]) => ({ queryKey: key });
    expect(resourceAffectsQuery(Resource.CN_INDEX_FACTORS, q(queryKeys.marketBars.list({ market: 'CN' })))).toBe(true);
    expect(resourceAffectsQuery(Resource.CN_INDEX_FACTORS, q(queryKeys.marketCapTierTrends.all))).toBe(false);
    expect(resourceAffectsQuery(Resource.CN_INDEX_BARS, q(queryKeys.marketBoardTrends.all))).toBe(true);
    expect(resourceAffectsQuery(Resource.CN_SECTOR_DAILY, q(queryKeys.conceptBars.all))).toBe(true);
    expect(resourceAffectsQuery(Resource.CN_STOCK_DAILY, q(queryKeys.conceptTree.all))).toBe(true);
    expect(resourceAffectsQuery(Resource.US_INDEX_BARS, q(queryKeys.marketBars.list({ market: 'KR' })))).toBe(false);
    expect(resourceAffectsQuery(Resource.CN_STOCK_DAILY, q(queryKeys.conceptBars.all))).toBe(false);
  });
});
