// 量化策略查询封装（plan 4.4.1）：列表/详情/草稿读写/发布/归档 + 信号 cursor 分页。

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { requestEnvelope } from '../../../../api/client';
import { queryKeys } from '../../../../api/queryKeys';
import {
  QuantStrategiesService,
  type QuantStrategyCreateRequest,
  type QuantStrategyDTO,
  type QuantStrategyDraftDTO,
  type QuantStrategyDraftUpdateRequest,
  type QuantStrategyPublishData,
  type QuantStrategyTemplateDTO,
  type QuantStrategyVersionDTO,
} from '../../../../api/generated';
import { QuantSignalsService } from '../../../../api/generated/services/QuantSignalsService';
import type { QuantSignalPageData } from '../../../../api/generated';

export type QuantSignalKind = 'buy' | 'holding' | 'orders' | 'errors';

export function useQuantStrategyTemplates() {
  return useQuery({
    queryKey: [...queryKeys.quantStrategies.all, 'templates'],
    queryFn: async (): Promise<QuantStrategyTemplateDTO[]> =>
      (await requestEnvelope<{ items: QuantStrategyTemplateDTO[] }>(
        QuantStrategiesService.listStrategyTemplatesApiV1QuantStrategyTemplatesGet(),
      )).data.items,
    staleTime: Infinity,
  });
}

export function useListQuantStrategies() {
  return useQuery({
    queryKey: queryKeys.quantStrategies.lists(),
    queryFn: async (): Promise<QuantStrategyDTO[]> =>
      (await requestEnvelope<{ items: QuantStrategyDTO[] }>(
        QuantStrategiesService.listStrategiesApiV1QuantStrategiesGet(),
      )).data.items,
  });
}

export function useQuantStrategyDraft(strategyId: string | null) {
  return useQuery({
    queryKey: queryKeys.quantStrategies.detail(strategyId ?? 'none'),
    queryFn: async (): Promise<QuantStrategyDraftDTO> =>
      (await requestEnvelope<QuantStrategyDraftDTO>(
        QuantStrategiesService.getDraftApiV1QuantStrategiesStrategyIdDraftGet(strategyId as string),
      )).data,
    enabled: strategyId !== null,
  });
}

export function useCreateQuantStrategyMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: QuantStrategyCreateRequest) =>
      (
        await requestEnvelope<QuantStrategyDTO>(
          QuantStrategiesService.createStrategyApiV1QuantStrategiesPost(payload),
        )
      ).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.quantStrategies.all });
    },
    onError: () => {
      // 409 冲突（STRATEGY_REVISION_CONFLICT 等）：重拉服务端数据，让 expected_version 收敛
      void queryClient.invalidateQueries({ queryKey: queryKeys.quantStrategies.all });
    },
  });
}

export function useSaveDraftMutation(strategyId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (payload: QuantStrategyDraftUpdateRequest) =>
      (
        await requestEnvelope<QuantStrategyDraftDTO>(
          QuantStrategiesService.saveDraftApiV1QuantStrategiesStrategyIdDraftPut(strategyId, payload),
        )
      ).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.quantStrategies.all });
    },
    onError: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.quantStrategies.all });
    },
  });
}

export function usePublishVersionMutation(strategyId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ versionId, expectedVersion }: { versionId: string; expectedVersion: number }) =>
      (
        await requestEnvelope<QuantStrategyPublishData>(
          QuantStrategiesService.publishVersionApiV1QuantStrategiesStrategyIdVersionsVersionIdPublishPost(
            strategyId,
            versionId,
            { expected_version: expectedVersion },
          ),
        )
      ).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.quantStrategies.all });
    },
    onError: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.quantStrategies.all });
    },
  });
}

export function useArchiveVersionMutation(strategyId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (versionId: string) =>
      (
        await requestEnvelope<QuantStrategyVersionDTO>(
          QuantStrategiesService.archiveVersionApiV1QuantStrategiesStrategyIdVersionsVersionIdArchivePost(
            strategyId,
            versionId,
          ),
        )
      ).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.quantStrategies.all });
    },
    onError: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.quantStrategies.all });
    },
  });
}

export function useFormatStrategySourceMutation() {
  return useMutation({
    mutationFn: async (sourceCode: string) =>
      (
        await requestEnvelope<{ source_code: string }>(
          QuantStrategiesService.formatStrategySourceApiV1QuantStrategiesFormatPost({ source_code: sourceCode }),
        )
      ).data,
  });
}

export function useQuantSignalsInfiniteQuery(taskId: string, kind: QuantSignalKind) {
  return useInfiniteQuery({
    queryKey: [...queryKeys.quantSignals.all, taskId, kind],
    queryFn: async ({ pageParam }): Promise<QuantSignalPageData> => {
      // cursor 请求必须同时携带 attempt_no（后端契约）；首页无 cursor 时后端解析最新成功 attempt
      const attemptNo = (pageParam as { attempt_no: number; cursor: string | null } | undefined)?.attempt_no;
      const cursor = (pageParam as { attempt_no: number; cursor: string | null } | undefined)?.cursor ?? null;
      return (
        await requestEnvelope<QuantSignalPageData>(
          QuantSignalsService.listQuantSignalsApiV1AnalysisTasksTaskIdQuantSignalsGet(
            taskId, kind, attemptNo ?? null, cursor, 50,
          ),
        )
      ).data;
    },
    initialPageParam: undefined as { attempt_no: number; cursor: string | null } | undefined,
    getNextPageParam: (lastPage) =>
      lastPage.next_cursor
        ? { attempt_no: lastPage.attempt_no, cursor: lastPage.next_cursor }
        : undefined,
    enabled: Boolean(taskId),
  });
}
