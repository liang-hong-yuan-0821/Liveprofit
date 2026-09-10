/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Envelope_AgentPromptDTO_ } from '../models/Envelope_AgentPromptDTO_';
import type { Envelope_AgentPromptListData_ } from '../models/Envelope_AgentPromptListData_';
import type { Envelope_AgentTopologyDTO_ } from '../models/Envelope_AgentTopologyDTO_';
import type { UpsertAgentPromptRequest } from '../models/UpsertAgentPromptRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AgentsService {
    /**
     * Get Agents Topology
     * 静态全局拓扑（全层形态）+ 节点提示词编辑性/已自定义标注。
     * @returns Envelope_AgentTopologyDTO_ Successful Response
     * @throws ApiError
     */
    public static getAgentsTopologyApiV1AgentsTopologyGet(): CancelablePromise<Envelope_AgentTopologyDTO_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/agents/topology',
        });
    }
    /**
     * List Agent Prompts
     * 全部可编辑节点的默认提示词 + 覆盖叠加列表。
     * @returns Envelope_AgentPromptListData_ Successful Response
     * @throws ApiError
     */
    public static listAgentPromptsApiV1AgentsPromptsGet(): CancelablePromise<Envelope_AgentPromptListData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/agents/prompts',
        });
    }
    /**
     * Upsert Agent Prompt
     * 保存/更新单 Agent 提示词覆盖（对新建任务生效；进行中任务不受影响）。
     * @param nodeId
     * @param requestBody
     * @returns Envelope_AgentPromptDTO_ Successful Response
     * @throws ApiError
     */
    public static upsertAgentPromptApiV1AgentsPromptsNodeIdPut(
        nodeId: string,
        requestBody: UpsertAgentPromptRequest,
    ): CancelablePromise<Envelope_AgentPromptDTO_> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/v1/agents/prompts/{node_id}',
            path: {
                'node_id': nodeId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Reset Agent Prompt
     * 恢复默认提示词（删除覆盖；幂等）。
     * @param nodeId
     * @returns Envelope_AgentPromptDTO_ Successful Response
     * @throws ApiError
     */
    public static resetAgentPromptApiV1AgentsPromptsNodeIdDelete(
        nodeId: string,
    ): CancelablePromise<Envelope_AgentPromptDTO_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/v1/agents/prompts/{node_id}',
            path: {
                'node_id': nodeId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
