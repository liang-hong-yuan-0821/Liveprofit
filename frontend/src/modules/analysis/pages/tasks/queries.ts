import { useInfiniteQuery } from '@tanstack/react-query';
import { AnalysisTasksService } from '../../../../api/generated/services/AnalysisTasksService';
import { requestEnvelope } from '../../../../api/client';
import { queryKeys } from '../../../../api/queryKeys';
import type { TaskListData } from '../../../../api/generated';

// 任务中心：服务端状态筛选 + Cursor（keyset）分页。
// Query Key 固定包含 limit 与服务端 status；cursor 由 useInfiniteQuery 的 pageParam 承载。
export type TaskListStatusFilter = 'all' | 'active' | 'succeeded' | 'failed' | 'cancelled';

export const TASK_LIST_LIMIT = 20;

export interface TaskListPage {
  items: TaskListData['items'];
  next_cursor: string | null;
}

export function useAnalysisTasksQuery(status: TaskListStatusFilter) {
  return useInfiniteQuery({
    queryKey: queryKeys.analysisTasks.list({ status, limit: TASK_LIST_LIMIT }),
    initialPageParam: undefined as string | undefined,
    queryFn: async ({ pageParam }): Promise<TaskListPage> => {
      const envelope = await requestEnvelope<TaskListData>(
        AnalysisTasksService.listTasksApiV1AnalysisTasksGet(pageParam, TASK_LIST_LIMIT, status === 'all' ? 'all' : status),
      );
      return { items: envelope.data.items, next_cursor: envelope.meta.next_cursor ?? null };
    },
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
}
