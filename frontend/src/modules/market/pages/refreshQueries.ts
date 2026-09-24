import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient, type Query } from '@tanstack/react-query';
import { requestEnvelope } from '../../../api/client';
import { queryKeys } from '../../../api/queryKeys';
import { MarketDataService } from '../../../api/generated/services/MarketDataService';
import { Resource, RefreshRequest, type RefreshStatusData, type RefreshDecisionsData } from '../../../api/generated';
import { todayLocalDate } from '../../../shared/format/dateTime';

export const MarketDatesContext = createContext<Partial<Record<string, string>>>({});
export function useMarketDate(market: string) {
  return useContext(MarketDatesContext)[market] ?? todayLocalDate();
}
export function daysBefore(date: string, days: number) {
  const value = new Date(`${date}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() - days);
  return value.toISOString().slice(0, 10);
}

const activeJobs = new Set(['QUEUED', 'RUNNING', 'RETRY_WAIT']);
type RefreshScope = 'market' | 'stock';
const marketResources = new Set<Resource>([
  Resource.CN_INDEX_BARS, Resource.CN_INDEX_FACTORS, Resource.US_INDEX_BARS,
  Resource.KR_INDEX_BARS, Resource.CN_SECTOR_DAILY,
]);
function inScope(resource: Resource, scope: RefreshScope) {
  return scope === 'stock' ? resource === Resource.CN_STOCK_DAILY : marketResources.has(resource);
}
const marketDomains = new Set<string>([
  queryKeys.marketBars.all[0], queryKeys.marketCapTierTrends.all[0],
  queryKeys.marketBoardTrends.all[0], queryKeys.conceptTree.all[0],
  queryKeys.conceptBars.all[0], queryKeys.stockBars.all[0],
]);

/** Match only affected caches. Inactive queries become stale without fetching history. */
export function resourceAffectsQuery(resource: Resource, query: Pick<Query, 'queryKey'>) {
  const [domain, , filters] = query.queryKey;
  const market = (filters as { market?: string } | undefined)?.market;
  if (domain === queryKeys.marketBars.all[0]) {
    return market === resource.slice(0, 2) && [Resource.CN_INDEX_BARS, Resource.CN_INDEX_FACTORS, Resource.US_INDEX_BARS, Resource.KR_INDEX_BARS].includes(resource);
  }
  if (resource === Resource.CN_INDEX_BARS) {
    return domain === queryKeys.marketCapTierTrends.all[0] || domain === queryKeys.marketBoardTrends.all[0];
  }
  if (resource === Resource.CN_SECTOR_DAILY) {
    return domain === queryKeys.conceptTree.all[0] || domain === queryKeys.conceptBars.all[0];
  }
  return resource === Resource.CN_STOCK_DAILY && (domain === queryKeys.conceptTree.all[0] || domain === queryKeys.stockBars.all[0]);
}

/** One coordinator per overview page; visibility/focus, polling and submissions share clocks. */
export function useMarketRefresh(scope: RefreshScope = 'market') {
  const client = useQueryClient();
  const [visible, setVisible] = useState(() => document.visibilityState !== 'hidden');
  const lastRead = useRef(0);
  const blockedUntil = useRef(0);
  const inFlight = useRef(false);
  const submitted = useRef(new Map<Resource, string>());
  const versions = useRef(new Map<Resource, string>());
  const pendingVersions = useRef(new Set<Resource>());
  const lastInvalidation = useRef(0);
  const lastFallback = useRef(Date.now());

  const status = useQuery({
    queryKey: queryKeys.marketRefresh.all,
    enabled: visible && Date.now() >= blockedUntil.current,
    retry: false,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    queryFn: async () => {
      lastRead.current = Date.now();
      try {
        return (await requestEnvelope<RefreshStatusData>(MarketDataService.refreshStatusApiV1MarketDataRefreshStatusGet())).data;
      } catch (error) {
        blockedUntil.current = Date.now() + 60_000;
        throw error;
      }
    },
  });
  const current = useRef(status);
  current.current = status;
  const mutation = useMutation({
    retry: false,
    mutationFn: async (request: RefreshRequest) =>
      (await requestEnvelope<RefreshDecisionsData>(MarketDataService.refreshApiV1MarketDataRefreshPost(request))).data,
    onSuccess: () => { void current.current.refetch(); },
    onError: () => {
      blockedUntil.current = Date.now() + 60_000;
      submitted.current.clear();
    },
    onSettled: () => { inFlight.current = false; },
  });
  const mutate = mutation.mutate;
  const resetMutation = mutation.reset;
  useEffect(() => {
    if (mutation.isError && !status.isError && status.dataUpdatedAt > mutation.submittedAt) resetMutation();
  }, [mutation.isError, mutation.submittedAt, status.isError, status.dataUpdatedAt, resetMutation]);
  const submit = useCallback((resources: Resource[], mode: RefreshRequest.mode) => {
    if (inFlight.current || Date.now() < blockedUntil.current || document.visibilityState === 'hidden') return;
    if (resources.some(resource => !inScope(resource, scope))) return;
    inFlight.current = true;
    mutate({ resources, mode });
  }, [mutate, scope]);

  useEffect(() => {
    const data = status.data;
    if (!visible || status.isError || !data?.refresh_available || !data.worker_online || inFlight.current || Date.now() < blockedUntil.current) return;
    const resources: Resource[] = [];
    for (const group of data.groups) {
      if (!inScope(group.resource, scope)) continue;
      if (!group.auto_eligibility.allowed) {
        submitted.current.delete(group.resource);
        continue;
      }
      const trigger = `${group.expected_trade_date}:${group.auto_eligibility.next_retry_at}`;
      if (submitted.current.get(group.resource) !== trigger) {
        submitted.current.set(group.resource, trigger);
        resources.push(group.resource);
      }
    }
    if (resources.length) submit(resources, RefreshRequest.mode.AUTO);
  }, [status.data, status.dataUpdatedAt, status.isError, visible, submit, scope]);

  const flushVersions = useCallback(() => {
    if (document.visibilityState === 'hidden' || !pendingVersions.current.size) return;
    const resources = [...pendingVersions.current];
    pendingVersions.current.clear();
    lastInvalidation.current = Date.now();
    void client.invalidateQueries({
      predicate: (query) => resources.some((resource) => resourceAffectsQuery(resource, query)),
      refetchType: 'active',
    }, { cancelRefetch: false });
  }, [client]);
  useEffect(() => {
    for (const group of status.data?.groups ?? []) {
      // The overview still consumes stock bars in concept trees and open
      // K-line dialogs; keep those caches current without showing stock jobs.
      if (!inScope(group.resource, scope) && !(scope === 'market' && group.resource === Resource.CN_STOCK_DAILY)) continue;
      // Date/freshness changes need refreshing even when no new row was written.
      const version = `${group.data_version}:${group.expected_trade_date}:${group.freshness}`;
      if (versions.current.get(group.resource) !== version) {
        versions.current.set(group.resource, version);
        pendingVersions.current.add(group.resource);
      }
    }
    const timer = window.setTimeout(flushVersions, Math.max(0, 10_000 - (Date.now() - lastInvalidation.current)));
    return () => window.clearTimeout(timer);
  }, [status.data, flushVersions, scope]);

  useEffect(() => {
    const refreshStatus = (resumed = false) => {
      if (document.visibilityState === 'hidden' || Date.now() < blockedUntil.current || current.current.isFetching) return;
      if (resumed || Date.now() - lastRead.current >= 30_000) void current.current.refetch();
      flushVersions();
    };
    const focus = () => refreshStatus();
    const visibility = () => {
      const shown = document.visibilityState !== 'hidden';
      setVisible(shown);
      if (shown) refreshStatus(true);
    };
    window.addEventListener('focus', focus);
    document.addEventListener('visibilitychange', visibility);
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'hidden') return;
      const now = Date.now();
      const busy = current.current.data?.groups.some((group) => inScope(group.resource, scope) && group.job && activeJobs.has(group.job.status));
      const cadence = current.current.isError || mutation.isError ? 60_000 : busy ? 10_000 : 300_000;
      if (!current.current.isFetching && now >= blockedUntil.current && now - lastRead.current >= cadence) void current.current.refetch();
      if (scope === 'market' && now - lastFallback.current >= 300_000) {
        lastFallback.current = now;
        void client.refetchQueries({ predicate: (query) => marketDomains.has(String(query.queryKey[0])), type: 'active' });
      }
      flushVersions();
    }, 10_000);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', focus);
      document.removeEventListener('visibilitychange', visibility);
    };
  }, [client, flushVersions, mutation.isError, scope]);

  const dates = useMemo(() => Object.fromEntries((status.data?.groups ?? []).map((group) => [group.market, group.market_date])), [status.data]);
  return {
    status: status.data,
    error: status.isError || mutation.isError,
    pending: mutation.isPending,
    dates,
    retry: (resource: Resource) => submit([resource], RefreshRequest.mode.RETRY),
  };
}
