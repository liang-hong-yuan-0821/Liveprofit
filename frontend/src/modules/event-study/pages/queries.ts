import { useMutation, useQuery } from '@tanstack/react-query';
import { EventStudiesService } from '../../../api/generated/services/EventStudiesService';
import { requestEnvelope } from '../../../api/client';
import { queryKeys } from '../../../api/queryKeys';
import type { AssetListData, PredictionData, PredictionRequest } from '../../../api/generated';

// 事件研究：同步一次性预测（不创建分析任务、不进任务时间线、不保存为全局状态）。
// 503/504 仅手动重试；表单驱动 mutation 不自动重试。

export function useEventStudyAssetsQuery() {
  return useQuery({
    queryKey: queryKeys.eventStudyAssets.all,
    queryFn: async (): Promise<AssetListData> =>
      (await requestEnvelope<AssetListData>(EventStudiesService.listAssetsApiV1EventStudiesAssetsGet())).data,
  });
}

export function usePredictionMutation() {
  return useMutation({
    mutationFn: async (request: PredictionRequest): Promise<PredictionData> =>
      (await requestEnvelope<PredictionData>(EventStudiesService.predictApiV1EventStudiesPredictionsPost(request))).data,
  });
}
