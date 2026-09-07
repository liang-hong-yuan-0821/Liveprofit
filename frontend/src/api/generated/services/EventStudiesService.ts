/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Envelope_AssetListData_ } from '../models/Envelope_AssetListData_';
import type { Envelope_PredictionData_ } from '../models/Envelope_PredictionData_';
import type { PredictionRequest } from '../models/PredictionRequest';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class EventStudiesService {
    /**
     * Predict
     * @param requestBody
     * @returns Envelope_PredictionData_ Successful Response
     * @throws ApiError
     */
    public static predictApiV1EventStudiesPredictionsPost(
        requestBody: PredictionRequest,
    ): CancelablePromise<Envelope_PredictionData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/event-studies/predictions',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Assets
     * @returns Envelope_AssetListData_ Successful Response
     * @throws ApiError
     */
    public static listAssetsApiV1EventStudiesAssetsGet(): CancelablePromise<Envelope_AssetListData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/event-studies/assets',
        });
    }
}
