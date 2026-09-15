import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { MarketDataService } from '../../../api/generated/services/MarketDataService';
import { MacroInformationService } from '../../../api/generated/services/MacroInformationService';
import { requestEnvelope } from '../../../api/client';
import { queryKeys } from '../../../api/queryKeys';
import type {
  BarsData,
  ConceptTreeData,
  MacroInformationData,
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
  from: string;
  to: string;
}

export const CONCEPT_TREE_LIMIT = 30;

// 概念树（板块概念Treemap方案 3.4）：热度 top 30 + 成分股截断 top 100。
// from→as_of 透传（m6 定稿同款口径）、to 忽略（后端只认 as_of）。
export function useConceptTreeQuery(filters: ConceptTreeFilters) {
  return useQuery({
    queryKey: queryKeys.conceptTree.list({ ...filters, limit: CONCEPT_TREE_LIMIT }),
    queryFn: async (): Promise<ConceptTreeData> =>
      (
        await requestEnvelope<ConceptTreeData>(
          MarketDataService.conceptTreeApiV1MarketDataConceptsTreeGet(
            filters.market,
            filters.interval,
            filters.from,
            filters.to,
            CONCEPT_TREE_LIMIT,
            filters.from,
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
    queryFn: async (): Promise<BarsData> =>
      (
        await requestEnvelope<BarsData>(
          MarketDataService.stockBarsApiV1MarketDataStocksSymbolBarsGet(
            symbol,
            filters.market,
            filters.interval,
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
