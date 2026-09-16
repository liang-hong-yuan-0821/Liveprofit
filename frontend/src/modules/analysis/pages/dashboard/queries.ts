import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError } from '../../../../api/client';
import { AnalysisDashboardService } from '../../../../api/generated/services/AnalysisDashboardService';
import { AnalysisTasksService } from '../../../../api/generated/services/AnalysisTasksService';
import { requestEnvelope } from '../../../../api/client';
import { queryKeys } from '../../../../api/queryKeys';
import type {
  ActiveTaskDTO,
  AnalysisDashboardDTO,
  MarketWideCreateRequest,
  PendingActionDTO,
  RecentConclusionDTO,
  SingleStockCreateRequest,
  TaskCreatedData,
} from '../../../../api/generated';

// 看板三区块独立 Query（各自 Query Key + select 投影），任一失败仅重试自身。
export type DashboardSection = 'pending_actions' | 'active_tasks' | 'recent_conclusions';

export interface DashboardSectionMap {
  pending_actions: PendingActionDTO[];
  active_tasks: ActiveTaskDTO[];
  recent_conclusions: RecentConclusionDTO[];
}

export function useDashboardSectionQuery<S extends DashboardSection, T = DashboardSectionMap[S]>(
  section: S,
  select?: (dto: AnalysisDashboardDTO) => T,
) {
  return useQuery({
    queryKey: queryKeys.analysisDashboard.detail(section),
    queryFn: async () => (await requestEnvelope<AnalysisDashboardDTO>(AnalysisDashboardService.getDashboardApiV1AnalysisDashboardGet())).data,
    select: (select ?? ((dto: AnalysisDashboardDTO) => dto[section] as unknown as T)) as (dto: AnalysisDashboardDTO) => T,
  });
}

export type CreateAnalysisTaskBody = SingleStockCreateRequest | MarketWideCreateRequest;

export interface CreateTaskVariables {
  request: CreateAnalysisTaskBody;
  idempotencyKey: string;
}

export function useCreateAnalysisTaskMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (vars: CreateTaskVariables): Promise<TaskCreatedData> =>
      (await requestEnvelope<TaskCreatedData>(AnalysisTasksService.createTaskApiV1AnalysisTasksPost(vars.request, vars.idempotencyKey))).data,
    onSuccess: () => {
      // 创建成功后精准失效看板三区块
      void queryClient.invalidateQueries({ queryKey: queryKeys.analysisDashboard.all });
    },
    onError: () => {
      // 组合/策略快照冲突：重拉，让表单里的版本号收敛（重试不再用陈旧 expected_version）
      void queryClient.invalidateQueries({ queryKey: queryKeys.portfolios.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.quantStrategies.all });
    },
  });
}

/** 输入已过期的冲突类错误：重试必然失败，不给「重试」按钮。 */
export function isStaleInputError(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  return (
    error.code === 'PORTFOLIO_SNAPSHOT_CONFLICT' ||
    error.code === 'STRATEGY_VERSION_NOT_PUBLISHED' ||
    error.code === 'STRATEGY_NOT_FOUND'
  );
}
