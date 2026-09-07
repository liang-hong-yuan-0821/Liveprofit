/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Envelope_AnalysisDashboardDTO_ } from '../models/Envelope_AnalysisDashboardDTO_';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class AnalysisDashboardService {
    /**
     * Get Dashboard
     * @returns Envelope_AnalysisDashboardDTO_ Successful Response
     * @throws ApiError
     */
    public static getDashboardApiV1AnalysisDashboardGet(): CancelablePromise<Envelope_AnalysisDashboardDTO_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/analysis-dashboard',
        });
    }
}
