/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ComputeRequest } from '../models/ComputeRequest';
import type { ConfirmImpactsRequest } from '../models/ConfirmImpactsRequest';
import type { Envelope_AssetListData_ } from '../models/Envelope_AssetListData_';
import type { Envelope_ComputeData_ } from '../models/Envelope_ComputeData_';
import type { Envelope_ConfirmImpactsData_ } from '../models/Envelope_ConfirmImpactsData_';
import type { Envelope_ImpactDraftListData_ } from '../models/Envelope_ImpactDraftListData_';
import type { Envelope_PendingEventListData_ } from '../models/Envelope_PendingEventListData_';
import type { Envelope_PredictionData_ } from '../models/Envelope_PredictionData_';
import type { Envelope_PrelabelData_ } from '../models/Envelope_PrelabelData_';
import type { Envelope_RefreshData_ } from '../models/Envelope_RefreshData_';
import type { Envelope_ReviewBatchData_ } from '../models/Envelope_ReviewBatchData_';
import type { PredictionRequest } from '../models/PredictionRequest';
import type { PrelabelRequest } from '../models/PrelabelRequest';
import type { ReviewBatchRequest } from '../models/ReviewBatchRequest';
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
    /**
     * List Pending Events
     * @returns Envelope_PendingEventListData_ Successful Response
     * @throws ApiError
     */
    public static listPendingEventsApiV1EventStudiesReviewPendingEventsGet(): CancelablePromise<Envelope_PendingEventListData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/event-studies/review/pending-events',
        });
    }
    /**
     * Prelabel
     * @param requestBody
     * @returns Envelope_PrelabelData_ Successful Response
     * @throws ApiError
     */
    public static prelabelApiV1EventStudiesReviewPrelabelPost(
        requestBody: PrelabelRequest,
    ): CancelablePromise<Envelope_PrelabelData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/event-studies/review/prelabel',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Refresh
     * @returns Envelope_RefreshData_ Successful Response
     * @throws ApiError
     */
    public static refreshApiV1EventStudiesReviewRefreshPost(): CancelablePromise<Envelope_RefreshData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/event-studies/review/refresh',
        });
    }
    /**
     * Submit Batch
     * @param requestBody
     * @returns Envelope_ReviewBatchData_ Successful Response
     * @throws ApiError
     */
    public static submitBatchApiV1EventStudiesReviewBatchPost(
        requestBody: ReviewBatchRequest,
    ): CancelablePromise<Envelope_ReviewBatchData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/event-studies/review/batch',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Compute
     * @param eventId
     * @param requestBody
     * @returns Envelope_ComputeData_ Successful Response
     * @throws ApiError
     */
    public static computeApiV1EventStudiesReviewEventsEventIdComputePost(
        eventId: number,
        requestBody: ComputeRequest,
    ): CancelablePromise<Envelope_ComputeData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/event-studies/review/events/{event_id}/compute',
            path: {
                'event_id': eventId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Impact Drafts
     * @returns Envelope_ImpactDraftListData_ Successful Response
     * @throws ApiError
     */
    public static listImpactDraftsApiV1EventStudiesReviewImpactDraftsGet(): CancelablePromise<Envelope_ImpactDraftListData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/event-studies/review/impact-drafts',
        });
    }
    /**
     * Confirm Impacts
     * @param eventId
     * @param requestBody
     * @returns Envelope_ConfirmImpactsData_ Successful Response
     * @throws ApiError
     */
    public static confirmImpactsApiV1EventStudiesReviewImpactDraftsEventIdConfirmPost(
        eventId: number,
        requestBody: ConfirmImpactsRequest,
    ): CancelablePromise<Envelope_ConfirmImpactsData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/event-studies/review/impact-drafts/{event_id}/confirm',
            path: {
                'event_id': eventId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}
