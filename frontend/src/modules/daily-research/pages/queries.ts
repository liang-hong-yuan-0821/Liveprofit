import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  DailyResearchService,
  type AssessmentReviewData,
  type AssessmentReviewRequest,
  type DailyResearchManualData,
  DailyResearchManualRequest,
  type DailyResearchRunDetail,
  type DailyResearchRunSummary,
  type DisputedAssessment,
} from '../../../api/generated';
import { requestEnvelope } from '../../../api/client';
import { queryKeys } from '../../../api/queryKeys';

export type DailyResearchKind = 'news' | 'quant';

export function useDailyResearchRuns(kind: DailyResearchKind) {
  return useQuery({
    queryKey: queryKeys.dailyResearch.list({ kind }),
    queryFn: async (): Promise<DailyResearchRunSummary[]> =>
      (await requestEnvelope<{ items: DailyResearchRunSummary[] }>(
        DailyResearchService.listResearchRunsApiV1DailyResearchRunsGet(kind, null, 30),
      )).data.items,
    refetchInterval: (query) =>
      query.state.data?.some((run) => ['PENDING', 'QUEUED', 'RUNNING', 'RETRYING', 'CANCEL_REQUESTED'].includes(run.status))
        ? 5_000
        : 30_000,
    refetchOnWindowFocus: true,
  });
}

export function useDailyResearchRun(taskId: string | null) {
  return useQuery({
    queryKey: queryKeys.dailyResearch.detail(taskId ?? ''),
    enabled: Boolean(taskId),
    queryFn: async (): Promise<DailyResearchRunDetail | null> =>
      (await requestEnvelope<{ item: DailyResearchRunDetail | null }>(
        DailyResearchService.getResearchRunApiV1DailyResearchRunsTaskIdGet(taskId as string),
      )).data.item,
    refetchInterval: (query) => {
      const status = query.state.data?.run.status;
      return status && ['PENDING', 'QUEUED', 'RUNNING', 'RETRYING', 'CANCEL_REQUESTED'].includes(status)
        ? 5_000
        : false;
    },
  });
}

export function useLatestEventResearchRun(eventId: number | null) {
  return useQuery({
    queryKey: [...queryKeys.dailyResearch.detail('latest-event'), eventId],
    enabled: eventId !== null && Number.isInteger(eventId) && eventId > 0,
    queryFn: async (): Promise<DailyResearchRunDetail | null> =>
      (await requestEnvelope<{ item: DailyResearchRunDetail | null }>(
        DailyResearchService.getLatestResearchRunApiV1DailyResearchRunsLatestGet('news', eventId),
      )).data.item,
    staleTime: 30_000,
  });
}

export function useDisputedAssessments() {
  return useQuery({
    queryKey: [...queryKeys.dailyResearch.all, 'disputed-assessments'],
    queryFn: async (): Promise<DisputedAssessment[]> =>
      (await requestEnvelope<{ items: DisputedAssessment[] }>(
        DailyResearchService.listDisputedEventAssessmentsApiV1DailyResearchAssessmentsDisputedGet(100),
      )).data.items,
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
  });
}

export function useReviewDisputedAssessment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { assessmentId: string; payload: AssessmentReviewRequest }): Promise<AssessmentReviewData> => {
      const idempotencyKey = globalThis.crypto?.randomUUID?.() ??
        `review-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
      return (await requestEnvelope<{ item: AssessmentReviewData }>(
        DailyResearchService.reviewDisputedEventAssessmentApiV1DailyResearchAssessmentsAssessmentIdReviewPost(
          input.assessmentId,
          input.payload,
          idempotencyKey,
        ),
      )).data.item;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.dailyResearch.all });
    },
  });
}

export function useStartDailyResearch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (kind: DailyResearchKind): Promise<DailyResearchManualData> => {
      const idempotencyKey = globalThis.crypto?.randomUUID?.() ??
        `manual-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
      return (await requestEnvelope<DailyResearchManualData>(
        DailyResearchService.createManualResearchRunApiV1DailyResearchRunsPost(
          { kind: kind === 'news' ? DailyResearchManualRequest.kind.NEWS : DailyResearchManualRequest.kind.QUANT }, idempotencyKey,
        ),
      )).data;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.dailyResearch.all });
    },
  });
}
