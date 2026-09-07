/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { SSEContract } from '../models/SSEContract';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AnalysisEventsService {
    /**
     * Task Events
     * @param taskId
     * @param after
     * @param lastEventId
     * @returns SSEContract Successful Response
     * @throws ApiError
     */
    public static taskEventsApiV1AnalysisTasksTaskIdEventsGet(
        taskId: string,
        after?: (string | null),
        lastEventId?: (string | null),
    ): CancelablePromise<SSEContract> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/analysis-tasks/{task_id}/events',
            path: {
                'task_id': taskId,
            },
            headers: {
                'Last-Event-ID': lastEventId,
            },
            query: {
                'after': after,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
