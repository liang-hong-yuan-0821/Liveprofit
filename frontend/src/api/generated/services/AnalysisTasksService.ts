/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Envelope_DeleteResultData_ } from '../models/Envelope_DeleteResultData_';
import type { Envelope_ExecutionFileDTO_ } from '../models/Envelope_ExecutionFileDTO_';
import type { Envelope_ExecutionLogsDTO_ } from '../models/Envelope_ExecutionLogsDTO_';
import type { Envelope_GraphTopologyDTO_ } from '../models/Envelope_GraphTopologyDTO_';
import type { Envelope_TaskCreatedData_ } from '../models/Envelope_TaskCreatedData_';
import type { Envelope_TaskDTO_ } from '../models/Envelope_TaskDTO_';
import type { Envelope_TaskListData_ } from '../models/Envelope_TaskListData_';
import type { MarketWideCreateRequest } from '../models/MarketWideCreateRequest';
import type { SingleStockCreateRequest } from '../models/SingleStockCreateRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AnalysisTasksService {
    /**
     * Create Task
     * @param requestBody
     * @param idempotencyKey
     * @returns Envelope_TaskCreatedData_ Successful Response
     * @throws ApiError
     */
    public static createTaskApiV1AnalysisTasksPost(
        requestBody: (SingleStockCreateRequest | MarketWideCreateRequest),
        idempotencyKey?: (string | null),
    ): CancelablePromise<Envelope_TaskCreatedData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/analysis-tasks',
            headers: {
                'Idempotency-Key': idempotencyKey,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Tasks
     * @param cursor
     * @param limit
     * @param status
     * @returns Envelope_TaskListData_ Successful Response
     * @throws ApiError
     */
    public static listTasksApiV1AnalysisTasksGet(
        cursor?: (string | null),
        limit: number = 20,
        status: string = 'all',
    ): CancelablePromise<Envelope_TaskListData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/analysis-tasks',
            query: {
                'cursor': cursor,
                'limit': limit,
                'status': status,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Task
     * 删除任务记录（仅终态任务可删；FK 级联删除报告与 Outbox，DELETE 返回 200 envelope）。
     * @param taskId
     * @returns Envelope_DeleteResultData_ Successful Response
     * @throws ApiError
     */
    public static deleteTaskApiV1AnalysisTasksTaskIdDelete(
        taskId: string,
    ): CancelablePromise<Envelope_DeleteResultData_> {
        return __request(OpenAPI, {
            method: 'DELETE',
            url: '/api/v1/analysis-tasks/{task_id}',
            path: {
                'task_id': taskId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Task
     * @param taskId
     * @returns Envelope_TaskDTO_ Successful Response
     * @throws ApiError
     */
    public static getTaskApiV1AnalysisTasksTaskIdGet(
        taskId: string,
    ): CancelablePromise<Envelope_TaskDTO_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/analysis-tasks/{task_id}',
            path: {
                'task_id': taskId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Cancel Task
     * @param taskId
     * @returns Envelope_TaskDTO_ Successful Response
     * @throws ApiError
     */
    public static cancelTaskApiV1AnalysisTasksTaskIdCancelPost(
        taskId: string,
    ): CancelablePromise<Envelope_TaskDTO_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/analysis-tasks/{task_id}/cancel',
            path: {
                'task_id': taskId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Execution Logs
     * @param taskId
     * @returns Envelope_ExecutionLogsDTO_ Successful Response
     * @throws ApiError
     */
    public static getExecutionLogsApiV1AnalysisTasksTaskIdExecutionLogsGet(
        taskId: string,
    ): CancelablePromise<Envelope_ExecutionLogsDTO_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/analysis-tasks/{task_id}/execution-logs',
            path: {
                'task_id': taskId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Execution Log Content
     * @param taskId
     * @param file 相对任务日志目录的文件路径（/ 分隔）
     * @returns Envelope_ExecutionFileDTO_ Successful Response
     * @throws ApiError
     */
    public static getExecutionLogContentApiV1AnalysisTasksTaskIdExecutionLogsContentGet(
        taskId: string,
        file: string,
    ): CancelablePromise<Envelope_ExecutionFileDTO_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/analysis-tasks/{task_id}/execution-logs/content',
            path: {
                'task_id': taskId,
            },
            query: {
                'file': file,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Graph Topology
     * @param taskId
     * @returns Envelope_GraphTopologyDTO_ Successful Response
     * @throws ApiError
     */
    public static getGraphTopologyApiV1AnalysisTasksTaskIdGraphTopologyGet(
        taskId: string,
    ): CancelablePromise<Envelope_GraphTopologyDTO_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/analysis-tasks/{task_id}/graph-topology',
            path: {
                'task_id': taskId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
