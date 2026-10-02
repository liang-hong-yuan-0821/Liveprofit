import { useInfiniteQuery, useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { WatchlistsService } from '../../api/generated/services/WatchlistsService';
import { PortfoliosService } from '../../api/generated/services/PortfoliosService';
import { requestEnvelope, toApiError } from '../../api/client';
import { queryKeys } from '../../api/queryKeys';
import type {
  DeleteResultData,
  PortfolioDTO,
  PortfolioPositionMutationData,
  PortfolioPositionsData,
  WatchlistDTO,
  WatchlistItemCreateRequest,
  WatchlistItemMutationData,
  WatchlistItemOrderUpdateRequest,
  WatchlistItemsData,
  WatchlistOrderData,
} from '../../api/generated';

// 自选与组合两个聚合：不共享 Query、Mutation、revision；写入成功、409 冲突或资源 404 后
// 精准失效各自父/子 Query，以服务端 DTO 重渲染；不做乐观排序/删除或客户端并发合并。

const CONFLICT_CODES = [
  'REVISION_CONFLICT',
  'WATCHLIST_ITEM_ORDER_CONFLICT',
  'PORTFOLIO_POSITION_CONFLICT',
] as const;

export function isConflictCode(code: string): boolean {
  return (CONFLICT_CODES as readonly string[]).includes(code);
}

function recoverOnConflict(queryClient: QueryClient, domain: { all: readonly unknown[] }) {
  return (error: unknown) => {
    const apiError = toApiError(error);
    // 409 并发冲突或资源 404：重新拉取父/子资源作为下一轮操作基线
    if (isConflictCode(apiError.code) || apiError.status === 404 || apiError.code === 'RESOURCE_NOT_FOUND') {
      void queryClient.invalidateQueries({ queryKey: domain.all });
    }
  };
}

// ---------- 自选分组与标的 ----------

export const WATCHLIST_PAGE_LIMIT = 20;

export function useWatchlistsQuery() {
  return useInfiniteQuery({
    queryKey: queryKeys.watchlists.all,
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) => {
      const envelope = await requestEnvelope<{ items: WatchlistDTO[] }>(
        WatchlistsService.listWatchlistsApiV1WatchlistsGet(pageParam, WATCHLIST_PAGE_LIMIT),
      );
      return { items: envelope.data.items, nextCursor: envelope.meta.next_cursor ?? null };
    },
    getNextPageParam: (lastPage) => lastPage.nextCursor ?? undefined,
  });
}

export function useCreateWatchlistMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (name: string): Promise<WatchlistDTO> =>
      (await requestEnvelope<WatchlistDTO>(WatchlistsService.createWatchlistApiV1WatchlistsPost({ name }))).data,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.watchlists.all }),
  });
}

export function useRenameWatchlistMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { watchlistId: string; name: string; expectedVersion: number }): Promise<WatchlistDTO> =>
      (
        await requestEnvelope<WatchlistDTO>(
          WatchlistsService.renameWatchlistApiV1WatchlistsWatchlistIdPatch(vars.watchlistId, {
            name: vars.name,
            expected_version: vars.expectedVersion,
          }),
        )
      ).data,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.watchlists.all }),
    onError: recoverOnConflict(queryClient, queryKeys.watchlists),
  });
}

export function useDeleteWatchlistMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { watchlistId: string; expectedVersion: number }): Promise<DeleteResultData> =>
      (
        await requestEnvelope<DeleteResultData>(
          WatchlistsService.deleteWatchlistApiV1WatchlistsWatchlistIdDelete(vars.watchlistId, vars.expectedVersion),
        )
      ).data,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.watchlists.all }),
    onError: recoverOnConflict(queryClient, queryKeys.watchlists),
  });
}

export interface WatchlistItemsState {
  revision: number;
  items: NonNullable<WatchlistItemsData['items']>;
}

