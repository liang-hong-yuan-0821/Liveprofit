import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { MarketAssetsService } from '../../../api/generated/services/MarketAssetsService';
import { MarketDataService } from '../../../api/generated/services/MarketDataService';
import { MacroInformationService } from '../../../api/generated/services/MacroInformationService';
import { requestEnvelope } from '../../../api/client';
import { queryKeys } from '../../../api/queryKeys';
import type {
  BarsData,
  HotConceptsData,
  MacroInformationData,
  MarketAssetsData,
} from '../../../api/generated';

// 大盘三区块的页面私有 Query；目录只读、不维护静态资产或热点名单。

export function useMarketAssetsQuery() {
  return useQuery({
    queryKey: queryKeys.marketAssets.all,
    queryFn: async (): Promise<MarketAssetsData> =>
      (await requestEnvelope<MarketAssetsData>(MarketAssetsService.listMarketAssetsApiV1MarketAssetsGet())).data,
  });
}

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

export interface HotConceptsFilters {
  market: string;
  interval: string;
  from: string;
  to: string;
}

export const HOT_CONCEPTS_LIMIT = 30;

// 首期热点无 Cursor（后端：top_n≤30 全量返回，meta.next_cursor 恒 null）
export function useHotConceptsQuery(filters: HotConceptsFilters) {
  return useQuery({
    queryKey: queryKeys.hotConcepts.list({ ...filters, limit: HOT_CONCEPTS_LIMIT }),
    queryFn: async (): Promise<HotConceptsData> =>
      (
        await requestEnvelope<HotConceptsData>(
          MarketDataService.hotConceptsApiV1MarketDataConceptsHotGet(
            filters.market,
            filters.interval,
            filters.from,
            filters.to,
            HOT_CONCEPTS_LIMIT,
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
