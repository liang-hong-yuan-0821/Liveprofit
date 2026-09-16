/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Envelope_QuantStrategyDraftDTO_ } from '../models/Envelope_QuantStrategyDraftDTO_';
import type { Envelope_QuantStrategyDTO_ } from '../models/Envelope_QuantStrategyDTO_';
import type { Envelope_QuantStrategyFormatData_ } from '../models/Envelope_QuantStrategyFormatData_';
import type { Envelope_QuantStrategyListData_ } from '../models/Envelope_QuantStrategyListData_';
import type { Envelope_QuantStrategyPublishData_ } from '../models/Envelope_QuantStrategyPublishData_';
import type { Envelope_QuantStrategyVersionDTO_ } from '../models/Envelope_QuantStrategyVersionDTO_';
import type { QuantStrategyCreateRequest } from '../models/QuantStrategyCreateRequest';
import type { QuantStrategyDraftUpdateRequest } from '../models/QuantStrategyDraftUpdateRequest';
import type { QuantStrategyFormatRequest } from '../models/QuantStrategyFormatRequest';
import type { QuantStrategyPublishRequest } from '../models/QuantStrategyPublishRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class QuantStrategiesService {
    /**
     * List Strategies
     * @returns Envelope_QuantStrategyListData_ Successful Response
     * @throws ApiError
     */
    public static listStrategiesApiV1QuantStrategiesGet(): CancelablePromise<Envelope_QuantStrategyListData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/quant-strategies',
        });
    }
    /**
     * Create Strategy
     * @param requestBody
     * @returns Envelope_QuantStrategyDTO_ Successful Response
     * @throws ApiError
     */
    public static createStrategyApiV1QuantStrategiesPost(
        requestBody: QuantStrategyCreateRequest,
    ): CancelablePromise<Envelope_QuantStrategyDTO_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/quant-strategies',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Format Strategy Source
     * 本地 ruff 格式化策略源码（编辑器体验；不落库、不校验七键合同）。
     * @param requestBody
     * @returns Envelope_QuantStrategyFormatData_ Successful Response
     * @throws ApiError
     */
    public static formatStrategySourceApiV1QuantStrategiesFormatPost(
        requestBody: QuantStrategyFormatRequest,
    ): CancelablePromise<Envelope_QuantStrategyFormatData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/quant-strategies/format',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Strategy
     * @param strategyId
     * @returns Envelope_QuantStrategyDTO_ Successful Response
     * @throws ApiError
     */
    public static getStrategyApiV1QuantStrategiesStrategyIdGet(
        strategyId: string,
    ): CancelablePromise<Envelope_QuantStrategyDTO_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/quant-strategies/{strategy_id}',
            path: {
                'strategy_id': strategyId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Draft
     * @param strategyId
     * @returns Envelope_QuantStrategyDraftDTO_ Successful Response
     * @throws ApiError
     */
    public static getDraftApiV1QuantStrategiesStrategyIdDraftGet(
        strategyId: string,
    ): CancelablePromise<Envelope_QuantStrategyDraftDTO_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/quant-strategies/{strategy_id}/draft',
            path: {
                'strategy_id': strategyId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Save Draft
     * @param strategyId
     * @param requestBody
     * @returns Envelope_QuantStrategyDraftDTO_ Successful Response
     * @throws ApiError
     */
    public static saveDraftApiV1QuantStrategiesStrategyIdDraftPut(
        strategyId: string,
        requestBody: QuantStrategyDraftUpdateRequest,
    ): CancelablePromise<Envelope_QuantStrategyDraftDTO_> {
        return __request(OpenAPI, {
            method: 'PUT',
            url: '/api/v1/quant-strategies/{strategy_id}/draft',
            path: {
                'strategy_id': strategyId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Publish Version
     * @param strategyId
     * @param versionId
     * @param requestBody
     * @returns Envelope_QuantStrategyPublishData_ Successful Response
     * @throws ApiError
     */
    public static publishVersionApiV1QuantStrategiesStrategyIdVersionsVersionIdPublishPost(
        strategyId: string,
        versionId: string,
        requestBody: QuantStrategyPublishRequest,
    ): CancelablePromise<Envelope_QuantStrategyPublishData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/quant-strategies/{strategy_id}/versions/{version_id}/publish',
            path: {
                'strategy_id': strategyId,
                'version_id': versionId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Archive Version
     * @param strategyId
     * @param versionId
     * @returns Envelope_QuantStrategyVersionDTO_ Successful Response
     * @throws ApiError
     */
    public static archiveVersionApiV1QuantStrategiesStrategyIdVersionsVersionIdArchivePost(
        strategyId: string,
        versionId: string,
    ): CancelablePromise<Envelope_QuantStrategyVersionDTO_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/quant-strategies/{strategy_id}/versions/{version_id}/archive',
            path: {
                'strategy_id': strategyId,
                'version_id': versionId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
