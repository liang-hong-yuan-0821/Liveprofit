// Agent 提示词管理 Query/Mutation（单Agent重跑与提示词编辑方案 3.5）。
// 静态全局拓扑与提示词列表为低频数据：无轮询、staleTime 走全局默认。

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AgentsService } from '../../../../api/generated/services/AgentsService';
import { requestEnvelope } from '../../../../api/client';
import { queryKeys } from '../../../../api/queryKeys';
import type {
  AgentPromptDTO,
  AgentPromptListData,
  AgentTopologyDTO,
} from '../../../../api/generated';

export function useAgentsTopologyQuery() {
  return useQuery({
    queryKey: queryKeys.agentsTopology.detail('full'),
    queryFn: async (): Promise<AgentTopologyDTO> =>
      (await requestEnvelope<AgentTopologyDTO>(
        AgentsService.getAgentsTopologyApiV1AgentsTopologyGet(),
      )).data,
  });
}

export function useAgentsPromptsQuery(enabled: boolean = true) {
  return useQuery({
    queryKey: queryKeys.agentPrompts.detail('all'),
    enabled,
    queryFn: async (): Promise<AgentPromptListData> =>
      (await requestEnvelope<AgentPromptListData>(
        AgentsService.listAgentPromptsApiV1AgentsPromptsGet(),
      )).data,
  });
}

export function useUpdateAgentPromptMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ nodeId, promptText }: { nodeId: string; promptText: string }) =>
      (await requestEnvelope<AgentPromptDTO>(
        AgentsService.upsertAgentPromptApiV1AgentsPromptsNodeIdPut(
          encodeURIComponent(nodeId),
          { prompt_text: promptText },
        ),
      )).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentsTopology.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentPrompts.all });
    },
  });
}

export function useResetAgentPromptMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (nodeId: string) =>
      (await requestEnvelope<AgentPromptDTO>(
        AgentsService.resetAgentPromptApiV1AgentsPromptsNodeIdDelete(
          encodeURIComponent(nodeId),
        ),
      )).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentsTopology.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.agentPrompts.all });
    },
  });
}
