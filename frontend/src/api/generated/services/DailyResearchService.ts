/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AssessmentReviewRequest } from '../models/AssessmentReviewRequest';
import type { DailyResearchManualRequest } from '../models/DailyResearchManualRequest';
import type { Envelope_AssessmentReviewResponseData_ } from '../models/Envelope_AssessmentReviewResponseData_';
import type { Envelope_DailyResearchManualData_ } from '../models/Envelope_DailyResearchManualData_';
import type { Envelope_DailyResearchRunDetailData_ } from '../models/Envelope_DailyResearchRunDetailData_';
import type { Envelope_DailyResearchRunListData_ } from '../models/Envelope_DailyResearchRunListData_';
import type { Envelope_DisputedAssessmentListData_ } from '../models/Envelope_DisputedAssessmentListData_';
import type { CancelablePromise } from '../core/CancelablePromise';
import { OpenAPI } from '../core/OpenAPI';
import { request as __request } from '../core/request';
export class DailyResearchService {
    /**
     * Create Manual Research Run
     * @param requestBody
     * @param idempotencyKey
     * @returns Envelope_DailyResearchManualData_ Successful Response
     * @throws ApiError
     */
    public static createManualResearchRunApiV1DailyResearchRunsPost(
        requestBody: DailyResearchManualRequest,
        idempotencyKey?: (string | null),
    ): CancelablePromise<Envelope_DailyResearchManualData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/daily-research/runs',
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
     * List Research Runs
     * @param kind
     * @param cursor
     * @param limit
     * @returns Envelope_DailyResearchRunListData_ Successful Response
     * @throws ApiError
     */
    public static listResearchRunsApiV1DailyResearchRunsGet(
        kind?: ('news' | 'quant' | null),
        cursor?: (string | null),
        limit: number = 20,
    ): CancelablePromise<Envelope_DailyResearchRunListData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/daily-research/runs',
            query: {
                'kind': kind,
                'cursor': cursor,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Latest Research Run
     * @param kind
     * @param eventId
     * @returns Envelope_DailyResearchRunDetailData_ Successful Response
     * @throws ApiError
     */
    public static getLatestResearchRunApiV1DailyResearchRunsLatestGet(
        kind: 'news' | 'quant',
        eventId?: (number | null),
    ): CancelablePromise<Envelope_DailyResearchRunDetailData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/daily-research/runs/latest',
            query: {
                'kind': kind,
                'event_id': eventId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Research Run
     * @param taskId
     * @returns Envelope_DailyResearchRunDetailData_ Successful Response
     * @throws ApiError
     */
    public static getResearchRunApiV1DailyResearchRunsTaskIdGet(
        taskId: string,
    ): CancelablePromise<Envelope_DailyResearchRunDetailData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/daily-research/runs/{task_id}',
            path: {
                'task_id': taskId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Disputed Event Assessments
     * List the current unresolved event labels backed by immutable PG assessments.
     * @param limit
     * @returns Envelope_DisputedAssessmentListData_ Successful Response
     * @throws ApiError
     */
    public static listDisputedEventAssessmentsApiV1DailyResearchAssessmentsDisputedGet(
        limit: number = 100,
    ): CancelablePromise<Envelope_DisputedAssessmentListData_> {
        return __request(OpenAPI, {
            method: 'GET',
            url: '/api/v1/daily-research/assessments/disputed',
            query: {
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Review Disputed Event Assessment
     * @param assessmentId
     * @param requestBody
     * @param idempotencyKey
     * @returns Envelope_AssessmentReviewResponseData_ Successful Response
     * @throws ApiError
     */
    public static reviewDisputedEventAssessmentApiV1DailyResearchAssessmentsAssessmentIdReviewPost(
        assessmentId: string,
        requestBody: AssessmentReviewRequest,
        idempotencyKey?: (string | null),
    ): CancelablePromise<Envelope_AssessmentReviewResponseData_> {
        return __request(OpenAPI, {
            method: 'POST',
            url: '/api/v1/daily-research/assessments/{assessment_id}/review',
            path: {
                'assessment_id': assessmentId,
            },
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
}
