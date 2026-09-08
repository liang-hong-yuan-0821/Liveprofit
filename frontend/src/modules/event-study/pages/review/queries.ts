import { useMutation, useQuery } from '@tanstack/react-query';
import { EventStudiesService } from '../../../../api/generated/services/EventStudiesService';
import { requestEnvelope } from '../../../../api/client';
import { queryKeys } from '../../../../api/queryKeys';
import type {
  ComputeData,
  ComputeRequest,
  ConfirmImpactsData,
  ConfirmImpactsRequest,
  ImpactDraftListData,
  PendingEventListData,
  PrelabelData,
  PrelabelRequest,
  ReviewBatchData,
  ReviewBatchRequest,
} from '../../../../api/generated';

// 事件研究审核：批量提交/预填/补算均可能耗时数分钟（内联影响计算 + LLM 调用），
// 超时放宽到 300s；页面按 10 行/块分批提交规避单请求过大。
const REVIEW_TIMEOUT_MS = 300_000;

export function usePendingEventsQuery() {
  return useQuery({
    queryKey: queryKeys.eventStudyReview.list({ kind: 'pending-events' }),
    queryFn: async (): Promise<PendingEventListData> =>
      (
        await requestEnvelope<PendingEventListData>(
          EventStudiesService.listPendingEventsApiV1EventStudiesReviewPendingEventsGet(),
        )
      ).data,
  });
}

export function usePrelabelMutation() {
  return useMutation({
    mutationFn: async (request: PrelabelRequest): Promise<PrelabelData> =>
      (
        await requestEnvelope<PrelabelData>(
          EventStudiesService.prelabelApiV1EventStudiesReviewPrelabelPost(request),
          { timeoutMs: REVIEW_TIMEOUT_MS },
        )
      ).data,
  });
}

export function useBatchMutation() {
  return useMutation({
    mutationFn: async (request: ReviewBatchRequest): Promise<ReviewBatchData> =>
      (
        await requestEnvelope<ReviewBatchData>(
          EventStudiesService.submitBatchApiV1EventStudiesReviewBatchPost(request),
          { timeoutMs: REVIEW_TIMEOUT_MS },
        )
      ).data,
  });
}

export function useComputeMutation() {
  return useMutation({
    mutationFn: async ({ eventId, request }: { eventId: number; request: ComputeRequest }): Promise<ComputeData> =>
      (
        await requestEnvelope<ComputeData>(
          EventStudiesService.computeApiV1EventStudiesReviewEventsEventIdComputePost(eventId, request),
          { timeoutMs: REVIEW_TIMEOUT_MS },
        )
      ).data,
  });
}

export function useImpactDraftsQuery() {
  return useQuery({
    queryKey: queryKeys.eventStudyReview.list({ kind: 'impact-drafts' }),
    queryFn: async (): Promise<ImpactDraftListData> =>
      (
        await requestEnvelope<ImpactDraftListData>(
          EventStudiesService.listImpactDraftsApiV1EventStudiesReviewImpactDraftsGet(),
        )
      ).data,
  });
}

export function useConfirmImpactsMutation() {
  return useMutation({
    mutationFn: async ({
      eventId,
      request,
    }: {
      eventId: number;
      request: ConfirmImpactsRequest;
    }): Promise<ConfirmImpactsData> =>
      (
        await requestEnvelope<ConfirmImpactsData>(
          EventStudiesService.confirmImpactsApiV1EventStudiesReviewImpactDraftsEventIdConfirmPost(eventId, request),
          { timeoutMs: REVIEW_TIMEOUT_MS },
        )
      ).data,
  });
}