export function useWatchlistItemsQuery(watchlistId: string | null) {
  return useQuery({
    queryKey: queryKeys.watchlists.detail(watchlistId ?? ''),
    enabled: watchlistId !== null,
    queryFn: async (): Promise<WatchlistItemsState> => {
      const data = (
        await requestEnvelope<WatchlistItemsData>(
          WatchlistsService.listWatchlistItemsApiV1WatchlistsWatchlistIdItemsGet(watchlistId as string),
        )
      ).data;
      return { revision: data.watchlist_revision, items: data.items };
    },
  });
}

export function useAddWatchlistItemMutation(watchlistId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { market: string; symbol: string; expectedWatchlistRevision: number }): Promise<WatchlistItemMutationData> =>
      (
        await requestEnvelope<WatchlistItemMutationData>(
          WatchlistsService.addWatchlistItemApiV1WatchlistsWatchlistIdItemsPost(watchlistId, {
            market: vars.market as WatchlistItemCreateRequest['market'],
            symbol: vars.symbol,
            expected_watchlist_revision: vars.expectedWatchlistRevision,
          }),
        )
      ).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.watchlists.all });
    },
    onError: recoverOnConflict(queryClient, queryKeys.watchlists),
  });
}

export function useReorderWatchlistItemsMutation(watchlistId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { expectedWatchlistRevision: number; items: { market: string; symbol: string; display_order: number }[] }): Promise<WatchlistOrderData> =>
      (
        await requestEnvelope<WatchlistOrderData>(
          WatchlistsService.reorderWatchlistItemsApiV1WatchlistsWatchlistIdItemsOrderPut(watchlistId, {
            expected_watchlist_revision: vars.expectedWatchlistRevision,
            items: vars.items as NonNullable<WatchlistItemOrderUpdateRequest['items']>,
          }),
        )
      ).data,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.watchlists.all }),
    onError: recoverOnConflict(queryClient, queryKeys.watchlists),
  });
}

export function useRemoveWatchlistItemMutation(watchlistId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { itemId: string; expectedWatchlistRevision: number }): Promise<DeleteResultData> =>
      (
        await requestEnvelope<DeleteResultData>(
          WatchlistsService.removeWatchlistItemApiV1WatchlistsWatchlistIdItemsItemIdDelete(
            watchlistId,
            vars.itemId,
            vars.expectedWatchlistRevision,
          ),
        )
      ).data,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.watchlists.all }),
    onError: recoverOnConflict(queryClient, queryKeys.watchlists),
  });
}

// ---------- 组合与持仓 ----------

export function usePortfoliosQuery() {
  return useInfiniteQuery({
    queryKey: queryKeys.portfolios.all,
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }) => {
      const envelope = await requestEnvelope<{ items: PortfolioDTO[] }>(
        PortfoliosService.listPortfoliosApiV1PortfoliosGet(pageParam, WATCHLIST_PAGE_LIMIT),
      );
      return { items: envelope.data.items, nextCursor: envelope.meta.next_cursor ?? null };
    },
    getNextPageParam: (lastPage) => lastPage.nextCursor ?? undefined,
  });
}

export function useCreatePortfolioMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (name: string): Promise<PortfolioDTO> =>
      (await requestEnvelope<PortfolioDTO>(PortfoliosService.createPortfolioApiV1PortfoliosPost({ name }))).data,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.portfolios.all }),
  });
}

export interface UpdatePortfolioVariables {
  portfolioId: string;
  name: string;
  totalAssets: number;
  availableCash: number;
  riskPerTradePct: number;
  minRiskRewardRatio: number;
  maxTotalPositionPct: number;
  maxSingleStockPct: number;
  maxSectorPct: number;
  maxPortfolioOpenRiskPct: number;
  maxSectorOpenRiskPct: number;
  maxDailyNewRiskPct: number;
  maxDrawdownPct: number;
  maxDailyLossPct: number;
  netAssetValue: number | null;
  peakNetAssetValue: number | null;
  dayStartNetAssetValue: number | null;
  riskFactsAsOf: string | null;
  riskProfile?: PortfolioDTO["risk_profile"];
  expectedVersion: number;
}

