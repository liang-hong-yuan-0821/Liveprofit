/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Envelope_QuantSignalPageData_ } from '../models/Envelope_QuantSignalPageData_';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class QuantSignalsService {
    /**
     * List Quant Signals
     * @param taskId
     * @param kind
     * @param attemptNo
     * @param cursor
     * @param limit
     * @returns Envelope_QuantSignalPageData_ Successful Response
     * @throws ApiError
     */
    public static listQuantSignalsApiV1AnalysisTasksTaskIdQuantSignalsGet(
        taskId: string,
        kind: 'buy' | 'holding' | 'orders' | 'errors' = 'buy',
        attemptNo?: (number | null),
        cursor?: (string | null),
        limit: number = 50,
    ): CancelablePromise<Envelope_QuantSignalPageData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/analysis-tasks/{task_id}/quant-signals',
            path: {
                'task_id': taskId,
            },
            query: {
                'kind': kind,
                'attempt_no': attemptNo,
                'cursor': cursor,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
