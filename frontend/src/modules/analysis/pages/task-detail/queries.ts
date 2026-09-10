import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AnalysisTasksService } from '../../../../api/generated/services/AnalysisTasksService';
import { ReportsService } from '../../../../api/generated/services/ReportsService';
import { requestEnvelope } from '../../../../api/client';
import { queryKeys } from '../../../../api/queryKeys';
import type { DeleteResultData, ExecutionLogsDTO, GraphTopologyDTO, ReportDTO, TaskDTO } from '../../../../api/generated';

// 任务详情：Task REST Query 是正式状态唯一真相；非终态每 5 秒 refetch；
// 成功时启用 Report Query；取消 Mutation 成功后以服务端 TaskDTO 更新缓存。
export const TERMINAL_STATUSES: string[] = ['SUCCEEDED', 'FAILED', 'CANCELLED'];

export function isTerminalStatus(status: string): boolean {
  return TERMINAL_STATUSES.includes(status);
}

export function useTaskQuery(taskId: string) {
  return useQuery({
    queryKey: queryKeys.analysisTask.detail(taskId),
    queryFn: async (): Promise<TaskDTO> =>
      (await requestEnvelope<TaskDTO>(AnalysisTasksService.getTaskApiV1AnalysisTasksTaskIdGet(taskId))).data,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && !isTerminalStatus(status) ? 5000 : false;
    },
  });
}

export function useReportQuery(taskId: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.analysisReport.detail(taskId),
    enabled,
    queryFn: async (): Promise<ReportDTO> =>
      (await requestEnvelope<ReportDTO>(ReportsService.getReportApiV1AnalysisTasksTaskIdReportGet(taskId))).data,
  });
}

// 执行调用日志树：非终态每 5s 轮询"生长"，终态停止（REST Task Query 每 5s 已提供状态真值）。
export function useExecutionLogsQuery(taskId: string, enabled: boolean, terminal: boolean) {
  return useQuery({
    queryKey: queryKeys.analysisTask.executionLogs(taskId),
    enabled,
    queryFn: async (): Promise<ExecutionLogsDTO> =>
      (
        await requestEnvelope<ExecutionLogsDTO>(
          AnalysisTasksService.getExecutionLogsApiV1AnalysisTasksTaskIdExecutionLogsGet(taskId),
        )
      ).data,
    refetchInterval: terminal ? false : 5000,
  });
}

// 单Agent重跑：终态任务从指定节点续跑（上游复用 checkpoint、目标+下游重算）。
// onSuccess setQueryData 后 status 变 PENDING → useTaskQuery 的 refetchInterval
// 回调读 query.state.data?.status 自动恢复 5s 轮询（终态→非终态翻转）；同时
// invalidate 拓扑/日志（新 attempt 目录）与列表/看板。
export function useRerunTaskMutation(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (nodeId: string): Promise<TaskDTO> =>
      (
        await requestEnvelope<TaskDTO>(
          AnalysisTasksService.rerunTaskApiV1AnalysisTasksTaskIdRerunPost(taskId, {
            node_id: nodeId,
          }),
        )
      ).data,
    onSuccess: (dto) => {
      queryClient.setQueryData(queryKeys.analysisTask.detail(taskId), dto);
      void queryClient.invalidateQueries({ queryKey: queryKeys.analysisTask.graphTopology(taskId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.analysisTask.executionLogs(taskId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.analysisTasks.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.analysisDashboard.all });
    },
  });
}

// 图拓扑（静态结构 + 运行状态叠加）：与执行日志同节奏轮询，终态停止。
export function useGraphTopologyQuery(taskId: string, enabled: boolean, terminal: boolean) {
  return useQuery({
    queryKey: queryKeys.analysisTask.graphTopology(taskId),
    enabled,
    queryFn: async (): Promise<GraphTopologyDTO> =>
      (
        await requestEnvelope<GraphTopologyDTO>(
          AnalysisTasksService.getGraphTopologyApiV1AnalysisTasksTaskIdGraphTopologyGet(taskId),
        )
      ).data,
    refetchInterval: terminal ? false : 5000,
  });
}

export function useDeleteTaskMutation(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<DeleteResultData> =>
      (
        await requestEnvelope<DeleteResultData>(
          AnalysisTasksService.deleteTaskApiV1AnalysisTasksTaskIdDelete(taskId),
        )
      ).data,
    onSuccess: () => {
      // 删除成功：任务详情失效，同时刷新任务中心与看板域
      void queryClient.invalidateQueries({ queryKey: queryKeys.analysisTasks.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.analysisDashboard.all });
      void queryClient.removeQueries({ queryKey: queryKeys.analysisTask.detail(taskId) });
    },
  });
}

export function useCancelTaskMutation(taskId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<TaskDTO> =>
      (await requestEnvelope<TaskDTO>(AnalysisTasksService.cancelTaskApiV1AnalysisTasksTaskIdCancelPost(taskId))).data,
    onSuccess: (dto) => {
      // 取消结果以服务端 TaskDTO 为准
      queryClient.setQueryData(queryKeys.analysisTask.detail(taskId), dto);
    },
  });
}