export function useUpdatePortfolioMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (vars: UpdatePortfolioVariables): Promise<PortfolioDTO> =>
      (
        await requestEnvelope<PortfolioDTO>(
          PortfoliosService.updatePortfolioApiV1PortfoliosPortfolioIdPatch(vars.portfolioId, {
            name: vars.name,
            total_assets: vars.totalAssets,
            available_cash: vars.availableCash,
            risk_per_trade_pct: vars.riskPerTradePct,
            min_risk_reward_ratio: vars.minRiskRewardRatio,
            max_total_position_pct: vars.maxTotalPositionPct,
            max_single_stock_pct: vars.maxSingleStockPct,
            max_sector_pct: vars.maxSectorPct,
            max_portfolio_open_risk_pct: vars.maxPortfolioOpenRiskPct,
            max_sector_open_risk_pct: vars.maxSectorOpenRiskPct,
            max_daily_new_risk_pct: vars.maxDailyNewRiskPct,
            max_drawdown_pct: vars.maxDrawdownPct,
            max_daily_loss_pct: vars.maxDailyLossPct,
            net_asset_value: vars.netAssetValue,
            peak_net_asset_value: vars.peakNetAssetValue,
            day_start_net_asset_value: vars.dayStartNetAssetValue,
            risk_facts_as_of: vars.riskFactsAsOf,
            risk_profile: vars.riskProfile,
            expected_version: vars.expectedVersion,
          }),
        )
      ).data,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.portfolios.all }),
    onError: recoverOnConflict(queryClient, queryKeys.portfolios),
  });
}

export function useDeletePortfolioMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { portfolioId: string; expectedVersion: number }): Promise<DeleteResultData> =>
      (
        await requestEnvelope<DeleteResultData>(
          PortfoliosService.deletePortfolioApiV1PortfoliosPortfolioIdDelete(vars.portfolioId, vars.expectedVersion),
        )
      ).data,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.portfolios.all }),
    onError: recoverOnConflict(queryClient, queryKeys.portfolios),
  });
}

export interface PortfolioPositionsState {
  revision: number;
  items: NonNullable<PortfolioPositionsData['items']>;
}

export function usePositionsQuery(portfolioId: string | null) {
  return useQuery({
    queryKey: queryKeys.portfolios.detail(portfolioId ?? ''),
    enabled: portfolioId !== null,
    queryFn: async (): Promise<PortfolioPositionsState> => {
      const data = (
        await requestEnvelope<PortfolioPositionsData>(
          PortfoliosService.listPositionsApiV1PortfoliosPortfolioIdPositionsGet(portfolioId as string),
        )
      ).data;
      return { revision: data.portfolio_revision, items: data.items };
    },
  });
}

export function useUpsertPositionMutation(portfolioId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (vars: {
      market: string;
      symbol: string;
      quantity: number;
      averageCost: number;
      activeStopPrice: number | null;
      expectedPortfolioRevision: number;
    }): Promise<PortfolioPositionMutationData> =>
      (
        await requestEnvelope<PortfolioPositionMutationData>(
          PortfoliosService.upsertPositionApiV1PortfoliosPortfolioIdPositionsMarketSymbolPut(
            portfolioId,
            vars.market,
            vars.symbol,
            {
              quantity: vars.quantity, average_cost: vars.averageCost,
              active_stop_price: vars.activeStopPrice,
              expected_portfolio_revision: vars.expectedPortfolioRevision,
            },
          ),
        )
      ).data,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.portfolios.all }),
    onError: recoverOnConflict(queryClient, queryKeys.portfolios),
  });
}

export function useRemovePositionMutation(portfolioId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { market: string; symbol: string; expectedPortfolioRevision: number }): Promise<DeleteResultData> =>
      (
        await requestEnvelope<DeleteResultData>(
          PortfoliosService.removePositionApiV1PortfoliosPortfolioIdPositionsMarketSymbolDelete(
            portfolioId,
            vars.market,
            vars.symbol,
            vars.expectedPortfolioRevision,
          ),
        )
      ).data,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: queryKeys.portfolios.all }),
    onError: recoverOnConflict(queryClient, queryKeys.portfolios),
  });
}
