/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Envelope_ReportDTO_ } from '../models/Envelope_ReportDTO_';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class ReportsService {
    /**
     * Get Report
     * @param taskId
     * @returns Envelope_ReportDTO_ Successful Response
     * @throws ApiError
     */
    public static getReportApiV1AnalysisTasksTaskIdReportGet(
        taskId: string,
    ): CancelablePromise<Envelope_ReportDTO_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/analysis-tasks/{task_id}/report',
            path: {
                'task_id': taskId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
