/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class SystemService {
    /**
     * Health Live
     * @returns any Successful Response
     * @throws ApiError
     */
    public static healthLiveHealthLiveGet(): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/health/live',
        });
    }
    /**
     * Health Ready
     * @returns any Successful Response
     * @throws ApiError
     */
    public static healthReadyHealthReadyGet(): CancelablePromise<Record<string, any>> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/health/ready',
        });
    }
    /**
     * Metrics
     * @returns any Successful Response
     * @throws ApiError
     */
    public static metricsMetricsGet(): CancelablePromise<any> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/metrics',
        });
    }
}
