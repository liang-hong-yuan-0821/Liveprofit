import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { MarketDataService } from '../../../api/generated/services/MarketDataService';
import { MacroInformationService } from '../../../api/generated/services/MacroInformationService';
import { requestEnvelope } from '../../../api/client';
import { queryKeys } from '../../../api/queryKeys';
import type {
  BarsData,
  ConceptTreeData,
  MacroInformationData,
  TrendsData,
} from '../../../api/generated';

// 大盘区块的页面私有 Query；目录前端写死（MARKET_INDEX_CATALOG），
// useMarketAssetsQuery 随目录端点一并删除（证券市场数据库统一方案 3.3.4）。

export interface BarsParams {
  market: string;
  interval: string;
  from: string;
  to: string;
}

export function useMarketBarsQuery(symbol: string, params: BarsParams, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.marketBars.list({ symbol, ...params }),
    enabled,
    refetchOnMount: false, // 摘要与展开图复用缓存；失败仍可显式重试。
    // 扩展请求（loadedFrom 前移 → 新 key）期间旧数据继续渲染，不闪 LoadingState（§3.4）
    placeholderData: (prev) => prev,
    queryFn: async (): Promise<BarsData> =>
      (
        await requestEnvelope<BarsData>(
          MarketDataService.indexBarsApiV1MarketDataIndicesSymbolBarsGet(
            symbol,
            params.market,
            params.interval,
            params.from,
            params.to,
          ),
        )
      ).data,
  });
}

export interface ConceptTreeFilters {
  market: string;
  interval: string;
  asOf?: string;
}

export const CONCEPT_TREE_LIMIT = 30;

// 概念树（板块概念Treemap方案 3.4）：热度 top 30 + 成分股截断 top 100。
// 不传 asOf 表示最近可展示日；显式日期只读历史快照。
export function useConceptTreeQuery(filters: ConceptTreeFilters) {
  return useQuery({
    queryKey: queryKeys.conceptTree.list({ ...filters, limit: CONCEPT_TREE_LIMIT }),
    placeholderData: (previous) => previous,
    queryFn: async (): Promise<ConceptTreeData> =>
      (
        await requestEnvelope<ConceptTreeData>(
          MarketDataService.conceptTreeApiV1MarketDataConceptsTreeGet(
            filters.market,
            filters.interval,
            CONCEPT_TREE_LIMIT,
            filters.asOf,
          ),
        )
      ).data,
  });
}

export interface ConceptBarsFilters {
  market: string;
  source: string;
  interval: string;
  from: string;
  to: string;
}

export function useConceptBarsQuery(sectorCode: string, filters: ConceptBarsFilters, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.conceptBars.list({ sectorCode, ...filters }),
    enabled,
    placeholderData: (previous) => previous,
    queryFn: async (): Promise<BarsData> =>
      (
        await requestEnvelope<BarsData>(
          MarketDataService.sectorBarsApiV1MarketDataConceptsSectorCodeBarsGet(
            sectorCode,
            filters.market,
            filters.interval,
            filters.from,
            filters.to,
            filters.source,
          ),
        )
      ).data,
  });
}

export interface StockBarsFilters {
  market: string;
  interval: string;
  from: string;
  to: string;
}

export function useStockBarsQuery(symbol: string, filters: StockBarsFilters, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.stockBars.list({ symbol, ...filters }),
    enabled,
    placeholderData: (previous) => previous,
    queryFn: async (): Promise<BarsData> =>
      (
        await requestEnvelope<BarsData>(
          MarketDataService.stockBarsApiV1MarketDataStocksSymbolBarsGet(
            symbol,
            filters.market,
            filters.interval,
            filters.from,
            filters.to,
            'cache_only',
          ),
        )
      ).data,
  });
}

export interface TrendsFilters {
  from: string;
  to: string;
}

// 趋势对比两 Tab（趋势对比面板方案 4.4.1）：组语义由后端常量承载，前端只传区间。
export function useCapTierTrendsQuery(filters: TrendsFilters) {
  return useQuery({
    queryKey: queryKeys.marketCapTierTrends.list({ ...filters }),
    queryFn: async (): Promise<TrendsData> =>
      (
        await requestEnvelope<TrendsData>(
          MarketDataService.capTierTrendsApiV1MarketDataTrendsCapTiersGet(
            filters.from,
            filters.to,
          ),
        )
      ).data,
  });
}

export function useBoardTrendsQuery(filters: TrendsFilters) {
  return useQuery({
    queryKey: queryKeys.marketBoardTrends.list({ ...filters }),
    queryFn: async (): Promise<TrendsData> =>
      (
        await requestEnvelope<TrendsData>(
          MarketDataService.boardTrendsApiV1MarketDataTrendsBoardsGet(
            filters.from,
            filters.to,
          ),
        )
      ).data,
  });
}

export interface MacroInfoFilters {
  market?: string;
  topic?: string;
}

export const MACRO_INFO_LIMIT = 20;

export function useMacroInformationQuery(filters: MacroInfoFilters) {
  return useInfiniteQuery({
    queryKey: queryKeys.macroInformation.list({ ...filters, limit: MACRO_INFO_LIMIT }),
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) => {
      const envelope = await requestEnvelope<MacroInformationData>(
        MacroInformationService.listMacroInformationApiV1MacroInformationGet(
          MACRO_INFO_LIMIT,
          pageParam,
          filters.market ?? undefined,
          filters.topic ?? undefined,
        ),
      );
      return { items: envelope.data.items, nextCursor: envelope.meta.next_cursor ?? null };
    },
    getNextPageParam: (lastPage) => lastPage.nextCursor ?? undefined,
  });
}
